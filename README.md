# OpenCode Go 模型帕累托图

> 基于 OpenCode Go 配额与 AA Intelligence Index 的「智力 × 成本」帕累托最优分析。单文件离线 HTML，配额以**最多者 = 1.0** 为基准，左上为理想方向。

### 🌐 在线查看

**https://jst-well-dan.github.io/opencode-go-model-pareto/opencode-go-model-pareto.html**

无需克隆，直接打开即可切换日期（时间轴滑块 / 下拉）、切换对数/线性刻度，所有数据与图标已内联，离线可用。

**当前快照：2026-08-23 · 22 模型（20 可绘制）· 3 帕累托最优**

`Muse Spark 1.2 Contributor` (成本 1.00 / 智力 56.8) · `GLM-5.3` (205.9 / 59.5) · `Kimi K3` (411.8 / 59.7)

---

## 目录结构

> **根目录仅保留 `opencode-go-model-pareto.html` 成品**，其余按职责分文件夹。

```
.
├── opencode-go-model-pareto.html        # 成品主图（唯一位于根目录的文件）
├── data/
│   ├── quota-snapshots.json             # 按日期的配额快照
│   └── aa-scores.json                   # AA 智力评分
├── template/
│   └── opencode-go-model-pareto.template.html  # 主图模板（含图标与布局）
├── scripts/
│   ├── fetch_data.py                    # 抓取官方数据并校验生成
│   ├── generate_html.py                 # 渲染主图 HTML
│   └── generate_card.py                 # 渲染分享卡片（本地使用，不提交）
├── cards/                               # 本地 1080px 分享卡片（已 gitignore）
└── docs/README.md                       # 详细文档
```

---

## 快速开始

```bash
# 1. 抓取最新配额 + AA 评分，校验后更新 data/ 并重建主图
python scripts/fetch_data.py

# 2. 仅生成主图（不抓取）
python scripts/generate_html.py

# 3. 生成分享卡片（本地使用，不提交仓库）
python scripts/generate_card.py
#  → cards/opencode-go-model-pareto-card-2026-08-*-cropped.png
#  → cards/opencode-go-model-pareto-card-cropped.png (通用=最新)
```

数据来源：

- OpenCode Go 配额：https://opencode.ai/docs/zh-cn/go/
- AA 评分：https://aihot.virxact.com/leaderboard/methodology

抓取结果写入 `data/quota-snapshots.json` 与 `data/aa-scores.json`。网络失败、页面结构变化或校验失败时**不会覆盖原 JSON**。

常用参数：

```bash
python scripts/fetch_data.py --output-dir test   # 输出到 test/，不覆盖正式文件
python scripts/fetch_data.py --no-generate       # 仅更新 JSON
python scripts/generate_card.py --keep-html      # 保留中间 HTML 供调试
```

`cards/` 已加入 `.gitignore`，仅本地生成用于发社交媒体，不提交到仓库。

---

## 核心设计

- **配额基准**：`相对成本 = 基准 / 配额`，基准取最新快照中最大值（当前 `Muse Spark 1.2 Contributor` 45300 / 5h）
- **时间轴**：顶部滑块 `◀ 拖动 ▶` + 下拉双控，支持 90+ 天历史回溯，按月分组不再拥挤
- **空配额/缺分容错**：`Ox Alpha Free` 等免费档以虚线头像锚定在横轴最左端；`DeepSeek V4 Flash Vision Exp` 等有配额无 AA 分的模型标在其成本位置
- **每日自动更新**：GitHub Actions 每天 09:00 CST 拉取并提交，无变化则跳过

详细说明见 `docs/README.md`。

---

## 部署

本仓库已启用 GitHub Pages（`main / (root)`）：

- 仓库：https://github.com/Jst-Well-Dan/opencode-go-model-pareto
- Actions：每天 09:00 CST 自动更新，也可手动 `Run workflow`

本地 Actions 配置见 `.github/workflows/daily.yml`。
