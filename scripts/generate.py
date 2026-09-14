#!/usr/bin/env python3
"""生成器：生成 index.html（OpenCode Go 帕累托单页）

Usage:
    python scripts/generate.py              # 生成 index.html
    python scripts/generate.py --all        # 同上（兼容旧 --all 调用）
    python scripts/generate.py --output index.html --template template/xxx.html

仅保留单一输出（index.html，无别名文件）。Command GOAT / 对比页已冻结为
独立存档页 goat-compare-archive.html，不再由此生成；
相关旧代码备份在 archive/ 目录。
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "opencode-go-model-pareto.template.html"
OC_QUOTA = ROOT / "data" / "snapshots" / "quota-snapshots.json"
AA_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
OUTPUT = ROOT / "index.html"

def _load_json_strict(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"missing required data file {path} ({name}) — 需在 data/ 下提供该 json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"failed to load {path}: {e}")

# 注册表（data/model-meta.json）
MODEL_META = _load_json_strict(ROOT / "data" / "registry" / "model-meta.json", "model-meta")
ICONS_DATA = _load_json_strict(ROOT / "data" / "registry" / "icons.json", "icons")

def _get_model_meta_strict(m: str) -> dict[str, str]:
    if m not in MODEL_META:
        raise RuntimeError(f"model {m!r} not found in data/model-meta.json — 请在该文件中追加 {{\"brand\": ..., \"modality\": \"多模态/纯文字/未知\"}}")
    v = MODEL_META[m]
    if not isinstance(v, dict) or "brand" not in v or "modality" not in v:
        raise RuntimeError(f"data/model-meta.json entry for {m!r} malformed: {v}")
    if v["modality"] not in ("多模态", "纯文字", "未知"):
        raise RuntimeError(f"data/model-meta.json modality for {m!r} must be 多模态/纯文字/未知, got {v['modality']}")
    return v

def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _nice_floor(v: float) -> float:
    """1-2-5 取整下界（对数轴定义域用）。"""
    if v <= 0:
        return v
    exp = math.floor(math.log10(v))
    f = v / 10 ** exp
    return (5 if f >= 5 else (2 if f >= 2 else 1)) * 10 ** exp


def _nice_ceil(v: float) -> float:
    """1-2-5 取整上界。"""
    if v <= 0:
        return v
    exp = math.floor(math.log10(v))
    f = v / 10 ** exp
    return (1 if f <= 1 else (2 if f <= 2 else (5 if f <= 5 else 10))) * 10 ** exp


def _clean_tick(v: float) -> float:
    return float(f"{v:.9g}")


def _log_ticks(lo: float, hi: float) -> list:
    """定义域内的 1-2-5 对数刻度。"""
    out = []
    for exp in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 2):
        for m in (1, 2, 5):
            v = _clean_tick(m * 10 ** exp)
            if lo * (1 - 1e-9) <= v <= hi * (1 + 1e-9):
                out.append(v)
    return sorted(set(out))


def _linear_ticks(lo: float, hi: float, count: int = 6) -> list:
    """定义域内的 nice 线性刻度。"""
    if not hi > lo:
        return [_clean_tick(lo)]
    step0 = (hi - lo) / (count - 1)
    mag = 10 ** math.floor(math.log10(step0))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= step0), 10 * mag)
    out = []
    t = math.ceil(lo / step - 1e-9) * step
    while t <= hi * (1 + 1e-9):
        out.append(_clean_tick(t))
        t += step
    return out or [_clean_tick(lo), _clean_tick(hi)]


def generate(template_path: Path, oc_quota_path: Path, aa_path: Path, output_path: Path, domain: str = "legacy") -> None:
    tpl = template_path.read_text(encoding="utf-8")
    oc_doc = load(oc_quota_path)
    aa_doc = load(aa_path)
    aa_snapshots = aa_doc.get("snapshots")
    if not isinstance(aa_snapshots, dict):
        legacy_date = str(aa_doc.get("last_fetched_at", ""))[:10]
        aa_snapshots = {legacy_date: {"models": aa_doc.get("models", [])}}
    aa_dates = sorted(aa_snapshots)

    def aa_rows_for_date(day):
        candidates = [d for d in aa_dates if d <= day]
        chosen = max(candidates) if candidates else aa_dates[0]
        return aa_snapshots[chosen].get("models", [])

    style_m = re.search(r"<style>(.*?)</style>", tpl, re.S)
    STYLE = style_m.group(1) if style_m else ""
    # 图标来自 data/icons.json（严格模式，缺失已在顶部报错）
    ICONS_OBJ = json.dumps(ICONS_DATA, ensure_ascii=False)

    import math

    def build_oc_payload():
        snapshots = oc_doc["snapshots"]
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
        live_ref = max(valid)
        live_min = min(valid)
        anchor_doc = oc_doc.get("normalization_reference") or {}
        anchor = anchor_doc.get("requests_per_5h") if isinstance(anchor_doc.get("requests_per_5h"), int) else live_ref
        def score_rows(day):
            return {r["model"]: r for r in aa_rows_for_date(day)}

        def axis_range(values):
            ints = [v for v in values if isinstance(v, (int, float))]
            y_min = max(0, math.floor(min(ints) - 2))
            y_max = math.ceil(max(ints) + 2)
            if y_max - y_min < 12:
                y_min = max(0, y_min - 4)
                y_max += 4
            return y_min, y_max

        score_snapshots = {}
        y_ranges = {}
        y_ticks_snapshots = {}
        for d in dates:
            by = score_rows(d)
            values = [by.get(m, {}).get("intelligence") for m in model_order]
            score_snapshots[d] = values
            lo, hi = axis_range(values)
            y_ranges[d] = [lo, hi]
            y_ticks_snapshots[d] = list(range(lo, hi + 1, 4))
        y_min, y_max = y_ranges[dates[0]]
        latest_scores = score_rows(dates[0])
        base_data = [{"model": m, "intelligence": latest_scores.get(m, {}).get("intelligence"), **_get_model_meta_strict(m)} for m in model_order]
        quota_snapshots = {}
        for d in dates:
            rows_by = {r["model"]: r for r in snapshots[d].get("models", [])}
            normed = []
            for m in model_order:
                r = rows_by.get(m)
                if not r:
                    normed.append({"requests": None, "weekly": None, "monthly": None, "absent": True, "note": None})
                else:
                    normed.append({"requests": r.get("requests_per_5h"), "weekly": r.get("requests_per_week"), "monthly": r.get("requests_per_month"), "note": r.get("note")})
            quota_snapshots[d] = normed
        norm_ref = oc_doc.get("normalization_reference", {"model": "配额最多者", "requests_per_5h": live_ref})
        # 横轴定义域与刻度：legacy=历史行为（最新日 live 最大为 ref，左端固定 1）；
        # pinned=全日期共用固定锚与全局定义域；adaptive=逐日独立 ref 与定义域。
        all_reqs = [q["requests"] for d in dates for q in quota_snapshots[d]]
        refs, domains, ticks = {}, {}, {}
        for d in dates:
            reqs = [q["requests"] for q in quota_snapshots[d]]
            if domain == "adaptive":
                day_valid = [q for q in reqs if isinstance(q, int)]
                r = max(day_valid) if day_valid else live_ref
            elif domain == "pinned":
                r = anchor
            else:
                r = live_ref
            refs[d] = r
            if domain == "legacy":
                lo, hi = 1, live_ref / live_min
                ticks[d] = {"log": [1, 2, 5, 10, 20, 50, 100, 200, 300], "linear": [1, 50, 100, 150, 200, 250, 300]}
            else:
                pool = reqs if domain == "adaptive" else all_reqs
                costs = [r / q for q in pool if isinstance(q, int) and q > 0]
                if costs:
                    lo, hi = _nice_floor(min(costs)), _nice_ceil(max(costs))
                else:
                    lo, hi = 1, 2
                ticks[d] = {"log": _log_ticks(lo, hi), "linear": _linear_ticks(lo, hi)}
            domains[d] = [lo, hi]
        x_config = {"basis": domain, "refs": refs, "domains": domains, "ticks": ticks}
        y_ticks = y_ticks_snapshots[dates[0]]

        def make_opts():
            opts = []
            for i, d in enumerate(dates):
                raw = snapshots[d].get("label", "")
                label = "今日" if i == 0 and raw in ("", "今日", "历史") else ("" if raw in ("今日", "历史") else raw)
                suffix = f" · {label}" if label else ""
                sel = " selected" if i == 0 else ""
                opts.append(f'<option value="{html.escape(d, quote=True)}"{sel}>{html.escape(d + suffix)}</option>')
            return "".join(opts)

        return dict(y_min=y_min, y_max=y_max, base_data=base_data, quota_snapshots=quota_snapshots, score_snapshots=score_snapshots, y_ranges=y_ranges, y_ticks_snapshots=y_ticks_snapshots, dates=dates, norm_ref=norm_ref, y_ticks=y_ticks, date_opts=make_opts(), x_config=x_config)

    oc_payload = build_oc_payload()



    EXTRA_CSS = """
