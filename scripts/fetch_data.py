#!/usr/bin/env python3
"""Fetch OpenCode Go quotas and AA scores via official Data API, then regenerate the HTML.

- 智力分：走官方 https://artificialanalysis.ai/api/v2/data/llms/models 需 x-api-key（免费 Key）
  解析失败直接抛错，交由 GitHub Actions 失败通知邮件
- 配额：仍抓 https://opencode.ai/docs/zh-cn/go/
- GOAT：默认同步 https://commandcode.ai/docs/plans/goat 核心两表
  （Estimated request counts + Monthly credits），严格失败不回退；可用 --no-include-goat 跳过
- 模态：scrape 结果缓存到 data/aa-modality-cache.json，新模型才抓取

Usage:
    python fetch_data.py                          # 默认含 GOAT
    python fetch_data.py --no-include-goat         # 仅 OC+AA
    python fetch_data.py --date 2026-08-18 --no-generate
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
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
QUOTA_PATH = ROOT / "data" / "snapshots" / "quota-snapshots.json"
AA_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
GOAT_PATH = ROOT / "data" / "snapshots" / "goat-snapshots.json"
MODALITY_CACHE_PATH = ROOT / "data" / "cache" / "aa-modality-cache.json"
GENERATOR_PATH = ROOT / "scripts" / "generate.py"
OPENCODE_URL = "https://opencode.ai/docs/zh-cn/go/"
GOAT_URL = "https://commandcode.ai/docs/plans/goat"
AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
USER_AGENT = "opencode-go-model-pareto/1.0 (+https://opencode.ai/docs/zh-cn/go/)"

def _load_json_strict_fetch(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"missing required data file {path} ({name})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"failed to load {path}: {e}")

AA_SLUG_ALIAS: dict[str, str] = _load_json_strict_fetch(ROOT / "data" / "registry" / "slug-alias.json", "slug-alias")


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
    """Collect rows from every HTML table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            value = " ".join("".join(self._cell).split())
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


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
    """走官方 Data API 获取智力分，失败直接抛错（由 Actions 邮件通知）"""
    api_key = _get_api_key()
    raw = fetch(AA_API_URL, retries=5, timeout=45, headers={"x-api-key": api_key, "Accept": "application/json"})
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"AA API 返回非 JSON，解析失败: {e}\nbody={raw[:1000]}")
    if not isinstance(doc, dict) or doc.get("status") != 200:
        raise RuntimeError(f"AA API 返回异常 status: {doc.get('status')} body={raw[:1000]}")
    data = doc.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"AA API data 为空或非数组 body={raw[:1000]}")
    scores: dict[str, float] = {}
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
    if not scores:
        raise RuntimeError(f"AA API 解析后得分为空，已抓 {len(data)} 条但无 intelligence_index")
    return scores


def parse_quota_value(value: str) -> int | None:
    """Parse a quota cell. '-' means unlimited / not applicable (e.g. free tier)."""
    cleaned = value.replace(",", "").replace("，", "").replace("\u00a0", "").strip()
    if cleaned == "-":
        return None
    if not cleaned.isdigit():
        raise ValueError(f"expected a non-negative integer or '-', got {value!r}")
    return int(cleaned)


def parse_opencode_quotas(source: str) -> list[dict[str, Any]]:
    parser = TableParser()
    parser.feed(source)
    expected_headers = ["Model", "每 5 小时请求数", "每周请求数", "每月请求数"]
    for table in parser.tables:
        if not table or table[0] != expected_headers:
            continue
        rows = []
        for cells in table[1:]:
            if len(cells) != 4:
                continue
            rows.append({
                "model": cells[0],
                "requests_per_5h": parse_quota_value(cells[1]),
                "requests_per_week": parse_quota_value(cells[2]),
                "requests_per_month": parse_quota_value(cells[3]),
            })
        if rows:
            return rows
    raise RuntimeError("could not find the OpenCode Go quota table")


# ---- GOAT parsing (仅核心两表，严格失败) ----

