#!/usr/bin/env python3
"""统一生成器：生成 index.html（三图合一：OpenCode Go 帕累托 + Command GOAT 帕累托 + 代表模型对比）

Usage:
    python scripts/generate.py              # 生成 index.html
    python scripts/generate.py --all        # 同上（兼容旧 --all 调用）
    python scripts/generate.py --output index.html --template template/xxx.html

仅保留单一输出（index.html），旧的 4 个独立脚本已合并至此单一文件。
卡片脚本 generate_card.py 通过 `from generate import MODEL_META` 复用此文件的品牌/模态元数据。
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "opencode-go-model-pareto.template.html"
OC_QUOTA = ROOT / "data" / "snapshots" / "quota-snapshots.json"
GOAT_QUOTA = ROOT / "data" / "snapshots" / "goat-snapshots.json"
AA_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
OUTPUT = ROOT / "index.html"

def _load_json_strict(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"missing required data file {path} ({name}) — 需在 data/ 下提供该 json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"failed to load {path}: {e}")

# 统一注册表：OC 与 GOAT 共用（data/model-meta.json）
MODEL_META = _load_json_strict(ROOT / "data" / "registry" / "model-meta.json", "model-meta")
CURATED_DEFS = _load_json_strict(ROOT / "data" / "registry" / "curated-defs.json", "curated-defs")
SLUG_ALIAS = _load_json_strict(ROOT / "data" / "registry" / "slug-alias.json", "slug-alias")
ICONS_DATA = _load_json_strict(ROOT / "data" / "registry" / "icons.json", "icons")

def _slug_for_model(m: str) -> str:
    if m in SLUG_ALIAS:
        return SLUG_ALIAS[m]
    c = m.lower().replace(" ", "-").replace(".", "-").replace("_", "-")
    while "--" in c:
        c = c.replace("--", "-")
    return c.strip("-")

def _get_model_meta_strict(m: str) -> dict[str, str]:
    if m not in MODEL_META:
        raise RuntimeError(f"model {m!r} not found in data/model-meta.json — 请在该文件中追加 {{\"brand\": ..., \"modality\": \"多模态/纯文字\"}}")
    v = MODEL_META[m]
    if not isinstance(v, dict) or "brand" not in v or "modality" not in v:
        raise RuntimeError(f"data/model-meta.json entry for {m!r} malformed: {v}")
    if v["modality"] not in ("多模态", "纯文字"):
        raise RuntimeError(f"data/model-meta.json modality for {m!r} must be 多模态/纯文字, got {v['modality']}")
    return v

def _get_icon_strict(brand: str) -> str:
    if brand not in ICONS_DATA:
        raise RuntimeError(f"brand {brand!r} not found in data/icons.json — 请在该文件中追加 data:image/svg+xml;base64,...")
    return ICONS_DATA[brand]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def generate(template_path: Path, oc_quota_path: Path, goat_quota_path: Path, aa_path: Path, output_path: Path) -> None:
    tpl = template_path.read_text(encoding="utf-8")
    oc_doc = load(oc_quota_path)
    goat_doc = load(goat_quota_path)
    aa_doc = load(aa_path)

    style_m = re.search(r"<style>(.*?)</style>", tpl, re.S)
    STYLE = style_m.group(1) if style_m else ""
    # 图标来自 data/icons.json（严格模式，缺失已在顶部报错）
    ICONS_OBJ = json.dumps(ICONS_DATA, ensure_ascii=False)
    ICONS_JS = f"const icons={ICONS_OBJ};"

    import math

    def build_oc_payload():
        snapshots = oc_doc["snapshots"]
        aa_rows = aa_doc["models"]
        aa_by = {r["model"]: r for r in aa_rows}
        dates = sorted(snapshots, reverse=True)
        first_rows = snapshots[dates[0]]["models"]
        model_order = [r["model"] for r in first_rows]
        allm = set(model_order)
        for d in dates[1:]:
            for r in snapshots[d].get("models", []):
                m = r.get("model")
                if m and m not in allm:
                    model_order.append(m)
                    allm.add(m)
        valid = [r.get("requests_per_5h") for r in first_rows if isinstance(r.get("requests_per_5h"), int)]
        ref = max(valid)
        x_max = ref / min(valid)
        ints = [aa_by[m].get("intelligence") for m in model_order if isinstance(aa_by[m].get("intelligence"), (int, float))]
        y_min = max(0, math.floor(min(ints) - 2))
        y_max = math.ceil(max(ints) + 2)
        if y_max - y_min < 12:
            y_min = max(0, y_min - 4)
            y_max += 4
        base_data = [{"model": m, "intelligence": aa_by[m].get("intelligence"), **_get_model_meta_strict(m)} for m in model_order]
        quota_snapshots = {}
        for d in dates:
            rows_by = {r["model"]: r for r in snapshots[d].get("models", [])}
            normed = []
            for m in model_order:
                r = rows_by.get(m)
                if not r:
                    normed.append({"requests": None, "weekly": None, "monthly": None, "absent": True})
                else:
                    normed.append({"requests": r.get("requests_per_5h"), "weekly": r.get("requests_per_week"), "monthly": r.get("requests_per_month")})
            quota_snapshots[d] = normed
        norm_ref = oc_doc.get("normalization_reference", {"model": "配额最多者", "requests_per_5h": ref})
        y_ticks = list(range(y_min, y_max + 1, 4))

        def make_opts():
            opts = []
            for i, d in enumerate(dates):
                raw = snapshots[d].get("label", "")
                label = "今日" if i == 0 and raw in ("", "今日", "历史") else ("" if raw in ("今日", "历史") else raw)
                suffix = f" · {label}" if label else ""
                sel = " selected" if i == 0 else ""
                opts.append(f'<option value="{html.escape(d, quote=True)}"{sel}>{html.escape(d + suffix)}</option>')
            return "".join(opts)

        return dict(ref=ref, x_max=x_max, y_min=y_min, y_max=y_max, base_data=base_data, quota_snapshots=quota_snapshots, dates=dates, norm_ref=norm_ref, y_ticks=y_ticks, date_opts=make_opts())

    oc_payload = build_oc_payload()

    def build_goat_payload():
        aa_by_norm = {}
        for r in aa_doc["models"]:
            for k in [r["model"], r.get("aa_model_id") or ""]:
                if k:
                    aa_by_norm[norm(k)] = r
            aa_by_norm[norm(r["model"].replace("-", " ").replace(".", "").lower())] = r
        goat_intel_by_norm = {}
        try:
            _snap = goat_doc.get("snapshots") or {}
            _latest = max(_snap)
            for _m in _snap[_latest]["models"]:
                if isinstance(_m.get("intelligence"), (int, float)):
                    goat_intel_by_norm[norm(_m["model"])] = _m["intelligence"]
        except Exception:
            pass
        snapshots = goat_doc["snapshots"]
        dates = sorted(snapshots, reverse=True)
        model_order = [m["model"] for m in snapshots[dates[0]]["models"]]
        valid = [m["requests_per_5h"] for m in snapshots[dates[0]]["models"] if isinstance(m.get("requests_per_5h"), int)]
        ref = max(valid)
        x_max = ref / min(valid)
        def _intel_for(m):
            v = goat_intel_by_norm.get(norm(m))
            if isinstance(v, (int, float)): return v
            r = aa_by_norm.get(norm(m))
            if r and isinstance(r.get("intelligence"), (int, float)): return r["intelligence"]
            # alias 回落（Fast/HighSpeed 复用基座）
            alias = SLUG_ALIAS.get(m)
            if alias:
                r2 = aa_by_norm.get(norm(alias))
                if r2 and isinstance(r2.get("intelligence"), (int, float)): return r2["intelligence"]
            return None
        ints = []
        for m in model_order:
            v = _intel_for(m)
            if isinstance(v, (int, float)):
                ints.append(v)
        y_min = max(0, math.floor(min(ints) - 2))
        y_max = math.ceil(max(ints) + 2)
        if y_max - y_min < 12:
            y_min = max(0, y_min - 4)
            y_max += 4
        base_data = []
        for m in model_order:
            intel = _intel_for(m)
            meta = _get_model_meta_strict(m)
            base_data.append({"model": m, "intelligence": intel, **meta})
        quota_snapshots = {}
        for d in dates:
            rows = snapshots[d]["models"]
            by = {r["model"]: r for r in rows}
            normed = []
            for m in model_order:
                row = by.get(m)
                if not row:
                    normed.append({"requests": None, "weekly": None, "monthly": None, "absent": True})
                else:
                    normed.append({"requests": row.get("requests_per_5h"), "weekly": row.get("requests_per_week"), "monthly": row.get("requests_per_month")})
            quota_snapshots[d] = normed
        norm_ref = goat_doc.get("normalization_reference", {"model": "配额最多者", "requests_per_5h": ref})
        y_ticks = list(range(y_min, y_max + 1, 4))

        def make_opts():
            opts = []
            for i, d in enumerate(dates):
                raw = snapshots[d].get("label", "")
                label = "今日" if i == 0 and raw in ("", "今日", "历史") else ("" if raw in ("今日", "历史") else raw)
                suffix = f" · {label}" if label else ""
                sel = " selected" if i == 0 else ""
                opts.append(f'<option value="{html.escape(d, quote=True)}"{sel}>{html.escape(d + suffix)}</option>')
            return "".join(opts)

        return dict(ref=ref, x_max=x_max, y_min=y_min, y_max=y_max, base_data=base_data, quota_snapshots=quota_snapshots, dates=dates, norm_ref=norm_ref, y_ticks=y_ticks, date_opts=make_opts())

    goat_payload = build_goat_payload()

    # comparison payload (精选 10)
    ocr = {norm(r["model"]): r for r in oc_doc["snapshots"][max(oc_doc["snapshots"])]["models"]}
    gr2 = {norm(r["model"]): r for r in goat_doc["snapshots"][max(goat_doc["snapshots"])]["models"]}
    aa_by2 = {}
    for rr in aa_doc["models"]:
        for k in [rr["model"], rr.get("aa_model_id")]:
            if k and norm(k):
                aa_by2[norm(k)] = rr.get("intelligence")
        aa_by2[norm(rr["model"].replace("-", " ").replace(".", "").lower())] = rr.get("intelligence")

    def get_iq2(n, goat_key=None):
        g = gr2.get(goat_key or n)
        if g and isinstance(g.get("intelligence"), (int, float)):
            return g["intelligence"]
        if n in aa_by2 and isinstance(aa_by2[n], (int, float)):
            return aa_by2[n]
        if goat_key and goat_key in aa_by2 and isinstance(aa_by2[goat_key], (int, float)):
            return aa_by2[goat_key]
        return None

    def _brand_for_curated(display: str) -> str:
        # 严格从 model-meta.json 取 brand，缺失即报错
        return _get_model_meta_strict(display)["brand"]

    cmp_rows = []
    for d in CURATED_DEFS:
        o = ocr.get(d["oc_key"])
        g = gr2.get(d["goat_key"])
        if not o or not g:
            print(f"WARN cmp missing {d['display']}", file=sys.stderr)
            continue
        cmp_rows.append({"model": d["display"], "short": d["short"], "goat_name": g["model"], "iq": get_iq2(d["oc_key"], d["goat_key"]), "oc_m": o.get("requests_per_month"), "goat_m": g.get("requests_per_month"), "goat_credit": g.get("monthly_credits"), "brand": _brand_for_curated(d["display"])})
    cmp_rows.sort(key=lambda r: (-(r["iq"] or -1), r["model"]))

    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")

    EXTRA_CSS = """
