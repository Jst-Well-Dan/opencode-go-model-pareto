#!/usr/bin/env python3
"""抓取 OpenCode Go / OpenDesign Go 的套餐额度与模型单价，产出 archive/plans.json。

归档状态（2026-11-17）：本文件已从 scripts/ 搬入 archive/，**不再参与每日任务**。
页面是冻结快照 archive/opendesign-pareto.html。需要手动刷新时：
    python archive/fetch_plans.py --generate

口径（两边共用同一套语义，才能同图比较）：
  - list price：厂商自己公布的输入/输出单价（美元 / 百万 token）。
    注意两家对同一个模型可能报不同价（OpenDesign 的 DeepSeek 价正好是 OpenCode 的 2 倍），
    所以必须先抓各自的公开价，不能拿一家的价去套另一家的额度。
  - allowance：官方公布的「只用该模型一个月，能消耗多少美元的列表价用量」。
    OpenCode 来自 /inference/go/v1/models 的 go.monthly_usage.go；
    OpenDesign 来自定价页 Coding Plan 使用额度表中按套餐分列的 $ 数字。
  - 有效单价 = list_price × (套餐月费 / allowance)。等价于「这 10 美元/100 美元如果全砸在这个模型上，
    每百万 token 实际花多少」。跨厂商可比，因为 token 就是 token。

数据源：
  - OpenCode 模型 + 额度：https://opencode.ai/inference/go/v1/models
  - OpenCode 套餐价 / 窗口规则：https://opencode.ai/v2/docs/console/go/
  - OpenDesign 模型目录（列表价）：https://go.open-design.ai/zh/models/
  - OpenDesign 套餐价 + 各模型额度：https://open-design.ai/zh/pricing/
  - OpenDesign 额度扣除倍率：https://open-design.ai/zh/model-credits/
  - AA Intelligence Index：https://artificialanalysis.ai/api/v2/language/models/free（免费 Key）

Usage:
    python scripts/fetch_plans.py                  # 抓取 + 写 plans.json（不生成 HTML）
    python scripts/fetch_plans.py --generate       # 顺带重建 opendesign-pareto.html
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARCHIVE_DIR = Path(__file__).resolve().parent
ROOT = ARCHIVE_DIR.parent  # 仍要读仓库根的 data/ 与 .env
OUT_PATH = ARCHIVE_DIR / "plans.json"
AA_LOCAL_PATH = ROOT / "data" / "snapshots" / "aa-scores.json"
ICONS_PATH = ROOT / "data" / "registry" / "icons.json"
GENERATOR = ARCHIVE_DIR / "generate_plan_pareto.py"

OC_MODELS_URL = "https://opencode.ai/inference/go/v1/models"
OD_MODELS_URL = "https://go.open-design.ai/zh/models/"
OD_PRICING_URL = "https://open-design.ai/zh/pricing/"
AA_API_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
USER_AGENT = "opencode-go-model-pareto/1.0 (+https://opencode.ai/v2/docs/console/go/)"
AA_MAX_PAGES = 8

# 套餐价格手写在下面：两个定价页的套餐卡片是客户端渲染，抓取成本高于收益，改价时直接改这里。
# OpenCode: https://opencode.ai/v2/docs/console/go/ 的 Plan 表（Go $10 / Go Plus $40 / Go Teams $20/人）
# OpenDesign: https://open-design.ai/zh/pricing/ 的套餐卡（Go $10 / Plus $20 / Pro $100 / Max $200）
PLANS: dict[str, dict] = {
    "oc-go": {
        "vendor": "opencode",
        "name": "OpenCode Go",
        "price": 10.0,
        "monthly_credit": None,
        "coding_pool": None,
        "flagship_pool": 0.0,
        "windows": {
            "five_hour": "月额度的 20%",
            "seven_day": "月额度的 50%（周一 00:00 UTC 重置）",
            "month": "随付费周期重置",
        },
        "source": "https://opencode.ai/v2/docs/console/go/",
        "note": "单一额度池；DeepSeek V4.1 Flash / V4 Pro 在工作日 UTC 01–04、06–10 按 2× 计价。",
    },
    "od-go": {
        "vendor": "opendesign",
        "name": "OpenDesign Go",
        "price": 10.0,
        "monthly_credit": 220.0,
        "coding_pool": 220.0,
        "flagship_pool": 0.0,
        "windows": {
            "five_hour": "$18（仅 Coding Plan 额度）",
            "seven_day": "$55（订阅时刻起每 7 天重置）",
            "month": "$220（不结转）",
        },
        "source": "https://open-design.ai/zh/pricing/",
        "note": "不含旗舰模型额度；可用 Codex / Claude Code / OpenCode / Hermes 等走 API Key。",
    },
    "od-pro": {
        "vendor": "opendesign",
        "name": "OpenDesign Go Pro",
        "price": 100.0,
        "monthly_credit": 780.0,
        "coding_pool": 660.0,
        "flagship_pool": 120.0,
        "windows": {
            "five_hour": "无此窗口",
            "seven_day": "$90（仅 Coding Plan 额度）",
            "month": "Coding Plan 赠送 $660 + 旗舰模型 $120",
        },
        "source": "https://open-design.ai/zh/pricing/",
        "note": "两池顺序消耗：先 Coding Plan，用尽后按实际用量扣旗舰模型额度。",
    },
}

# 两家都明说不做 ZDR / 会用你的 prompt 训练的模型（写进 tooltip 提醒）
TRAINS_ON_DATA = {"Muse Spark 1.3", "Muse Spark 1.2 Contributor", "Muse Ember"}

# 模型名 → AA slug。AA 免费档只覆盖当前主力代，其余走本地 aa-scores.json 的最后已知分（标 stale）。
AA_SLUG: dict[str, str] = {
    "DeepSeek V4.1 Flash": "deepseek-v4-1-flash-1017",
    "DeepSeek V4.1 Flash 1017": "deepseek-v4-1-flash-1017",
    "DeepSeek V4 Pro": "deepseek-v4-pro",
    "DeepSeek V4 Flash": "deepseek-v4-flash",
    "DeepSeek V4 Flash Vision Exp": "deepseek-v4-flash-vision",
    "GLM-5.3": "glm-5-3",
    "GLM-5.2": "glm-5-2",
    "GLM-5.3 Flash": "glm-5-3-flash",
    "GLM-5.3-Flash": "glm-5-3-flash",
    "Kimi K3": "kimi-k3",
    "Kimi K3.1 Pro": "kimi-k3-1-pro",
    "Kimi K2.7 Code": "kimi-k2-7-code",
    "MiMo-V2.6-Flash": "mimo-v2-6-flash",
    "MiMo V2.6 Flash": "mimo-v2-6-flash",
    "MiMo-V2.6-Pro": "mimo-v2-6-pro",
    "MiMo V2.6 Pro": "mimo-v2-6-pro",
    "MiniMax-M3": "minimax-m3",
    "MiniMax M3.1 Flash": "minimax-m3-1-flash",
    "Hy3": "hy3",
    "Hy4": "tencent-hy4",
    "Qwen3.8 Max": "qwen3-8-max",
    "Qwen3.8 Flash": "qwen3-8-flash-next",
    "Qwen3.7 Plus": "qwen3-7-plus",
    "Qwen4 Max": "qwen4-max",
    "Grok 4.7": "grok-4-7",
    "GPT-6 Luna": "gpt-6-luna",
    "GPT-5.6 Luna": "gpt-5-6-luna",
    "GPT-6 Sol": "gpt-6-sol",
    "GPT-6.1 Sol": "gpt-6-1-sol",
    "GPT-6 Astra": "gpt-6-astra",
    "Claude Haiku 5.5": "claude-haiku-5-5",
    "Claude Sonnet 5.5": "claude-sonnet-5-5",
    "Claude Opus 5.5": "claude-opus-5-5",
    "Claude Fable 5.1": "claude-fable-5-1",
    "Gemini 3.8 Flash": "gemini-3-8-flash",
    "Gemini 4 Argon": "gemini-4-argon",
    "Muse Spark 1.3": "muse-spark-1-3",
    "Muse Spark 1.3 Contributor": "muse-spark-1-3",
    "Muse Spark 1.2 Contributor": "muse-spark-1-2",
    "Muse Ember": "muse-ember",
    "LongCat-2.0": "longcat-2-0",
    "Step 5 Preview": "step-5",
    # 免费档 → 无额度，不参与帕累托
    "Beam Free": "",
    "Iceman Contributor Free": "",
    "LongCat 2.5 Free": "",
    "Hy4 flash Free": "",
}

# 品牌 → icons.json 的 key（缺失时用 unknown）
BRAND_PREFIX = [
    ("deepseek", "deepseek"),
    ("glm", "zhipu"),
    ("mimo", "xiaomimimo"),
    ("minimax", "minimax"),
    ("kimi", "kimi"),
    ("qwen", "qwen"),
    ("grok", "grok"),
    ("gpt", "openai"),
    ("claude", "claude"),
    ("gemini", "gemini"),
    ("muse", "muse"),
    ("hy", "hunyuan"),
    ("hunyuan", "hunyuan"),
    ("longcat", "longcat"),
    ("step", "step"),
]

# 同一模型在两家的名字不同（用于跨套餐对照表）
CANON_ALIAS = {
    "deepseekv41flash1017": "deepseek-v4.1-flash",
    "deepseekv41flash": "deepseek-v4.1-flash",
    "glm53flash": "glm-5.3-flash",
    "mimov26flash": "mimo-v2.6-flash",
    "mimov26pro": "mimo-v2.6-pro",
    "hy4preview": "hy4-preview",
    "kimi27code": "kimi-k2.7-code",
}


def fetch(url: str, retries: int = 5, timeout: int = 45, headers: dict[str, str] | None = None) -> str:
    import random

    base = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Cache-Control": "no-cache",
    }
    if headers:
        base.update(headers)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers=base), timeout=timeout) as resp:
                payload = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as err:
            last = err
            status = getattr(err, "code", None)
            if status == 404 or attempt + 1 >= retries:
                break
            time.sleep(min(30, 2 ** attempt + random.uniform(0, 1)))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def _brand_for(name: str) -> str:
    low = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for prefix, brand in BRAND_PREFIX:
        if low.startswith(prefix):
            return brand
    return "unknown"


def _modality_from_oc(model: dict) -> str:
    inputs = ((model.get("modalities") or {}).get("input")) or []
    return "多模态" if any(str(i).startswith("image") for i in inputs) else "纯文字"


def canon(name: str) -> str:
    key = name.lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
    key = re.sub(r"(1017|0902)$", "", key)
    return CANON_ALIAS.get(key, key)


# --------------------------------------------------------------------------- OpenCode
def fetch_opencode() -> list[dict]:
    doc = json.loads(fetch(OC_MODELS_URL))
    rows = []
    for m in doc.get("data", []):
        cost = ((m.get("cost") or {}).get("text")) or {}
        price_in = ((cost.get("input") or {}).get("price"))
        price_out = ((cost.get("output") or {}).get("price"))
        cache = ((cost.get("input") or {}).get("cache") or {}).get("read")
        usage = ((m.get("go") or {}).get("monthly_usage") or {})
        allowance = usage.get("go")
        # 价格倍数：只有按 utc 星期/小时限定的是「工作日高峰加价」；
        # 按 date 限定的是限期促销（如 Gemini 4 Argon 折扣到 11/30），单独标出来
        peak_factor = None
        promo = None
        for mult in ((m.get("cost") or {}).get("multipliers") or []):
            when = mult.get("when") or {}
            factor = float(mult.get("factor") or 1)
            if "utc_days" in when or "utc_hours" in when:
                peak_factor = max(peak_factor or 1.0, factor)
            elif "date" in when:
                promo = {"factor": factor, "from": (when.get("date") or {}).get("gte")}
        go_meta = m.get("go") or {}
        name = m.get("name") or m.get("id")
        rows.append(
            {
                "vendor": "opencode",
                "name": name,
                "canon": canon(name.split(" 1017")[0]),
                "brand": _brand_for(name),
                "modality": _modality_from_oc(m),
                "list_in": price_in,
                "list_out": price_out,
                "list_cache": cache,
                "allowance": allowance,
                "allowance_assumed": False,
                "peak_factor": peak_factor,
                "promo": promo,
                "trains_on_data": bool(go_meta.get("training")),
                "no_zdr": go_meta.get("zdr") is False,
                "context": (m.get("limit") or {}).get("context"),
                "note": None if allowance else "免费档，无月度额度",
                "aa_slug": AA_SLUG.get(name, AA_SLUG.get(name.replace(" 1017", ""), "")),
            }
        )
    return rows


# --------------------------------------------------------------------------- OpenDesign
def fetch_od_prices() -> dict[str, dict]:
    """从 go.open-design.ai/zh/models/ 的 model-explorer 面板取列表价（取第一档）。"""
    page = fetch(OD_MODELS_URL)
    blocks = re.split(r'<div class="model-explorer-panel" ', page)[1:]
    prices: dict[str, dict] = {}
    for block in blocks:
        slug = re.search(r'data-model-panel="([^"]*)"', block)
        name = re.search(r"<h3>(.*?)</h3>", block, re.S)
        if not slug or not name:
            continue
        tier = re.search(r"<h4>.*?</h4>(.*?)(?=<h4>|</div></div>)", block, re.S)
        pairs = re.findall(r"<dt>(.*?)</dt><dd[^>]*>(.*?)</dd>", tier.group(1) if tier else block, re.S)
        values = {html_mod.unescape(a).strip(): html_mod.unescape(b).strip() for a, b in pairs}

        def num(label: str):
            raw = values.get(label, "")
            m = re.search(r"\$([0-9.]+)", raw)
            return float(m.group(1)) if m else None

        prices[html_mod.unescape(name.group(1)).strip()] = {
            "slug": slug.group(1),
            "list_in": num("输入 / 百万 Token"),
            "list_out": num("输出 / 百万 Token"),
            "list_cache": num("缓存读取 / 百万 Token"),
        }
    return prices


def fetch_od_allowances() -> dict[str, dict[str, float]]:
    """从定价页的 Coding Plan 使用额度表取每个模型的 7 天请求数与每月额度 $。"""
    page = fetch(OD_PRICING_URL)
    parts = page.split('<div class="dpu-row"')[1:]
    out: dict[str, dict[str, float]] = {}
    drop = {"DeepSeek V4.1 Flash Eco", "DeepSeek V4.1 Flash 限时谷价"}
    for row in parts:
        name = re.search(r"<strong[^>]*>(.*?)</strong>", row)
        if not name:
            continue
        label = html_mod.unescape(name.group(1)).strip()
        if label in drop or "限时谷价" in label:
            continue
        dollars = re.findall(r'data-usage-value="(go|plus|pro|max)"[^>]*>\s*\$([0-9,]+)', row)
        if not dollars:
            continue
        out[label] = {k: float(v.replace(",", "")) for k, v in dollars}
    return out


def fetch_od_multipliers() -> dict[str, float]:
    page = fetch("https://open-design.ai/zh/model-credits/")
    table = re.search(r"额度扣除倍率(.*?)(?:</section>|<h2)", page, re.S)
    seg = table.group(1) if table else page
    pairs = re.findall(r"<td>([^<]*[A-Za-z][^<]*)</td>\s*<td>([0-9.]+\s*×)</td>", seg)
    return {html_mod.unescape(a).strip(): float(b.replace("×", "")) for a, b in pairs}


def fetch_od_plan_lists() -> dict[str, dict[str, list[str]]]:
    """定价页每个套餐卡片里两块模型清单：
      design-plan-model-list-<plan>  → Coding Plan 池
      plan-model-list-<plan>         → 旗舰模型池（unavailable 标记的表示该档不支持）
    Go 的旗舰清单整块标 unavailable，等价于「不含旗舰额度」。"""
    page = fetch(OD_PRICING_URL)
    out: dict[str, dict[str, list[str]]] = {}
    for plan in ("go", "plus", "pro", "max"):
        bucket: dict[str, list[str]] = {"coding": [], "flagship": []}
        for kind, pool in (("design-plan", "coding"), ("plan", "flagship")):
            start = page.find(f'id="{kind}-model-list-{plan}"')
            if start < 0:
                continue
            nxt = page.find("<ul id=", start + 10)
            seg = page[start: nxt if nxt > 0 else start + 9000]
            for attr, body in re.findall(r"<li([^>]*)>(.*?)</li>", seg, re.S):
                if "unavailable" in attr:
                    continue
                label = html_mod.unescape(re.sub(r"<[^>]+>", " ", body))
                label = re.sub(r"\s+", " ", label).strip()
                label = re.sub(r"\s*(限时谷价|暂不支持)$", "", label).strip()
                if label and label not in bucket[pool]:
                    bucket[pool].append(label)
        out[plan] = bucket
    return out


def build_od_rows(
    prices: dict,
    allowances: dict,
    multipliers: dict,
    plan_lists: dict[str, dict[str, list[str]]],
    plan_id: str,
    aa_price: dict[str, dict] | None = None,
) -> list[dict]:
    """只收官方在套餐卡片上明示包含的模型，不猜：
      - coding 池：模型在 design-plan 清单里 → 额度取「Coding Plan 使用额度」表的本档 $ 值
      - flagship 池：模型在旗舰清单里 → 额度取该档旗舰池总额（Go Pro $120），池内共享
    Go 的旗舰清单为空，所以 Go 只剩 coding 池模型。"""
    aa_price = aa_price or {}
    key = plan_id.split("-", 1)[1]  # go / pro
    bucket = plan_lists.get(key) or {"coding": [], "flagship": []}
    rows = []
    for name in bucket["coding"] + bucket["flagship"]:
        if name in ("GPT Image 2", "Nano Banana 2", "Seedream 5 Lite / Pro"):
            continue  # 图像模型不进编码帕累托
        if name == "DeepSeek V4.1 Flash Eco":
            continue  # 同模型降速档，官方未公布独立列表价，单列脚注而非编造价格
        price = dict(prices.get(name) or {})
        if price.get("list_in") is None:
            # 目录未收该模型（如 MiniMax M3.1 Flash 只出现在额度表里），
            # 退到 AA 收录的厂商公开列表价，并标明价格来源
            fallback = aa_price.get(name)
            if not fallback:
                continue
            price.update(fallback)
            price["fallback"] = True
        pool = "coding" if name in bucket["coding"] else "flagship"
        allowance = (allowances.get(name) or {}).get(key) if pool == "coding" else PLANS[plan_id]["flagship_pool"]
        if not allowance:
            continue
        rows.append(
            {
                "vendor": "opendesign",
                "name": name,
                "canon": canon(name),
                "brand": _brand_for(name),
                "modality": "未知",
                "list_in": price.get("list_in"),
                "list_out": price.get("list_out"),
                "list_cache": price.get("list_cache"),
                "list_price_source": "aa" if price.get("fallback") else "opendesign",
                "allowance": allowance,
                "allowance_assumed": False,
                "peak_factor": None,
                "promo": None,
                "trains_on_data": name in TRAINS_ON_DATA,
                "no_zdr": name in TRAINS_ON_DATA,
                "multiplier": multipliers.get(name),
                "pool": pool,
                "note": "旗舰模型额度池内共享，不是该模型独占额度" if pool == "flagship" else None,
                "aa_slug": AA_SLUG.get(name, ""),
            }
        )
    return rows


# --------------------------------------------------------------------------- AA
def fetch_aa() -> tuple[dict[str, float], int, dict[str, dict]]:
    api_key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip()
    if not api_key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("ARTIFICIAL_ANALYSIS_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
    if not api_key:
        raise RuntimeError("缺 ARTIFICIAL_ANALYSIS_API_KEY（.env 或环境变量）")
    scores: dict[str, float] = {}
    prices: dict[str, dict] = {}
    version = 0
    page = 1
    while page <= AA_MAX_PAGES:
        doc = json.loads(fetch(f"{AA_API_URL}?page={page}", headers={"x-api-key": api_key}))
        version = doc.get("intelligence_index_version") or version
        for item in doc.get("data") or []:
            slug = item.get("slug")
            intel = ((item.get("evaluations") or {}).get("artificial_analysis_intelligence_index"))
            if not slug:
                continue
            if intel is not None:
                scores[slug] = float(intel)
            pack = item.get("pricing") or {}
            if pack.get("price_1m_input_tokens") is not None and pack.get("price_1m_output_tokens") is not None:
                prices[slug] = {
                    "list_in": float(pack["price_1m_input_tokens"]),
                    "list_out": float(pack["price_1m_output_tokens"]),
                    "list_cache": pack.get("price_1m_cache_hit_tokens"),
                }
        if not (doc.get("pagination") or {}).get("has_more"):
            break
        page += 1
    if len(scores) < 20:
        raise RuntimeError(f"AA 免费档只返回 {len(scores)} 个分值，疑似接口异常，本次不更新")
    return scores, version, prices


def local_stale() -> dict[str, dict]:
    """本地 aa-scores.json 最新快照里带 stale 的分值（免费档已停供）。"""
    try:
        doc = json.loads(AA_LOCAL_PATH.read_text(encoding="utf-8"))
        snaps = doc.get("snapshots") or {}
        latest = snaps[max(snaps)]
    except Exception:
        return {}
    out = {}
    for row in latest.get("models", []):
        slug = row.get("aa_model_id")
        if slug and row.get("intelligence") is not None:
            out[slug] = {"intelligence": float(row["intelligence"]), "asof": row.get("asof")}
    return out


def attach_scores(rows: list[dict], fresh: dict[str, float], stale: dict[str, dict], today: str) -> None:
    for row in rows:
        slug = row.get("aa_slug") or ""
        if not slug:
            row.update({"aa_score": None, "aa_stale": False, "aa_asof": None})
            continue
        if slug in fresh:
            row.update({"aa_score": fresh[slug], "aa_stale": False, "aa_asof": today})
        elif slug in stale:
            row.update({"aa_score": stale[slug]["intelligence"], "aa_stale": True, "aa_asof": stale[slug]["asof"]})
        else:
            row.update({"aa_score": None, "aa_stale": False, "aa_asof": None})


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true", help="写完 archive/plans.json 后顺便重建 HTML")
    args = parser.parse_args()

    today = date.today().isoformat()
    print("→ OpenCode 模型与额度")
    oc_rows = fetch_opencode()
    print("→ OpenDesign 列表价")
    od_prices = fetch_od_prices()
    print("→ OpenDesign 各模型额度")
    allowances = fetch_od_allowances()
    print("→ OpenDesign 扣除倍率")
    multipliers = fetch_od_multipliers()
    print("→ OpenDesign 各套餐模型清单")
    plan_lists = fetch_od_plan_lists()
    print("→ AA Intelligence Index + 厂商公开列表价")
    fresh, version, aa_lists = fetch_aa()
    aa_price = {name: aa_lists[slug] for name, slug in AA_SLUG.items() if slug in aa_lists}
    stale = local_stale()
    od_go = build_od_rows(od_prices, allowances, multipliers, plan_lists, "od-go", aa_price)
    od_pro = build_od_rows(od_prices, allowances, multipliers, plan_lists, "od-pro", aa_price)

    attach_scores(oc_rows, fresh, stale, today)
    attach_scores(od_go, fresh, stale, today)
    attach_scores(od_pro, fresh, stale, today)

    icons = set(json.loads(ICONS_PATH.read_text(encoding="utf-8"))) if ICONS_PATH.exists() else set()
    for row in oc_rows + od_go + od_pro:
        if row["brand"] not in icons:
            row["brand"] = "unknown"

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asof": today,
        "aa_index_version": version,
        "blend": "3 份输入 : 1 份输出（编码 agent 典型比例）",
        "plans": PLANS,
        "rows": {"oc-go": oc_rows, "od-go": od_go, "od-pro": od_pro},
        "sources": {
            "oc_models": OC_MODELS_URL,
            "od_models": OD_MODELS_URL,
            "od_pricing": OD_PRICING_URL,
            "od_credits": "https://open-design.ai/zh/model-credits/",
            "aa": AA_API_URL,
        },
    }
    atomic_write(OUT_PATH, payload)
    scored = {k: sum(1 for r in v if r.get("aa_score") is not None) for k, v in payload["rows"].items()}
    print(f"written {OUT_PATH}  models={ {k: len(v) for k, v in payload['rows'].items()} } scored={scored}")

    if args.generate:
        subprocess.run([sys.executable, str(GENERATOR)], check=True)


if __name__ == "__main__":
    main()
