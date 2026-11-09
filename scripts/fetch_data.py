#!/usr/bin/env python3
"""Fetch OpenCode Go quotas and AA scores via official Data API, then regenerate the HTML (OC only).

- 智力分：走官方 https://artificialanalysis.ai/api/v2/language/models/free 需 x-api-key（免费 Key）
  旧 /api/v2/data/llms/models 已于 2026-11-04 23:59:59 GMT 下线（410 Gone），新接口分页，逐页翻完
  解析失败（接口本身异常）直接抛错，交由 GitHub Actions 记录（作业变红 + 发失败通知，见 daily.yml）；
  AA 免费档覆盖缩水导致的缺分不算失败：保留最后已知分值 + stale/asof 标记，前端展示为灰虚线圈且不参与帕累托
- 配额：仍抓 https://opencode.ai/v2/docs/console/go/
- 模态：scrape 结果缓存到 data/aa-modality-cache.json，新模型才抓取

Usage:
    python fetch_data.py                          # 仅 OC+AA（GOAT 抓取已归档，见 archive/goat_fetch.py）
    python fetch_data.py --date 2026-08-18 --no-generate

数据源：OpenCode 配额、AA 官方 Data API（免费 Key）、LMArena（HF datasets-server）、
AIHOT 模型榜（网页解析，仅前 30 名）。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
QUOTA_PATH = ROOT / "data" / "snapshots" / "quota-snapshots.json"
AA_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
LMARENA_PATH = ROOT / "data" / "snapshots" / "lmarena-scores.json"
LMARENA_ALIAS_PATH = ROOT / "data" / "registry" / "lmarena-alias.json"
LMARENA_ROWS_URL = "https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&config=agent&split=latest&offset={off}&length=100"
LMARENA_FULL_URL = "https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&config=agent&split=full&offset={off}&length=100"
LMARENA_STALE_MAX_DAYS = 60  # 退榜模型最后已知分最多顺延天数，超期仍记缺失
LMARENA_STALE_MAX_DAYS_OVERRIDE = {"Qwen3.6 Plus": 120}  # 配额窗口前退榜（最后见于 2026-07-07），破例顺延
# AIHOT 模型榜（第三方评测综合分）。官方明确「模型榜目前只有网页」，/api/v1 与 OpenAPI
# 里没有榜单端点，只能解析服务端渲染的表格；只取前 30 名（有斩杀线），未上榜属常态。
AIHOT_PATH = ROOT / "data" / "snapshots" / "aihot-scores.json"
AIHOT_ALIAS_PATH = ROOT / "data" / "registry" / "aihot-alias.json"
AIHOT_URL = "https://aihot.news/leaderboard"
AIHOT_STALE_MAX_DAYS = 60  # 掉榜模型最后已知分最多顺延天数
AIHOT_MIN_ROWS = 10  # 低于此行数视为页面改版/抓取失败
MODALITY_CACHE_PATH = ROOT / "data" / "cache" / "aa-modality-cache.json"
GENERATOR_PATH = ROOT / "scripts" / "generate.py"
OPENCODE_URL = "https://opencode.ai/v2/docs/console/go/"
# 旧 /api/v2/data/* 已下线（410 Gone，2026-11-04 23:59:59 GMT）。
# 新免费档端点：{tier, intelligence_index_version, pagination, data}，无 status 信封字段，每页最多 200 条。
AA_API_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_MAX_PAGES = 20  # 免费档当前 4 页（约 800 个模型），留足余量防翻页死循环
# 免费档只覆盖当前主力代（2026-10 起约 90/720 个模型带 intelligence index）；
# 低于该阈值视为接口本身异常（而非覆盖范围变化），本次不更新。
AA_MIN_SCORED_MODELS = 20
USER_AGENT = "opencode-go-model-pareto/1.0 (+https://opencode.ai/v2/docs/console/go/)"

def _load_json_strict_fetch(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"missing required data file {path} ({name})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"failed to load {path}: {e}")

AA_SLUG_ALIAS: dict[str, str] = _load_json_strict_fetch(ROOT / "data" / "registry" / "slug-alias.json", "slug-alias")
LMARENA_ALIAS: dict[str, str | list[str] | None] = _load_json_strict_fetch(LMARENA_ALIAS_PATH, "lmarena-alias")
# 可选：AIHOT 别名（模型名 -> 榜单 slug）。缺文件时只走归一化默认映射。
if AIHOT_ALIAS_PATH.exists():
    AIHOT_ALIAS: dict[str, str | None] = _load_json_strict_fetch(AIHOT_ALIAS_PATH, "aihot-alias")
else:
    AIHOT_ALIAS = {}


def _slug_for_model(model: str) -> str:
    if model in AA_SLUG_ALIAS:
        return AA_SLUG_ALIAS[model]
    candidate = model.lower().replace(" ", "-").replace(".", "-").replace("_", "-")
    while "--" in candidate:
        candidate = candidate.replace("--", "-")
    return candidate.strip("-")


def _load_dotenv() -> None:
    """轻量加载 ROOT/.env 到 os.environ（不覆盖已有环境变量），避免依赖 python-dotenv"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        print(f"load .env failed: {e}", file=sys.stderr)


def _get_api_key() -> str:
    _load_dotenv()
    key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ARTIFICIAL_ANALYSIS_API_KEY 未配置：本地请在 .env 中设置，GitHub Actions 请在 Settings > Secrets and variables > Actions 中添加同名 Secret"
        )
    return key


