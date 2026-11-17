#!/usr/bin/env python3
"""生成 archive/opendesign-pareto.html：OpenCode Go / OpenDesign Go / Go Pro 的
有效单价 × 智力 帕累托对比单页。

归档状态（2026-11-17）：本文件已从 scripts/ 搬入 archive/，**不再参与每日任务**。
数据来自 archive/plans.json（由 archive/fetch_plans.py 抓取），
图标仍复用 data/registry/icons.json，模板为 archive/plan-pareto.template.html。

Usage:
    python archive/generate_plan_pareto.py
    python archive/generate_plan_pareto.py --output /tmp/x.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ARCHIVE_DIR = Path(__file__).resolve().parent
ROOT = ARCHIVE_DIR.parent  # 图标仍从仓库根的 registry 读
TEMPLATE = ARCHIVE_DIR / "plan-pareto.template.html"
PLANS_PATH = ARCHIVE_DIR / "plans.json"
ICONS_PATH = ROOT / "data" / "registry" / "icons.json"
OUTPUT = ARCHIVE_DIR / "opendesign-pareto.html"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    args = parser.parse_args()

    for path, name in ((args.template, "template"), (PLANS_PATH, "plans.json"), (ICONS_PATH, "icons")):
        if not path.exists():
            raise SystemExit(f"缺文件 {path}（{name}）—— 先跑 python archive/fetch_plans.py")

    template = args.template.read_text(encoding="utf-8")
    plans = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
    icons = json.loads(ICONS_PATH.read_text(encoding="utf-8"))

    used = {row["brand"] for rows in plans["rows"].values() for row in rows}
    used.add("unknown")
    payload = json.dumps(plans, ensure_ascii=False, separators=(",", ":"))
    icon_payload = json.dumps({k: v for k, v in icons.items() if k in used}, ensure_ascii=False, separators=(",", ":"))
    missing = used - set(icons)
    if missing:
        print(f"警告：icons.json 缺 {sorted(missing)}，这些模型会回落到 unknown 图标")

    html = template.replace("__DATA__", payload).replace("__ICONS__", icon_payload)
    args.output.write_text(html, encoding="utf-8")
    size = args.output.stat().st_size / 1024
    counts = {k: len(v) for k, v in plans["rows"].items()}
    print(f"written {args.output}  {size:.0f} KB  models={counts}")


if __name__ == "__main__":
    main()
