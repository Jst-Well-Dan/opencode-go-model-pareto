#!/usr/bin/env python3
"""Archived GOAT fetcher (manual use only, NOT part of the daily job).

2026-09-08 起每日任务只更新 OpenCode Go，不再抓取 Command GOAT。
本文件是从 scripts/fetch_data.py 原样搬出的 GOAT 链路
（parse_goat_quotas + update_goat_documents + 主流程 GOAT 段），
共享 helper（fetch/TableParser/fetch_aa_via_api/ensure_modality...）仍从
scripts/fetch_data.py 导入。如需手动刷新 data/snapshots/goat-snapshots.json：

    python archive/goat_fetch.py
    python archive/goat_fetch.py --date 2026-09-08 --output-dir test
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ARCHIVE_DIR = Path(__file__).resolve().parent
ROOT = ARCHIVE_DIR.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_data import (  # noqa: E402  共享 helper，仍由 scripts/fetch_data.py 维护
    AA_SLUG_ALIAS,
    TableParser,
    atomic_write_json,
    ensure_modality_and_icon_for_models,
    fetch,
    fetch_aa_via_api,
)

GOAT_URL = "https://commandcode.ai/docs/plans/goat"
GOAT_PATH = ROOT / "data" / "snapshots" / "goat-snapshots.json"

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, help="write updated JSON under this directory instead of the project root")
    args = parser.parse_args()

    output_dir = args.output_dir or ROOT
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_goat_path = output_dir / "data" / "snapshots" / GOAT_PATH.name

    print(f"Fetching {GOAT_URL} (archived manual fetcher, strict)")
    goat_source = fetch(GOAT_URL)
    goat_rows = parse_goat_quotas(goat_source)
    print(f"Parsed {len(goat_rows)} GOAT rows (requests + credits)")
    aa_scores = fetch_aa_via_api()
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


if __name__ == "__main__":
    main()
