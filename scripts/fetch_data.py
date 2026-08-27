#!/usr/bin/env python3
"""Fetch OpenCode Go quotas and AA scores via official Data API, then regenerate the HTML.

- 智力分：走官方 https://artificialanalysis.ai/api/v2/data/llms/models 需 x-api-key（免费 Key）
  解析失败直接抛错，交由 GitHub Actions 失败通知邮件
- 配额：仍抓 https://opencode.ai/docs/zh-cn/go/
- 模态：scrape 结果缓存到 data/aa-modality-cache.json，新模型才抓取

Usage:
    python fetch_data.py
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
QUOTA_PATH = ROOT / "data" / "quota-snapshots.json"
AA_PATH = ROOT / "data" / "aa-scores.json"
MODALITY_CACHE_PATH = ROOT / "data" / "aa-modality-cache.json"
GENERATOR_PATH = ROOT / "scripts" / "generate_html.py"
OPENCODE_URL = "https://opencode.ai/docs/zh-cn/go/"
AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
USER_AGENT = "opencode-go-model-pareto/1.0 (+https://opencode.ai/docs/zh-cn/go/)"
# 手工别名：模型名 -> 官方 slug 不一致时使用
AA_SLUG_ALIAS: dict[str, str] = {
    "DeepSeek V4 Flash Vision Exp": "deepseek-v4-flash-vision",
    "MiMo-V2.5": "mimo-v2-5-0424",
}


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


def scrape_aa_modality(slug: str) -> str | None:
    """带缓存的抓取：历史结果从 data/aa-modality-cache.json 读取，新 slug 才请求官网"""
    cache = _load_modality_cache()
    if slug in cache and cache[slug] in ("多模态", "纯文字"):
        return cache[slug]
    url = f"https://artificialanalysis.ai/models/{slug}"
    try:
        html = fetch(url, retries=3, timeout=30)
    except Exception as e:
        print(f"scrape modality for {slug} failed: {e}", file=sys.stderr)
        return None
    m = re.search(r'Input modality.*?sr-only"><p>Supports:\s*([^<]+)</p>', html, re.S | re.I)
    if not m:
        print(f"scrape modality for {slug}: pattern not found", file=sys.stderr)
        return None
    supports = m.group(1).strip().lower()
    result = None
    if "image" in supports:
        result = "多模态"
    elif "text" in supports:
        result = "纯文字"
    else:
        return None
    # 写回缓存
    cache[slug] = result
    try:
        _save_modality_cache(cache)
        print(f"cached modality {slug} -> {result}")
    except Exception as e:
        print(f"save modality cache failed: {e}", file=sys.stderr)
    return result


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
            print(f"No change vs {latest_date}, skipping snapshot {snapshot_date} (deduplicated)")
            updated_quota = dict(quota_doc)
            updated_quota["source_url"] = OPENCODE_URL
            updated_quota["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
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
    args = parser.parse_args()

    output_dir = args.output_dir or ROOT
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_quota_path = output_dir / "data" / QUOTA_PATH.name
    output_aa_path = output_dir / "data" / AA_PATH.name
    output_html_path = output_dir / "opencode-go-model-pareto.html"

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

    if not args.no_generate:
        import subprocess
        subprocess.run([
            sys.executable,
            str(GENERATOR_PATH),
            "--template", str(ROOT / "template" / "opencode-go-model-pareto.template.html"),
            "--quota", str(output_quota_path),
            "--aa", str(output_aa_path),
            "--output", str(output_html_path),
        ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
