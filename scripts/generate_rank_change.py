#!/usr/bin/env python3
"""Generate a standalone OpenCode AA score/rank-change HTML card."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AA_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
QUOTA_PATH = ROOT / "data" / "snapshots" / "quota-snapshots.json"
META_PATH = ROOT / "data" / "registry" / "model-meta.json"
ICONS_PATH = ROOT / "data" / "registry" / "icons.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows_for_date(aa: dict, day: str) -> list[dict]:
    snapshots = aa.get("snapshots")
    if not isinstance(snapshots, dict):
        return aa.get("models", [])
    dates = sorted(snapshots)
    candidates = [d for d in dates if d <= day]
    return snapshots[max(candidates) if candidates else dates[0]].get("models", [])


def detect_change(aa: dict) -> tuple[str, str]:
    dates = sorted((aa.get("snapshots") or {}).keys())
    transitions = []
    for before, after in zip(dates, dates[1:]):
        old = {r["model"]: r.get("intelligence") for r in rows_for_date(aa, before)}
        new = {r["model"]: r.get("intelligence") for r in rows_for_date(aa, after)}
        diffs = [new[m] - old[m] for m in old.keys() & new.keys() if isinstance(old[m], (int, float)) and isinstance(new[m], (int, float))]
        if diffs:
            transitions.append((sum(diffs) / len(diffs), before, after))
    if not transitions:
        raise RuntimeError("未找到至少两个可比较的 AA 评分快照")
    _, before, after = max(transitions, key=lambda item: abs(item[0]))
    return before, after


def build_rows(aa: dict, quota: dict) -> tuple[str, str, list[dict]]:
    before_date, after_date = detect_change(aa)
    old = {r["model"]: r.get("intelligence") for r in rows_for_date(aa, before_date)}
    new = {r["model"]: r.get("intelligence") for r in rows_for_date(aa, after_date)}
    latest_models = [r["model"] for r in quota["snapshots"][max(quota["snapshots"])]["models"]]
    common = [m for m in latest_models if isinstance(old.get(m), (int, float)) and isinstance(new.get(m), (int, float))]
    old_order = sorted(common, key=lambda m: (-old[m], m))
    new_order = sorted(common, key=lambda m: (-new[m], m))
    old_rank = {m: i + 1 for i, m in enumerate(old_order)}
    new_rank = {m: i + 1 for i, m in enumerate(new_order)}
    rows = [{"model": m, "before": old[m], "after": new[m], "before_rank": old_rank[m], "after_rank": new_rank[m], "delta": old_rank[m] - new_rank[m]} for m in common]
    rows.sort(key=lambda r: (-r["delta"], r["after_rank"], r["model"]))
    return before_date, after_date, rows


def build_svg(rows: list[dict], icons: dict[str, str], brands: dict[str, str]) -> str:
    width, left, right, top, row_h = 1080, 250, 930, 58, 32
    height = max(260, 92 + len(rows) * row_h)
    count = max(1, len(rows))

    def x_rank(rank: int) -> float:
        return left + (rank - 1) / (count - 1 or 1) * (right - left)

    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    parts = [f'<svg class="rank-chart" viewBox="0 0 {width} {height}" role="img">', '<title>AA 评分标准变化前后 OpenCode Go 模型相对排名</title>']
    for rank in dict.fromkeys([1, math.ceil(count / 2), count]):
        x = x_rank(rank)
        parts.append(f'<line x1="{x:.1f}" y1="34" x2="{x:.1f}" y2="{height - 34}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{x:.1f}" y="28" text-anchor="middle" fill="#64748b" font-size="11">#{rank}</text>')
    parts.append(f'<text x="{left}" y="18" fill="#64748b" font-size="12" font-weight="700">相对排名（越左越高）</text>')
    for i, row in enumerate(rows):
        y = top + i * row_h
        x_old, x_new = x_rank(row["before_rank"]), x_rank(row["after_rank"])
        delta = row["delta"]
        color = "#16a34a" if delta > 0 else "#dc2626" if delta < 0 else "#94a3b8"
        label = f"↑ +{delta}" if delta > 0 else f"↓ {delta}" if delta < 0 else "≈ 0"
        brand = brands.get(row["model"], "unknown")
        icon = html.escape(icons.get(brand, icons.get("unknown", "")), quote=True)
        parts.extend([
            f'<line x1="{left}" y1="{y + 15}" x2="{right}" y2="{y + 15}" stroke="#f1f5f9"/>',
            f'<image href="{icon}" x="14" y="{y - 9}" width="18" height="18" preserveAspectRatio="xMidYMid meet"/>',
            f'<text x="40" y="{y + 3}" fill="#172235" font-size="12" font-weight="700">{esc(row["model"])}</text>',
            f'<text x="40" y="{y + 17}" fill="#94a3b8" font-size="10">{row["before"]:.1f} → {row["after"]:.1f}</text>',
            f'<line x1="{x_old:.1f}" y1="{y}" x2="{x_new:.1f}" y2="{y}" stroke="#cbd5e1" stroke-width="3" stroke-linecap="round"/>',
            f'<circle cx="{x_old:.1f}" cy="{y}" r="7" fill="#2563eb" stroke="#fff" stroke-width="2"/>',
            f'<circle cx="{x_new:.1f}" cy="{y}" r="7" fill="#ea580c" stroke="#fff" stroke-width="2"/>',
            f'<text x="1062" y="{y + 4}" text-anchor="end" fill="{color}" font-size="11.5" font-weight="800">{label}</text>',
        ])
    parts.append("</svg>")
    return "\n".join(parts)


def generate(output: Path) -> None:
    aa, quota = load(AA_PATH), load(QUOTA_PATH)
    brands = {m: v.get("brand", "unknown") for m, v in load(META_PATH).items()}
    icons = load(ICONS_PATH)
    before, after, rows = build_rows(aa, quota)
    svg = build_svg(rows, icons, brands)
    up = sum(row["delta"] > 0 for row in rows)
    down = sum(row["delta"] < 0 for row in rows)
    html_out = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AA 评分标准变化 · OpenCode Go 排名</title>
<style>
:root{{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--teal:#0f766e}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#eef2f7}}
body{{font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);padding:28px 16px}}
.shell{{width:min(1080px,100%);margin:auto}}.card{{background:#fff;border:1px solid #e6eef6;border-radius:28px;overflow:hidden;box-shadow:0 24px 64px #0f172a24}}
.top{{height:6px;background:linear-gradient(90deg,#2563eb,#0f766e,#ea580c)}}.head{{padding:34px 40px 0;display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}
.eyebrow{{font-size:12.5px;letter-spacing:.14em;color:var(--teal);font-weight:800}}h1{{margin:6px 0 0;font-size:34px;line-height:1.15;letter-spacing:-.03em}}.sub{{margin:10px 0 0;color:#475569;max-width:700px}}
.badge{{background:#0f172a;color:#fff;border-radius:999px;padding:9px 14px;font-weight:700;white-space:nowrap}}.legend{{display:flex;gap:14px;flex-wrap:wrap;padding:22px 40px 0;color:var(--muted);font-size:12px}}.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}}.before{{background:#2563eb}}.after{{background:#ea580c}}
.chart-wrap{{overflow-x:auto;padding:8px 20px 18px}}.rank-chart{{display:block;width:100%;min-width:760px;height:auto}}.summary{{margin:0 40px 24px;padding:13px 16px;background:#f8fafc;border:1px solid #e6eef6;border-radius:14px;color:#475569;font-size:12.5px}}.summary b{{color:#172235}}.up{{color:#16a34a;font-weight:750}}.down{{color:#dc2626;font-weight:750}}
.foot{{padding:16px 40px 22px;border-top:1px solid #f1f5f9;color:#94a3b8;font-size:11.5px}}a{{color:#64748b}}@media(max-width:720px){{.head{{padding-left:22px;padding-right:22px;flex-direction:column}}.legend{{padding-left:22px;padding-right:22px}}.summary{{margin-left:22px;margin-right:22px}}.foot{{padding-left:22px;padding-right:22px}}}}
</style></head><body><main class="shell"><div class="card"><div class="top"></div>
<header class="head"><div><div class="eyebrow">OpenCode Go · AA Intelligence Index</div><h1>评分标准变化 · <span>相对排名迁移</span></h1><p class="sub">比较 {before} 与 {after} 两套 AA 评分标准下，OpenCode Go 模型的相对排名。排名数字越小越靠前；分数仅用于展示口径变化。</p></div><div class="badge">{after} 最新</div></header>
<div class="legend"><span><i class="dot before"></i>变化前 · {before}</span><span><i class="dot after"></i>变化后 · {after}</span><span>↑ 排名上升　↓ 排名下降</span></div>
<div class="chart-wrap">{svg}</div>
<div class="summary"><b>结果：</b> <span class="up">{up} 个模型排名上升</span> · <span class="down">{down} 个模型排名下降</span> · {len(rows)} 个模型前后均有评分。排名按 AA Intelligence Index 从高到低计算。</div>
<footer class="foot">数据来源：<a href="https://aihot.virxact.com/leaderboard/methodology">Artificial Analysis</a> Intelligence Index 与 <a href="https://opencode.ai/docs/zh-cn/go/">OpenCode Go</a> 快照 · 数据全部内嵌，可离线打开<br>生成脚本：scripts/generate_rank_change.py</footer>
</div></main></body></html>'''
    output.write_text(html_out, encoding="utf-8")
    print(f"Generated {output} ({len(html_out)} bytes): {before} -> {after}, {len(rows)} models")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "aa-rank-change.html")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    generate(output)


if __name__ == "__main__":
    main()
