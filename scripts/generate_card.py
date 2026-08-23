#!/usr/bin/env python3
"""Generate social-share cards (1080px vertical) for each snapshot date.

Directly renders cropped PNGs via Playwright — no manual intermediate step.
Usage:
  python scripts/generate_card.py              # 生成 cards/*-cropped.png
  python scripts/generate_card.py --keep-html  # 同时保留中间 HTML 供调试
  python scripts/generate_card.py --no-image   # 仅生成 HTML
"""
import argparse
import json
import math
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
quota = json.loads((ROOT / "data/quota-snapshots.json").read_text(encoding="utf-8"))
aa = json.loads((ROOT / "data/aa-scores.json").read_text(encoding="utf-8"))

from generate_html import MODEL_META

# reference is latest max (most generous) for consistent cost scale
latest = max(quota["snapshots"])
_tracked = [r["requests_per_5h"] for r in quota["snapshots"][latest]["models"] if r["requests_per_5h"]]
ref = max(_tracked)
xMax_global = ref / min(_tracked)
aa_map = {m["model"]: m["intelligence"] for m in aa["models"]}

tpl = (ROOT / "template/opencode-go-model-pareto.template.html").read_text(encoding="utf-8")
m = re.search(r"const icons=\{(.*?)\};", tpl, re.S)
icons = {}
for k, v in re.findall(r'(\w+):"(data:image[^"]+)"', m.group(1)):
    icons[k] = v


def is_pareto(p, allpts):
    for q in allpts:
        if q is p:
            continue
        if q["intel"] >= p["intel"] and q["cost"] <= p["cost"] and (q["intel"] > p["intel"] or q["cost"] < p["cost"]):
            return False
    return True


def build_card(date):
    snap = quota["snapshots"][date]["models"]
    snap_by_model = {r["model"]: r for r in snap}
    pts = []
    for model in [r["model"] for r in quota["snapshots"][latest]["models"]]:
        if model not in snap_by_model:
            continue
        r = snap_by_model[model]
        if not r["requests_per_5h"] or aa_map[model] is None:
            continue  # null quota (free/unlimited) or missing AA score -> not plottable
        pts.append({
            "model": model,
            "requests": r["requests_per_5h"],
            "intel": aa_map[model],
            "cost": ref / r["requests_per_5h"],
            "brand": MODEL_META[model]["brand"],
            "modality": MODEL_META[model]["modality"],
        })
    for p in pts:
        p["pareto"] = is_pareto(p, pts)
    # 无 AA 评分的模型：横轴参考点（免费档锚定最左端）
    refs = []
    for model in [r["model"] for r in quota["snapshots"][latest]["models"]]:
        r = snap_by_model.get(model)
        if r is None or aa_map[model] is not None:
            continue
        refs.append({
            "model": model,
            "cost": (ref / r["requests_per_5h"]) if r["requests_per_5h"] else None,
            "free": not r["requests_per_5h"],
            "brand": MODEL_META[model]["brand"],
        })
    frontier = sorted([p for p in pts if p["pareto"]], key=lambda x: x["cost"])
    label = quota["snapshots"][date].get("label", "")
    badge_label = f"{label}快照" if label == "今日" else label
    count = len(pts)
    fcount = len(frontier)

    W, H = 1000, 420
    M = {"l": 74, "r": 18, "t": 26, "b": 42}
    yMin, yMax = 36, 62
    xMax = xMax_global

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
    pts_str = " ".join(f'{x(d["cost"]):.1f},{y(d["intel"]):.1f}' for d in frontier)
    svg_parts.append(f'<polyline points="{pts_str}" fill="none" stroke="#ea580c" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>')
    ordered = [p for p in pts if not p["pareto"]] + frontier
    for p in ordered:
        cx, cy = x(p["cost"]), y(p["intel"])
        stroke = "#ea580c" if p["pareto"] else "#cbd5e1"
        sw = "3.6" if p["pareto"] else "1.4"
        badge_color = "#2563eb" if p["modality"] == "多模态" else "#0f766e"
        badge_mark = "M" if p["modality"] == "多模态" else "T"
        href = icons[p["brand"]]
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
        svg_parts.append(f'<image href="{icons[d["brand"]]}" x="-8" y="-8" width="16" height="16" opacity="0.6" preserveAspectRatio="xMidYMid meet"/>')
        svg_parts.append(f'<text y="-19" text-anchor="middle" font-size="10.5" font-weight="600" fill="#64748b" font-family="system-ui">{label}</text>')
        svg_parts.append("</g>")
    svg_parts.append("</svg>")
    svg_str = "\n".join(svg_parts)

    winners_html = ""
    for i, p in enumerate(frontier[:3]):
        brand_meta = {
            "muse": "Meta", "zhipu": "智谱", "kimi": "月之暗面", "qwen": "阿里", "deepseek": "深度求索",
            "grok": "xAI", "openai": "OpenAI", "xiaomimimo": "小米", "minimax": "MiniMax", "hunyuan": "腾讯",
        }
        bname = brand_meta.get(p["brand"], p["brand"])
        winners_html += f'''
  <div class="winner optimal">
    <div class="rank gold">0{i+1}</div>
    <div class="winner-head">
      <div class="avatar"><img src="{icons[p["brand"]]}" alt="{p["model"]}"></div>
      <div>
        <div class="winner-name">{p["model"]}</div>
        <div class="winner-meta"><span class="tag {"m" if p["modality"]=="多模态" else "t"}">{"M" if p["modality"]=="多模态" else "T"}</span> {p["modality"]} · {bname}</div>
      </div>
    </div>
    <div class="winner-stats">
      <div class="stat cost"><label>相对成本</label><strong>{p["cost"]:.2f}</strong></div>
      <div class="stat intel"><label>AA 智力</label><strong>{p["intel"]:.1f}</strong></div>
    </div>
    <div class="winner-note">配额 <b>{p["requests"]:,} / 5h</b> · 智力 {p["intel"]:.1f}</div>
  </div>'''
    baseline = quota["snapshots"][latest]["models"]
    base_model = max((r for r in baseline if r["requests_per_5h"]), key=lambda r: r["requests_per_5h"])
    card_html = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode Go 帕累托最优 · {date} · 分享卡片</title>
