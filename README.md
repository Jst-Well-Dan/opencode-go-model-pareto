# OpenCode Go 模型帕累托图

> 基于 OpenCode Go 配额与 AA Intelligence Index 的「智力 × 成本」帕累托最优分析。单文件离线 HTML，配额以**最多者 = 1.0** 为基准，左上为理想方向。

### 🌐 在线查看

**https://jst-well-dan.github.io/opencode-go-model-pareto/**


无需克隆，直接打开即可切换日期（时间轴滑块 / 下拉）、切换对数/线性刻度，所有数据与图标已内联，完全支持离线可用。

---

## 目录结构

```
.
├── index.html                           # 成品主图（GitHub Pages 入口，仅 OpenCode Go 单页）
├── goat-compare-archive.html            # 存档冻结页（Command GOAT + 对比，已停止更新）
├── archive/                             # 归档的 GOAT 相关代码（备用，不参与每日任务）
├── data/
│   ├── snapshots/                       # 时序快照（quota/aa；goat 已冻结不再新增）
│   ├── registry/                        # 注册表（model-meta/icons/slug-alias/crosswalk/curated）
│   └── cache/                           # 缓存（aa-modality-cache，可重建）
├── template/
│   └── opencode-go-model-pareto.template.html  # 主图模板（含图标与交互布局）
└── scripts/
    ├── fetch_data.py                    # 抓取 OpenCode 配额 + AA 评分并校验生成（仅 OC）
    ├── generate.py                      # 渲染主图 HTML（OC 单页生成器，产出 index.html + 别名）
    ├── generate_card.py                 # 渲染今天的分享卡片（本地使用）
    └── generate_rank_change.py          # 生成独立的 AA 前后排名 HTML 卡片
```

---

## 快速开始

### 1. 自动抓取与生成（推荐）

```bash
# 抓取最新配额 + AA 评分，校验后更新 data/ 并自动重建主图
python scripts/fetch_data.py

# 仅重新生成主图（不发起网络请求抓取）
python scripts/generate.py

# 生成今天的 1080px OpenCode 社交媒体分享卡片
python scripts/generate_card.py

# 单独生成 AA 评分标准变化前后的 OpenCode 排名 HTML 卡片
python scripts/generate_rank_change.py
```

数据来源：
- **OpenCode Go 配额**：https://opencode.ai/docs/zh-cn/go/
- **AA 智力评分**：https://aihot.virxact.com/leaderboard/methodology（镜像，免 Key；官网 `api/v2/data/llms/models` 为 Pro 付费已不再尝试）
- **模态与图标**：https://artificialanalysis.ai/models/<slug>（`Input modality` → `Supports: text / text and image`）及 https://artificialanalysis.ai/img/logos/<slug>_small.svg（如 `LongCat-2.0` 已替换为官方绿猫 Logo）

常用参数：
```bash
python scripts/fetch_data.py --output-dir test   # 输出到 test/，不覆盖正式文件
python scripts/fetch_data.py --no-generate       # 仅更新 JSON 数据，不渲染 HTML
python scripts/generate_card.py --keep-html      # 保留中间生成的 HTML 供调试
python scripts/generate_card.py --no-image       # 仅生成 HTML，不截图 PNG
```

### 2. 手动维护与新增模型

1. **更新快照**：在 `data/snapshots/quota-snapshots.json` 新增或修改日期快照（每个模型配置 `requests_per_5h` / `requests_per_week` / `requests_per_month`）。
2. **更新评分**：在 `data/snapshots/aa-scores.json` 对应日期快照中修改或补充模型的 `intelligence`；时间轴会同步显示各日期的评分标准。
3. **补充元信息**：若引入了**全新模型**，建议在 `data/registry/model-meta.json` 中配置其 `brand` 与 `modality`（`scripts/generate.py` 严格 require，缺失将抛错）。未配置时由 `scripts/fetch_data.py` 自动抓取官网 `https://artificialanalysis.ai/models/<slug>` 的 `Input modality` 自动判定（`image` → 多模态），`brand` 按模型名前缀启发式推断并自动写入 `data/registry/icons.json`；`aa_model_id` 通过 `data/registry/slug-alias.json` + `_slug_for_model()` 自动映射。
4. **重新渲染**：执行 `python scripts/generate.py`。

---

## 核心设计与算法机制

