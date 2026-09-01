#!/usr/bin/env python3
"""Generate social-share cards (1080px vertical) for latest snapshot — 3 graphs.

Unified card generator for OpenCode Go Pareto / Command GOAT Pareto / Comparison dumbbell.
Only latest snapshot is rendered; output is flat in cards/ as *-card.png.

Usage:
  python scripts/generate_card.py              # 生成 3 张 cards/*-card.png（默认）
  python scripts/generate_card.py --type oc    # 仅 OC
  python scripts/generate_card.py --type goat  # 仅 GOAT
  python scripts/generate_card.py --type cmp   # 仅对比
  python scripts/generate_card.py --keep-html  # 同时保留中间 HTML 供调试
  python scripts/generate_card.py --no-image   # 仅生成 HTML，不截图
"""
import argparse
import json
import math
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "template/opencode-go-model-pareto.template.html"

def _load_json_strict_card(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"missing required data file {path} ({name})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"failed to load {path}: {e}")

MODEL_META = _load_json_strict_card(ROOT / "data" / "registry" / "model-meta.json", "model-meta")
ICONS_JSON = _load_json_strict_card(ROOT / "data" / "registry" / "icons.json", "icons")
CURATED_DEFS = _load_json_strict_card(ROOT / "data" / "registry" / "curated-defs.json", "curated-defs")
SLUG_ALIAS_CARD = _load_json_strict_card(ROOT / "data" / "registry" / "slug-alias.json", "slug-alias")

def _slug_for_model_card(m: str) -> str:
    if m in SLUG_ALIAS_CARD:
        return SLUG_ALIAS_CARD[m]
    c = m.lower().replace(" ", "-").replace(".", "-").replace("_", "-")
    while "--" in c:
        c = c.replace("--", "-")
    return c.strip("-")

def _get_model_meta_strict_card(m: str) -> dict:
    if m not in MODEL_META:
        raise RuntimeError(f"model {m!r} not found in data/model-meta.json — 请先通过 fetch_data 自动写入或手工追加")
    v = MODEL_META[m]
    if v.get("modality") not in ("多模态", "纯文字"):
        raise RuntimeError(f"data/model-meta.json modality for {m!r} invalid: {v}")
    return v


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_icons() -> dict:
    return dict(ICONS_JSON)



def is_pareto(p, allpts):
    for q in allpts:
        if q is p:
            continue
        if q["intel"] >= p["intel"] and q["cost"] <= p["cost"] and (q["intel"] > p["intel"] or q["cost"] < p["cost"]):
            return False
    return True


def _infer_goat_brand(m: str) -> str:
    low = m.lower()
    if low.startswith("grok"):
        return "grok"
    if low.startswith("glm"):
        return "zhipu"
    if "muse" in low:
        return "muse"
    if low.startswith("qwen"):
        return "qwen"
    if low.startswith("deepseek"):
        return "deepseek"
    if low.startswith("kimi"):
        return "kimi"
    if low.startswith("mimo"):
        return "xiaomimimo"
    if low.startswith("minimax"):
        return "minimax"
    if "hy3" in low or "hunyuan" in low:
        return "hunyuan"
    if low.startswith("gpt"):
        return "openai"
    if "inkling" in low:
        return "inkling"
    if "nemotron" in low:
        return "nemotron"
    if low.startswith("step"):
        return "step"
    if low.startswith("gemini"):
        return "gemini"
    return "unknown"


# ---------- SVG helpers ----------
def build_pareto_svg(pts, frontier, refs, xMax, icons, W=1000, H=420):
    M = {"l": 74, "r": 18, "t": 26, "b": 42}
    yMin, yMax = 36, 62

    def x(v):
        return M["l"] + math.log10(v) / math.log10(xMax) * (W - M["l"] - M["r"])

    def y(v):
        return H - M["b"] - (v - yMin) / (yMax - yMin) * (H - M["t"] - M["b"])

    svg_parts = []
    svg_parts.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">')
    svg_parts.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#0f766e"/></marker></defs>')
    for v in [36, 40, 44, 48, 52, 56, 60]:
        svg_parts.append(f'<line x1="{M["l"]}" y1="{y(v):.1f}" x2="{W-M["r"]}" y2="{y(v):.1f}" stroke="#e8eef5" stroke-width="1"/>')
        svg_parts.append(f'<text x="{M["l"]-10}" y="{y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#64748b" font-family="system-ui">{v}</text>')
    for v in [1, 2, 5, 10, 20, 50, 100, 200, 400]:
        if v > xMax * 1.02:
            continue
        svg_parts.append(f'<line x1="{x(v):.1f}" y1="{M["t"]}" x2="{x(v):.1f}" y2="{H-M["b"]}" stroke="#e8eef5" stroke-width="1"/>')
        svg_parts.append(f'<text x="{x(v):.1f}" y="{H-M["b"]+18}" text-anchor="middle" font-size="11" fill="#64748b" font-family="system-ui">{v}</text>')
    svg_parts.append(f'<line x1="{M["l"]}" y1="{H-M["b"]}" x2="{W-M["r"]}" y2="{H-M["b"]}" stroke="#94a3b8" stroke-width="1.3"/>')
    svg_parts.append(f'<line x1="{M["l"]}" y1="{M["t"]}" x2="{M["l"]}" y2="{H-M["b"]}" stroke="#94a3b8" stroke-width="1.3"/>')
    svg_parts.append(f'<text x="{(M["l"]+W-M["r"])/2:.0f}" y="{H-6}" text-anchor="middle" font-size="12.5" fill="#334155" font-weight="650">相对配额成本（越低越好 · 对数刻度）</text>')
    svg_parts.append(f'<text x="16" y="{(M["t"]+H-M["b"])/2:.0f}" transform="rotate(-90 16 {(M["t"]+H-M["b"])/2:.0f})" text-anchor="middle" font-size="12.5" fill="#334155" font-weight="650">AA Intelligence Index</text>')
    svg_parts.append('<line x1="150" y1="78" x2="92" y2="42" stroke="#0f766e" stroke-width="1.6" marker-end="url(#arrow)"/>')
    svg_parts.append('<text x="156" y="82" font-size="11.5" fill="#0f766e" font-weight="700">理想方向：左上 ↖</text>')
    if frontier:
        pts_str = " ".join(f'{x(d["cost"]):.1f},{y(d["intel"]):.1f}' for d in frontier)
        svg_parts.append(f'<polyline points="{pts_str}" fill="none" stroke="#ea580c" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>')
    ordered = [p for p in pts if not p["pareto"]] + frontier
    for p in ordered:
        cx, cy = x(p["cost"]), y(p["intel"])
        stroke = "#ea580c" if p["pareto"] else "#cbd5e1"
        sw = "3.6" if p["pareto"] else "1.4"
        badge_color = "#2563eb" if p["modality"] == "多模态" else "#0f766e"
        badge_mark = "M" if p["modality"] == "多模态" else "T"
        href = icons.get(p["brand"], icons.get("unknown", ""))
        svg_parts.append(f'<g transform="translate({cx:.1f} {cy:.1f})">')
        svg_parts.append(f'<circle r="17" fill="#fff" stroke="{stroke}" stroke-width="{sw}"/>')
        svg_parts.append(f'<image href="{href}" x="-11" y="-11" width="22" height="22" preserveAspectRatio="xMidYMid meet"/>')
        svg_parts.append(f'<circle cx="12" cy="12" r="7.2" fill="{badge_color}" stroke="#fff" stroke-width="1.4"/>')
        svg_parts.append(f'<text x="12" y="12" text-anchor="middle" dominant-baseline="central" font-size="8.5" font-weight="800" fill="#fff" font-family="system-ui">{badge_mark}</text>')
        svg_parts.append("</g>")
    for d in refs:
        cx = M["l"] if d["free"] else x(d["cost"])
        label = "免费 · 不限" if d["free"] else f'{d["model"]}（无 AA 分）'
        svg_parts.append(f'<g transform="translate({cx:.1f} {H-M["b"]})">')
        svg_parts.append('<circle r="13" fill="#fff" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="3 3"/>')
        svg_parts.append(f'<image href="{icons.get(d["brand"], icons.get("unknown",""))}" x="-8" y="-8" width="16" height="16" opacity="0.6" preserveAspectRatio="xMidYMid meet"/>')
        svg_parts.append(f'<text y="-19" text-anchor="middle" font-size="10.5" font-weight="600" fill="#64748b" font-family="system-ui">{label}</text>')
        svg_parts.append("</g>")
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def build_cmp_svg(rows, icons, W=1000, MT=18, MB=42, ROWH=52):
    ML, MR = 164, 96
    H = MT + len(rows) * ROWH + MB
    # log scale
    allM = [v for r in rows for v in [r["oc_m"], r["goat_m"]] if v and v > 0]
    LO, HI = min(allM), max(allM)
    Y0 = math.pow(10, math.log10(LO) - 0.15)
    Y1 = math.pow(10, math.log10(HI) + 0.12)

    def xLog(v):
        return (math.log10(v) - math.log10(Y0)) / (math.log10(Y1) - math.log10(Y0)) * (W - ML - MR) + ML

    def fmt(v):
        if v is None:
            return "—"
        if v >= 1e6:
            return f"{v/1e6:.2f}M"
        if v >= 1e3:
            return f"{v/1e3:.1f}k" if v < 1e5 else f"{v/1e3:.0f}k"
        return str(v)

    def logTicks(lo, hi):
        out, seen = [], set()
        p = math.floor(math.log10(lo))
        guard = 0
        while p <= math.ceil(math.log10(hi)) and guard < 200:
            guard += 1
            for b in [1, 2, 5]:
                v = b * math.pow(10, p)
                if v > hi:
                    break
                if v >= lo and round(v * 1e4) not in seen:
                    out.append(v)
                    seen.add(round(v * 1e4))
            p += 1
        return out

    svg_parts = []
    svg_parts.append(f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">')
    # x grid
    for v in logTicks(Y0, Y1):
        xx = xLog(v)
        svg_parts.append(f'<line x1="{xx:.1f}" y1="{MT}" x2="{xx:.1f}" y2="{H-MB}" stroke="#dce4ed" stroke-width="1"/>')
        svg_parts.append(f'<text x="{xx:.1f}" y="{H-MB+18}" text-anchor="middle" fill="#64748b" font-size="11" font-family="system-ui">{fmt(v)}</text>')
    for i, d in enumerate(rows):
        yc = MT + i * ROWH + ROWH / 2
        xo = xLog(d["oc_m"])
        xg = xLog(d["goat_m"] or d["oc_m"])
        # row line
        svg_parts.append(f'<line x1="{ML}" y1="{yc}" x2="{W-MR}" y2="{yc}" stroke="#dce4ed" stroke-width="1"/>')
        # icon + name + IQ
        svg_parts.append(f'<image href="{icons.get(d["brand"], icons.get("unknown",""))}" x="12" y="{yc-19}" width="16" height="16" preserveAspectRatio="xMidYMid meet"/>')
        svg_parts.append(f'<text x="34" y="{yc-2:.1f}" fill="#172235" font-size="11.5" font-weight="700" font-family="system-ui">{d["short"]}</text>')
        iq_txt = f'IQ {d["iq"]:.1f}' if isinstance(d["iq"], (int, float)) else "IQ —"
        svg_parts.append(f'<text x="34" y="{yc+13:.1f}" fill="#64748b" font-size="9.5" font-family="system-ui">{iq_txt}</text>')
        # segment
        stroke = "#ea580c" if (d["goat_m"] or 0) > (d["oc_m"] or 0) else "#2563eb"
        svg_parts.append(f'<line x1="{xo:.1f}" y1="{yc}" x2="{xg:.1f}" y2="{yc}" stroke="{stroke}" stroke-width="1.7" stroke-dasharray="5 4"/>')
        # OC dot
        svg_parts.append(f'<circle cx="{xo:.1f}" cy="{yc}" r="6.5" fill="#2563eb" stroke="#fff" stroke-width="1.4"/>')
        # GOAT diamond
        r = 7.5
        svg_parts.append(f'<path d="M {xg:.1f} {yc-r:.1f} L {xg+r:.1f} {yc:.1f} L {xg:.1f} {yc+r:.1f} L {xg-r:.1f} {yc:.1f} Z" fill="#ea580c" stroke="#fff" stroke-width="1.4"/>')
        # winner tag
        ratio = (d["goat_m"] / d["oc_m"]) if d["goat_m"] and d["oc_m"] else None
        if ratio is not None:
            if ratio >= 1.05:
                txt, col = f"GOAT +{(ratio-1)*100:.0f}%", "#ea580c"
            elif ratio <= 0.95:
                txt, col = f"OC +{(1/ratio-1)*100:.0f}%", "#2563eb"
            else:
                txt, col = "≈", "#94a3b8"
            svg_parts.append(f'<text x="{W-MR+12}" y="{yc+4:.1f}" fill="{col}" font-size="11" font-weight="750" font-family="system-ui">{txt}</text>')
    svg_parts.append(f'<text x="{(ML+W-MR)/2:.0f}" y="{H-10}" text-anchor="middle" fill="#64748b" font-size="12" font-weight="600" font-family="system-ui">每月请求数 (log) →</text>')
    svg_parts.append("</svg>")
    return "\n".join(svg_parts), H


# ---------- card builders ----------

def build_oc_card(date, quota, aa, icons):
    latest = max(quota["snapshots"])
    _tracked = [r["requests_per_5h"] for r in quota["snapshots"][latest]["models"] if r["requests_per_5h"]]
    ref = max(_tracked)
    xMax_global = ref / min(_tracked)
    aa_map = {m["model"]: m["intelligence"] for m in aa["models"]}
    snap = quota["snapshots"][date]["models"]
    snap_by = {r["model"]: r for r in snap}
    pts = []
    for model in [r["model"] for r in quota["snapshots"][latest]["models"]]:
        if model not in snap_by:
            continue
        r = snap_by[model]
        if not r["requests_per_5h"] or aa_map.get(model) is None:
            continue
        meta = _get_model_meta_strict_card(model)
        pts.append({"model": model, "requests": r["requests_per_5h"], "intel": aa_map[model], "cost": ref / r["requests_per_5h"], "brand": meta["brand"], "modality": meta["modality"]})
    for p in pts:
        p["pareto"] = is_pareto(p, pts)
    refs = []
    for model in [r["model"] for r in quota["snapshots"][latest]["models"]]:
        r = snap_by.get(model)
        if r is None or aa_map.get(model) is not None:
            continue
        meta = _get_model_meta_strict_card(model)
        refs.append({"model": model, "cost": (ref / r["requests_per_5h"]) if r["requests_per_5h"] else None, "free": not r["requests_per_5h"], "brand": meta["brand"]})
    frontier = sorted([p for p in pts if p["pareto"]], key=lambda x: x["cost"])
    label = quota["snapshots"][date].get("label", "")
    badge_label = f"{label}快照" if label == "今日" else (label or date)
    count, fcount = len(pts), len(frontier)
    svg_str = build_pareto_svg(pts, frontier, refs, xMax_global, icons)
    # winners
    brand_meta = {"muse": "Meta", "zhipu": "智谱", "kimi": "月之暗面", "qwen": "阿里", "deepseek": "深度求索", "grok": "xAI", "openai": "OpenAI", "xiaomimimo": "小米", "minimax": "MiniMax", "hunyuan": "腾讯"}
    winners_html = ""
    for i, p in enumerate(frontier[:3]):
        bname = brand_meta.get(p["brand"], p["brand"])
        winners_html += f'''
  <div class="winner optimal">
    <div class="rank gold">0{i+1}</div>
    <div class="winner-head">
      <div class="avatar"><img src="{icons.get(p["brand"], icons.get("unknown",""))}" alt="{p["model"]}"></div>
      <div><div class="winner-name">{p["model"]}</div><div class="winner-meta"><span class="tag {"m" if p["modality"]=="多模态" else "t"}">{"M" if p["modality"]=="多模态" else "T"}</span> {p["modality"]} · {bname}</div></div>
    </div>
    <div class="winner-stats"><div class="stat cost"><label>相对成本</label><strong>{p["cost"]:.2f}</strong></div><div class="stat intel"><label>AA 智力</label><strong>{p["intel"]:.1f}</strong></div></div>
    <div class="winner-note">配额 <b>{p["requests"]:,} / 5h</b> · 智力 {p["intel"]:.1f}</div>
  </div>'''
    baseline = quota["snapshots"][latest]["models"]
    base_model = max((r for r in baseline if r["requests_per_5h"]), key=lambda r: r["requests_per_5h"])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode Go 帕累托最优 · {date} · 分享卡片</title>
<meta property="og:title" content="OpenCode Go 模型帕累托最优 · {date}">
<meta property="og:description" content="{frontier[0]["model"] if frontier else ""} 以成本 {frontier[0]["cost"]:.2f} 领跑，{fcount} 模型构成帕累托最优">
<style>
:root{{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--teal:#0f766e;--orange:#ea580c;--blue:#2563eb}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#eef2f7}}
body{{font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);display:flex;justify-content:center;padding:28px 16px}}
.shell{{width:min(1080px,100%)}}.hint{{text-align:center;color:#94a3b8;font-size:13px;margin:0 0 14px;letter-spacing:.02em}}
.card{{width:1080px;max-width:100%;background:#fff;border-radius:28px;overflow:hidden;box-shadow:0 24px 64px rgba(15,23,42,.14),0 2px 10px rgba(15,23,42,.06);border:1px solid #e6eef6}}
.card-top{{height:6px;background:linear-gradient(90deg,#0f766e 0%,#2563eb 55%,#ea580c 100%)}}
.card-head{{padding:34px 40px 0;display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}
.eyebrow{{font-size:12.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--teal);font-weight:800}}
h1{{margin:6px 0 0;font-size:34px;line-height:1.15;letter-spacing:-.03em;font-weight:850}}
h1 span{{background:linear-gradient(90deg,#0f172a 0%,#334155 100%);-webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{margin:10px 0 0;color:#475569;font-size:14.5px;line-height:1.6;max-width:620px}}
.badges{{display:flex;flex-direction:column;gap:10px;align-items:flex-end;flex:none}}
.badge-date{{display:inline-flex;align-items:center;gap:8px;background:#0f172a;color:#fff;border-radius:999px;padding:9px 14px;font-weight:700;font-size:13.5px;letter-spacing:.02em}}
.badge-date i{{width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 6px rgba(34,197,94,.18);display:inline-block}}
.badge-10{{display:inline-flex;align-items:center;gap:6px;background:#f1f5f9;border:1px solid #e2e8f0;color:#334155;border-radius:999px;padding:7px 12px;font-size:12.5px;font-weight:650}}
.chart-box{{margin:22px 32px 0;background:linear-gradient(180deg,#fbfdff 0%,#f8fafc 100%);border:1px solid #e6eef6;border-radius:20px;padding:14px 14px 6px}}
.chart-legend{{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:0 8px 10px;color:#475569;font-size:12.5px}}
.dot{{width:14px;height:14px;border-radius:50%;border:2.5px solid var(--orange);background:#fff;display:inline-block}}.line{{width:18px;height:3px;border-radius:2px;background:var(--orange);display:inline-block}}
.legend-m{{width:16px;height:16px;border-radius:50%;background:var(--blue);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.legend-t{{width:16px;height:16px;border-radius:50%;background:var(--teal);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.winners{{padding:18px 32px 0;display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
.winner{{position:relative;background:#fff;border:1.5px solid #e2e8f0;border-radius:18px;padding:16px 16px 14px;overflow:hidden}}
.winner.optimal{{border-color:#fed7aa;box-shadow:0 8px 20px rgba(234,88,12,.10)}}.winner.optimal::before{{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:linear-gradient(90deg,#ea580c,#f59e0b)}}
.rank{{position:absolute;top:12px;right:12px;width:28px;height:28px;border-radius:50%;background:#0f172a;color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800}}.rank.gold{{background:linear-gradient(135deg,#ea580c,#f59e0b)}}
.winner-head{{display:flex;gap:12px;align-items:center}}.avatar{{width:44px;height:44px;border-radius:50%;background:#fff;border:1.5px solid #e2e8f0;display:grid;place-items:center;overflow:hidden;flex:none;padding:6px}}.avatar img{{width:100%;height:100%;object-fit:contain}}
.winner-name{{font-weight:800;font-size:15px;line-height:1.25}}.winner-meta{{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;margin-top:2px}}
.tag{{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;color:#fff;font-size:9px;font-weight:800}}.tag.m{{background:var(--blue)}}.tag.t{{background:var(--teal)}}
.winner-stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;background:#f8fafc;border-radius:12px;padding:11px 12px}}
.stat label{{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;font-weight:700}}.stat strong{{display:block;font-size:18px;line-height:1.1;margin-top:2px;letter-spacing:-.02em}}.stat.cost strong{{color:var(--orange)}}.stat.intel strong{{color:var(--teal)}}.winner-note{{margin-top:10px;font-size:12px;color:#64748b;line-height:1.5}}
.foot{{padding:16px 32px 22px;display:flex;justify-content:space-between;gap:16px;align-items:flex-end;color:#94a3b8;font-size:11.5px;line-height:1.6;border-top:1px solid #f1f5f9;margin-top:18px}}.foot a{{color:#64748b;text-decoration:none;border-bottom:1px dashed #cbd5e1}}
.hash{{display:flex;gap:8px;flex-wrap:wrap}}.hash span{{background:#f1f5f9;border:1px solid #e2e8f0;color:#475569;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:600}}
@media(max-width:720px){{.card-head{{flex-direction:column}}.winners{{grid-template-columns:1fr}}.chart-box{{margin-left:16px;margin-right:16px}}}}
</style></head><body><div class="shell"><p class="hint">长按 / 右键保存图片分享 · 1080px 竖版卡片 · 数据截至 {date}</p>
<div class="card" id="capture"><div class="card-top"></div>
<div class="card-head"><div><div class="eyebrow">OpenCode Go · Pareto Optimal</div><h1>OpenCode Go · {date} · <span>智力 × 配额成本</span> 帕累托最优</h1>
<p class="sub">纵轴 <b>AA Intelligence Index</b>（越高越好）· 横轴 <b>相对配额成本</b>（越低越好，<b>配额最多者 = 1.0</b>）<br>橙色外环与连线为帕累托最优集合 · 左上为理想方向</p></div>
<div class="badges"><div class="badge-date"><i></i> {badge_label} </div><div class="badge-10">{count} 模型 · {fcount} 帕累托最优</div></div></div>
<div class="chart-box"><div class="chart-legend">
<span style="display:inline-flex;gap:6px;align-items:center"><i class="dot"></i> 帕累托最优</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="line"></i> 最优前沿</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-m">M</i> 多模态</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-t">T</i> 纯文字</span>
<span style="margin-left:auto;color:#94a3b8">对数刻度 · 基准 {base_model["model"]} ({base_model["requests_per_5h"]:,} / 5h)</span></div>
{svg_str}</div><div class="winners">{winners_html}</div>
<div class="foot"><div>数据来源：<a href="https://opencode.ai/docs/zh-cn/go/">OpenCode Go</a> 用量快照 &amp; <a href="https://aihot.virxact.com/leaderboard/methodology">AA Index</a> · 相对成本以配额最多者为 1.0<br>
生成时间 {date} · 已嵌入全部数据，无需联网 · <span style="color:#64748b">opencode-go-model-pareto.html</span> · <a href="https://github.com/Jst-Well-Dan/opencode-go-model-pareto" style="color:#0f766e;font-weight:650;text-decoration:none;border-bottom:1px dashed #99f6e4">GitHub: Jst-Well-Dan/opencode-go-model-pareto</a></div>
</div></div></div></body></html>''', frontier


def build_goat_card(date, goat_quota, aa, icons):
    latest = max(goat_quota["snapshots"])
    _tracked = [r["requests_per_5h"] for r in goat_quota["snapshots"][latest]["models"] if r["requests_per_5h"]]
    ref = max(_tracked)
    xMax_global = ref / min(_tracked)
    # intelligence: goat primary
    goat_intel = {}
    for m in goat_quota["snapshots"][latest]["models"]:
        if isinstance(m.get("intelligence"), (int, float)):
            goat_intel[norm(m["model"])] = m["intelligence"]
    aa_map = {m["model"]: m["intelligence"] for m in aa["models"]}
    aa_by_norm = {}
    for r in aa["models"]:
        for k in [r["model"], r.get("aa_model_id") or ""]:
            if k:
                aa_by_norm[norm(k)] = r
    snap = goat_quota["snapshots"][date]["models"]
    snap_by = {r["model"]: r for r in snap}
    pts = []
    for model in [r["model"] for r in goat_quota["snapshots"][latest]["models"]]:
        if model not in snap_by:
            continue
        r = snap_by[model]
        intel = goat_intel.get(norm(model))
        if intel is None:
            rr = aa_by_norm.get(norm(model))
            intel = rr.get("intelligence") if rr and isinstance(rr.get("intelligence"), (int, float)) else None
        if not r["requests_per_5h"] or intel is None:
            continue
        meta = _get_model_meta_strict_card(model)
        pts.append({"model": model, "requests": r["requests_per_5h"], "intel": intel, "cost": ref / r["requests_per_5h"], "brand": meta["brand"], "modality": meta["modality"]})
    for p in pts:
        p["pareto"] = is_pareto(p, pts)
    refs = []
    for model in [r["model"] for r in goat_quota["snapshots"][latest]["models"]]:
        r = snap_by.get(model)
        intel = goat_intel.get(norm(model))
        if intel is None:
            rr = aa_by_norm.get(norm(model))
            intel = rr.get("intelligence") if rr and isinstance(rr.get("intelligence"), (int, float)) else None
        if r is None or intel is not None:
            continue
        meta = _get_model_meta_strict_card(model)
        refs.append({"model": model, "cost": (ref / r["requests_per_5h"]) if r["requests_per_5h"] else None, "free": not r["requests_per_5h"], "brand": meta["brand"]})
    frontier = sorted([p for p in pts if p["pareto"]], key=lambda x: x["cost"])
    label = goat_quota["snapshots"][date].get("label", "")
    badge_label = f"{label}快照" if label == "今日" else (label or date)
    count, fcount = len(pts), len(frontier)
    svg_str = build_pareto_svg(pts, frontier, refs, xMax_global, icons)
    brand_meta = {"muse": "Meta", "zhipu": "智谱", "kimi": "月之暗面", "qwen": "阿里", "deepseek": "深度求索", "grok": "xAI", "openai": "OpenAI", "xiaomimimo": "小米", "minimax": "MiniMax", "hunyuan": "腾讯", "gemini": "Google", "inkling": "Inkling", "nemotron": "NVIDIA", "step": "阶跃"}
    winners_html = ""
    for i, p in enumerate(frontier[:3]):
        bname = brand_meta.get(p["brand"], p["brand"])
        winners_html += f'''
  <div class="winner optimal">
    <div class="rank gold">0{i+1}</div>
    <div class="winner-head">
      <div class="avatar"><img src="{icons.get(p["brand"], icons.get("unknown",""))}" alt="{p["model"]}"></div>
      <div><div class="winner-name">{p["model"]}</div><div class="winner-meta"><span class="tag {"m" if p["modality"]=="多模态" else "t"}">{"M" if p["modality"]=="多模态" else "T"}</span> {p["modality"]} · {bname}</div></div>
    </div>
    <div class="winner-stats"><div class="stat cost"><label>相对成本</label><strong>{p["cost"]:.2f}</strong></div><div class="stat intel"><label>AA 智力</label><strong>{p["intel"]:.1f}</strong></div></div>
    <div class="winner-note">配额 <b>{p["requests"]:,} / 5h</b> · 智力 {p["intel"]:.1f}</div>
  </div>'''
    baseline = goat_quota["snapshots"][latest]["models"]
    base_model = max((r for r in baseline if r["requests_per_5h"]), key=lambda r: r["requests_per_5h"])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Command GOAT 帕累托最优 · {date} · 分享卡片</title>
<meta property="og:title" content="Command GOAT 模型帕累托最优 · {date}">
<meta property="og:description" content="{frontier[0]["model"] if frontier else ""} 以成本 {frontier[0]["cost"]:.2f} 领跑，{fcount} 模型构成帕累托最优">
<style>
:root{{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--teal:#0f766e;--orange:#ea580c;--blue:#2563eb}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#eef2f7}}
body{{font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);display:flex;justify-content:center;padding:28px 16px}}
.shell{{width:min(1080px,100%)}}.hint{{text-align:center;color:#94a3b8;font-size:13px;margin:0 0 14px;letter-spacing:.02em}}
.card{{width:1080px;max-width:100%;background:#fff;border-radius:28px;overflow:hidden;box-shadow:0 24px 64px rgba(15,23,42,.14),0 2px 10px rgba(15,23,42,.06);border:1px solid #e6eef6}}
.card-top{{height:6px;background:linear-gradient(90deg,#0f766e 0%,#2563eb 55%,#ea580c 100%)}}
.card-head{{padding:34px 40px 0;display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}
.eyebrow{{font-size:12.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--teal);font-weight:800}}
h1{{margin:6px 0 0;font-size:34px;line-height:1.15;letter-spacing:-.03em;font-weight:850}}
h1 span{{background:linear-gradient(90deg,#0f172a 0%,#334155 100%);-webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{margin:10px 0 0;color:#475569;font-size:14.5px;line-height:1.6;max-width:620px}}
.badges{{display:flex;flex-direction:column;gap:10px;align-items:flex-end;flex:none}}
.badge-date{{display:inline-flex;align-items:center;gap:8px;background:#0f172a;color:#fff;border-radius:999px;padding:9px 14px;font-weight:700;font-size:13.5px;letter-spacing:.02em}}
.badge-date i{{width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 6px rgba(34,197,94,.18);display:inline-block}}
.badge-10{{display:inline-flex;align-items:center;gap:6px;background:#f1f5f9;border:1px solid #e2e8f0;color:#334155;border-radius:999px;padding:7px 12px;font-size:12.5px;font-weight:650}}
.chart-box{{margin:22px 32px 0;background:linear-gradient(180deg,#fbfdff 0%,#f8fafc 100%);border:1px solid #e6eef6;border-radius:20px;padding:14px 14px 6px}}
.chart-legend{{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:0 8px 10px;color:#475569;font-size:12.5px}}
.dot{{width:14px;height:14px;border-radius:50%;border:2.5px solid var(--orange);background:#fff;display:inline-block}}.line{{width:18px;height:3px;border-radius:2px;background:var(--orange);display:inline-block}}
.legend-m{{width:16px;height:16px;border-radius:50%;background:var(--blue);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.legend-t{{width:16px;height:16px;border-radius:50%;background:var(--teal);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.winners{{padding:18px 32px 0;display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
.winner{{position:relative;background:#fff;border:1.5px solid #e2e8f0;border-radius:18px;padding:16px 16px 14px;overflow:hidden}}
.winner.optimal{{border-color:#fed7aa;box-shadow:0 8px 20px rgba(234,88,12,.10)}}.winner.optimal::before{{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:linear-gradient(90deg,#ea580c,#f59e0b)}}
.rank{{position:absolute;top:12px;right:12px;width:28px;height:28px;border-radius:50%;background:#0f172a;color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800}}.rank.gold{{background:linear-gradient(135deg,#ea580c,#f59e0b)}}
.winner-head{{display:flex;gap:12px;align-items:center}}.avatar{{width:44px;height:44px;border-radius:50%;background:#fff;border:1.5px solid #e2e8f0;display:grid;place-items:center;overflow:hidden;flex:none;padding:6px}}.avatar img{{width:100%;height:100%;object-fit:contain}}
.winner-name{{font-weight:800;font-size:15px;line-height:1.25}}.winner-meta{{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;margin-top:2px}}
.tag{{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;color:#fff;font-size:9px;font-weight:800}}.tag.m{{background:var(--blue)}}.tag.t{{background:var(--teal)}}
.winner-stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;background:#f8fafc;border-radius:12px;padding:11px 12px}}
.stat label{{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;font-weight:700}}.stat strong{{display:block;font-size:18px;line-height:1.1;margin-top:2px;letter-spacing:-.02em}}.stat.cost strong{{color:var(--orange)}}.stat.intel strong{{color:var(--teal)}}.winner-note{{margin-top:10px;font-size:12px;color:#64748b;line-height:1.5}}
.foot{{padding:16px 32px 22px;display:flex;justify-content:space-between;gap:16px;align-items:flex-end;color:#94a3b8;font-size:11.5px;line-height:1.6;border-top:1px solid #f1f5f9;margin-top:18px}}.foot a{{color:#64748b;text-decoration:none;border-bottom:1px dashed #cbd5e1}}
.hash{{display:flex;gap:8px;flex-wrap:wrap}}.hash span{{background:#f1f5f9;border:1px solid #e2e8f0;color:#475569;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:600}}
@media(max-width:720px){{.card-head{{flex-direction:column}}.winners{{grid-template-columns:1fr}}.chart-box{{margin-left:16px;margin-right:16px}}}}
</style></head><body><div class="shell"><p class="hint">长按 / 右键保存图片分享 · 1080px 竖版卡片 · 数据截至 {date}</p>
<div class="card" id="capture"><div class="card-top"></div>
<div class="card-head"><div><div class="eyebrow">Command GOAT · Pareto Optimal</div><h1>Command GOAT · {date} · <span>智力 × 配额成本</span> 帕累托最优</h1>
<p class="sub">纵轴 <b>AA Intelligence Index</b>（越高越好）· 横轴 <b>相对配额成本</b>（越低越好，<b>配额最多者 = 1.0</b>）<br>橙色外环与连线为帕累托最优集合 · 左上为理想方向 · 32 模型</p></div>
<div class="badges"><div class="badge-date"><i></i> {badge_label} </div><div class="badge-10">{count} 模型 · {fcount} 帕累托最优</div></div></div>
<div class="chart-box"><div class="chart-legend">
<span style="display:inline-flex;gap:6px;align-items:center"><i class="dot"></i> 帕累托最优</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="line"></i> 最优前沿</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-m">M</i> 多模态</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-t">T</i> 纯文字</span>
<span style="margin-left:auto;color:#94a3b8">对数刻度 · 基准 {base_model["model"]} ({base_model["requests_per_5h"]:,} / 5h)</span></div>
{svg_str}</div><div class="winners">{winners_html}</div>
<div class="foot"><div>数据来源：<a href="https://commandcode.ai/docs/plans/goat">Command GOAT</a> 核心两表 &amp; <a href="https://aihot.virxact.com/leaderboard/methodology">AA Index</a> · 相对成本以配额最多者为 1.0<br>
生成时间 {date} · 已嵌入全部数据，无需联网 · <a href="https://github.com/Jst-Well-Dan/opencode-go-model-pareto" style="color:#0f766e;font-weight:650;text-decoration:none;border-bottom:1px dashed #99f6e4">GitHub: Jst-Well-Dan/opencode-go-model-pareto</a></div>
</div></div></div></body></html>''', frontier


def build_comparison_card(oc_quota, goat_quota, aa, icons):
    ocd = max(oc_quota["snapshots"])
    god = max(goat_quota["snapshots"])
    ocr = {norm(r["model"]): r for r in oc_quota["snapshots"][ocd]["models"]}
    gr = {norm(r["model"]): r for r in goat_quota["snapshots"][god]["models"]}
    aa_by = {}
    for rr in aa["models"]:
        for k in [rr["model"], rr.get("aa_model_id")]:
            if k and norm(k):
                aa_by[norm(k)] = rr.get("intelligence")
        aa_by[norm(rr["model"].replace("-", " ").replace(".", "").lower())] = rr.get("intelligence")

    def get_iq(n, gk=None):
        g = gr.get(gk or n)
        if g and isinstance(g.get("intelligence"), (int, float)):
            return g["intelligence"]
        if n in aa_by and isinstance(aa_by[n], (int, float)):
            return aa_by[n]
        if gk and gk in aa_by and isinstance(aa_by[gk], (int, float)):
            return aa_by[gk]
        return None

    def infer_brand(m):
        low = m.lower()
        if low.startswith("grok"):
            return "grok"
        if low.startswith("glm"):
            return "zhipu"
        if "muse" in low:
            return "muse"
        if low.startswith("deepseek"):
            return "deepseek"
        if low.startswith("kimi"):
            return "kimi"
        if low.startswith("mimo"):
            return "xiaomimimo"
        if low.startswith("gpt") or "luna" in low:
            return "openai"
        return "unknown"

    rows = []
    for d in CURATED_DEFS:
        o = ocr.get(d["oc_key"])
        g = gr.get(d["goat_key"])
        if not o or not g:
            continue
        rows.append({"model": d["display"], "short": d["short"], "goat_name": g["model"], "iq": get_iq(d["oc_key"], d["goat_key"]), "oc_m": o.get("requests_per_month"), "goat_m": g.get("requests_per_month"), "goat_credit": g.get("monthly_credits"), "brand": infer_brand(d["display"])})
    rows.sort(key=lambda r: (-(r["iq"] or -1), r["model"]))
    svg_str, H = build_cmp_svg(rows, icons)
    # summary badges for winners
    date = god  # latest goat date as version
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode Go vs Command GOAT · 代表模型月额对比 · 分享卡片</title>
<meta property="og:title" content="OpenCode Go vs Command GOAT · 代表模型对比 · {date}">
<meta property="og:description" content="精选 {len(rows)} 个代表模型，按 AA 智商降序对比月请求额">
<style>
:root{{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--teal:#0f766e;--orange:#ea580c;--blue:#2563eb}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#eef2f7}}
body{{font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);display:flex;justify-content:center;padding:28px 16px}}
.shell{{width:min(1080px,100%)}}.hint{{text-align:center;color:#94a3b8;font-size:13px;margin:0 0 14px;letter-spacing:.02em}}
.card{{width:1080px;max-width:100%;background:#fff;border-radius:28px;overflow:hidden;box-shadow:0 24px 64px rgba(15,23,42,.14),0 2px 10px rgba(15,23,42,.06);border:1px solid #e6eef6}}
.card-top{{height:6px;background:linear-gradient(90deg,#0f766e 0%,#2563eb 55%,#ea580c 100%)}}
.card-head{{padding:34px 40px 0;display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}
.eyebrow{{font-size:12.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--teal);font-weight:800}}
h1{{margin:6px 0 0;font-size:30px;line-height:1.15;letter-spacing:-.03em;font-weight:850}}
h1 span{{background:linear-gradient(90deg,#0f172a 0%,#334155 100%);-webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{margin:10px 0 0;color:#475569;font-size:13.5px;line-height:1.6;max-width:640px}}
.badges{{display:flex;flex-direction:column;gap:10px;align-items:flex-end;flex:none}}
.badge-date{{display:inline-flex;align-items:center;gap:8px;background:#0f172a;color:#fff;border-radius:999px;padding:9px 14px;font-weight:700;font-size:13.5px;letter-spacing:.02em}}
.badge-date i{{width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 6px rgba(34,197,94,.18);display:inline-block}}
.badge-10{{display:inline-flex;align-items:center;gap:6px;background:#f1f5f9;border:1px solid #e2e8f0;color:#334155;border-radius:999px;padding:7px 12px;font-size:12.5px;font-weight:650}}
.chart-box{{margin:22px 32px 0;background:linear-gradient(180deg,#fbfdff 0%,#f8fafc 100%);border:1px solid #e6eef6;border-radius:20px;padding:14px 14px 6px}}
.chart-legend{{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:0 8px 10px;color:#475569;font-size:12.5px}}
.dot-oc{{width:10px;height:10px;border-radius:50%;background:#2563eb;display:inline-block;border:1.5px solid #fff;box-shadow:0 0 0 1px #cbd5e1}}
.dot-goat{{width:10px;height:10px;background:#ea580c;display:inline-block;transform:rotate(45deg);border-radius:2px;border:1.5px solid #fff;box-shadow:0 0 0 1px #cbd5e1}}
.foot{{padding:16px 32px 22px;display:flex;justify-content:space-between;gap:16px;align-items:flex-end;color:#94a3b8;font-size:11.5px;line-height:1.6;border-top:1px solid #f1f5f9;margin-top:18px}}.foot a{{color:#64748b;text-decoration:none;border-bottom:1px dashed #cbd5e1}}
.hash{{display:flex;gap:8px;flex-wrap:wrap}}.hash span{{background:#f1f5f9;border:1px solid #e2e8f0;color:#475569;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:600}}
@media(max-width:720px){{.card-head{{flex-direction:column}}.chart-box{{margin-left:16px;margin-right:16px}}}}
</style></head><body><div class="shell"><p class="hint">长按 / 右键保存图片分享 · 1080px 竖版卡片 · 数据截至 {date}</p>
<div class="card" id="capture"><div class="card-top"></div>
<div class="card-head"><div><div class="eyebrow">Selected models — ranked by intelligence</div><h1>OpenCode Go vs Command GOAT · <span>代表模型月额对比</span></h1>
<p class="sub">精选 <b>{len(rows)} 个代表模型</b>（帕累托前沿 ∪ DeepSeek 全部 ∪ GPT/MiMo/Kimi），<b>按 AA 智商从高到低</b> · 每行 <span style="color:#2563eb;font-weight:700">● OC</span> vs <span style="color:#ea580c;font-weight:700">◆ GOAT</span> 月请求数（对数刻度），右端为赢家与幅度</p></div>
<div class="badges"><div class="badge-date"><i></i> {date} </div><div class="badge-10">{len(rows)} 模型 · 哑铃对比</div></div></div>
<div class="chart-box"><div class="chart-legend">
<span style="display:inline-flex;gap:6px;align-items:center"><i class="dot-oc"></i> OpenCode Go</span>
<span style="display:inline-flex;gap:6px;align-items:center"><i class="dot-goat"></i> Command GOAT</span>
<span style="margin-left:auto;color:#94a3b8">对数刻度 · 按智商降序</span></div>
{svg_str}</div>
<div class="foot"><div>数据来源：<a href="https://opencode.ai/docs/zh-cn/go/">OpenCode Go</a> 用量快照 &amp; <a href="https://commandcode.ai/docs/plans/goat">GOAT</a> 核心两表 &amp; <a href="https://aihot.virxact.com/leaderboard/methodology">AA Index</a> · 对数刻度；GOAT 月档位 $20~$70，OC 固定 $15/30/60<br>
生成时间 {date} · 已嵌入全部数据，无需联网 · <a href="https://github.com/Jst-Well-Dan/opencode-go-model-pareto" style="color:#0f766e;font-weight:650;text-decoration:none;border-bottom:1px dashed #99f6e4">GitHub: Jst-Well-Dan/opencode-go-model-pareto</a></div>
</div></div></div></body></html>''', rows


def html_to_cropped_png(html_str: str, output_path: Path):
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html_str)
        tmp = Path(f.name)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1140, "height": 2400})
            page.goto(tmp.absolute().as_uri())
            page.wait_for_timeout(800)
            page.locator("#capture").screenshot(path=str(output_path))
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Generate share cards as cropped PNGs (latest snapshot only, 3 graphs)")
    parser.add_argument("--keep-html", action="store_true", help="保留中间 HTML 文件到 cards/ 供调试")
    parser.add_argument("--no-image", action="store_true", help="仅生成 HTML，不截图")
    parser.add_argument("--type", choices=["oc", "goat", "cmp", "all"], default="all", help="生成类型，默认 all（3 张）")
    args = parser.parse_args()

    quota = json.loads((ROOT / "data" / "snapshots" / "quota-snapshots.json").read_text(encoding="utf-8"))
    goat_quota = json.loads((ROOT / "data" / "snapshots" / "goat-snapshots.json").read_text(encoding="utf-8"))
    aa = json.loads((ROOT / "data" / "snapshots" / "aa-scores.json").read_text(encoding="utf-8"))
    icons = load_icons()

    latest_oc = max(quota["snapshots"])
    latest_goat = max(goat_quota["snapshots"])

    cards_dir = ROOT / "cards"
    cards_dir.mkdir(exist_ok=True)

    targets = []
    if args.type in ("oc", "all"):
        targets.append("oc")
    if args.type in ("goat", "all"):
        targets.append("goat")
    if args.type in ("cmp", "all"):
        targets.append("cmp")

    # build HTMLs
    html_map = {}
    if "oc" in targets:
        html_oc, frontier_oc = build_oc_card(latest_oc, quota, aa, icons)
        html_map["oc"] = (html_oc, f"OC {latest_oc} frontier {[f['model'] for f in frontier_oc]}")
        png_oc = cards_dir / f"opencode-card-{latest_oc}.png"
        html_oc_path = cards_dir / f"opencode-card-{latest_oc}.html"
        if args.keep_html or args.no_image:
            html_oc_path.write_text(html_oc, encoding="utf-8")
            print(f"Generated {html_oc_path.name} ({len(html_oc)} bytes) {html_map['oc'][1]}")
        if not args.no_image:
            html_map["oc"] = (html_oc, png_oc)
    if "goat" in targets:
        html_goat, frontier_goat = build_goat_card(latest_goat, goat_quota, aa, icons)
        html_map["goat"] = (html_goat, f"GOAT {latest_goat} frontier {[f['model'] for f in frontier_goat]}")
        png_goat = cards_dir / f"goat-card-{latest_goat}.png"
        html_goat_path = cards_dir / f"goat-card-{latest_goat}.html"
        if args.keep_html or args.no_image:
            html_goat_path.write_text(html_goat, encoding="utf-8")
            print(f"Generated {html_goat_path.name} ({len(html_goat)} bytes) {html_map['goat'][1]}")
        if not args.no_image:
            html_map["goat"] = (html_goat, png_goat)
    if "cmp" in targets:
        html_cmp, rows_cmp = build_comparison_card(quota, goat_quota, aa, icons)
        html_map["cmp"] = (html_cmp, f"CMP {len(rows_cmp)} models")
        # comparison uses latest_goat as version (same as goat latest)
        png_cmp = cards_dir / f"comparison-card-{latest_goat}.png"
        html_cmp_path = cards_dir / f"comparison-card-{latest_goat}.html"
        if args.keep_html or args.no_image:
            html_cmp_path.write_text(html_cmp, encoding="utf-8")
            print(f"Generated {html_cmp_path.name} ({len(html_cmp)} bytes) {html_map['cmp'][1]}")
        if not args.no_image:
            html_map["cmp"] = (html_cmp, png_cmp)

    if args.no_image:
        print(f"Done (no-image). HTMLs in {cards_dir}")
        return

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Playwright 未安装，跳过截图。请执行: pip install playwright && playwright install chromium")
        print("已生成 HTML，可手动截图；或安装后重跑本脚本直接出图。")
        return

    for key in targets:
        html_str, out_png = html_map[key]
        html_to_cropped_png(html_str, out_png)
        print(f"Screenshotted {out_png.name}")

    if not args.keep_html:
        for p in cards_dir.glob("*.html"):
            if p.name.startswith(("opencode-card-", "goat-card-", "comparison-card-")):
                p.unlink(missing_ok=True)
        print("已清理中间 HTML，仅保留 *-card-*.png")

    print(f"Done. Cards in {cards_dir} -> {[p.name for p in sorted(cards_dir.glob('*-card-*.png'))]}")


if __name__ == "__main__":
    main()