<meta property="og:title" content="OpenCode Go 模型帕累托最优 · {date}">
<meta property="og:description" content="{frontier[0]["model"] if frontier else ""} 以成本 {frontier[0]["cost"]:.2f} 领跑，{fcount} 模型构成帕累托最优 · 数据来自 OpenCode Go & AA Index">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<style>
:root{{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--teal:#0f766e;--orange:#ea580c;--blue:#2563eb}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#eef2f7}}
body{{font:14px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);display:flex;justify-content:center;padding:28px 16px}}
.shell{{width:min(1080px,100%)}}
.hint{{text-align:center;color:#94a3b8;font-size:13px;margin:0 0 14px;letter-spacing:.02em}}
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
.dot{{width:14px;height:14px;border-radius:50%;border:2.5px solid var(--orange);background:#fff;display:inline-block}}
.line{{width:18px;height:3px;border-radius:2px;background:var(--orange);display:inline-block}}
.legend-m{{width:16px;height:16px;border-radius:50%;background:var(--blue);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.legend-t{{width:16px;height:16px;border-radius:50%;background:var(--teal);color:#fff;display:grid;place-items:center;font-size:10px;font-weight:800}}
.winners{{padding:18px 32px 0;display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
.winner{{position:relative;background:#fff;border:1.5px solid #e2e8f0;border-radius:18px;padding:16px 16px 14px;overflow:hidden}}
.winner.optimal{{border-color:#fed7aa;box-shadow:0 8px 20px rgba(234,88,12,.10)}}
.winner.optimal::before{{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:linear-gradient(90deg,#ea580c,#f59e0b)}}
.rank{{position:absolute;top:12px;right:12px;width:28px;height:28px;border-radius:50%;background:#0f172a;color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800}}
.rank.gold{{background:linear-gradient(135deg,#ea580c,#f59e0b)}}
.winner-head{{display:flex;gap:12px;align-items:center}}
.avatar{{width:44px;height:44px;border-radius:50%;background:#fff;border:1.5px solid #e2e8f0;display:grid;place-items:center;overflow:hidden;flex:none;padding:6px}}
.avatar img{{width:100%;height:100%;object-fit:contain}}
.winner-name{{font-weight:800;font-size:15px;line-height:1.25}}
.winner-meta{{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;margin-top:2px}}
.tag{{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;color:#fff;font-size:9px;font-weight:800}}
.tag.m{{background:var(--blue)}}.tag.t{{background:var(--teal)}}
.winner-stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;background:#f8fafc;border-radius:12px;padding:11px 12px}}
.stat label{{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;font-weight:700}}
.stat strong{{display:block;font-size:18px;line-height:1.1;margin-top:2px;letter-spacing:-.02em}}
.stat.cost strong{{color:var(--orange)}}.stat.intel strong{{color:var(--teal)}}
.winner-note{{margin-top:10px;font-size:12px;color:#64748b;line-height:1.5}}
.foot{{padding:16px 32px 22px;display:flex;justify-content:space-between;gap:16px;align-items:flex-end;color:#94a3b8;font-size:11.5px;line-height:1.6;border-top:1px solid #f1f5f9;margin-top:18px}}
.foot a{{color:#64748b;text-decoration:none;border-bottom:1px dashed #cbd5e1}}
.hash{{display:flex;gap:8px;flex-wrap:wrap}}
.hash span{{background:#f1f5f9;border:1px solid #e2e8f0;color:#475569;border-radius:999px;padding:4px 10px;font-size:11.5px;font-weight:600}}
@media(max-width:720px){{.card-head{{flex-direction:column}}.winners{{grid-template-columns:1fr}}.chart-box{{margin-left:16px;margin-right:16px}}}}
</style>
</head>
<body>
<div class="shell">
<p class="hint">长按 / 右键保存图片分享 · 1080px 竖版卡片 · 数据截至 {date}</p>
<div class="card" id="capture">
<div class="card-top"></div>
<div class="card-head">
  <div>
    <div class="eyebrow">OpenCode Go · Pareto Optimal</div>
    <h1>{date} · <span>智力 × 配额成本</span> 帕累托最优</h1>
    <p class="sub">纵轴 <b>AA Intelligence Index</b>（越高越好）· 横轴 <b>相对配额成本</b>（越低越好，<b>配额最多者 = 1.0</b>）<br>橙色外环与连线为帕累托最优集合 · 左上为理想方向</p>
  </div>
  <div class="badges">
    <div class="badge-date"><i></i> {badge_label} </div>
    <div class="badge-10">{count} 模型 · {fcount} 帕累托最优</div>
  </div>
</div>
<div class="chart-box">
  <div class="chart-legend">
    <span style="display:inline-flex;gap:6px;align-items:center"><i class="dot"></i> 帕累托最优</span>
    <span style="display:inline-flex;gap:6px;align-items:center"><i class="line"></i> 最优前沿</span>
    <span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-m">M</i> 多模态</span>
    <span style="display:inline-flex;gap:6px;align-items:center"><i class="legend-t">T</i> 纯文字</span>
    <span style="margin-left:auto;color:#94a3b8">对数刻度 · 基准 {base_model["model"]} ({base_model["requests_per_5h"]:,} / 5h)</span>
  </div>
  {svg_str}
</div>
<div class="winners">
  {winners_html}
</div>
<div class="foot">
  <div>
    数据来源：<a href="https://opencode.ai/docs/zh-cn/go/">OpenCode Go</a> 用量快照 &amp; <a href="https://aihot.virxact.com/leaderboard/methodology">AA Index</a> · 相对成本以配额最多者为 1.0<br>
    生成时间 {date} · 已嵌入全部数据，无需联网 · <span style="color:#64748b">opencode-go-model-pareto.html</span>
  </div>
  <div class="hash"><span>#OpenCodeGo</span><span>#帕累托最优</span><span>#AI模型</span></div>
</div>
</div>
</div>
</body>
</html>'''
    return card_html, frontier


def html_to_cropped_png(html_str: str, output_path: Path):
    """Render HTML string and screenshot #capture to output_path (cropped)."""
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
    parser = argparse.ArgumentParser(description="Generate share cards as cropped PNGs")
    parser.add_argument("--keep-html", action="store_true", help="保留中间 HTML 文件到 cards/ 供调试")
    parser.add_argument("--no-image", action="store_true", help="仅生成 HTML，不截图")
    args = parser.parse_args()

    cards_dir = ROOT / "cards"
    cards_dir.mkdir(exist_ok=True)

    dates = sorted(quota["snapshots"].keys())
    html_map = {}
    for d in dates:
        html, frontier = build_card(d)
        html_map[d] = (html, frontier)
        if args.keep_html or args.no_image:
            out_html = cards_dir / f"opencode-go-model-pareto-card-{d}.html"
            out_html.write_text(html, encoding="utf-8")
            print(f"Generated {out_html.name} ({len(html)} bytes) frontier {[f['model'] for f in frontier]}")

    # latest -> generic card.html
    latest_html, latest_frontier = html_map[latest]
    if args.keep_html or args.no_image:
        generic = cards_dir / "opencode-go-model-pareto-card.html"
        generic.write_text(latest_html, encoding="utf-8")
        print(f"Generated opencode-go-model-pareto-card.html (latest {latest})")

    if args.no_image:
        return

    # --- 直接生成图片，无需手动写中间代码 ---
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Playwright 未安装，跳过截图。请执行: pip install playwright && playwright install chromium")
        print("已生成 HTML，可手动截图；或安装后重跑本脚本直接出图。")
        return

    # 截图所有日期 + 通用卡片（通用即最新）
    for d in dates:
        html, _ = html_map[d]
        out_png = cards_dir / f"opencode-go-model-pareto-card-{d}-cropped.png"
        html_to_cropped_png(html, out_png)
        print(f"Screenshotted {out_png.name}")
    # 通用卡片（复用 latest 的图，但文件名不带日期）
    generic_png = cards_dir / "opencode-go-model-pareto-card-cropped.png"
    html_to_cropped_png(latest_html, generic_png)
    print(f"Screenshotted {generic_png.name}")

    # 清理：默认不保留 HTML，只留 cropped.png（符合你的要求）
    if not args.keep_html:
        for p in cards_dir.glob("*.html"):
            p.unlink()
        # 删除可能遗留的 full-page png
        for p in list(cards_dir.glob("*.png")):
            if "cropped" not in p.name:
                p.unlink()
        print("已清理中间 HTML，仅保留 *-cropped.png")

    print(f"Done. Cards in {cards_dir} -> {[p.name for p in sorted(cards_dir.glob('*.png'))]}")


if __name__ == "__main__":
    main()
