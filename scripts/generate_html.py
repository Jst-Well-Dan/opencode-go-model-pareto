#!/usr/bin/env python3
"""Generate the standalone Pareto HTML from the quota and AA JSON files.

Usage:
    python generate_html.py
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = ROOT / "template" / "opencode-go-model-pareto.template.html"
DEFAULT_QUOTA = ROOT / "data" / "quota-snapshots.json"
DEFAULT_AA = ROOT / "data" / "aa-scores.json"
DEFAULT_OUTPUT = ROOT / "opencode-go-model-pareto.html"

# Visual metadata is intentionally kept out of the two data files: the JSON files
# remain pure quota/score data while this mapping describes how models are drawn.
MODEL_META = {
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
}


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


def build_payload(quota_doc: dict[str, Any], aa_doc: dict[str, Any]) -> tuple[int, float, list[dict[str, Any]], dict[str, list[dict[str, int]]], list[str]]:
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

    # Normalize against the most generous quota (largest requests_per_5h) on the
    # latest snapshot, so the model that grants the most requests has cost = 1.0.
    # Models with null quotas (e.g. free/unlimited tiers) are excluded from scaling.
    latest_requests = [row.get("requests_per_5h") for row in first_rows]
    valid_requests = [v for v in latest_requests if isinstance(v, int) and v > 0]
    if not valid_requests:
        raise ValueError(f"snapshot {dates[0]} has no valid requests_per_5h value")
    reference_requests = max(valid_requests)
    x_max = reference_requests / min(valid_requests)

    if set(model_order) != set(aa_models):
        missing_in_aa = sorted(set(model_order) - set(aa_models))
        missing_in_quota = sorted(set(aa_models) - set(model_order))
        raise ValueError(f"quota/AA model sets differ; missing in AA={missing_in_aa}, missing in quota={missing_in_quota}")

    base_data = []
    for model in model_order:
        score = aa_by_model[model]
        meta = MODEL_META.get(model)
        if meta is None:
            raise ValueError(f"MODEL_META is missing visual metadata for {model}")
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
        # Allow historical snapshots to miss models that were added later (e.g. Muse Spark)
        unknown = sorted(set(models) - set(model_order))
        if unknown:
            raise ValueError(f"snapshot {date} contains unknown models not in {dates[0]}: {unknown}")
        rows_by_model = {r["model"]: r for r in rows}
        normalized_rows = []
        for model in model_order:
            row = rows_by_model.get(model)
            if row is None:
                # Model did not exist at this snapshot date -> marked absent
                # (distinct from present-but-unlimited models whose quotas are null)
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

    return reference_requests, x_max, base_data, quota_snapshots, dates


def make_date_options(quota_doc: dict[str, Any], dates: list[str]) -> str:
    options = []
    for index, date in enumerate(dates):
        label = quota_doc["snapshots"][date].get("label", "")
        suffix = f" · {label}" if label else ""
        selected = " selected" if index == 0 else ""
        options.append(f'<option value="{html.escape(date, quote=True)}"{selected}>{html.escape(date + suffix)}</option>')
    return "".join(options)


def generate(template_path: Path, quota_path: Path, aa_path: Path, output_path: Path) -> None:
    template = template_path.read_text(encoding="utf-8")
    quota_doc = load_json(quota_path)
    aa_doc = load_json(aa_path)
    reference, x_max, base_data, quota_snapshots, dates = build_payload(quota_doc, aa_doc)

    replacements = {
        "__QUOTA_REFERENCE__": json.dumps(reference),
        "__X_MAX__": json.dumps(x_max),
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
