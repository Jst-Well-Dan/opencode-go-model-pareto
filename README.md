# OpenCode Go 模型帕累托图

> 基于 OpenCode Go 配额与 AA Intelligence Index 的「智力 × 成本」帕累托最优分析。单文件离线 HTML，配额以**最多者 = 1.0** 为基准，左上为理想方向。

### 🌐 在线查看

**https://jst-well-dan.github.io/opencode-go-model-pareto/opencode-go-model-pareto.html**

无需克隆，直接打开即可切换日期（时间轴滑块 / 下拉）、切换对数/线性刻度，所有数据与图标已内联，完全支持离线可用。

---

## 目录结构

```
.
├── opencode-go-model-pareto.html        # 成品主图（唯一位于根目录的单文件 HTML）
├── data/
│   ├── quota-snapshots.json             # 按日期的配额快照数据
│   └── aa-scores.json                   # AA 智力评分数据
├── template/
│   └── opencode-go-model-pareto.template.html  # 主图模板（含图标与交互布局）
└── scripts/
    ├── fetch_data.py                    # 抓取官方数据并校验生成
    ├── generate_html.py                 # 渲染主图 HTML
    └── generate_card.py                 # 渲染分享卡片（本地使用）
```

---

## 快速开始

### 1. 自动抓取与生成（推荐）

```bash
# 抓取最新配额 + AA 评分，校验后更新 data/ 并自动重建主图
python scripts/fetch_data.py

# 仅重新生成主图（不发起网络请求抓取）
python scripts/generate_html.py

# 生成 1080px 社交媒体分享卡片（直接产出 cards/ 目录下的 PNG）
python scripts/generate_card.py
```

数据来源：
- **OpenCode Go 配额**：https://opencode.ai/docs/zh-cn/go/
- **AA 智力评分**：https://aihot.virxact.com/leaderboard/methodology

> 抓取结果写入 `data/quota-snapshots.json` 与 `data/aa-scores.json`。若遇到网络失败、页面结构变动或校验不通过，**不会覆盖原 JSON**。

常用参数：
```bash
python scripts/fetch_data.py --output-dir test   # 输出到 test/，不覆盖正式文件
python scripts/fetch_data.py --no-generate       # 仅更新 JSON 数据，不渲染 HTML
python scripts/generate_card.py --keep-html      # 保留中间生成的 HTML 供调试
python scripts/generate_card.py --no-image       # 仅生成 HTML，不截图 PNG
```

### 2. 手动维护与新增模型

1. **更新快照**：在 `data/quota-snapshots.json` 新增或修改日期快照（每个模型配置 `requests_per_5h` / `requests_per_week` / `requests_per_month`）。
2. **更新评分**：在 `data/aa-scores.json` 修改或补充对应模型的 `intelligence`。
3. **补充元信息**：若引入了**全新模型**，需在 `scripts/generate_html.py` 的 `MODEL_META` 中配置其 `brand`（品牌图标）与 `modality`（`多模态` / `纯文字`）。
4. **重新渲染**：执行 `python scripts/generate_html.py`。

---

## 核心设计与算法机制

### 1. 配额基准与相对成本
- **相对配额成本**：$\text{相对成本} = \frac{\text{基准配额}}{\text{模型自身配额}}$。
- **基准设定**：基准动态取**最新快照中的最大配额值**（当前为 `Muse Spark 1.2 Contributor` 的 45,300 次/5h）。因此最慷慨的模型相对成本恒为 `1.0`。
- **动态横轴 $x_{\max}$**：横轴最大值动态计算为 $\frac{\text{基准配额}}{\text{最小配额}}$（当前为 411.8），确保所有低配额模型完全落入图表可视区域内，避免固定刻度导致越界。

### 2. 容错机制与异常值处理
- **空配额/免费档**：线上配额为 `-` 的模型（如免费档 `Ox Alpha Free`）标记为 `cost: null`，不参与帕累托计算，在横轴最左端以虚线头像锚定展示（`免费 · 不限`）。
- **缺分模型**：有配额但暂无 AA 评测分的模型（如 `DeepSeek V4 Flash Vision Exp`）以虚线头像标在其相对成本位置，并在缺失信息面板中以等宽卡片展示。
- **历史快照缺失对齐**：最新快照决定模型全集。当切换到新模型尚未上线的历史日期时，缺失模型自动以 `null` 对齐过滤，不会在历史图表中误渲染。

### 3. 时间轴与交互
- 顶部支持滑块拖动（`◀ 拖动 ▶`）与下拉菜单双控，支持 90+ 天历史快照回溯。
- 支持线性刻度与对数刻度（Log Scale）一键平滑切换。

### 4. 社交分享卡片
- `scripts/generate_card.py` 基于 Playwright 渲染 1080px 竖版渐变卡片并精准裁切。
- 自动精选最多 3 个帕累托最优模型卡片，同步绘制横轴虚线参考点。

---

## 数据结构规范

- **`data/quota-snapshots.json`**
  ```json
  {
    "snapshots": {
      "2026-08-23": {
        "label": "今日",
        "models": [
          {
            "name": "GLM-5.3",
            "requests_per_5h": 220,
            "requests_per_week": 2640,
            "requests_per_month": 10560
          }
        ]
      }
    }
  }
  ```
- **`data/aa-scores.json`**
  ```json
  {
    "models": [
      {
        "name": "GLM-5.3",
        "aa_model_id": "glm-5-3",
        "intelligence": 59.5
      }
    ]
  }
  ```

---

## 自动化与部署

- **GitHub Pages**：静态单文件托管（`main / (root)`）。
- **GitHub Actions**：每天 09:00 CST 定时触发 `.github/workflows/daily.yml`，拉取最新配额与评分；若数据有更新则自动提交并部署。
- **技术栈**：
  - 数据与生成：Python 3.13（标准库 `HTMLParser` 解析 + `Playwright` 截图）
  - 前端：单文件原生 HTML5 + 内联 SVG + Base64 图标，零外部第三方 CDN 依赖。