class TableParser(HTMLParser):
    """Collect rows from every HTML table.

    - `<del>` 包裹的是被划掉的旧值（如促销改价 `<del>6,500</del> 26,000`），直接丢弃；
    - `<small>` 包裹的是单元格备注（如模型行的活动说明），记入与 tables
      平行的 cell_notes，不混入主文本。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.cell_notes: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._notes_table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._notes_row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_note: list[str] | None = None
        self._del_depth = 0
        self._small_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
            self._notes_table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._notes_row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
            self._cell_note = []
        elif tag == "del":
            self._del_depth += 1
        elif tag == "small":
            self._small_depth += 1

    def handle_data(self, data: str) -> None:
        if self._cell is None or self._del_depth:
            return
        if self._small_depth and self._cell_note is not None:
            self._cell_note.append(data)
        else:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            value = " ".join("".join(self._cell).split())
            note = " ".join("".join(self._cell_note or []).split())
            self._row.append(value)
            if self._notes_row is not None:
                self._notes_row.append(note)
            self._cell = None
            self._cell_note = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
                if self._notes_table is not None:
                    self._notes_table.append(self._notes_row or [])
            self._row = None
            self._notes_row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self.cell_notes.append(self._notes_table or [])
            self._table = None
            self._notes_table = None
        elif tag == "del":
            self._del_depth = max(0, self._del_depth - 1)
        elif tag == "small":
            self._small_depth = max(0, self._small_depth - 1)


def fetch(url: str, retries: int = 5, timeout: int = 45, headers: dict[str, str] | None = None) -> str:
    import random
    last_error: Exception | None = None
    base_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Encoding": "gzip",
        "Cache-Control": "no-cache",
    }
    if headers:
        base_headers.update(headers)
    for attempt in range(retries):
        try:
            request = Request(url, headers=base_headers)
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(10 * 1024 * 1024 + 1)
                if len(payload) > 10 * 1024 * 1024:
                    raise ValueError(f"response from {url} exceeds 10 MiB")
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                charset = response.headers.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            last_error = error
            status = getattr(error, "code", None)
            body = ""
            if isinstance(error, HTTPError):
                try:
                    body = error.read().decode(errors="replace")[:500]
                except Exception:
                    pass
            is_retryable_5xx = isinstance(error, HTTPError) and status is not None and 500 <= status < 600
            is_timeout = isinstance(error, (TimeoutError, URLError)) or "timed out" in str(error).lower()
            is_rate_limit = status == 429
            if status == 404:
                # 404 不会因重试而恢复（slug 错误或页面不存在），直接失败由上层如实上报
                break
            if attempt + 1 >= retries:
                break
            base = 4 * (2**attempt) if (is_retryable_5xx or is_timeout or is_rate_limit) else 2**attempt
            sleep_s = min(60, base + random.uniform(0, 1))
            print(f"fetch {url} failed (attempt {attempt+1}/{retries}): {error} (status={status}) body={body[:200]} retry in {sleep_s:.1f}s", file=sys.stderr)
            time.sleep(sleep_s)
    raise RuntimeError(f"failed to fetch {url} after {retries} attempts: {last_error}")


def fetch_aa_via_api() -> dict[str, float]:
    """走官方 Data API 获取智力分，失败直接抛错（由 Actions 记为失败）

    新 V2 接口按 page/page_size 分页（每页最多 200，page_size 传更大无效），
    需顺着 pagination.has_more 翻完才算抓全；响应用 has_more 判断而非固定页数，
    模型总数增长时不会静默截断。"""
    api_key = _get_api_key()
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    scores: dict[str, float] = {}
    total_rows = 0
    page = 1
    while page <= AA_MAX_PAGES:
        raw = fetch(f"{AA_API_URL}?page={page}", retries=5, timeout=45, headers=headers)
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"AA API 返回非 JSON（page={page}），解析失败: {e}\nbody={raw[:1000]}")
        if not isinstance(doc, dict):
            raise RuntimeError(f"AA API 返回非对象（page={page}）body={raw[:1000]}")
        data = doc.get("data")
        if not isinstance(data, list) or not data:
            # 首页就空说明接口异常；后续页空说明已翻到头
            if page == 1:
                raise RuntimeError(f"AA API data 为空或非数组 body={raw[:1000]}")
            break
        total_rows += len(data)
        for item in data:
            slug = item.get("slug")
            evals = item.get("evaluations") or {}
            intel = evals.get("artificial_analysis_intelligence_index")
            if not slug or intel is None:
                continue
            try:
                score = float(intel)
            except (TypeError, ValueError):
                continue
            # API 的 slug 即为 aa_model_id（如 glm-5-3），直接可用
            scores[slug] = score
        if not (doc.get("pagination") or {}).get("has_more"):
            break
        page += 1
        time.sleep(0.3)  # 免费档 100 次/日，翻页间稍作间隔
    if not scores:
        raise RuntimeError(f"AA API 解析后得分为空，已抓 {total_rows} 条但无 intelligence_index")
    print(f"AA API: {page} 页 / {total_rows} 个模型，{len(scores)} 个带 intelligence index")
    return scores


def fetch_lmarena_overall() -> dict[str, dict[str, Any]]:
    """抓 LMArena agent/latest 全量 rows，本地过滤 category=overall。
    返回 {model_name: {score, rank, date}}。score 为官网 Overall Dynamic ranking
    的 win share（约 -0.16 ~ +0.14，4 位小数），与 text_style_control 的
    Bradley-Terry rating（1400 分段）不可比。stdlib 无依赖；服务端限流较严，
    页间 sleep 1s，429/5xx 按 fetch() 同风格退避重试。"""
    import random
    def _get(off: int) -> dict[str, Any]:
        url = LMARENA_ROWS_URL.format(off=off)
        last: Exception | None = None
        for attempt in range(8):
            try:
                req = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                last = e
                if e.code == 404:
                    break
            except (URLError, TimeoutError, OSError) as e:
                last = e
            sleep_s = min(90, 5 * (attempt + 1) + random.uniform(0, 1))
            print(f"lmarena rows {off} failed (attempt {attempt+1}/8): {last} retry in {sleep_s:.1f}s", file=sys.stderr)
            time.sleep(sleep_s)
        raise RuntimeError(f"failed to fetch lmarena rows offset={off}: {last}")
    first = _get(0)
    total = first.get("num_rows_total") or len(first.get("rows", []))
    rows = [r["row"] for r in first.get("rows", [])]
    for off in range(100, total, 100):
        time.sleep(1.0)
        doc = _get(off)
        rows += [r["row"] for r in doc.get("rows", [])]
    overall: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("category") != "overall":
            continue
        name = r.get("model_name")
        if not name or not isinstance(r.get("score"), (int, float)):
            continue
        overall[name] = {"score": float(r["score"]), "rank": r.get("rank"), "date": r.get("leaderboard_publish_date")}
    if not overall:
        raise RuntimeError("lmarena overall 解析为空")
    return overall


def fetch_lmarena_full() -> list[dict[str, Any]]:
    """抓 LMArena agent/full 全量历史 rows（约 1300+ 行，32 个 publish 快照）。
    只做分页拉取与 JSON 解析，不过滤，由 backfill_lmarena_history 按配额日期消费。"""
    import random
    def _get(off: int) -> dict[str, Any]:
        url = LMARENA_FULL_URL.format(off=off)
        last: Exception | None = None
        for attempt in range(8):
            try:
                req = Request(url, headers={"User-Agent": USER_AGENT})
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                last = e
                if e.code == 404:
                    break
            except (URLError, TimeoutError, OSError) as e:
                last = e
            sleep_s = min(90, 5 * (attempt + 1) + random.uniform(0, 1))
            print(f"lmarena full {off} failed (attempt {attempt+1}/8): {last} retry in {sleep_s:.1f}s", file=sys.stderr)
            time.sleep(sleep_s)
        raise RuntimeError(f"failed to fetch lmarena full offset={off}: {last}")
    first = _get(0)
    total = first.get("num_rows_total") or len(first.get("rows", []))
    rows = [r["row"] for r in first.get("rows", [])]
    for off in range(100, total, 100):
        time.sleep(1.0)
        doc = _get(off)
        rows += [r["row"] for r in doc.get("rows", [])]
    if not rows:
        raise RuntimeError("lmarena full 拉取为空")
    return rows


def _norm_agent_name(name: str) -> str:
    """Agent 榜名归一：去括号配置后缀（如 (Max)/(xHigh)/(High) (0813)）、去
    配额侧 Contributor 尾缀，再 lowercase 去非字母数字。用于历史快照的模糊匹配
    （括号内的 Max/High 是评测配置而非不同模型；Qwen Plus/Max 这类非括号差异不受影响）。"""
    base = re.sub(r"\s*\([^)]*\)", "", name or "")
    base = re.sub(r"\s+contributor\s*$", "", base, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def _lmarena_candidates(model: str) -> list[str]:
    """alias 支持单个字符串或按优先级排列的候选列表（LMArena 改名/换检查点后仍可回溯）。"""
    value = LMARENA_ALIAS.get(model)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _match_old_publish(model: str, by_pub: dict[str, dict[str, Any]], before_day: str) -> tuple[str, dict[str, Any], str] | None:
    """配额窗口前就退榜的模型：在 before_day 之前的原始 publish 中找最后一期归一匹配。
    超该模型 cap 直接停（更早只会更老）。返回 (lmarena_model_id, row, publish_date)。"""
    want = _norm_agent_name(model)
    cap = _stale_cap_for(model)
    try:
        end = date.fromisoformat(before_day[:10])
    except ValueError:
        return None
    for pub in sorted(by_pub, reverse=True):
        if pub >= before_day[:10]:
            continue
        rows = by_pub[pub]
        for lid0 in _lmarena_candidates(model):
            if lid0 in rows:
                return lid0, rows[lid0], pub
        cands = [(n, r) for n, r in rows.items() if _norm_agent_name(n) == want]
        if not cands:
            continue
        cands.sort(key=lambda t: (1 if "(" in t[0] else 0, t[1].get("rank") or 9999))
        try:
            age = (end - date.fromisoformat(pub)).days
        except ValueError:
            continue
        if age < 0 or age > cap:
            return None
        return cands[0][0], cands[0][1], pub
    return None


def backfill_lmarena_history(quota_doc: dict[str, Any], lmarena_doc: dict[str, Any], full_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """用 agent/full 历史为每个配额日期重建 LMArena 快照（最新日除外，由每日 latest 流程维护）。
    每个配额日期取 publish_date <= 当日的最近一期榜单；匹配优先级：alias 精确 > 归一模糊
    （同分多配置时优先无括号的基础版，再按 rank）。alias 缺失/榜单无分记 null。"""
    from collections import defaultdict
    by_pub: dict[str, dict[str, Any]] = defaultdict(dict)
    for r in full_rows:
        if r.get("category") != "overall":
            continue
        name = r.get("model_name")
        if not name or not isinstance(r.get("score"), (int, float)):
            continue
        by_pub[r.get("leaderboard_publish_date")][name] = r
    pubs = sorted(by_pub)
    quota_dates = sorted(quota_doc.get("snapshots", {}))
    if not quota_dates:
        raise ValueError("quota snapshots 为空，无法回填")
    latest = quota_dates[-1]
    updated = dict(lmarena_doc)
    updated["metric"] = "LMArena Agent overall score (win share, Overall Dynamic ranking)"
    updated["source"] = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
    updated["source_url"] = "https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&config=agent&split=latest"
    updated.setdefault("snapshots", {})
    for d in quota_dates:
        if d == latest:
            continue
        cand = [p for p in pubs if p <= d[:10]]
        if not cand:
            print(f"lmarena history: no publish <= {d}, skip", file=sys.stderr)
            continue
        pub = max(cand)
        rows = by_pub[pub]
        fuzzy_used: list[str] = []
        models = []
        for qrow in quota_doc["snapshots"][d].get("models", []):
            model = qrow["model"]
            alias_cands = _lmarena_candidates(model)
            alias_lid = alias_cands[0] if alias_cands else None
            lid, hit = alias_lid, None
            for cand in alias_cands:
                if cand in rows:
                    lid, hit = cand, rows[cand]
                    break
            if hit is None:
                want = _norm_agent_name(model)
                cands = [(n, r) for n, r in rows.items() if _norm_agent_name(n) == want]
                if cands:
                    cands.sort(key=lambda t: (1 if "(" in t[0] else 0, t[1].get("rank") or 9999))
                    lid, hit = cands[0][0], cands[0][1]
                    fuzzy_used.append(f"{model}~{lid}")
                else:
                    lid = alias_lid
            if hit is None and model in LMARENA_ALIAS:
                filled = _fill_lmarena_stale(model, updated["snapshots"], d)
                if filled:
                    lid, hit = filled["lmarena_model_id"], {"score": filled["rating"], "rank": filled["rank"]}
                    stale_asof = filled["asof"]
                else:
                    old = _match_old_publish(model, by_pub, d[:10])
                    if old:
                        lid, hit = old[0], {"score": old[1]["score"], "rank": old[1].get("rank")}
                        stale_asof = old[2]
                        fuzzy_used.append(f"{model}~{lid}@{old[2]}")
                    else:
                        stale_asof = None
            else:
                stale_asof = None
            entry = {
                "model": model,
                "lmarena_model_id": lid,
                "rating": round(float(hit["score"]), 4) if hit else None,
                "rank": hit.get("rank") if hit else None,
            }
            if stale_asof:
                entry["stale"] = True
                entry["asof"] = stale_asof
            models.append(entry)
        hits = sum(1 for m in models if m["rating"] is not None)
        nstale = sum(1 for m in models if m.get("stale"))
        print(f"lmarena history {d}: publish={pub} hits={hits}/{len(models)} (stale={nstale})" + (f" fuzzy={fuzzy_used}" if fuzzy_used else ""))
        updated["snapshots"][d] = {"label": "", "source_date": pub, "models": models}
    return updated


def _stale_cap_for(model: str) -> int:
    return LMARENA_STALE_MAX_DAYS_OVERRIDE.get(model, LMARENA_STALE_MAX_DAYS)


def _fill_lmarena_stale(model: str, snapshots: dict[str, Any], before_date: str, max_days: int | None = None) -> dict[str, Any] | None:
    """在已有快照中找 before_date 之前该模型最后一次非 null 评分（顺延）。
    超 LMARENA_STALE_MAX_DAYS 天返回 None。返回 {lmarena_model_id, rating, rank, asof}，
    asof 取该期快照的 source_date（实际榜单日）或快照键。"""
    try:
        end = date.fromisoformat(before_date[:10])
    except ValueError:
        return None
    for d in sorted(snapshots, reverse=True):
        if d >= before_date:
            continue
        snap = snapshots[d]
        if not isinstance(snap, dict):
            continue
        for m in snap.get("models", []):
            if m.get("model") != model or m.get("rating") is None:
                continue
            asof = m.get("asof") or snap.get("source_date") or d[:10]
            try:
                age = (end - date.fromisoformat(asof[:10])).days
            except ValueError:
                continue
            if age < 0 or age > (max_days if max_days is not None else _stale_cap_for(model)):
                return None
            return {"lmarena_model_id": m.get("lmarena_model_id"), "rating": m["rating"],
                    "rank": m.get("rank"), "asof": asof[:10]}
    return None


def update_lmarena_doc(lmarena_doc: dict[str, Any], quota_rows: list[dict[str, Any]], overall: dict[str, dict[str, Any]], snapshot_date: str) -> dict[str, Any]:
    """按配额模型顺序重建当日 LMArena 快照。alias 缺失/榜单无分时用历史最后已知分顺延
    （stale=true + asof，不阻断更新）；无历史或超期才记 null。"""
    snapshots = lmarena_doc.get("snapshots")
    if not isinstance(snapshots, dict) or not snapshots:
        raise ValueError("lmarena JSON does not match the expected schema")
    snap_models = []
    for row in quota_rows:
        model = row["model"]
        cands = _lmarena_candidates(model)
        lid, hit, matched_rank = (cands[0], None, None) if cands else (None, None, None)
        for idx, cand in enumerate(cands):
            if cand in overall:
                lid, hit, matched_rank = cand, overall[cand], idx
                break
        if cands and hit is None:
            print(f"lmarena no rating for {model!r} (ids={cands})，尝试顺延历史", file=sys.stderr)
        elif not cands and model not in LMARENA_ALIAS:
            print(f"lmarena alias missing for new model {model!r}，记为缺失（请补 data/registry/lmarena-alias.json 后重跑）", file=sys.stderr)
        if hit:
            entry = {
                "model": model,
                "lmarena_model_id": lid,
                "rating": round(hit["score"], 4),
                "rank": hit.get("rank"),
            }
            # 主候选（最新检查点）未上榜、退用旧检查点/旧命名时标注旧分，不参与帕累托
            if matched_rank:
                entry["stale"] = True
                entry["asof"] = (hit.get("date") or snapshot_date)[:10]
            snap_models.append(entry)
            continue
        filled = _fill_lmarena_stale(model, snapshots, snapshot_date) if cands else None
        if filled:
            print(f"lmarena stale fill {model!r} <- {filled['asof']} ({filled['rating']})", file=sys.stderr)
            snap_models.append({
                "model": model,
                "lmarena_model_id": filled["lmarena_model_id"],
                "rating": filled["rating"],
                "rank": filled["rank"],
                "stale": True,
                "asof": filled["asof"],
            })
        else:
            snap_models.append({
                "model": model,
                "lmarena_model_id": lid,
                "rating": None,
                "rank": None,
            })
    updated = dict(lmarena_doc)
    updated["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    updated.setdefault("snapshots", {})
    for d, snap in list(updated["snapshots"].items()):
        if d != snapshot_date and snap.get("label") == "今日":
            snap["label"] = ""
    updated["snapshots"][snapshot_date] = {"label": "今日", "models": snap_models}
    return updated


# ---------------------------------------------------------------- AIHOT 模型榜

def _aihot_norm(name: str) -> str:
    """归一化模型名：忽略大小写/空格/连字符，并去掉末尾的检查点日期（如 Qwen3.8 Max 0902）。"""
    s = re.sub(r"\s+\d{4}$", "", name.strip())
    return re.sub(r"[^0-9a-zA-Z.]", "", s).lower()


def fetch_aihot_board() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """抓 AIHOT 综合榜（服务端渲染表格）。返回 ({slug: {...}}, 榜单元信息)。

    官方未提供榜单 API（/api/v1 + OpenAPI 只有资讯类端点），只能解析 HTML；
    行结构：<tr data-slug="..."> 内含 aria-label="<模型名> AIHOT 评分 <分数>"、
    「覆盖 <!-- -->100<!-- -->%」以及人民币三档价格。
    """
    raw = fetch(AIHOT_URL, retries=3, timeout=45, headers={"Accept": "text/html"})
    # React 把拼接文本拆成 <!-- --> 分隔，先抹平再解析
    flat = re.sub(r"<!-- -->", "", raw)
    board: dict[str, dict[str, Any]] = {}
    for idx, match in enumerate(re.finditer(r'<tr data-slug="([^"]+)"(.*?)</tr>', flat, re.S)):
        slug, body = match.group(1), match.group(2)
        label = re.search(r'aria-label="([^"]+?) AIHOT 评分 ([\d.]+)"', body)
        if not label:
            continue
        cov = re.search(r"覆盖\s*(\d+)\s*%", body)
        board[slug] = {
            "name": label.group(1),
            "score": float(label.group(2)),
            "coverage": int(cov.group(1)) if cov else None,
            "rank": idx + 1,
        }
    if len(board) < AIHOT_MIN_ROWS:
        raise RuntimeError(f"AIHOT 榜单只解析到 {len(board)} 行（阀值 {AIHOT_MIN_ROWS}），页面可能改版，本次不更新 AIHOT 数据")
    updated = re.search(r"(\d{2}/\d{2} \d{2}:\d{2})\s*更新", flat)
    evals = re.search(r"(\d+)\s*项评测.{0,80}?(\d+)\s*家机构", flat, re.S)
    meta = {
        "board_updated_at": updated.group(1) if updated else None,
        "evaluations": int(evals.group(1)) if evals else None,
        "institutions": int(evals.group(2)) if evals else None,
        "rows": len(board),
    }
    return board, meta


def _aihot_slug_for(model: str, board_by_norm: dict[str, str]) -> str | None:
    """模型名 -> AIHOT slug。registry 里显式配置优先（null 表示确定不在榜），否则按名字归一化匹配。"""
    if model in AIHOT_ALIAS:
        return AIHOT_ALIAS[model]
    return board_by_norm.get(_aihot_norm(model))


def _fill_aihot_stale(model: str, snapshots: dict[str, Any], before_date: str) -> dict[str, Any] | None:
    """沿历史快照向前找最后已知分（衡量掉榜模型），超过 AIHOT_STALE_MAX_DAYS 天放弃。"""
    for d in sorted((x for x in snapshots if x < before_date), reverse=True):
        for m in snapshots[d].get("models", []):
            if m.get("model") != model or not isinstance(m.get("score"), (int, float)):
                continue
            asof = (m.get("asof") or d)[:10]
            try:
                age = (date.fromisoformat(before_date[:10]) - date.fromisoformat(asof)).days
            except ValueError:
                return None
            if age < 0 or age > AIHOT_STALE_MAX_DAYS:
                return None
            return {"aihot_model_id": m.get("aihot_model_id"), "score": m["score"],
                    "coverage": m.get("coverage"), "rank": m.get("rank"), "asof": asof}
    return None


def update_aihot_doc(aihot_doc: dict[str, Any], quota_rows: list[dict[str, Any]], board: dict[str, dict[str, Any]], snapshot_date: str, source_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """按配额模型顺序重建当日 AIHOT 快照。榜单只收前 30 名，未上榜不算错误：
    有历史就顺延最后已知分（stale=true + asof，不参与帕累托），否则计 null。"""
    snapshots = aihot_doc.get("snapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}
    board_by_norm: dict[str, str] = {}
    for slug, row in board.items():
        board_by_norm.setdefault(_aihot_norm(row["name"]), slug)
    snap_models = []
    fresh = stale = missing = 0
    missing_models: list[str] = []
    for row in quota_rows:
        model = row["model"]
        slug = _aihot_slug_for(model, board_by_norm)
        hit = board.get(slug) if slug else None
        if hit:
            snap_models.append({
                "model": model,
                "aihot_model_id": slug,
                "score": hit["score"],
                "coverage": hit.get("coverage"),
                "rank": hit.get("rank"),
            })
            fresh += 1
            continue
        filled = _fill_aihot_stale(model, snapshots, snapshot_date)
        if filled:
            snap_models.append({
                "model": model,
                "aihot_model_id": filled["aihot_model_id"],
                "score": filled["score"],
                "coverage": filled.get("coverage"),
                "rank": filled.get("rank"),
                "stale": True,
                "asof": filled["asof"],
            })
            stale += 1
            continue
        snap_models.append({"model": model, "aihot_model_id": slug, "score": None, "coverage": None, "rank": None})
        missing += 1
        if slug:
            missing_models.append(f"{model}({slug})")
    if missing_models:
        print(f"AIHOT 榜单未收录 {len(missing_models)} 个模型（榜单只有前 {len(board)} 名）: " + ", ".join(missing_models), file=sys.stderr)
    print(f"AIHOT {snapshot_date}: fresh={fresh} stale={stale} missing={missing}")
    updated = dict(aihot_doc)
    updated["metric"] = "AIHOT 评分（综合榜）"
    updated["source"] = AIHOT_URL
    updated["source_url"] = AIHOT_URL
    updated["source_note"] = (
        "AIHOT 综合榜（第三方评测加权：编程 38%、科研 19%、专业办公 19%、知识问答 14%、真人盲选 10%）；"
        "官方暂无榜单 API，本项目只展示前 30 名内的分数，属个人非商业引用，附回链"
    )
    updated["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    if source_meta:
        updated["source_meta"] = source_meta
    updated.setdefault("snapshots", {})
    for d, snap in list(updated["snapshots"].items()):
        if d != snapshot_date and snap.get("label") == "今日":
            snap["label"] = ""
    updated["snapshots"][snapshot_date] = {"label": "今日", "models": snap_models}
    return updated


def parse_quota_value(value: str) -> int | None:
    """Parse a quota cell. '-' / 无限制 means unlimited / not applicable."""
    cleaned = value.replace(",", "").replace("，", "").replace("\u00a0", "").strip()
    if cleaned in ("-", "–", "—", "无限制", "无限", "不限", "∞"):
        return None
    if cleaned.lower() in ("unlimited", "infinite", "n/a", "na"):
        return None
    if not cleaned.isdigit():
        raise ValueError(f"expected a non-negative integer, '-' or '无限制', got {value!r}")
    return int(cleaned)


def parse_opencode_quotas(source: str) -> list[dict[str, Any]]:
    parser = TableParser()
    parser.feed(source)
    for idx, table in enumerate(parser.tables):
        if not table or len(table[0]) != 4:
            continue
        # 官网表头第一列曾为英文 "Model"，现为中文 "模型"，兼容两者
        # 2026-10 官网迁移到 /v2/docs/，旧 zh-cn URL 301 到英文页，表头随之变回英文，两种都兼容
        if table[0][0] not in ("Model", "模型") or table[0][1:] not in (
            ["每 5 小时请求数", "每周请求数", "每月请求数"],
            ["Requests per 5 hours", "Requests per week", "Requests per month"],
        ):
            continue
        notes = parser.cell_notes[idx] if idx < len(parser.cell_notes) else []
        rows = []
        for i, cells in enumerate(table[1:]):
            if len(cells) != 4:
                continue
            note_cells = notes[i + 1] if i + 1 < len(notes) else []
            note = (note_cells[0] if note_cells else "") or None
            rows.append({
                "model": cells[0],
                "note": note,
                "requests_per_5h": parse_quota_value(cells[1]),
                "requests_per_week": parse_quota_value(cells[2]),
                "requests_per_month": parse_quota_value(cells[3]),
            })
        if rows:
            return rows
    raise RuntimeError("could not find the OpenCode Go quota table")


# ---- Modality 缓存 ----

def _load_modality_cache() -> dict[str, str]:
    if not MODALITY_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(MODALITY_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception as e:
        print(f"load modality cache failed: {e}, will rebuild", file=sys.stderr)
    return {}


def _save_modality_cache(cache: dict[str, str]) -> None:
    MODALITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=MODALITY_CACHE_PATH.parent, delete=False) as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(MODALITY_CACHE_PATH)


def scrape_aa_modality(slug: str) -> str:
    """抓取官网 Input modality。严格模式：404（slug 拼错或 AA 尚未收录）与解析失败一律抛错，
    不回落默认值；由调用方阻断本次更新，失败信息写入 Actions 日志（不标红、不发邮件）
    （变体行请在 data/registry/slug-alias.json 补映射后重跑）。"""
    cache = _load_modality_cache()
    if slug in cache and cache[slug] in ("多模态", "纯文字"):
        return cache[slug]
    from urllib.parse import quote
    url = f"https://artificialanalysis.ai/models/{quote(slug, safe='')}"
    try:
        html = fetch(url, retries=3, timeout=30)
    except Exception as e:
        if "404" in str(e):
            raise RuntimeError(
                f"AA 页面 404：{url}（slug={slug} 可能拼错或 AA 尚未收录；"
                f"若是变体行请在 data/registry/slug-alias.json 补映射）: {e}"
            )
        raise RuntimeError(f"抓取 AA 模态页失败：{url}: {e}")
    m = re.search(r'Input modality.*?sr-only"><p>Supports:\s*([^<]+)</p>', html, re.S | re.I)
    if not m:
        raise RuntimeError(f"AA 模态解析失败：{url} 页面存在但未匹配到 Input modality（官网结构可能变更）")
    supports = m.group(1).strip().lower()
    if "image" in supports or "video" in supports:
        result = "多模态"
    elif "text" in supports:
        result = "纯文字"
    else:
        raise RuntimeError(f"AA 模态值未知：{url} Supports={m.group(1).strip()!r}")
    # 写回缓存
    cache[slug] = result
    try:
        _save_modality_cache(cache)
        print(f"cached modality {slug} -> {result}")
    except Exception as e:
        print(f"save modality cache failed: {e}", file=sys.stderr)
    return result

def _load_icons_json() -> dict[str, str]:
    p = ROOT / "data" / "registry" / "icons.json"
    if not p.exists():
        raise RuntimeError(f"missing required data file {p} (icons)")
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e: raise RuntimeError(f"failed to load {p}: {e}")

def _save_icons_json(icons: dict[str, str]) -> None:
    p = ROOT / "data" / "registry" / "icons.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False) as h:
        json.dump(icons, h, ensure_ascii=False, indent=2, sort_keys=True)
        h.write("\n")
        tmp=Path(h.name)
    tmp.replace(p)

def _fetch_svg_bytes(logo_name: str) -> bytes | None:
    for ext in ["svg", "png", "jpg", "webp"]:
        url = f"https://artificialanalysis.ai/img/logos/{logo_name}_small.{ext}"
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=15) as r:
                data = r.read()
                if data and len(data) < 500_000: return data
        except Exception: continue
    return None

def _fetch_logo_for_model_slug(model_slug: str) -> tuple[str, bytes] | None:
    try:
        html = fetch(f"https://artificialanalysis.ai/models/{model_slug}", retries=2, timeout=15)
        m = re.search(r'/img/logos/([a-z0-9_.-]+_small\.\w+)', html)
        if m:
            fname = m.group(1)
            logo = fname.split("_small")[0]
            data = _fetch_svg_bytes(logo)
            if data: return logo, data
    except Exception: pass
    return None

def ensure_icon_for_brand(brand: str, model_slug: str) -> None:
    """严格：若 data/icons.json 缺 brand 则自动抓取并写入，失败抛错"""
    icons = _load_icons_json()
    if brand in icons: return
    # 尝试直接用 brand 名
    data = _fetch_svg_bytes(brand)
    logo_name = brand
    if not data:
        res = _fetch_logo_for_model_slug(model_slug)
        if res:
            logo_name, data = res
    if not data:
        # 常见品牌映射回落（与 generate 共用逻辑）
        fallback_map = {"zhipu":"zai","muse":"meta","qwen":"alibaba","hunyuan":"tencent","grok":"spacexai","nemotron":"nvidia","step":"stepfun","xiaomimimo":"xiaomi","claude":"anthropic"}
        alt = fallback_map.get(brand)
        if alt: data = _fetch_svg_bytes(alt); logo_name = alt if data else logo_name
    if not data:
        raise RuntimeError(f"图标自动抓取失败：brand={brand} model_slug={model_slug} 无法在 artificialanalysis.ai 找到 /img/logos/*_small.*，请手工在 data/icons.json 追加")
    import base64
    _is_svg = (logo_name and logo_name.endswith(("svg","svg+xml"))) or (data[:200].find(b"<svg") != -1)
    mime = "image/svg+xml" if _is_svg else "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"
    icons[brand]=data_uri
    _save_icons_json(icons)
    print(f"auto-injected icon {brand} ({logo_name}) -> data/icons.json")

def _ensure_model_meta_entry(model: str, brand: str, modality: str) -> None:
    p = ROOT / "data" / "registry" / "model-meta.json"
    try: data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e: raise RuntimeError(f"failed to load {p}: {e}")
    if model in data: return
    data[model] = {"brand": brand, "modality": modality}
    # 按 key 排序写回
    ordered = {k: data[k] for k in sorted(data)}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False) as h:
        json.dump(ordered, h, ensure_ascii=False, indent=2)
        h.write("\n")
        tmp=Path(h.name)
    tmp.replace(p)
    print(f"auto-added model-meta {model} -> {brand}/{modality}")

UNKNOWN_MODALITY = "未知"


def ensure_modality_and_icon_for_models(models: list[str]) -> None:
    """降级模式：为新增模型列表确保模态与图标。模态抓取失败（AA 404/解析失败，
    常见于 AA 尚未收录的新模型）时记为 "未知" 并告警，不阻断整日更新；
    前端以灰色 ? 徽章展示，有分正常画点、无分走虚线+缺失面板。
    图标缺失仍抛错（新厂商需人工确认 logo）。"""
    for model in models:
        slug = _slug_for_model(model)
        try:
            modality = scrape_aa_modality(slug)
        except Exception as e:
            print(f"modality unknown for {model!r} (slug={slug})，记为'未知'稍后回填: {e}", file=sys.stderr)
            modality = UNKNOWN_MODALITY
        # 推断 brand
        low = model.lower()
        if low.startswith("grok"): brand="grok"
        elif low.startswith("glm"): brand="zhipu"
        elif "muse" in low: brand="muse"
        elif low.startswith("qwen"): brand="qwen"
        elif low.startswith("deepseek"): brand="deepseek"
        elif low.startswith("kimi"): brand="kimi"
        elif low.startswith("mimo"): brand="xiaomimimo"
        elif low.startswith("minimax"): brand="minimax"
        elif "hy" in low or "hunyuan" in low: brand="hunyuan"
        elif low.startswith("gpt"): brand="openai"
        elif low.startswith("gemini"): brand="gemini"
        elif "inkling" in low: brand="inkling"
        elif "nemotron" in low: brand="nemotron"
        elif low.startswith("step"): brand="step"
        elif low.startswith("longcat"): brand="longcat"
        elif low.startswith("ox"): brand="ox"
        elif low.startswith("claude"): brand="claude"
        else: brand="unknown"
        _ensure_model_meta_entry(model, brand, modality)
        ensure_icon_for_brand(brand, slug)


def _backfill_unknown_modalities() -> None:
    """每日自愈：对 model-meta.json 里模态为"未知"的模型重试抓取，
    AA 收录后自动回填为多模态/纯文字；仍失败则保持未知并告警。"""
    p = ROOT / "data" / "registry" / "model-meta.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"backfill skipped, failed to load {p}: {e}", file=sys.stderr)
        return
    pending = [m for m, v in data.items()
               if isinstance(v, dict) and v.get("modality") == UNKNOWN_MODALITY]
    if not pending:
        return
    changed = False
    for model in pending:
        slug = _slug_for_model(model)
        try:
            modality = scrape_aa_modality(slug)
        except Exception as e:
            print(f"backfill still unknown: {model!r} (slug={slug}): {e}", file=sys.stderr)
            continue
        data[model]["modality"] = modality
        changed = True
        print(f"backfilled modality {model!r} -> {modality}")
    if changed:
        ordered = {k: data[k] for k in sorted(data)}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False) as h:
            json.dump(ordered, h, ensure_ascii=False, indent=2)
            h.write("\n")
            tmp = Path(h.name)
        tmp.replace(p)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def update_documents(quota_doc: dict[str, Any], aa_doc: dict[str, Any], quota_rows: list[dict[str, Any]], aa_scores: dict[str, float], snapshot_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_snapshots = quota_doc.get("snapshots")
    aa_snapshots = aa_doc.get("snapshots")
    if not isinstance(aa_snapshots, dict):
        # One-time compatibility for the old flat schema.
        legacy_date = str(aa_doc.get("last_fetched_at", ""))[:10] or snapshot_date
        legacy_models = aa_doc.get("models")
        if not isinstance(legacy_models, list):
            raise ValueError("AA JSON does not match the expected schema")
        aa_snapshots = {legacy_date: {"label": "今日", "models": legacy_models}}
    latest_aa_date = max(aa_snapshots)
    latest_aa_models = aa_snapshots[latest_aa_date].get("models")
    if not isinstance(existing_snapshots, dict) or not existing_snapshots or not isinstance(latest_aa_models, list):
        raise ValueError("existing JSON files do not match the expected schema")
    existing_aa = [dict(row) for row in latest_aa_models]

    source_models = [row["model"] for row in quota_rows]
    latest_date = max(existing_snapshots)
    reference_models = [row["model"] for row in existing_snapshots[latest_date]["models"]]
    added = [model for model in source_models if model not in set(reference_models)]
    removed = sorted(set(reference_models) - set(source_models))
    if removed:
        print(f"Models removed from OpenCode page (dropped from latest, kept in history): {removed}", file=sys.stderr)
    if added:
        print(f"New models on the OpenCode page (added): {added}")
        # 自动抓取模态+图标；模态缺失降级为"未知"不阻断
        ensure_modality_and_icon_for_models(added)
    # 每日对模态未知的旧模型重试回填（AA 收录后自愈）
    _backfill_unknown_modalities()
    ordered_models = [m for m in reference_models if m not in removed] + added
    quota_by_model = {row["model"]: row for row in quota_rows}
    snapshot_models = []
    for model in ordered_models:
        row = quota_by_model.get(model)
        if row is None:
            snapshot_models.append({
                "model": model,
                "note": None,
                "requests_per_5h": None,
                "requests_per_week": None,
                "requests_per_month": None,
            })
        else:
            snapshot_models.append({
                "model": model,
                "note": row.get("note"),
                "requests_per_5h": row["requests_per_5h"],
                "requests_per_week": row["requests_per_week"],
                "requests_per_month": row["requests_per_month"],
            })

    before_aa_intel = {row.get("model"): row.get("intelligence") for row in existing_aa}
    would_update_aa = False
    for row in existing_aa:
        slug = row.get("aa_model_id")
        if slug and slug in aa_scores:
            if row.get("intelligence") != aa_scores[slug]:
                would_update_aa = True
                break
        elif not slug:
            candidate = _slug_for_model(row["model"])
            if candidate in aa_scores:
                would_update_aa = True
                break
    quota_identical = False
    if snapshot_date != latest_date:
        latest_models = existing_snapshots[latest_date].get("models")
        quota_identical = snapshot_models == latest_models
        aa_identical = not would_update_aa
        if quota_identical and aa_identical:
            print(f"No change vs {latest_date}, bumping {latest_date} -> {snapshot_date} (date forward, data identical)")
            updated_quota = dict(quota_doc)
            updated_quota["source_url"] = OPENCODE_URL
            updated_quota["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
            # 保留旧日期历史，并在新日期追加一份相同数据。
            updated_quota["snapshots"] = dict(quota_doc["snapshots"])
            old_snap = updated_quota["snapshots"][latest_date]
            # 清理其他 今日 标签
            for d, snap in list(updated_quota["snapshots"].items()):
                if snap.get("label") == "今日":
                    snap["label"] = ""
            updated_quota["snapshots"][snapshot_date] = {**old_snap, "label": "今日"}
            tracked_rows = [row for row in quota_rows if row["requests_per_5h"] is not None]
            reference_row = max(tracked_rows, key=lambda row: row["requests_per_5h"])
            updated_quota["normalization_reference"] = {
                "model": reference_row["model"],
                "requests_per_5h": reference_row["requests_per_5h"],
            }
            updated_aa = dict(aa_doc)
            updated_aa.pop("models", None)
            updated_aa["source_url"] = AA_API_URL
            updated_aa["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
            updated_aa["snapshots"] = dict(aa_snapshots)
            old_aa_snap = updated_aa["snapshots"][latest_aa_date]
            for snap in updated_aa["snapshots"].values():
                if snap.get("label") == "今日":
                    snap["label"] = ""
            updated_aa["snapshots"][snapshot_date] = {**old_aa_snap, "label": "今日"}
            return updated_quota, updated_aa

    updated_quota = dict(quota_doc)
    updated_quota["source_url"] = OPENCODE_URL
    updated_quota["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    updated_quota.setdefault("snapshots", {})
    for d, snap in list(updated_quota["snapshots"].items()):
        if d != snapshot_date and snap.get("label") == "今日":
            snap["label"] = ""
    updated_quota["snapshots"][snapshot_date] = {
        "label": "今日",
        "models": snapshot_models,
    }
    tracked_rows = [row for row in quota_rows if row["requests_per_5h"] is not None]
    reference_row = max(tracked_rows, key=lambda row: row["requests_per_5h"])
    updated_quota["normalization_reference"] = {
        "model": reference_row["model"],
        "requests_per_5h": reference_row["requests_per_5h"],
    }

    updated_aa = dict(aa_doc)
    updated_aa.pop("models", None)
    updated_aa["source_url"] = AA_API_URL
    updated_aa["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    known_aa_models = {row.get("model") for row in existing_aa}
    for model in added:
        if model not in known_aa_models:
            candidate = _slug_for_model(model)
            if candidate in aa_scores:
                aa_id = candidate
                intelligence = aa_scores[aa_id]
                print(f"Auto-mapped new model {model!r} -> {aa_id} ({intelligence})")
            else:
                aa_id = None
                intelligence = None
            existing_aa.append({"model": model, "aa_model_id": aa_id, "intelligence": intelligence})
    for row in existing_aa:
        if not row.get("aa_model_id"):
            candidate = _slug_for_model(row["model"])
            if candidate in aa_scores:
                row["aa_model_id"] = candidate
                row["intelligence"] = aa_scores[candidate]
                print(f"Backfilled {row['model']!r} -> {candidate} ({aa_scores[candidate]})")
    # AA 免费档自 2026-10-29 起只覆盖各家的当前主力代，老模型不再返回 intelligence index。
    # 策略：缺分不再抛错阻断更新，而是保留最后已知分值并标 stale=true + asof；
    # 前端以灰虚线圈展示，明确排除在帕累托之外（新旧口径不可混算）。
    if len(aa_scores) < AA_MIN_SCORED_MODELS:
        raise RuntimeError(
            f"AA API 仅返回 {len(aa_scores)} 个智力分（阈值 {AA_MIN_SCORED_MODELS}），疑似接口异常，本次不更新"
        )
    stale_models: list[str] = []
    for row in existing_aa:
        slug = row.get("aa_model_id")
        if not slug:
            continue
        if slug in aa_scores:
            row["intelligence"] = aa_scores[slug]
            row["asof"] = snapshot_date
            row.pop("stale", None)
        elif row.get("intelligence") is not None:
            asof = row.get("asof") or latest_aa_date
            row["stale"] = True
            row["asof"] = asof
            if row.get("model") not in removed:
                stale_models.append(f"{row['model']}={row['intelligence']}@{asof}")
    if stale_models:
        print(
            f"AA 免费档未覆盖 {len(stale_models)} 个模型，保留最后已知分值并标记停更（不参与帕累托）: "
            + ", ".join(stale_models),
            file=sys.stderr,
        )
    updated_aa["snapshots"] = dict(aa_snapshots)
    for snap in updated_aa["snapshots"].values():
        if snap.get("label") == "今日":
            snap["label"] = ""
    updated_aa["snapshots"][snapshot_date] = {"label": "今日", "models": existing_aa}
    return updated_quota, updated_aa


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(timezone(timedelta(hours=8))).date().isoformat(), help="snapshot date, default: today (Asia/Shanghai)")
    parser.add_argument("--no-generate", action="store_true", help="only update JSON files")
    parser.add_argument("--output-dir", type=Path, help="write updated JSON/HTML under this directory instead of the project root")
    parser.add_argument("--lmarena-history", action="store_true", help="仅用 agent/full 历史回填各配额日期的 LMArena 快照（最新日不动），不抓配额/AA")
    args = parser.parse_args()

    output_dir = args.output_dir or ROOT
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_quota_path = output_dir / "data" / "snapshots" / QUOTA_PATH.name
    output_aa_path = output_dir / "data" / "snapshots" / AA_PATH.name
    output_lmarena_path = output_dir / "data" / "snapshots" / LMARENA_PATH.name
    output_aihot_path = output_dir / "data" / "snapshots" / AIHOT_PATH.name
    output_html_path = output_dir / "index.html"

    if args.lmarena_history:
        print("Backfilling LMArena history from agent/full ...")
        quota_doc = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
        full_rows = fetch_lmarena_full()
        print(f"Fetched {len(full_rows)} agent full rows")
        lmarena_doc = json.loads(LMARENA_PATH.read_text(encoding="utf-8"))
        updated = backfill_lmarena_history(quota_doc, lmarena_doc, full_rows)
        atomic_write_json(output_lmarena_path, updated)
        print(f"Updated {output_lmarena_path}")
        if not args.no_generate:
            import subprocess
            subprocess.run([
                sys.executable,
                str(GENERATOR_PATH),
                "--template", str(ROOT / "template" / "opencode-go-model-pareto.template.html"),
                "--quota", str(QUOTA_PATH),
                "--aa", str(AA_PATH),
                "--lmarena", str(output_lmarena_path),
                "--output", str(output_html_path),
            ], cwd=ROOT, check=True)
        return

    print(f"Fetching {OPENCODE_URL}")
    opencode_source = fetch(OPENCODE_URL)
    quota_rows = parse_opencode_quotas(opencode_source)
    print(f"Fetching {AA_API_URL} (official Data API, free key, paginated)")
    aa_scores = fetch_aa_via_api()
    print(f"Parsed {len(quota_rows)} quota rows and {len(aa_scores)} AA scores (official API)")

    print(f"Fetching LMArena agent/latest (overall)")
    lmarena_overall = fetch_lmarena_overall()
    print(f"Parsed {len(lmarena_overall)} lmarena overall ratings")

    quota_doc = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
    aa_doc = json.loads(AA_PATH.read_text(encoding="utf-8"))
    updated_quota, updated_aa = update_documents(quota_doc, aa_doc, quota_rows, aa_scores, args.date)
    atomic_write_json(output_quota_path, updated_quota)
    atomic_write_json(output_aa_path, updated_aa)
    lmarena_doc = json.loads(LMARENA_PATH.read_text(encoding="utf-8"))
    updated_lmarena = update_lmarena_doc(lmarena_doc, quota_rows, lmarena_overall, args.date)
    atomic_write_json(output_lmarena_path, updated_lmarena)
    print(f"Fetching {AIHOT_URL} (综合榜，网页解析)")
    aihot_board, aihot_meta = fetch_aihot_board()
    print(f"Parsed {len(aihot_board)} aihot rows (updated {aihot_meta.get('board_updated_at')})")
    aihot_doc = json.loads(AIHOT_PATH.read_text(encoding="utf-8")) if AIHOT_PATH.exists() else {}
    updated_aihot = update_aihot_doc(aihot_doc, quota_rows, aihot_board, args.date, aihot_meta)
    atomic_write_json(output_aihot_path, updated_aihot)
    print(f"Updated {output_quota_path}")
    print(f"Updated {output_aa_path}")
    print(f"Updated {output_lmarena_path}")
    print(f"Updated {output_aihot_path}")


    if not args.no_generate:
        import subprocess
        subprocess.run([
            sys.executable,
            str(GENERATOR_PATH),
            "--template", str(ROOT / "template" / "opencode-go-model-pareto.template.html"),
            "--quota", str(output_quota_path),
            "--aa", str(output_aa_path),
            "--lmarena", str(output_lmarena_path),
            "--aihot", str(output_aihot_path),
            "--output", str(output_html_path),
        ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
