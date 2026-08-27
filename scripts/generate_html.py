#!/usr/bin/env python3
"""Generate the standalone Pareto HTML from the quota and AA JSON files.

Usage:
    python generate_html.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "template" / "opencode-go-model-pareto.template.html"
DEFAULT_QUOTA = ROOT / "data" / "quota-snapshots.json"
DEFAULT_AA = ROOT / "data" / "aa-scores.json"
DEFAULT_OUTPUT = ROOT / "opencode-go-model-pareto.html"

# Visual metadata is intentionally kept out of the two data files: the JSON files
# remain pure quota/score data while this mapping describes how models are drawn.
MODEL_META = {
    "Grok 4.6": {"brand": "grok", "modality": "多模态"},
    "GLM-5.3-Flash": {"brand": "zhipu", "modality": "多模态"},
    "Grok 4.5": {"brand": "grok", "modality": "多模态"},
    "GPT 5.6 Luna": {"brand": "openai", "modality": "多模态"},
    "GLM-5.3": {"brand": "zhipu", "modality": "纯文字"},
    "GLM-5.2": {"brand": "zhipu", "modality": "纯文字"},
    "GLM-5.1": {"brand": "zhipu", "modality": "纯文字"},
    "Kimi K3": {"brand": "kimi", "modality": "多模态"},
    "Kimi K2.7 Code": {"brand": "kimi", "modality": "多模态"},
    "Kimi K2.6": {"brand": "kimi", "modality": "多模态"},
    "MiMo-V2.5": {"brand": "xiaomimimo", "modality": "多模态"},
    "MiMo-V2.5-Pro": {"brand": "xiaomimimo", "modality": "纯文字"},
    "MiniMax M3": {"brand": "minimax", "modality": "多模态"},
    "MiniMax M2.7": {"brand": "minimax", "modality": "纯文字"},
    "Muse Spark 1.2 Contributor": {"brand": "muse", "modality": "多模态"},
    "Qwen3.8 Max": {"brand": "qwen", "modality": "多模态"},
    "Qwen3.7 Max": {"brand": "qwen", "modality": "纯文字"},
    "Qwen3.7 Plus": {"brand": "qwen", "modality": "多模态"},
    "Qwen3.6 Plus": {"brand": "qwen", "modality": "多模态"},
    "DeepSeek V4 Pro": {"brand": "deepseek", "modality": "纯文字"},
    "DeepSeek V4 Flash": {"brand": "deepseek", "modality": "纯文字"},
    "DeepSeek V4 Flash Vision Exp": {"brand": "deepseek", "modality": "多模态"},
    "Hy3": {"brand": "hunyuan", "modality": "纯文字"},
    "Ox Alpha Free": {"brand": "ox", "modality": "纯文字"},
    "LongCat-2.0": {"brand": "longcat", "modality": "纯文字"},
}

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

def _scrape_modality(slug: str, timeout: int = 20) -> str | None:
    """从官网 https://artificialanalysis.ai/models/<slug> 抓取 Input modality。免 Pro Key。"""
    url = f"https://artificialanalysis.ai/models/{slug}"
    try:
        req = Request(url, headers={"User-Agent": "opencode-go-model-pareto/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            html_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"scrape modality for {slug} failed: {e}", file=sys.stderr)
        return None
    m = re.search(r'Input modality.*?sr-only"><p>Supports:\s*([^<]+)</p>', html_text, re.S | re.I)
    if not m:
        print(f"scrape modality for {slug}: pattern not found", file=sys.stderr)
        return None
    supports = m.group(1).strip().lower()
    if "image" in supports:
        return "多模态"
    if "text" in supports:
        return "纯文字"
    return None

def _infer_brand(model: str, slug: str | None) -> str:
    """基于模型名前缀推断 brand（与 icons 字典对齐），用于新模型官网抓取失败时的兜底。
    官网抓取本身可通过 logo 映射，但为保持离线可用，这里用启发式。"""
    low = model.lower()
    if low.startswith("grok"):
        return "grok"
    if low.startswith("glm"):
        return "zhipu"
    if low.startswith("kimi"):
        return "kimi"
    if low.startswith("mimo"):
        return "xiaomimimo"
    if low.startswith("minimax"):
        return "minimax"
    if "muse" in low:
        return "muse"
    if low.startswith("qwen"):
        return "qwen"
    if low.startswith("deepseek"):
        return "deepseek"
    if low.startswith("hy"):
        return "hunyuan"
    if low.startswith("ox"):
        return "ox"
    if "longcat" in low:
        return "longcat"
    if low.startswith("gpt"):
        return "openai"
    return "unknown"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def unique_models(rows: list[dict[str, Any]], source: str) -> list[str]:
    models = [row.get("model") for row in rows]
    if any(not isinstance(model, str) or not model for model in models):
        raise ValueError(f"{source} contains a row without a valid model name")
    if len(models) != len(set(models)):
        raise ValueError(f"{source} contains duplicate model names")
    return models


def build_payload(quota_doc: dict[str, Any], aa_doc: dict[str, Any]) -> tuple[int, float, int, int, list[dict[str, Any]], dict[str, list[dict[str, int]]], list[str]]:
    snapshots = quota_doc.get("snapshots")
    aa_rows = aa_doc.get("models")
    if not isinstance(snapshots, dict) or not snapshots:
        raise ValueError("quota JSON must contain a non-empty snapshots object")
    if not isinstance(aa_rows, list) or not aa_rows:
        raise ValueError("AA JSON must contain a non-empty models array")

    aa_models = unique_models(aa_rows, "AA JSON")
    aa_by_model = {row["model"]: row for row in aa_rows}
    dates = sorted(snapshots, reverse=True)
    first_rows = snapshots[dates[0]].get("models")
    if not isinstance(first_rows, list) or not first_rows:
        raise ValueError(f"snapshot {dates[0]} must contain a non-empty models array")
    model_order = unique_models(first_rows, f"snapshot {dates[0]}")
    all_models = set(model_order)
    for d in dates[1:]:
        rows = snapshots[d].get("models") or []
        for r in rows:
            m = r.get("model")
            if m and m not in all_models:
                print(f"Historical model {m!r} from {d} not in latest {dates[0]}, appending to order")
                model_order.append(m)
                all_models.add(m)

    latest_requests = [row.get("requests_per_5h") for row in first_rows]
    valid_requests = [v for v in latest_requests if isinstance(v, int) and v > 0]
    if not valid_requests:
        raise ValueError(f"snapshot {dates[0]} has no valid requests_per_5h value")
    reference_requests = max(valid_requests)
    x_max = reference_requests / min(valid_requests)

    import math
    intelligences = [d for d in [aa_by_model[m].get("intelligence") for m in model_order] if isinstance(d, (int, float))]
    if intelligences:
        y_data_min = min(intelligences)
        y_data_max = max(intelligences)
        y_min = max(0, math.floor(y_data_min - 2))
        y_max = math.ceil(y_data_max + 2)
        if y_max - y_min < 12:
            y_min = max(0, y_min - 4)
            y_max += 4
    else:
        y_min, y_max = 36, 62

    if set(model_order) != set(aa_models):
        missing_in_aa = sorted(set(model_order) - set(aa_models))
        missing_in_quota = sorted(set(aa_models) - set(model_order))
        raise ValueError(f"quota/AA model sets differ; missing in AA={missing_in_aa}, missing in quota={missing_in_quota}")

    base_data = []
    for model in model_order:
        score = aa_by_model[model]
        meta = MODEL_META.get(model)
        if meta is None:
            # 新模型默认走官网抓取，不再兜底为 unknown/纯文字
            slug = score.get("aa_model_id") or _slug_for_model(model)
            modality = _scrape_modality(slug) if slug else None
            if modality is None:
                raise ValueError(f"MODEL_META missing for {model!r} (slug={slug}) and website scrape failed; please add manually or check AA site")
            brand = _infer_brand(model, slug)
            print(f"MODEL_META missing for {model!r}, scraped from https://artificialanalysis.ai/models/{slug}: modality={modality}, brand={brand}")
            meta = {"brand": brand, "modality": modality}
        base_data.append({
            "model": model,
            "intelligence": score.get("intelligence"),
            **meta,
        })

    quota_snapshots: dict[str, list[dict[str, int | None]]] = {}
    for date in dates:
        snapshot = snapshots[date]
        rows = snapshot.get("models")
        if not isinstance(rows, list):
            raise ValueError(f"snapshot {date} must contain a models array")
        models = unique_models(rows, f"snapshot {date}")
        unknown = sorted(set(models) - set(model_order))
        if unknown:
            raise ValueError(f"snapshot {date} contains unknown models not in {dates[0]}: {unknown}")
        rows_by_model = {r["model"]: r for r in rows}
        normalized_rows = []
        for model in model_order:
            row = rows_by_model.get(model)
            if row is None:
                normalized_rows.append({"requests": None, "weekly": None, "monthly": None, "absent": True})
            else:
                values = {
                    "requests": row.get("requests_per_5h"),
                    "weekly": row.get("requests_per_week"),
                    "monthly": row.get("requests_per_month"),
                }
                for key, value in values.items():
                    if value is not None and (not isinstance(value, int) or value <= 0):
                        raise ValueError(f"snapshot {date}, {row['model']} has invalid {key} value: {value!r}")
                normalized_rows.append(values)
        quota_snapshots[date] = normalized_rows

    return reference_requests, x_max, y_min, y_max, base_data, quota_snapshots, dates


def make_date_options(quota_doc: dict[str, Any], dates: list[str]) -> str:
    options = []
    for index, date in enumerate(dates):
        raw_label = quota_doc["snapshots"][date].get("label", "")
        if index == 0:
            label = "今日" if raw_label in ("", "今日", "历史") else raw_label
        else:
            label = "" if raw_label in ("今日", "历史") else raw_label
        suffix = f" · {label}" if label else ""
        selected = " selected" if index == 0 else ""
        options.append(f'<option value="{html.escape(date, quote=True)}"{selected}>{html.escape(date + suffix)}</option>')
    return "".join(options)


def generate(template_path: Path, quota_path: Path, aa_path: Path, output_path: Path) -> None:
    template = template_path.read_text(encoding="utf-8")
    quota_doc = load_json(quota_path)
    aa_doc = load_json(aa_path)
    reference, x_max, y_min, y_max, base_data, quota_snapshots, dates = build_payload(quota_doc, aa_doc)

    norm_ref = quota_doc.get("normalization_reference", {"model": "配额最多者", "requests_per_5h": reference})
    y_ticks = list(range(y_min, y_max + 1, 4))
    replacements = {
        "__QUOTA_REFERENCE__": json.dumps(reference),
        "__NORMALIZATION_REFERENCE__": json.dumps(norm_ref, ensure_ascii=False),
        "__X_MAX__": json.dumps(x_max),
        "__Y_MIN__": json.dumps(y_min),
        "__Y_MAX__": json.dumps(y_max),
        "__Y_TICKS__": json.dumps(y_ticks),
        "__BASE_DATA__": json.dumps(base_data, ensure_ascii=False, indent=2),
        "__QUOTA_SNAPSHOTS__": json.dumps(quota_snapshots, ensure_ascii=False, indent=2),
        "<!-- __DATE_OPTIONS__ -->": make_date_options(quota_doc, dates),
    }
    for marker, value in replacements.items():
        if marker not in template:
            raise ValueError(f"template marker not found: {marker}")
        template = template.replace(marker, value)

    output_path.write_text(template, encoding="utf-8")
    print(f"Generated {output_path}")
    print(f"  dates: {', '.join(dates)}")
    print(f"  models: {len(base_data)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--quota", type=Path, default=DEFAULT_QUOTA)
    parser.add_argument("--aa", type=Path, default=DEFAULT_AA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.template, args.quota, args.aa, args.output)


if __name__ == "__main__":
    main()