def _norm(s: str) -> str:
    """模型名归一：去符号/空格/大小写差异（供跨表匹配）。"""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_goat_quotas(source: str) -> list[dict[str, Any]]:
    """从 GOAT 页面解析 Estimated request counts + Monthly credits + Intelligence 三表合并.

    返回 [{model, monthly_credits, requests_per_5h, requests_per_week, requests_per_month,
           intelligence}, ...]
    严格模式：任一核心表未找到即抛错（由上层转为邮件告警）
    """
    parser = TableParser()
    parser.feed(source)

    req_map: dict[str, dict[str, int]] = {}
    credit_map: dict[str, int] = {}
    intel_by_norm: dict[str, float] = {}
    for tbl in parser.tables:
        if not tbl:
            continue
        header = tbl[0]
        # 1. Estimated request counts
        if header == ["Model", "Requests / 5 hours", "Requests / week", "Requests / month"]:
            for cells in tbl[1:]:
                if len(cells) != 4:
                    continue
                name = cells[0].strip()
                if not name or name.lower() == "model":
                    continue
                try:
                    r5 = int(cells[1].replace(",", ""))
                    rw = int(cells[2].replace(",", ""))
                    rm = int(cells[3].replace(",", ""))
                except ValueError:
                    continue
                if name not in req_map:
                    req_map[name] = {"requests_per_5h": r5, "requests_per_week": rw, "requests_per_month": rm}
        # 2. Monthly credits
        elif header == ["Model", "Input", "Output", "Cache Read", "Cache Write", "Monthly credits"]:
            for cells in tbl[1:]:
                if len(cells) != 6:
                    continue
                name = cells[0].strip()
                if not name or name.lower() == "model":
                    continue
                credit_raw = cells[5].replace("$", "").replace(",", "").strip()
                try:
                    credit = int(credit_raw)
                except ValueError:
                    continue
                if name not in credit_map:
                    credit_map[name] = credit
        # 2b. Intelligence 列（AA Intelligence Index，GOAT 表自带）：header 表0 Model↕/Intelligence↕
        elif header and header[0] == "Model↕" and len(header) >= 3 and header[2] == "Intelligence↕":
            for cells in tbl[1:]:
                if len(cells) < 3:
                    continue
                raw = cells[0]
                # 清洗后缀：Off-peak... / -50% / Free...
                clean = re.sub(r"Off-peak.*", "", raw).strip()
                clean = re.sub(r"-\d+%.*", "", clean).strip()
                clean = re.sub(r"Free.*", "", clean).strip().strip()
                intel_raw = cells[2].strip()
                if intel_raw.lower().startswith("not yet"):
                    continue
                try:
                    val = float(intel_raw)
                except ValueError:
                    continue
                intel_by_norm[_norm(clean)] = val

    if not req_map:
        raise RuntimeError("could not find GOAT 'Estimated request counts' table")
    if not credit_map:
        raise RuntimeError("could not find GOAT 'Monthly credits' table")

    # 3. 合并：保留页面出现顺序（truncate text-white 为主），request+credit 并集
    model_order: list[str] = re.findall(r"truncate text-white[^>]*>([^<]+)</span>", source)
    seen: set[str] = set()
    ordered: list[str] = []
    for n in model_order:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    all_names = set(req_map) | set(credit_map)
    for n in all_names:
        if n not in seen:
            ordered.append(n)

    rows: list[dict[str, Any]] = []
    for name in ordered:
        if name not in req_map and name not in credit_map:
            continue
        req = req_map.get(name, {})
        rows.append({
            "model": name,
            "monthly_credits": credit_map.get(name),
            "requests_per_5h": req.get("requests_per_5h"),
            "requests_per_week": req.get("requests_per_week"),
            "requests_per_month": req.get("requests_per_month"),
            "intelligence": intel_by_norm.get(_norm(name)),
        })
    if not rows:
        raise RuntimeError("GOAT merged rows empty")
    return rows


