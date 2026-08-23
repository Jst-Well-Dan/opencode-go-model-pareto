#!/usr/bin/env python3
"""Fetch OpenCode Go quotas and AA scores, then regenerate the HTML.

The parser intentionally targets semantic table/row markers instead of fixed
character offsets. Files are only replaced after both pages pass validation.

Usage:
    python fetch_data.py
    python fetch_data.py --date 2026-08-18 --no-generate
"""

from __future__ import annotations

import argparse
import gzip
import html as html_lib
import json
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
GENERATOR_PATH = ROOT / "scripts" / "generate_html.py"
OPENCODE_URL = "https://opencode.ai/docs/zh-cn/go/"
AA_URL = "https://aihot.virxact.com/leaderboard/methodology"
USER_AGENT = "opencode-go-model-pareto/1.0 (+https://opencode.ai/docs/zh-cn/go/)"


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


class AARowParser(HTMLParser):
    """Extract model slugs and scores from AIHOT leaderboard rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._row_depth = 0

    @staticmethod
    def classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self.classes(attrs)
        if "lb-source-model-row" in classes:
            self._row = {}
            self._row_depth = 1
        elif self._row is not None:
            self._row_depth += 1
        if self._row is not None and "lb-source-model-name" in classes:
            self._capture = "model"
            self._buffer = []
        elif self._row is not None and "lb-source-model-score" in classes:
            self._capture = "score"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is not None and tag in {"span", "strong"}:
            value = " ".join("".join(self._buffer).split())
            if value:
                assert self._row is not None
                self._row[self._capture] = value
            self._capture = None
            self._buffer = []
        if self._row is not None:
            self._row_depth -= 1
            if self._row_depth == 0:
                if "model" in self._row and "score" in self._row:
                    self.rows.append(self._row)
                self._row = None


def fetch(url: str, retries: int = 3, timeout: int = 30) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip",
                "Cache-Control": "no-cache",
            })
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
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to fetch {url} after {retries} attempts: {last_error}")


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


def parse_aa_scores(source: str) -> dict[str, float]:
    parser = AARowParser()
    parser.feed(source)
    scores: dict[str, float] = {}
    for row in parser.rows:
        model = html_lib.unescape(row["model"])
        raw_score = row["score"].replace(",", ".")
        try:
            score = float(raw_score)
        except ValueError:
            continue
        # Keep the first occurrence of a slug; the visible AA Index table is
        # ordered and variants with suffixes have different slugs.
        scores.setdefault(model, score)
    if not scores:
        raise RuntimeError("could not find AA leaderboard model rows")
    return scores


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
    # Newly listed models are appended automatically (with null quotas/scores
    # until MODEL_META and AA mapping catch up); removals still need manual care.
    added = [model for model in source_models if model not in set(reference_models)]
    removed = sorted(set(reference_models) - set(source_models))
    if removed:
        raise RuntimeError(f"OpenCode model list changed; removed={removed}. Update JSON/MODEL_META manually first.")
    if added:
        print(f"New models on the OpenCode page (added with null quotas): {added}")
    ordered_models = reference_models + added
    quota_by_model = {row["model"]: row for row in quota_rows}
    snapshot_models = []
    for model in ordered_models:
        row = quota_by_model[model]
        snapshot_models.append({
            "model": model,
            "requests_per_5h": row["requests_per_5h"],
            "requests_per_week": row["requests_per_week"],
            "requests_per_month": row["requests_per_month"],
        })

    updated_quota = dict(quota_doc)
    updated_quota["source_url"] = OPENCODE_URL
    updated_quota["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    updated_quota.setdefault("snapshots", {})
    # 将此前“今日”快照降为无标签（历史），避免出现多个“今日”
    for d, snap in list(updated_quota["snapshots"].items()):
        if d != snapshot_date and snap.get("label") == "今日":
            snap["label"] = ""
    updated_quota["snapshots"][snapshot_date] = {
        "label": "今日",
        "models": snapshot_models,
    }
    # Record the most generous quota model (largest requests_per_5h).
    tracked_rows = [row for row in quota_rows if row["requests_per_5h"] is not None]
    reference_row = max(tracked_rows, key=lambda row: row["requests_per_5h"])
    updated_quota["normalization_reference"] = {
        "model": reference_row["model"],
        "requests_per_5h": reference_row["requests_per_5h"],
    }

    updated_aa = dict(aa_doc)
    updated_aa["source_url"] = AA_URL
    updated_aa["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    known_aa_models = {row.get("model") for row in existing_aa}
    for model in added:
        if model not in known_aa_models:
            existing_aa.append({"model": model, "aa_model_id": None, "intelligence": None})
    missing_scores = []
    for row in existing_aa:
        slug = row.get("aa_model_id")
        if not slug:
            continue
        if slug in aa_scores:
            row["intelligence"] = aa_scores[slug]
        elif row.get("intelligence") is not None:
            missing_scores.append(slug)
    if missing_scores:
        raise RuntimeError(f"AA page did not contain previously known model IDs: {missing_scores}")
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
    print(f"Fetching {AA_URL}")
    aa_source = fetch(AA_URL)
    quota_rows = parse_opencode_quotas(opencode_source)
    aa_scores = parse_aa_scores(aa_source)
    print(f"Parsed {len(quota_rows)} quota rows and {len(aa_scores)} AA scores")

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