.site-header--centered{position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;padding:28px 20px 18px;gap:0}
.site-header--centered h1{max-width:860px;margin:0 auto;text-align:center;text-wrap:balance;word-break:keep-all;overflow-wrap:normal}
.site-header--centered .lead{max-width:780px;margin:12px auto 0;text-align:center}
.nowrap{white-space:nowrap}
.site-header--centered .header-actions{position:absolute;top:18px;right:22px}
@media(max-width:900px){.site-header--centered .header-actions{position:static;margin-top:14px}}
.github-link.src{color:#0f766e;border-color:#a7f3d0}.github-link.src:hover{background:#f0fdfa;border-color:#5eead4;color:#0b5f59}
.foot a.src-link{color:#0f766e;font-weight:650;text-decoration:none;border-bottom:1px dashed #5eead4}.foot a.src-link:hover{background:#f0fdfa;border-bottom-style:solid}
"""




    oc_opts = oc_payload["date_opts"]
    if domain == "pinned":
        eyebrow = "Pareto optimal · OpenCode Go · 固定基准对照"
        foot_basis = f"相对配额成本以{oc_payload['norm_ref'].get('model', '固定锚')} = 1.0 为固定基准"
    elif domain == "adaptive":
        eyebrow = "Pareto optimal · OpenCode Go · 每日自适应对照"
        foot_basis = "相对配额成本以当日配额最多者 = 1.0 为基准（各日期独立归一）"
    else:
        eyebrow = "Pareto optimal · OpenCode Go"
        foot_basis = "相对配额成本以配额最多者 = 1.0 为基准"

    html_out = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCode Go · 模型额度全景</title>
<style>
{STYLE}
{EXTRA_CSS}
</style>
</head>
<body>
<main>
<header class="site-header--centered">
  <div>
    <h1>OpenCode Go · <span class="nowrap">模型额度全景</span></h1>
    <p class="lead">智力 × 成本帕累托最优解（按 AA 智商从高到低）。用量日期可通过时间轴 / 下拉切换，文件离线可用。</p>
  </div>
  <div class="header-actions"><a class="github-link" href="https://github.com/Jst-Well-Dan/opencode-go-model-pareto" target="_blank" rel="noopener" aria-label="GitHub repository"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg><span>GitHub</span></a><a class="github-link src" href="https://artificialanalysis.ai/leaderboards/models" target="_blank" rel="noopener" title="Artificial Analysis · Intelligence Index 榜单"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M6.5 3.5h-3v9h9v-3"/><path d="M9.75 2.5h3.75v3.75"/><path d="M13.5 2.5 8.25 7.75"/></svg><span>AA 榜单</span></a><a class="github-link src" href="https://opencode.ai/docs/zh-cn/go/" target="_blank" rel="noopener" title="OpenCode Go 官方额度文档"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M6.5 3.5h-3v9h9v-3"/><path d="M9.75 2.5h3.75v3.75"/><path d="M13.5 2.5 8.25 7.75"/></svg><span>Go 额度</span></a></div>
</header>

<!-- OC Pareto -->
<section id="tab-oc">
<section class="panel" aria-labelledby="chart-title-oc">
<div class="chart-head"><div><div class="eyebrow">{eyebrow}</div><div class="chart-title" id="chart-title-oc">模型效能分布</div></div><div class="controls"><div class="control-group"><label for="dateSelect-oc">数据日期</label><select id="dateSelect-oc">{oc_opts}</select></div><div class="control-group"><span>横轴刻度</span><button id="logBtn-oc" class="active" type="button" aria-pressed="true">对数</button><button id="linearBtn-oc" type="button" aria-pressed="false">线性</button></div></div></div>
<div class="timeline" id="timeline-oc"><button id="prevBtn-oc" class="pill" type="button">◀</button><input type="range" id="dateRange-oc"><button id="nextBtn-oc" class="pill" type="button">▶</button><span class="date-chip" id="dateChip-oc"></span></div>
<div class="legend"><span class="legend-key"><i class="ring-key"></i>橙色外环：帕累托最优</span><span class="legend-key"><i class="line-key"></i>帕累托最优连线</span><span class="legend-key"><i class="badge-key multi">M</i>多模态</span><span class="legend-key"><i class="badge-key text">T</i>纯文字</span><span class="legend-key"><i class="badge-key unknown">?</i>模态未知（AA 未收录）</span><span class="legend-key"><i class="ref-key"></i>虚线头像：无 AA 分</span></div>
<div class="chart-wrap" id="chartWrap-oc"><svg class="chart" id="chart-oc" viewBox="0 0 1080 560" role="img"><title>AA 智力指数与相对配额成本散点图</title></svg><div class="tooltip" id="tooltip-oc" role="status" aria-live="polite"></div></div>
</section>
<div class="below">
<section class="panel summary"><h2>帕累托最优解（按成本由低到高）</h2><div class="cards" id="cards-oc"></div></section>
<aside class="panel missing" id="missingPanel-oc"><h2>缺失数据</h2><div class="missing-row" id="missingRow-oc"></div><p>AA Index 列表截至 <strong id="missingDate-oc"></strong> 无匹配条目，因此智力指数为空；其相对成本已在图表横轴以虚线头像标注位置。</p><p>待指标补齐后方可判断是否属于帕累托最优集合。</p></aside>
</div>
<p class="foot">数据来源：<a class="src-link" href="https://opencode.ai/docs/zh-cn/go/" target="_blank" rel="noopener">OpenCode Go 用量快照</a> 与 <a class="src-link" href="https://artificialanalysis.ai/leaderboards/models" target="_blank" rel="noopener">AA Index</a>。{foot_basis}（当前 <span id="footRef-oc"></span>）；用量日期可通过上方时间轴/下拉切换。文件离线可用（图标已嵌入）。</p>
</section>


</main>
</body>
</html>
"""

    PARETO_FACTORY = """
function createParetoChart(prefix, baseData, quotaSnapshots, scoreSnapshots, yRanges, yTicksSnapshots, yMin, yMax, normalizationReference, yTicks, xConfig){
  const svg=document.getElementById("chart-"+prefix), tip=document.getElementById("tooltip-"+prefix);
  const wrap=document.getElementById("chartWrap-"+prefix);
  const dsSelect=document.getElementById("dateSelect-"+prefix), dsRange=document.getElementById("dateRange-"+prefix);
  const prevBtn=document.getElementById("prevBtn-"+prefix), nextBtn=document.getElementById("nextBtn-"+prefix), chip=document.getElementById("dateChip-"+prefix);
  const logBtn=document.getElementById("logBtn-"+prefix), linearBtn=document.getElementById("linearBtn-"+prefix);
  const cardsEl=document.getElementById("cards-"+prefix), missingPanel=document.getElementById("missingPanel-"+prefix), missingRow=document.getElementById("missingRow-"+prefix), missingDateEl=document.getElementById("missingDate-"+prefix), footRef=document.getElementById("footRef-"+prefix);
  const NS="http://www.w3.org/2000/svg", W=1080, H=560, M={l:75,r:35,t:42,b:72};
  let scaleMode="log", activeYMin=yMin, activeYMax=yMax, activeYTicks=yTicks, activeXMin=1, activeXMax=2, activeXTicks=[];
  const modalityMeta={"多模态":{mark:"M",cls:"multi"},"纯文字":{mark:"T",cls:"text"},"未知":{mark:"?",cls:"unknown"}};
  const icons=""" + ICONS_OBJ + """;
  function node(tag,attrs={},text=""){const el=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));if(text)el.textContent=text;return el;}
  function x(v){const t=scaleMode==="log"?(Math.log10(v)-Math.log10(activeXMin))/(Math.log10(activeXMax)-Math.log10(activeXMin)):(v-activeXMin)/(activeXMax-activeXMin);return M.l+t*(W-M.l-M.r);}
  function y(v){return H-M.b-(v-activeYMin)/(activeYMax-activeYMin)*(H-M.t-M.b);}
  function buildData(date){return quotaSnapshots[date].map((q,i)=>({...baseData[i],intelligence:scoreSnapshots[date][i],requests:q.requests,weekly:q.weekly,monthly:q.monthly,note:q.note||null,cost:q.requests?xConfig.refs[date]/q.requests:null,pareto:null,absent:!!q.absent}));}
  const datasets=Object.fromEntries(Object.keys(quotaSnapshots).map(d=>[d,buildData(d)]));
  const allDates=Object.keys(quotaSnapshots).sort(); let data=[],plotted=[],frontier=[],activeDate=allDates[allDates.length-1];
  function syncTimeline(date){const idx=allDates.indexOf(date);if(dsRange){dsRange.min=0;dsRange.max=Math.max(0,allDates.length-1);dsRange.value=idx}if(chip)chip.textContent=date+(date===allDates[allDates.length-1]?" · 今日":"");if(dsSelect)dsSelect.value=date;if(prevBtn)prevBtn.disabled=idx<=0;if(nextBtn)nextBtn.disabled=idx>=allDates.length-1;}
  function setParetoFlags(){plotted=data.filter(d=>d.intelligence!==null&&d.cost!==null);plotted.forEach(d=>{d.pareto=!plotted.some(o=>o!==d&&o.intelligence>=d.intelligence&&o.cost<=d.cost&&(o.intelligence>d.intelligence||o.cost<d.cost))});frontier=plotted.filter(d=>d.pareto).sort((a,b)=>a.cost-b.cost);}
  function showTip(d){const s=svg.getBoundingClientRect().width/W;tip.innerHTML=`<strong>${d.model}</strong><span style="display:block;color:#cbd5e1;font-size:.72rem">${activeDate}</span>${d.pareto?'<span style="color:#fdba74;font-weight:700">帕累托最优</span><br>':''}模态：${d.modality}${d.note?`<br>备注：${d.note}`:""}<br>智力指数：${d.intelligence.toFixed(1)}<br>相对成本：${d.cost.toFixed(3)}<br>配额：${d.requests.toLocaleString()} / 5 小时<br>每周：${d.weekly.toLocaleString()}<br>每月：${d.monthly.toLocaleString()}`;tip.style.left=`${x(d.cost)*s+10}px`;tip.style.top=`${y(d.intelligence)*s+10}px`;tip.classList.add("on");}
  function hideTip(){tip.classList.remove("on");}
  function render(){svg.replaceChildren();svg.append(node("title",{}, "AA 智力指数与相对配额成本"), node("desc",{}, `${plotted.length} 模型，${frontier.length} 帕累托最优`));const defs=node("defs"),mk=node("marker",{id:"arrow-"+prefix,viewBox:"0 0 10 10",refX:8,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"});mk.append(node("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#0f766e"}));defs.append(mk);svg.append(defs);activeYTicks.forEach(v=>svg.append(node("line",{x1:M.l,y1:y(v),x2:W-M.r,y2:y(v),class:"grid"}),node("text",{x:M.l-12,y:y(v)+4,"text-anchor":"end",class:"tick"},String(v))));const ticks=activeXTicks[scaleMode];ticks.forEach(v=>svg.append(node("line",{x1:x(v),y1:M.t,x2:x(v),y2:H-M.b,class:"grid"}),node("text",{x:x(v),y:H-M.b+24,"text-anchor":"middle",class:"tick"},String(v))));svg.append(node("line",{x1:M.l,y1:H-M.b,x2:W-M.r,y2:H-M.b,class:"axis"}),node("line",{x1:M.l,y1:M.t,x2:M.l,y2:H-M.b,class:"axis"}),node("text",{x:(M.l+W-M.r)/2,y:H-22,"text-anchor":"middle",class:"axis-label"},scaleMode==="log"?"相对配额成本（越低越好 · 对数刻度）":"相对配额成本（越低越好 · 线性刻度）"),node("text",{x:20,y:(M.t+H-M.b)/2,transform:`rotate(-90 20 ${(M.t+H-M.b)/2})`,"text-anchor":"middle",class:"axis-label"},"AA Intelligence Index"),node("line",{x1:178,y1:104,x2:111,y2:60,class:"ideal-line"}),node("text",{x:185,y:108,class:"ideal"},"理想方向：左上 ↖"),node("polyline",{points:frontier.map(d=>`${x(d.cost)},${y(d.intelligence)}`).join(" "),class:"optimal-line"}));plotted.filter(d=>!d.pareto).concat(frontier).forEach(d=>{const meta=modalityMeta[d.modality],g=node("g",{class:`point${d.pareto?" optimal":""}`,transform:`translate(${x(d.cost)} ${y(d.intelligence)})`,tabindex:"0"});g.append(node("circle",{r:18,class:"avatar-bg"}),node("image",{href:icons[d.brand],x:-12,y:-12,width:24,height:24,preserveAspectRatio:"xMidYMid meet"}),node("circle",{cx:13,cy:13,r:8,class:`point-badge ${meta.cls}`}),node("text",{x:13,y:13,class:"point-badge-text"},meta.mark));g.addEventListener("pointerenter",()=>showTip(d));g.addEventListener("pointerleave",hideTip);svg.append(g);});data.filter(d=>d.intelligence===null&&!d.absent).forEach(d=>{const onFree=d.cost===null,px=onFree?M.l:x(d.cost),g=node("g",{class:"axis-ref",transform:`translate(${px} ${H-M.b})`,tabindex:"0"});g.append(node("circle",{r:14,class:"ref-avatar-bg"}),node("image",{href:icons[d.brand],x:-9,y:-9,width:18,height:18,opacity:.6,preserveAspectRatio:"xMidYMid meet"}),node("text",{y:-21,"text-anchor":"middle",class:"ref-label"},onFree?"免费 · 不限":`${d.model} （无 AA 分）`));svg.append(g);});}
  function setScale(m){scaleMode=m;[logBtn,linearBtn].forEach(b=>{if(!b)return;b.classList.toggle("active",b.id.includes(m));b.setAttribute("aria-pressed",String(b.id.includes(m)))});hideTip();render();}
  if(logBtn)logBtn.addEventListener("click",()=>setScale("log"));if(linearBtn)linearBtn.addEventListener("click",()=>setScale("linear"));
  function avatarMarkup(d,cls="card-avatar"){const m=modalityMeta[d.modality];return `<span class="avatar-wrap"><img class="${cls}" src="${icons[d.brand]}" alt=""><i class="modality-badge ${m.cls}">${m.mark}</i></span>`;}
  function renderSummary(){if(!cardsEl)return;cardsEl.innerHTML=frontier.map((d,i)=>`<div class="card"><div>${avatarMarkup(d)}</div><div><strong>${i+1}. ${d.model}</strong><span>${d.modality} · 成本 ${d.cost.toFixed(3)} · 智力 ${d.intelligence.toFixed(1)}</span></div></div>`).join("");const miss=data.filter(d=>d.intelligence===null&&!d.absent);if(miss.length){missingPanel.style.display="";missingRow.innerHTML=miss.map(d=>`<div class="missing-item">${avatarMarkup(d,"missing-avatar")}<span><span class="model">${d.model}</span><br>${d.modality} · ${d.requests?d.requests.toLocaleString()+" / 5 小时":"免费 · 不限"}</span></div>`).join("");if(missingDateEl)missingDateEl.textContent=activeDate;}else missingPanel.style.display="none"; if(footRef){if(xConfig.basis==="adaptive"){const top=data.filter(d=>d.requests).sort((a,b)=>b.requests-a.requests)[0];footRef.textContent=top?`${top.model} ${top.requests.toLocaleString()} / 5 小时`:"—";}else{footRef.textContent=`${normalizationReference.model} ${normalizationReference.requests_per_5h.toLocaleString()} / 5 小时`;}}}
  function setDataset(date){activeDate=date;activeYMin=yRanges[date][0];activeYMax=yRanges[date][1];activeYTicks=yTicksSnapshots[date];activeXMin=xConfig.domains[date][0];activeXMax=xConfig.domains[date][1];activeXTicks=xConfig.ticks[date];data=datasets[date].map(d=>({...d}));setParetoFlags();syncTimeline(date);renderSummary();hideTip();render();}
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
createParetoChart("oc", {json.dumps(oc_payload['base_data'], ensure_ascii=False)}, {json.dumps(oc_payload['quota_snapshots'], ensure_ascii=False)}, {json.dumps(oc_payload['score_snapshots'], ensure_ascii=False)}, {json.dumps(oc_payload['y_ranges'], ensure_ascii=False)}, {json.dumps(oc_payload['y_ticks_snapshots'], ensure_ascii=False)}, {oc_payload['y_min']}, {oc_payload['y_max']}, {json.dumps(oc_payload['norm_ref'], ensure_ascii=False)}, {json.dumps(oc_payload['y_ticks'])}, {json.dumps(oc_payload['x_config'], ensure_ascii=False)});
</script>
</body>""",
        1,
    )

    output_path.write_text(full_html, encoding="utf-8")
    print(f"Generated {output_path} with OC {len(oc_payload['base_data'])} models")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate index.html (OpenCode Go only)")
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="template HTML path")
    parser.add_argument("--quota", type=Path, default=OC_QUOTA, help="OpenCode Go quota snapshots JSON")
    parser.add_argument("--aa", type=Path, default=AA_PATH, help="AA scores JSON")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="output HTML path")
    parser.add_argument("--all", action="store_true", help="alias for default behavior (generate index.html)")
    parser.add_argument("--domain", choices=["legacy", "pinned", "adaptive"], default="legacy", help="横轴方案：legacy=历史行为；pinned=固定锚+全局定义域；adaptive=逐日独立归一")
    args = parser.parse_args()
    generate(args.template, args.quota, args.aa, args.output, args.domain)


if __name__ == "__main__":
    main()
