# lessons/ — 错题本

| 路径 | 谁能写 | 内容 |
|---|---|---|
| `inbox/<机器>.jsonl` | **只有那台机器自己**（`tools/lesson_add.py`、`tools/review_scan.py --to-lessons`） | append-only 的错题候选，一行一条 JSON；同 hash 不重复写 |
| `index.json` | 只有 A 机（`tools/lesson_ingest.py`） | 合并去重后的正式错题本：同因合并、`count` = 不同来源出现次数、`force` = 强制方式 |
| `../LESSONS.md` 的 AUTO 区 | 只有 A 机（同上） | 人读的那张表；标记 `<!-- AUTO-INGEST:BEGIN/END -->` 之间的内容由工具维护 |

字段：`ts machine source severity title detail where evidence status hash`

**不许把测试数据当真错题**：自测 planted-defect、演练、demo 产生的 findings 必须用
`lesson_ingest.py --synthetic <该文件>` 摄入 —— 它们会留档（`status=synthetic`）但
**不计复发、不升门禁、不出现在未关闭计数里**。同理：任何机器都不要伪造 `inbox/<别的字母>.jsonl`，
也不要手改 `index.json`（复发数必须是真事）。

- `source`：`audit`（审查 agent 的 findings）｜`gate`（merge_check 挡单）｜`review_scan`（旧件复检）｜
  `self`（自己踩的）｜`buyer`（买方退的）
- `where`：`file:line` 或件号——**同因合并就靠它**，写准一点，别写"某个地方"
- `status`：`open`｜`partial`｜`fixed`｜`wontfix`

规则（代码强制，见 `lesson_ingest.py`）：`count ≥ 2`、`P0/P1`、或审计"部分通过" ⇒ 强制方式自动变
**「必须做进门禁」**；每次摄入 `LESSONS_ACK` 轮换 ⇒ 各机必须重跑 `tools/recall.py`。