.site-header--centered{position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;padding:28px 20px 18px;gap:0}
.site-header--centered h1{max-width:860px;margin:0 auto;text-align:center;text-wrap:balance;word-break:keep-all;overflow-wrap:normal}
.site-header--centered .lead{max-width:780px;margin:12px auto 0;text-align:center}
.nowrap{white-space:nowrap}
.site-header--centered .header-actions{position:absolute;top:18px;right:22px}
@media(max-width:900px){.site-header--centered .header-actions{position:static;margin-top:14px}}
.tabs{display:flex;justify-content:center;gap:10px;padding:18px 14px 10px;flex-wrap:wrap}
.tab{padding:9px 18px;border-radius:999px;border:1px solid #cbd5e1;background:#fff;color:#334155;font-weight:700;cursor:pointer;font-size:.88rem}
.tab.active{background:var(--teal);color:#fff;border-color:var(--teal);box-shadow:0 2px 10px rgba(15,118,110,.18)}
.panel-tab{display:none}
.panel-tab.active{display:block}
.row-grid{stroke:var(--line);stroke-width:1;opacity:.9}
.seg{stroke-width:1.7;stroke-dasharray:5 4;opacity:.9}
.dot-oc{fill:#2563eb;stroke:#fff;stroke-width:1.4}
.dot-goat{fill:#ea580c;stroke:#fff;stroke-width:1.4}
.win-tag{font-size:11px;font-weight:750}
#tooltip-cmp{position:absolute;z-index:2;pointer-events:none;min-width:220px;padding:10px 12px;background:#111827;color:#fff;border-radius:9px;box-shadow:0 8px 20px #0003;font-size:.8rem;line-height:1.5;opacity:0;transform:translate(-50%,calc(-100% - 16px));transition:opacity .12s}
#tooltip-cmp.on{opacity:1}
"""

    TABS_JS = """
document.querySelectorAll('.tab').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.panel-tab').forEach(p=>p.classList.remove('active'));
    document.getElementById('tab-'+btn.dataset.tab).classList.add('active');
    window.dispatchEvent(new Event('resize'));
  });
});
"""

    CMP_JS = f"""
