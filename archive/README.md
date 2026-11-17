# archive — 已归档代码（不参与每日任务）

本目录放**已冻结、不再自动更新**的一次性产物与对应脚本。每日任务
（`.github/workflows/daily.yml`）只跑 `scripts/fetch_data.py` 维护仓库根目录的
`index.html`（OpenCode Go 单页），不会碰这里的任何文件。

## 内容

### GOAT（2026-09-08 起停更）

2026-09-08 起，每日自动任务只更新 OpenCode Go，不再抓取 Command GOAT；
`index.html` 仅保留 OpenCode Go 单页。GOAT 帕累托与 OC×GOAT 对比已合并冻结为
仓库根目录的 `goat-compare-archive.html`（数据截至 2026-09-08，不再更新）。

- `generate.py.3tab-backup`：拆分前 `scripts/generate.py` 的完整备份
  （OpenCode Go + Command GOAT + 代表模型对比三页合一版本，
  含 AA 优先于 GOAT 表分的口径修正）。
- `goat_fetch.py`：从 `scripts/fetch_data.py` 原样搬出的 GOAT 抓取链路
 （`parse_goat_quotas` + `update_goat_documents` + 主流程 GOAT 段）。
  共享 helper 仍从 `scripts/fetch_data.py` 导入，需在仓库根目录运行：
  ```bash
  python archive/goat_fetch.py
  python archive/goat_fetch.py --date 2026-09-08 --output-dir test
  ```

### 其他冻结页面

- `aa-rank-change.html`：AA 评分标准变化前后的 OpenCode 排名卡片。
- `compare-pinned.html` / `compare-adaptive.html`：横轴方案的对照产物
  （仍可用 `scripts/generate.py --domain pinned|adaptive` 重新生成到任意位置）。

### 套餐对比页（2026-11-17 起停更）

`opendesign-pareto.html`：**OpenDesign Go / Go Pro 与 OpenCode Go 的
「有效单价 × 智力」帕累托对比**。2026-11-17 生成后从每日任务移出并归档。

页面结论（下次刷新前仍然成立的方向性判断）：

- 两个 $10 档：最便宜是 OpenDesign 的 `GLM-5.3 Flash`（$0.0198/Mtok），
  但天花板是 OpenCode 的 `Gemini 4 Argon 50.4`；OpenDesign Go 不含旗舰模型，
  天花板锁在 `DeepSeek V4.1 Flash 32.6`。
- Go Pro（$100）：前沿 6 点，`Claude Haiku 5.5` 因走 $120 旗舰池而只算
  $0.167/Mtok 拿 36.5 分，是整张图最反常的点；`Claude Opus 5.5 59.1` 顶到最高，
  `GPT-6 Astra 56.9` 却贵到 $16.67 且被它完全支配。

配套文件：

| 文件 | 作用 |
|---|---|
| `fetch_plans.py` | 抓 OpenCode `/inference/go/v1/models`、OpenDesign 目录价/定价页额度表/倍率/各套餐模型清单、AA 分值 → `archive/plans.json` |
| `generate_plan_pareto.py` | 用 `archive/plans.json` + `plan-pareto.template.html` 渲染页面 |
| `plan-pareto.template.html` | 页面模板（自带数据/图标占位符） |
| `plans.json` | 数据快照（32 / 10 / 25 个模型行，与页面同批生成） |

手动刷新（图标仍从仓库根 `data/registry/icons.json` 读）：

```bash
python archive/fetch_plans.py              # 只更新 archive/plans.json
python archive/fetch_plans.py --generate   # 更新数据 + 重建 archive/opendesign-pareto.html
python archive/generate_plan_pareto.py     # 只用现有 plans.json 重建 HTML
```

口径提醒：横轴是**有效 $/百万 token**（`(3×输入+1×输出)/4 × 月费 ÷ 该模型月额度`），
与主图 `index.html` 的「相对配额成本」不是同一个量，两页横轴不可直接比。
套餐价格与窗口规则硬编码在 `fetch_plans.py` 的 `PLANS` 字典，改价需手动同步。

## 恢复指引

- 完整历史（含拆分前代码）始终在 git 中：GOAT 拆分前最后一个提交为 `ab21c6d`，
  可用 `git show ab21c6d:scripts/generate.py` 查看任一历史版本。
- `data/registry/crosswalk.json` 与 `curated-defs.json` 仍保留在原位，
  因为本地脚本 `scripts/generate_card.py`（goat/cmp 卡片）还在使用它们。
- `data/snapshots/goat-snapshots.json` 保留在原位但已冻结（每日任务不再写入）。
- 套餐对比页若需恢复每日更新：把本目录的 `fetch_plans.py` / `generate_plan_pareto.py` /
  `plan-pareto.template.html` 搬回根目录，`plans.json` 搬到 `data/snapshots/`，
  并在 `daily.yml` 里加回 `python scripts/fetch_plans.py --generate` 一步
  （路径常量在文件头部，改 `ARCHIVE_DIR` 那几行即可）。
