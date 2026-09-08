# archive — 已归档的 GOAT 相关代码（备用）

2026-09-08 起，每日自动任务只更新 OpenCode Go，不再抓取 Command GOAT；
`index.html` 仅保留 OpenCode Go 单页。GOAT 帕累托与 OC×GOAT 对比已合并冻结为
仓库根目录的 `goat-compare-archive.html`（数据截至 2026-09-08，不再更新）。

## 内容

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

## 恢复指引

- 完整历史（含拆分前代码）始终在 git 中：拆分前最后一个提交为 `ab21c6d`，
  可用 `git show ab21c6d:scripts/generate.py` 查看任一历史版本。
- `data/registry/crosswalk.json` 与 `curated-defs.json` 仍保留在原位，
  因为本地脚本 `scripts/generate_card.py`（goat/cmp 卡片）还在使用它们。
- `data/snapshots/goat-snapshots.json` 保留在原位但已冻结（每日任务不再写入）。
