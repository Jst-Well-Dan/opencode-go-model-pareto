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
- **OpenCode Go 配额**：https://opencode.ai/v2/docs/console/go/
- **AA 智力评分**：https://artificialanalysis.ai/api/v2/data/llms/models（官方免费 Key，需 `ARTIFICIAL_ANALYSIS_API_KEY`）
- **LMArena Agent 分**：https://datasets-server.huggingface.co/rows?dataset=lmarena-ai/leaderboard-dataset&config=agent
- **AIHOT 评分（综合榜）**：https://aihot.news/leaderboard（第三方评测加权：编程 38%、科研 19%、专业办公 19%、知识问答 14%、真人盲选 10%。官方暂无榜单 API，本项目解析服务端渲染表格，只展示前 30 名内的分数；个人非商业引用，页脚保留回链）
- **模态与图标**：https://artificialanalysis.ai/models/<slug>（`Input modality` → `Supports: text / text and image`）及 https://artificialanalysis.ai/img/logos/<slug>_small.svg（如 `LongCat-2.0` 已替换为官方绿猫 Logo）

常用参数：
```bash
python scripts/fetch_data.py --output-dir test   # 输出到 test/，不覆盖正式文件
python scripts/fetch_data.py --no-generate       # 仅更新 JSON 数据，不渲染 HTML
python scripts/generate_card.py                  # 一次出齐：AA/LMArena/AIHOT 三张 + GOAT + 对比图
python scripts/generate_card.py --metric aihot   # 只出指定口径的 OC 卡片
python scripts/generate_card.py --keep-html      # 保留中间生成的 HTML 供调试
python scripts/generate_card.py --no-image       # 仅生成 HTML，不截图 PNG
python scripts/generate.py --domain pinned --output compare-pinned.html      # 固定基准对照版
python scripts/generate.py --domain adaptive --output compare-adaptive.html  # 每日自适应对照版
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
- **AA 免费档停供分值**：AA 官方免费 Key 自 2026-10-28 之后只覆盖各家当前主力代（约 90/720 个模型带 `intelligence index`），上一代模型不再返回分值。`update_documents()` 不再因“已知模型缺分”整体抛错，而是保留最后已知分值并写入 `stale: true` + `asof`（最后确认日期），前端以灰虚线圈展示、悬浮提示标明「AA 免费档已停供该模型，最后已知分值截至 …」，且**不参与帕累托计算**（新旧口径不可混算）。只有当接口返回的分值数量低于 `AA_MIN_SCORED_MODELS`（默认 20）时才视为接口异常、中止本次更新。
- **AIHOT 榜只收前 30 名**：`update_aihot_doc()` 对未上榜模型沿用最后已知分（`stale: true` + `asof`，≤60 天），超出或从未上榜则记 `null`（横轴虚线头像 + 缺失面板）；模型名与榜单 slug 的差异在 `data/registry/aihot-alias.json` 里配，缺省走归一化匹配（忽略大小写/连字符，自动去掉 `0902`、`1017` 这类检查点后缀）。解析行数低于 10 行视为页面改版，直接中止本次 AIHOT 更新而不写坏数据。
- **历史快照缺失对齐**：以最新快照为基准，结合历史快照并集（`generate_html.py` 自动追加历史独有模型）决定模型全集；当切换到新模型尚未上线的历史日期时，缺失模型自动以 `null`/`absent` 对齐过滤，不会在历史图表中误渲染。已下线模型（如 `Ox Alpha Free` / `Grok 4.5`）仅从最新快照移除，历史快照仍保留，AA 侧保留最后已知分数不报错。
- **去重快照**：`fetch_data.py:update_documents()` 在 `quota` 与 `AA` 均与最新快照完全一致（含 `AA_SLUG_ALIAS` 回填）时跳过新建当日快照，避免 `2026-08-23/24` 这类重复提交；新增模型或分数变动则仍正常落盘。
- **促销行解析**：配额表出现 `<del>旧值</del> 新值`（如 `4x` 活动改价）时丢弃删除线旧值、取现行值；模型格的 `<small>` 活动说明（如 `4x · 9 月 20 日结束`）记入行 `note` 字段（模型名保持归一不断历史），悬浮提示中以「备注」展示。
- **横轴方案对照**：`generate.py --domain` 支持 `legacy`（默认，最新日最大配额为 ref、左端固定 1）、`pinned`（`normalization_reference` 为永久锚＋全日期统一定义域，新极端不搬动老点）、`adaptive`（逐日独立 ref 与定义域，主聚类永远铺满但拨时间轴时坐标会跳）。
- **新模型模态**：`MODEL_META` 缺失时不再兜底为 `unknown/纯文字`，而是默认抓取官网 `https://artificialanalysis.ai/models/<slug>` 的 `Input modality`（`Supports: text and image` → 多模态）自动判定，`brand` 按前缀推断；失败才抛错提示手填。

### 3. 时间轴与交互
- 顶部支持滑块拖动（`◀ 拖动 ▶`）与下拉菜单双控，支持历史配额与 AA 分数快照回溯。
- 智力指标三档切换：`AA智力`（AA Intelligence Index）/ `LMArena`（Agent 榜真人盲选分）/ `AIHOT`（AIHOT 综合评分，附评测覆盖率与榜内名次）；每档独立纵轴、独立缺失判定，帕累托前沿按当前口径重算。
- 支持线性刻度与对数刻度（Log Scale）一键平滑切换。

### 4. 社交分享卡片
- `scripts/generate_card.py` 基于 Playwright 渲染 1080px 竖版卡片并对 `#capture` 元素精准裁切，**无需手工截图**。
- 默认输出到 `cards/<最新快照日期>/`（`cards/` 已 gitignore），一次生成 5 张：
  `opencode-card-<date>-{aa,lm,aihot}.png`、`goat-card-<date>.png`、`comparison-card-<date>.png`。
- OC 卡片三档口径与网页同构：`--metric aa|lm|aihot|all`（默认 all），每档独立纵轴与刻度
  （AA 步长自适应、Agent 分为 `±0.0xx` 带符号、AIHOT 分 0.5 级），副标题、坐标轴、页脚来源随口径切换。
- **停更分不进前沿**：`stale: true` 的模型只画灰色虚线圈（页脚注明停更数量与最后更新日期），
  不参与帕累托计算——卡片结论与页面完全一致，不会再出现「冻结旧分虚高上榜」。
- 该口径无分数的模型不画在图里，改为图下 `无 AA 分 / 无 Agent 分 / 无 AIHOT 分（N）：…` 一行说明。
- 精选卡片按帕累托前沿取前 3（不足 3 个时自动收缩栅格列数，不留空位）。

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
- **GitHub Actions**：每天 09:00 CST 定时触发 `.github/workflows/daily.yml`（`nick-fields/retry@v3` 重试 3 次），拉取最新配额与评分；无 Pro Key 依赖，纯镜像+官网 HTML 抓取，若数据无变化则去重跳过。抓取失败时作业仍保持成功（`continue-on-error` + 丢弃半成品改动），只在日志里留 `::warning::`，**不会触发 Actions 失败通知邮件**。
- **忽略规则**：`cards/` 为本地产物不提交。
- **技术栈**：
  - 数据与生成：Python 3.13（标准库 `HTMLParser` 解析 + `Playwright` 截图）
  - 前端：单文件原生 HTML5 + 内联 SVG + Base64 图标，零外部第三方 CDN 依赖。