### 1. 配额基准与相对成本
- **相对配额成本**：$\text{相对成本} = \frac{\text{基准配额}}{\text{模型自身配额}}$。
- **基准设定**：基准动态取**最新快照中的最大配额值**（当前为 `Muse Spark 1.2 Contributor` 的 45,300 次/5h）。因此最慷慨的模型相对成本恒为 `1.0`。
- **动态横轴 $x_{\max}$**：横轴最大值动态计算为 $\frac{\text{基准配额}}{\text{最小配额}}$（当前为 411.8），确保所有低配额模型完全落入图表可视区域内，避免固定刻度导致越界。
- **动态纵轴 $y_{\min}/y_{\max}$**：纵轴按当前日期快照中的 `intelligence` 分布动态计算：`y_min = max(0, floor(min-2))`、`y_max = ceil(max+2)`，跨度 `<12` 时各向外扩 `4`，刻度以日期快照注入模板，适配评分标准切换。

### 2. 容错机制与异常值处理
- **空配额/免费档**：线上配额为 `-` 的模型（如免费档 `Ox Alpha Free`）标记为 `cost: null`，不参与帕累托计算，在横轴最左端以虚线头像锚定展示（`免费 · 不限`）。
- **缺分模型**：有配额但暂无 AA 评测分的模型以虚线头像标在其相对成本位置，并在缺失信息面板中以等宽卡片展示。
- **历史快照缺失对齐**：以最新快照为基准，结合历史快照并集（`generate_html.py` 自动追加历史独有模型）决定模型全集；当切换到新模型尚未上线的历史日期时，缺失模型自动以 `null`/`absent` 对齐过滤，不会在历史图表中误渲染。已下线模型（如 `Ox Alpha Free` / `Grok 4.5`）仅从最新快照移除，历史快照仍保留，AA 侧保留最后已知分数不报错。
- **去重快照**：`fetch_data.py:update_documents()` 在 `quota` 与 `AA` 均与最新快照完全一致（含 `AA_SLUG_ALIAS` 回填）时跳过新建当日快照，避免 `2026-08-23/24` 这类重复提交；新增模型或分数变动则仍正常落盘。
- **新模型模态**：`MODEL_META` 缺失时不再兜底为 `unknown/纯文字`，而是默认抓取官网 `https://artificialanalysis.ai/models/<slug>` 的 `Input modality`（`Supports: text and image` → 多模态）自动判定，`brand` 按前缀推断；失败才抛错提示手填。

### 3. 时间轴与交互
- 顶部支持滑块拖动（`◀ 拖动 ▶`）与下拉菜单双控，支持历史配额与 AA 分数快照回溯。
- 支持线性刻度与对数刻度（Log Scale）一键平滑切换。

### 4. 社交分享卡片
- `scripts/generate_card.py` 基于 Playwright 渲染 1080px 竖版渐变卡片并精准裁切。
- 自动精选最多 3 个帕累托最优模型卡片，同步绘制横轴虚线参考点。

---

## 数据结构规范

- **`data/quota-snapshots.json`**（按日期快照，最新为 `2026-08-27`，含 `LongCat-2.0` / `Grok 4.6` / `GLM-5.3-Flash` 等 23 模型；`normalization_reference` 记录当前基准 `Muse Spark 1.2 Contributor 45300/5h`）
  ```json
  {
    "normalization_reference": { "model": "Muse Spark 1.2 Contributor", "requests_per_5h": 45300 },
    "snapshots": {
      "2026-08-27": {
        "label": "今日",
        "models": [
          { "model": "GLM-5.3", "requests_per_5h": 220, "requests_per_week": 540, "requests_per_month": 1080 }
        ]
      }
    }
  }
  ```
- **`data/snapshots/aa-scores.json`**（官方 Data API 的 `artificial_analysis_intelligence_index`，按日期保留快照）
  ```json
  {
    "source_url": "https://artificialanalysis.ai/api/v2/data/llms/models",
    "snapshots": {
      "2026-09-05": {
        "models": [
          { "model": "GLM-5.3", "aa_model_id": "glm-5-3", "intelligence": 48.6 }
        ]
      }
    }
  }
  ```
  > 字段以 `model` 为主键；旧版扁平 `models` 结构已迁移为日期快照。

---

## 自动化与部署

- **GitHub Pages**：静态单文件托管（`main / (root)`）。
- **GitHub Actions**：每天 09:00 CST 定时触发 `.github/workflows/daily.yml`（`nick-fields/retry@v3` 重试 3 次），拉取最新配额与评分；无 Pro Key 依赖，纯镜像+官网 HTML 抓取，若数据无变化则去重跳过。
- **忽略规则**：`cards/` 为本地产物不提交。
- **技术栈**：
  - 数据与生成：Python 3.13（标准库 `HTMLParser` 解析 + `Playwright` 截图）
  - 前端：单文件原生 HTML5 + 内联 SVG + Base64 图标，零外部第三方 CDN 依赖。