def update_goat_documents(goat_doc: dict[str, Any], goat_rows: list[dict[str, Any]], snapshot_date: str) -> dict[str, Any]:
    """维护 data/goat-snapshots.json 的 snapshots[date] 结构，严格去重."""
    existing_snapshots = goat_doc.get("snapshots")
    if not isinstance(existing_snapshots, dict):
        # 首次创建
        existing_snapshots = {}
        goat_doc = dict(goat_doc)
        goat_doc["snapshots"] = existing_snapshots

    # 去重：与最新快照完全一致则仅更新 last_fetched_at
    if existing_snapshots:
        latest_date = max(existing_snapshots)
        latest_models = existing_snapshots[latest_date].get("models")
        # 当前行按 model 排序后比较，避免顺序抖动
        def _key(m: dict[str, Any]) -> str:
            return m.get("model", "")
        if snapshot_date != latest_date and sorted(goat_rows, key=_key) == sorted(latest_models or [], key=_key):
            print(f"GOAT no change vs {latest_date}, bumping {latest_date} -> {snapshot_date} (date forward)")
            updated = dict(goat_doc)
            updated["source_url"] = GOAT_URL
            updated["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
            updated["snapshots"] = dict(goat_doc["snapshots"])
            old_snap = updated["snapshots"].pop(latest_date)
            for d, snap in list(updated["snapshots"].items()):
                if snap.get("label") == "今日":
                    snap["label"] = ""
            old_snap["label"] = "今日"
            updated["snapshots"][snapshot_date] = old_snap
            tracked = [r for r in goat_rows if r.get("requests_per_5h") is not None]
            if tracked:
                ref = max(tracked, key=lambda r: r["requests_per_5h"])
                updated["normalization_reference"] = {"model": ref["model"], "requests_per_5h": ref["requests_per_5h"]}
            return updated

    # GOAT 新增模型同样严格抓取模态+图标
    if existing_snapshots:
        latest_date_for_goat = max(existing_snapshots)
        existing_models = {m["model"] for m in existing_snapshots[latest_date_for_goat].get("models", [])}
        goat_added = [r["model"] for r in goat_rows if r["model"] not in existing_models]
        if goat_added:
            print(f"GOAT new models (added): {goat_added}")
            ensure_modality_and_icon_for_models(goat_added)
    else:
        # 首次建库：全部视为新增
        ensure_modality_and_icon_for_models([r["model"] for r in goat_rows])
    # 新增/覆盖快照
    updated = dict(goat_doc)
    updated["source_url"] = GOAT_URL
    updated["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    updated.setdefault("snapshots", {})
    # 清理旧 label
    for d, snap in list(updated["snapshots"].items()):
        if d != snapshot_date and snap.get("label") == "今日":
            snap["label"] = ""
    # 按 requests_per_5h 降序存储便于阅读（与 quota 一致为升序则此处改为升序更易 Pareto）
    # 保持与页面顺序一致：不强制排序，保留 parse 顺序
    updated["snapshots"][snapshot_date] = {
        "label": "今日",
        "models": goat_rows,
    }
    tracked = [r for r in goat_rows if r.get("requests_per_5h") is not None]
    if tracked:
        ref = max(tracked, key=lambda r: r["requests_per_5h"])
        updated["normalization_reference"] = {"model": ref["model"], "requests_per_5h": ref["requests_per_5h"]}
    return updated


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
    不回落默认值；由调用方阻断本次更新并经由 Actions 失败通知人工处理
    （变体行请在 data/registry/slug-alias.json 补映射后重跑）。"""
    cache = _load_modality_cache()
    if slug in cache and cache[slug] in ("多模态", "纯文字"):
        return cache[slug]
    url = f"https://artificialanalysis.ai/models/{slug}"
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
        fallback_map = {"zhipu":"zai","muse":"meta","qwen":"alibaba","hunyuan":"tencent","grok":"spacexai","nemotron":"nvidia","step":"stepfun","xiaomimimo":"xiaomi"}
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

def ensure_modality_and_icon_for_models(models: list[str]) -> None:
    """严格模式：为新增模型列表确保模态与图标。模态抓取失败（404/解析失败）直接抛错阻断，
    不回落默认值；由 GitHub Actions 失败通知人工处理。"""
    for model in models:
        slug = _slug_for_model(model)
        modality = scrape_aa_modality(slug)  # 失败即抛错，不兜底
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
        else: brand="unknown"
        _ensure_model_meta_entry(model, brand, modality)
        ensure_icon_for_brand(brand, slug)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def update_documents(quota_doc: dict[str, Any], aa_doc: dict[str, Any], quota_rows: list[dict[str, Any]], aa_scores: dict[str, float], snapshot_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_snapshots = quota_doc.get("snapshots")
    existing_aa = aa_doc.get("models")
    if not isinstance(existing_snapshots, dict) or not existing_snapshots or not isinstance(existing_aa, list):
        raise ValueError("existing JSON files do not match the expected schema")

    source_models = [row["model"] for row in quota_rows]
    latest_date = max(existing_snapshots)
    reference_models = [row["model"] for row in existing_snapshots[latest_date]["models"]]
    added = [model for model in source_models if model not in set(reference_models)]
    removed = sorted(set(reference_models) - set(source_models))
    if removed:
        print(f"Models removed from OpenCode page (dropped from latest, kept in history): {removed}", file=sys.stderr)
    if added:
        print(f"New models on the OpenCode page (added): {added}")
        # 严格自动抓取模态+图标，失败抛错阻断
        ensure_modality_and_icon_for_models(added)
    ordered_models = [m for m in reference_models if m not in removed] + added
    quota_by_model = {row["model"]: row for row in quota_rows}
    snapshot_models = []
    for model in ordered_models:
        row = quota_by_model.get(model)
        if row is None:
            snapshot_models.append({
                "model": model,
                "requests_per_5h": None,
                "requests_per_week": None,
                "requests_per_month": None,
            })
        else:
            snapshot_models.append({
                "model": model,
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
            # 搬运：删除旧 latest，写入新日期同一份数据，label 保持 今日
            updated_quota["snapshots"] = dict(quota_doc["snapshots"])
            old_snap = updated_quota["snapshots"].pop(latest_date)
            # 清理其他 今日 标签
            for d, snap in list(updated_quota["snapshots"].items()):
                if snap.get("label") == "今日":
                    snap["label"] = ""
            old_snap["label"] = "今日"
            updated_quota["snapshots"][snapshot_date] = old_snap
            tracked_rows = [row for row in quota_rows if row["requests_per_5h"] is not None]
            reference_row = max(tracked_rows, key=lambda row: row["requests_per_5h"])
            updated_quota["normalization_reference"] = {
                "model": reference_row["model"],
                "requests_per_5h": reference_row["requests_per_5h"],
            }
            updated_aa = dict(aa_doc)
            updated_aa["source_url"] = AA_API_URL
            updated_aa["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
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
    missing_scores = []
    for row in existing_aa:
        slug = row.get("aa_model_id")
        if not slug:
            continue
        if slug in aa_scores:
            row["intelligence"] = aa_scores[slug]
        elif row.get("intelligence") is not None:
            if row.get("model") in removed:
                print(f"AA score missing for retired model {row['model']} ({slug}), keeping previous {row['intelligence']}", file=sys.stderr)
            else:
                missing_scores.append(slug)
    if missing_scores:
        raise RuntimeError(f"AA API 未包含已知模型 IDs: {missing_scores}，请检查官方接口或模型是否更名")
    return updated_quota, updated_aa


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(), help="snapshot date, default: today")
    parser.add_argument("--no-generate", action="store_true", help="only update JSON files")
    parser.add_argument("--output-dir", type=Path, help="write updated JSON/HTML under this directory instead of the project root")
    parser.add_argument("--include-goat", action=argparse.BooleanOptionalAction, default=True, help="also fetch GOAT plan core tables (strict failure, default: enabled; use --no-include-goat to skip)")
    args = parser.parse_args()

    output_dir = args.output_dir or ROOT
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_quota_path = output_dir / "data" / "snapshots" / QUOTA_PATH.name
    output_aa_path = output_dir / "data" / "snapshots" / AA_PATH.name
    output_goat_path = output_dir / "data" / "snapshots" / GOAT_PATH.name
    output_html_path = output_dir / "index.html"

    print(f"Fetching {OPENCODE_URL}")
    opencode_source = fetch(OPENCODE_URL)
    quota_rows = parse_opencode_quotas(opencode_source)
    print(f"Fetching {AA_API_URL} (official Data API, free key)")
    aa_scores = fetch_aa_via_api()
    print(f"Parsed {len(quota_rows)} quota rows and {len(aa_scores)} AA scores (official API)")

    quota_doc = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
    aa_doc = json.loads(AA_PATH.read_text(encoding="utf-8"))
    updated_quota, updated_aa = update_documents(quota_doc, aa_doc, quota_rows, aa_scores, args.date)
    atomic_write_json(output_quota_path, updated_quota)
    atomic_write_json(output_aa_path, updated_aa)
    print(f"Updated {output_quota_path}")
    print(f"Updated {output_aa_path}")

    if args.include_goat:
        print(f"Fetching {GOAT_URL} (include-goat, strict)")
        goat_source = fetch(GOAT_URL)
        goat_rows = parse_goat_quotas(goat_source)
        print(f"Parsed {len(goat_rows)} GOAT rows (requests + credits)")
        # Fast/HighSpeed 变体复用基座智力（手工别名），否则 GOAT 表 Intelligence 为空
        for r in goat_rows:
            if r.get("intelligence") is None and r["model"] in AA_SLUG_ALIAS:
                alias_slug = AA_SLUG_ALIAS[r["model"]]
                if alias_slug in aa_scores:
                    r["intelligence"] = aa_scores[alias_slug]
                    print(f"GOAT alias backfill {r['model']} -> {alias_slug} ({r['intelligence']})")
        if GOAT_PATH.exists():
            goat_doc = json.loads(GOAT_PATH.read_text(encoding="utf-8"))
        else:
            goat_doc = {"source_url": GOAT_URL, "last_fetched_at": datetime.now(timezone.utc).isoformat(), "normalization_reference": {}, "snapshots": {}}
        updated_goat = update_goat_documents(goat_doc, goat_rows, args.date)
        atomic_write_json(output_goat_path, updated_goat)
        print(f"Updated {output_goat_path}")

    if not args.no_generate:
        import subprocess
        subprocess.run([
            sys.executable,
            str(GENERATOR_PATH),
            "--template", str(ROOT / "template" / "opencode-go-model-pareto.template.html"),
            "--quota", str(output_quota_path),
            "--goat-quota", str(output_goat_path),
            "--aa", str(output_aa_path),
            "--output", str(output_html_path),
        ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