const ICONS_CMP={ICONS_OBJ};
const DATA_CMP={json.dumps(cmp_rows, ensure_ascii=False, indent=2)};
const W_CMP=1040,ML_CMP=164,MR_CMP=96,MT_CMP=18,MB_CMP=42,ROWH_CMP=52;
function fmtCmp(v){{if(v==null)return"—";if(v>=1e6)return (v/1e6).toFixed(2)+"M";if(v>=1e3)return (v/1e3).toFixed(v>=1e5?0:1)+"k";return String(v);}}
const tooltipCmp=document.getElementById("tooltip-cmp");
function showTipCmp(html,anchor){{const r=anchor.getBoundingClientRect(),wrap=document.getElementById("chartWrap-cmp").getBoundingClientRect();tooltipCmp.innerHTML=html;tooltipCmp.classList.add("on");const cx=r.left+r.width/2-wrap.left,cy=r.top-wrap.top;tooltipCmp.style.left=cx+"px";tooltipCmp.style.top=(cy-10)+"px";tooltipCmp.style.transform="translate(-50%,-100%)";}}
function hideTipCmp(){{tooltipCmp.classList.remove("on");}}
function iconCmp(brand,x,y,s){{let im=document.createElementNS("http://www.w3.org/2000/svg","image");im.setAttribute("href",ICONS_CMP[brand]||ICONS_CMP.unknown);im.setAttribute("x",x);im.setAttribute("y",y);im.setAttribute("width",s);im.setAttribute("height",s);im.setAttribute("preserveAspectRatio","xMidYMid meet");return im;}}
function rowTipCmp(d){{const ratio=d.goat_m!=null&&d.oc_m?d.goat_m/d.oc_m:null;let win="";if(ratio!=null){{if(ratio>=1.05)win=` → <span style="color:#fb923c;font-weight:800">GOAT +${{((ratio-1)*100).toFixed(0)}}%</span>`;else if(ratio<=0.95)win=` → <span style="color:#60a5fa;font-weight:800">OC +${{((1/ratio-1)*100).toFixed(0)}}%</span>`;else win=" 持平";}}return `<strong>${{d.model}}</strong><span style="color:#cbd5e1;font-size:.78rem">IQ ${{d.iq!=null?d.iq.toFixed(1):"—"}}</span><span style="display:block;color:#e5e7eb">OpenCode Go 月请求：<b>${{fmtCmp(d.oc_m)}}</b></span><span style="display:block;color:#e5e7eb">Command GOAT 月请求：<b>${{fmtCmp(d.goat_m)}}</b></span><span style="display:block;color:#cbd5e1">GOAT 月档位 $${{d.goat_credit!=null?d.goat_credit:"—"}} · GOAT/OC ${{ratio!=null?(ratio*100).toFixed(0)+"%":"—"}}${{win}}</span>`;}}
(function(){{
const DATA=DATA_CMP, W=W_CMP, ML=ML_CMP, MR=MR_CMP, MT=MT_CMP, MB=MB_CMP, ROWH=ROWH_CMP;
const H=MT+DATA.length*ROWH+MB;
const svg=document.getElementById("svg-cmp");
svg.setAttribute("height",H); svg.setAttribute("viewBox",`0 0 1040 ${{H}}`);
const allM=DATA.map(d=>[d.oc_m,d.goat_m]).flat().filter(v=>v!=null&&v>0);
const LO=Math.min(...allM),HI=Math.max(...allM);
const Y0=Math.pow(10,Math.log10(LO)-0.15),Y1=Math.pow(10,Math.log10(HI)+0.12);
function xLog(v){{return (Math.log10(v)-Math.log10(Y0))/(Math.log10(Y1)-Math.log10(Y0))*(W-ML-MR)+ML;}}
function logTicks(lo,hi){{const out=[],seen=new Set();let p=Math.floor(Math.log10(lo));let guard=0;outer:while(p<=Math.ceil(Math.log10(hi))&&guard++<200){{for(const b of [1,2,5]){{const v=b*Math.pow(10,p);if(v>hi)break outer;if(v>=lo&&!seen.has(Math.round(v*1e4))){{out.push(v);seen.add(Math.round(v*1e4));}} }} p++;}}return out;}}
logTicks(Y0,Y1).forEach(v=>{{const xx=xLog(v);let l=document.createElementNS("http://www.w3.org/2000/svg","line");l.setAttribute("x1",xx);l.setAttribute("y1",MT);l.setAttribute("x2",xx);l.setAttribute("y2",H-MB);l.setAttribute("stroke","#dce4ed");l.setAttribute("stroke-width","1");svg.appendChild(l);let t=document.createElementNS("http://www.w3.org/2000/svg","text");t.setAttribute("x",xx);t.setAttribute("y",H-MB+18);t.setAttribute("text-anchor","middle");t.setAttribute("fill","#64748b");t.setAttribute("font-size","11");t.textContent=fmtCmp(v);svg.appendChild(t);}});
DATA.forEach((d,i)=>{{
  const yc=MT+i*ROWH+ROWH/2, xo=xLog(d.oc_m), xg=xLog(d.goat_m??d.oc_m);
  let g=document.createElementNS("http://www.w3.org/2000/svg","line");g.setAttribute("x1",ML);g.setAttribute("y1",yc);g.setAttribute("x2",W-MR);g.setAttribute("y2",yc);g.setAttribute("class","row-grid");svg.appendChild(g);
  svg.appendChild(iconCmp(d.brand,12,yc-19,16));
  let nm=document.createElementNS("http://www.w3.org/2000/svg","text");nm.setAttribute("x",34);nm.setAttribute("y",yc-2);nm.setAttribute("fill","#172235");nm.setAttribute("font-size","11.5");nm.setAttribute("font-weight","700");nm.textContent=d.short;svg.appendChild(nm);
  let iq=document.createElementNS("http://www.w3.org/2000/svg","text");iq.setAttribute("x",34);iq.setAttribute("y",yc+13);iq.setAttribute("fill","#64748b");iq.setAttribute("font-size","9.5");iq.textContent=`IQ ${{d.iq!=null?d.iq.toFixed(1):"—"}}`;svg.appendChild(iq);
  let seg=document.createElementNS("http://www.w3.org/2000/svg","line");seg.setAttribute("x1",xo);seg.setAttribute("y1",yc);seg.setAttribute("x2",xg);seg.setAttribute("y2",yc);seg.setAttribute("class","seg");seg.setAttribute("stroke",d.goat_m>d.oc_m?"#ea580c":"#2563eb");svg.appendChild(seg);
  let o=document.createElementNS("http://www.w3.org/2000/svg","circle");o.setAttribute("cx",xo);o.setAttribute("cy",yc);o.setAttribute("r",6.5);o.setAttribute("class","dot-oc");o.addEventListener("pointerenter",()=>showTipCmp(rowTipCmp(d),o));o.addEventListener("pointerleave",hideTipCmp);svg.appendChild(o);
  const r=7.5;let dp=document.createElementNS("http://www.w3.org/2000/svg","path");dp.setAttribute("d",`M ${{xg}} ${{yc-r}} L ${{xg+r}} ${{yc}} L ${{xg}} ${{yc+r}} L ${{xg-r}} ${{yc}} Z`);dp.setAttribute("class","dot-goat");dp.addEventListener("pointerenter",()=>showTipCmp(rowTipCmp(d),dp));dp.addEventListener("pointerleave",hideTipCmp);svg.appendChild(dp);
  const ratio=d.goat_m!=null&&d.oc_m?d.goat_m/d.oc_m:null;
  if(ratio!=null){{let wt=document.createElementNS("http://www.w3.org/2000/svg","text");const winner=ratio>=1.05?"GOAT":(ratio<=0.95?"OC":null);if(winner){{wt.setAttribute("x",W-MR+12);wt.setAttribute("y",yc+4);wt.setAttribute("class","win-tag");wt.setAttribute("fill",winner==="GOAT"?"#ea580c":"#2563eb");wt.textContent=`${{winner}} ${{((ratio>=1.05?((ratio-1)*100):((1/ratio-1)*100)).toFixed(0))}}%`;svg.appendChild(wt);}}else{{wt.setAttribute("x",W-MR+12);wt.setAttribute("y",yc+4);wt.setAttribute("class","win-tag");wt.setAttribute("fill","#94a3b8");wt.textContent="≈";svg.appendChild(wt);}}
}}}});
let cap=document.createElementNS("http://www.w3.org/2000/svg","text");cap.setAttribute("x",(ML+W-MR)/2);cap.setAttribute("y",H-10);cap.setAttribute("text-anchor","middle");cap.setAttribute("fill","#64748b");cap.setAttribute("font-size","12");cap.setAttribute("font-weight","600");cap.textContent="每月请求数 (log) →";svg.appendChild(cap);
}})();
"""

    oc_opts = oc_payload["date_opts"]
    goat_opts = goat_payload["date_opts"]

    html_out = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode Go × Command GOAT · 模型额度全景</title>
<style>
{STYLE}
{EXTRA_CSS}
</style>
</head>
<body>
<main>
<header class="site-header--centered">
  <div>
    <h1>OpenCode Go × Command GOAT · <span class="nowrap">模型额度全景</span></h1>
    <p class="lead">智力 × 成本帕累托最优解 · 代表模型月额对比（按 AA 智商从高到低）。三图合一：OpenCode Go 帕累托、Command GOAT 帕累托、代表模型横向哑铃对比。</p>
  </div>
  <div class="header-actions"><a class="github-link" href="https://github.com/Jst-Well-Dan/opencode-go-model-pareto" target="_blank" rel="noopener" aria-label="GitHub repository"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg><span>GitHub</span></a></div>
</header>

<nav class="tabs" role="tablist" aria-label="图表切换">
  <button class="tab active" data-tab="oc" role="tab" aria-selected="true">OpenCode Go · 帕累托</button>
  <button class="tab" data-tab="goat" role="tab">Command GOAT · 帕累托</button>
  <button class="tab" data-tab="cmp" role="tab">代表模型对比</button>
</nav>

<!-- OC Pareto -->
<section id="tab-oc" class="panel-tab active" role="tabpanel">
<section class="panel" aria-labelledby="chart-title-oc">
<div class="chart-head"><div><div class="eyebrow">Pareto optimal · OpenCode Go</div><div class="chart-title" id="chart-title-oc">模型效能分布</div></div><div class="controls"><div class="control-group"><label for="dateSelect-oc">数据日期</label><select id="dateSelect-oc">{oc_opts}</select></div><div class="control-group"><span>横轴刻度</span><button id="logBtn-oc" class="active" type="button" aria-pressed="true">对数</button><button id="linearBtn-oc" type="button" aria-pressed="false">线性</button></div></div></div>
<div class="timeline" id="timeline-oc"><button id="prevBtn-oc" class="pill" type="button">◀</button><input type="range" id="dateRange-oc"><button id="nextBtn-oc" class="pill" type="button">▶</button><span class="date-chip" id="dateChip-oc"></span></div>
<div class="legend"><span class="legend-key"><i class="ring-key"></i>橙色外环：帕累托最优</span><span class="legend-key"><i class="line-key"></i>帕累托最优连线</span><span class="legend-key"><i class="badge-key multi">M</i>多模态</span><span class="legend-key"><i class="badge-key text">T</i>纯文字</span><span class="legend-key"><i class="ref-key"></i>虚线头像：无 AA 分</span></div>
<div class="chart-wrap" id="chartWrap-oc"><svg class="chart" id="chart-oc" viewBox="0 0 1080 560" role="img"><title>AA 智力指数与相对配额成本散点图</title></svg><div class="tooltip" id="tooltip-oc" role="status" aria-live="polite"></div></div>
</section>
<div class="below">
<section class="panel summary"><h2>帕累托最优解（按成本由低到高）</h2><div class="cards" id="cards-oc"></div></section>
<aside class="panel missing" id="missingPanel-oc"><h2>缺失数据</h2><div class="missing-row" id="missingRow-oc"></div><p>AA Index 列表截至 <strong id="missingDate-oc"></strong> 无匹配条目，因此智力指数为空；其相对成本已在图表横轴以虚线头像标注位置。</p><p>待指标补齐后方可判断是否属于帕累托最优集合。</p></aside>
</div>
<p class="foot">数据来源：OpenCode Go 用量快照与 AA Index。相对配额成本以配额最多者 = 1.0 为基准（当前 <span id="footRef-oc"></span>）；用量日期可通过上方时间轴/下拉切换。文件离线可用（图标已嵌入）。</p>
</section>

<!-- GOAT Pareto -->
<section id="tab-goat" class="panel-tab" role="tabpanel">
<section class="panel" aria-labelledby="chart-title-goat">
<div class="chart-head"><div><div class="eyebrow">Pareto optimal · Command GOAT</div><div class="chart-title" id="chart-title-goat">模型效能分布 — GOAT 32 模型</div></div><div class="controls"><div class="control-group"><label for="dateSelect-goat">数据日期</label><select id="dateSelect-goat">{goat_opts}</select></div><div class="control-group"><span>横轴刻度</span><button id="logBtn-goat" class="active" type="button" aria-pressed="true">对数</button><button id="linearBtn-goat" type="button" aria-pressed="false">线性</button></div></div></div>
<div class="timeline" id="timeline-goat"><button id="prevBtn-goat" class="pill" type="button">◀</button><input type="range" id="dateRange-goat"><button id="nextBtn-goat" class="pill" type="button">▶</button><span class="date-chip" id="dateChip-goat"></span></div>
<div class="legend"><span class="legend-key"><i class="ring-key"></i>橙色外环：帕累托最优</span><span class="legend-key"><i class="line-key"></i>帕累托最优连线</span><span class="legend-key"><i class="badge-key multi">M</i>多模态</span><span class="legend-key"><i class="badge-key text">T</i>纯文字</span><span class="legend-key"><i class="ref-key"></i>虚线头像：无 AA 分</span></div>
<div class="chart-wrap" id="chartWrap-goat"><svg class="chart" id="chart-goat" viewBox="0 0 1080 560" role="img"><title>GOAT 智力与配额成本散点图</title></svg><div class="tooltip" id="tooltip-goat" role="status" aria-live="polite"></div></div>
</section>
<div class="below">
<section class="panel summary"><h2>帕累托最优解（按成本由低到高）</h2><div class="cards" id="cards-goat"></div></section>
<aside class="panel missing" id="missingPanel-goat"><h2>缺失数据</h2><div class="missing-row" id="missingRow-goat"></div><p>AA Index 列表截至 <strong id="missingDate-goat"></strong> 无匹配条目，因此智力指数为空；其相对成本已在图表横轴以虚线头像标注位置。</p><p>待指标补齐后方可判断是否属于帕累托最优集合。</p></aside>
</div>
<p class="foot">数据来源：Command GOAT 核心两表（Estimated request counts + Monthly credits）与 AA Index。相对配额成本以配额最多者 = 1.0 为基准（当前 <span id="footRef-goat"></span>）。文件离线可用。</p>
</section>

<!-- Comparison -->
<section id="tab-cmp" class="panel-tab" role="tabpanel">
<section class="panel">
<div class="chart-head"><div><div class="eyebrow">Selected models — ranked by intelligence</div><div class="chart-title">横向哑铃排行图 · 智商降序（高 → 低）</div></div></div>
<div class="legend"><span class="legend-key"><i class="dot-key oc"></i>OpenCode Go</span><span class="legend-key"><i class="dot-key goat"></i>Command GOAT</span><span class="legend-key" style="color:var(--muted)">右端标签：该 IQ 段赢家与幅度</span></div>
<div class="chart-wrap" id="chartWrap-cmp"><svg class="chart" id="svg-cmp" width="1040" height="{18 + len(cmp_rows)*52 + 42}" viewBox="0 0 1040 {18 + len(cmp_rows)*52 + 42}" role="img"><title>代表模型月请求额横向哑铃图</title></svg><div id="tooltip-cmp" role="status" aria-live="polite"></div></div>
</section>
<p class="foot">精选 {len(cmp_rows)} 个代表模型（帕累托最优前沿 ∪ DeepSeek 全部 ∪ ChatGPT/GPT ∪ MiMo V2.5 ∪ Kimi K3），按 AA 智商从高到低。数据来源：OpenCode Go 用量快照与 GOAT 核心两表，AA Intelligence Index。对数刻度；GOAT 各模型月档位 $20~$70，OpenCode 固定 $15/30/60。生成于 {generated_at}。</p>
</section>

</main>
<script>
{TABS_JS}
</script>
</body>
</html>
"""

    PARETO_FACTORY = """
function createParetoChart(prefix, baseData, quotaSnapshots, yMin, yMax, xMax, quotaReference, normalizationReference, yTicks){
  const svg=document.getElementById("chart-"+prefix), tip=document.getElementById("tooltip-"+prefix);
  const wrap=document.getElementById("chartWrap-"+prefix);
  const dsSelect=document.getElementById("dateSelect-"+prefix), dsRange=document.getElementById("dateRange-"+prefix);
  const prevBtn=document.getElementById("prevBtn-"+prefix), nextBtn=document.getElementById("nextBtn-"+prefix), chip=document.getElementById("dateChip-"+prefix);
  const logBtn=document.getElementById("logBtn-"+prefix), linearBtn=document.getElementById("linearBtn-"+prefix);
  const cardsEl=document.getElementById("cards-"+prefix), missingPanel=document.getElementById("missingPanel-"+prefix), missingRow=document.getElementById("missingRow-"+prefix), missingDateEl=document.getElementById("missingDate-"+prefix), footRef=document.getElementById("footRef-"+prefix);
  const NS="http://www.w3.org/2000/svg", W=1080, H=560, M={l:75,r:35,t:42,b:72};
  let scaleMode="log";
  const modalityMeta={"多模态":{mark:"M",cls:"multi"},"纯文字":{mark:"T",cls:"text"}};
  const icons=""" + ICONS_OBJ + """;
  function node(tag,attrs={},text=""){const el=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text)el.textContent=text;return el;}
  function x(v){const t=scaleMode==="log"?Math.log10(v)/Math.log10(xMax):(v-1)/(xMax-1);return M.l+t*(W-M.l-M.r);}
  function y(v){return H-M.b-(v-yMin)/(yMax-yMin)*(H-M.t-M.b);}
  function buildData(date){return quotaSnapshots[date].map((q,i)=>({...baseData[i],requests:q.requests,weekly:q.weekly,monthly:q.monthly,cost:q.requests?quotaReference/q.requests:null,pareto:null,absent:!!q.absent}));}
  const datasets=Object.fromEntries(Object.keys(quotaSnapshots).map(d=>[d,buildData(d)]));
  const allDates=Object.keys(quotaSnapshots).sort(); let data=[],plotted=[],frontier=[],activeDate=allDates[allDates.length-1];
  function syncTimeline(date){const idx=allDates.indexOf(date);if(dsRange){dsRange.min=0;dsRange.max=Math.max(0,allDates.length-1);dsRange.value=idx}if(chip)chip.textContent=date+(date===allDates[allDates.length-1]?" · 今日":"");if(dsSelect)dsSelect.value=date;if(prevBtn)prevBtn.disabled=idx<=0;if(nextBtn)nextBtn.disabled=idx>=allDates.length-1;}
  function setParetoFlags(){plotted=data.filter(d=>d.intelligence!==null&&d.cost!==null);plotted.forEach(d=>{d.pareto=!plotted.some(o=>o!==d&&o.intelligence>=d.intelligence&&o.cost<=d.cost&&(o.intelligence>d.intelligence||o.cost<d.cost))});frontier=plotted.filter(d=>d.pareto).sort((a,b)=>a.cost-b.cost);}
  function showTip(d){const s=svg.getBoundingClientRect().width/W;tip.innerHTML=`<strong>${d.model}</strong><span style="display:block;color:#cbd5e1;font-size:.72rem">${activeDate}</span>${d.pareto?'<span style="color:#fdba74;font-weight:700">帕累托最优</span><br>':''}模态：${d.modality}<br>智力指数：${d.intelligence.toFixed(1)}<br>相对成本：${d.cost.toFixed(3)}<br>配额：${d.requests.toLocaleString()} / 5 小时<br>每周：${d.weekly.toLocaleString()}<br>每月：${d.monthly.toLocaleString()}`;tip.style.left=`${x(d.cost)*s+10}px`;tip.style.top=`${y(d.intelligence)*s+10}px`;tip.classList.add("on");}
  function hideTip(){tip.classList.remove("on");}
  function render(){svg.replaceChildren();svg.append(node("title",{}, "AA 智力指数与相对配额成本"), node("desc",{}, `${plotted.length} 模型，${frontier.length} 帕累托最优`));const defs=node("defs"),mk=node("marker",{id:"arrow-"+prefix,viewBox:"0 0 10 10",refX:8,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"});mk.append(node("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#0f766e"}));defs.append(mk);svg.append(defs);yTicks.forEach(v=>svg.append(node("line",{x1:M.l,y1:y(v),x2:W-M.r,y2:y(v),class:"grid"}),node("text",{x:M.l-12,y:y(v)+4,"text-anchor":"end",class:"tick"},String(v))));const ticks=scaleMode==="log"?[1,2,5,10,20,50,100,200,300]:[1,50,100,150,200,250,300];ticks.forEach(v=>svg.append(node("line",{x1:x(v),y1:M.t,x2:x(v),y2:H-M.b,class:"grid"}),node("text",{x:x(v),y:H-M.b+24,"text-anchor":"middle",class:"tick"},String(v))));svg.append(node("line",{x1:M.l,y1:H-M.b,x2:W-M.r,y2:H-M.b,class:"axis"}),node("line",{x1:M.l,y1:M.t,x2:M.l,y2:H-M.b,class:"axis"}),node("text",{x:(M.l+W-M.r)/2,y:H-22,"text-anchor":"middle",class:"axis-label"},scaleMode==="log"?"相对配额成本（越低越好 · 对数刻度）":"相对配额成本（越低越好 · 线性刻度）"),node("text",{x:20,y:(M.t+H-M.b)/2,transform:`rotate(-90 20 ${(M.t+H-M.b)/2})`,"text-anchor":"middle",class:"axis-label"},"AA Intelligence Index"),node("line",{x1:178,y1:104,x2:111,y2:60,class:"ideal-line"}),node("text",{x:185,y:108,class:"ideal"},"理想方向：左上 ↖"),node("polyline",{points:frontier.map(d=>`${x(d.cost)},${y(d.intelligence)}`).join(" "),class:"optimal-line"}));plotted.filter(d=>!d.pareto).concat(frontier).forEach(d=>{const meta=modalityMeta[d.modality],g=node("g",{class:`point${d.pareto?" optimal":""}`,transform:`translate(${x(d.cost)} ${y(d.intelligence)})`,tabindex:"0"});g.append(node("circle",{r:18,class:"avatar-bg"}),node("image",{href:icons[d.brand],x:-12,y:-12,width:24,height:24,preserveAspectRatio:"xMidYMid meet"}),node("circle",{cx:13,cy:13,r:8,class:`point-badge ${meta.cls}`}),node("text",{x:13,y:13,class:"point-badge-text"},meta.mark));g.addEventListener("pointerenter",()=>showTip(d));g.addEventListener("pointerleave",hideTip);svg.append(g);});data.filter(d=>d.intelligence===null&&!d.absent).forEach(d=>{const onFree=d.cost===null,px=onFree?M.l:x(d.cost),g=node("g",{class:"axis-ref",transform:`translate(${px} ${H-M.b})`,tabindex:"0"});g.append(node("circle",{r:14,class:"ref-avatar-bg"}),node("image",{href:icons[d.brand],x:-9,y:-9,width:18,height:18,opacity:.6,preserveAspectRatio:"xMidYMid meet"}),node("text",{y:-21,"text-anchor":"middle",class:"ref-label"},onFree?"免费 · 不限":`${d.model} （无 AA 分）`));svg.append(g);});}
  function setScale(m){scaleMode=m;[logBtn,linearBtn].forEach(b=>{if(!b)return;b.classList.toggle("active",b.id.includes(m));b.setAttribute("aria-pressed",String(b.id.includes(m)))});hideTip();render();}
  if(logBtn)logBtn.addEventListener("click",()=>setScale("log"));if(linearBtn)linearBtn.addEventListener("click",()=>setScale("linear"));
  function avatarMarkup(d,cls="card-avatar"){const m=modalityMeta[d.modality];return `<span class="avatar-wrap"><img class="${cls}" src="${icons[d.brand]}" alt=""><i class="modality-badge ${m.cls}">${m.mark}</i></span>`;}
  function renderSummary(){if(!cardsEl)return;cardsEl.innerHTML=frontier.map((d,i)=>`<div class="card"><div>${avatarMarkup(d)}</div><div><strong>${i+1}. ${d.model}</strong><span>${d.modality} · 成本 ${d.cost.toFixed(3)} · 智力 ${d.intelligence.toFixed(1)}</span></div></div>`).join("");const miss=data.filter(d=>d.intelligence===null&&!d.absent);if(miss.length){missingPanel.style.display="";missingRow.innerHTML=miss.map(d=>`<div class="missing-item">${avatarMarkup(d,"missing-avatar")}<span><span class="model">${d.model}</span><br>${d.modality} · ${d.requests?d.requests.toLocaleString()+" / 5 小时":"免费 · 不限"}</span></div>`).join("");if(missingDateEl)missingDateEl.textContent=activeDate;}else missingPanel.style.display="none"; if(footRef)footRef.textContent=`${normalizationReference.model} ${normalizationReference.requests_per_5h.toLocaleString()} / 5 小时`;}
  function setDataset(date){activeDate=date;data=datasets[date].map(d=>({...d}));setParetoFlags();syncTimeline(date);renderSummary();hideTip();render();}
  if(dsSelect)dsSelect.addEventListener("change",e=>setDataset(e.target.value));
  if(dsRange)dsRange.addEventListener("input",e=>setDataset(allDates[Number(e.target.value)]));
  if(prevBtn)prevBtn.addEventListener("click",()=>{const i=allDates.indexOf(activeDate);if(i>0)setDataset(allDates[i-1])});
  if(nextBtn)nextBtn.addEventListener("click",()=>{const i=allDates.indexOf(activeDate);if(i<allDates.length-1)setDataset(allDates[i+1])});
  setDataset(activeDate);
}
"""

    full_html = html_out.replace(
        "</body>",
        f"""<script>
{PARETO_FACTORY}
createParetoChart("oc", {json.dumps(oc_payload['base_data'], ensure_ascii=False)}, {json.dumps(oc_payload['quota_snapshots'], ensure_ascii=False)}, {oc_payload['y_min']}, {oc_payload['y_max']}, {oc_payload['x_max']}, {oc_payload['ref']}, {json.dumps(oc_payload['norm_ref'], ensure_ascii=False)}, {json.dumps(oc_payload['y_ticks'])});
createParetoChart("goat", {json.dumps(goat_payload['base_data'], ensure_ascii=False)}, {json.dumps(goat_payload['quota_snapshots'], ensure_ascii=False)}, {goat_payload['y_min']}, {goat_payload['y_max']}, {goat_payload['x_max']}, {goat_payload['ref']}, {json.dumps(goat_payload['norm_ref'], ensure_ascii=False)}, {json.dumps(goat_payload['y_ticks'])});
</script>
<script>
{CMP_JS}
</script>
</body>""",
        1,
    )

    output_path.write_text(full_html, encoding="utf-8")
    print(f"Generated {output_path} with OC {len(oc_payload['base_data'])} models, GOAT {len(goat_payload['base_data'])} models, CMP {len(cmp_rows)} models at {generated_at}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate index.html (merged view)")
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="template HTML path")
    parser.add_argument("--quota", type=Path, default=OC_QUOTA, help="OpenCode Go quota snapshots JSON")
    parser.add_argument("--goat-quota", type=Path, default=GOAT_QUOTA, help="GOAT quota snapshots JSON")
    parser.add_argument("--aa", type=Path, default=AA_PATH, help="AA scores JSON")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="output HTML path")
    parser.add_argument("--all", action="store_true", help="alias for default behavior (generate merged index.html)")
    args = parser.parse_args()
    generate(args.template, args.quota, args.goat_quota, args.aa, args.output)


if __name__ == "__main__":
    main()
