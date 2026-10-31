# 工作流程约定（五机协作 · 2026-10-31 起生效）

适用范围：机器 A–E 的全部 CAD 生产与这个仓的每一次提交。冲突的场合以本文件为准；
本文件与 `LESSONS.md` 冲突时，以 `LESSONS.md` 更新的那条为准（教训优先于流程惯性）。

## 1. 角色

| 角色 | 谁 | 只做什么 |
|---|---|---|
| 签发方 / 收货方 | A 机的**协调会话** | 清单与合同、版本握手、`merge_check` 复核、底表合并、教训与记忆维护、门禁代码 |
| 生产线 | 各机的**生产会话**（A 机是另一个会话） | 只做自己前缀的件；只写自己拥有的路径 |
| 裁决 | 用户 | 清单优先级、判重争议、是否放行例外 |

## 2. 路径所有权（谁能写什么）

| 路径 | 拥有者 | 备注 |
|---|---|---|
| `catalog/` `intake/` `WORK-ORDER.md` `WORKFLOW.md` `LESSONS.md` `MEMORY.md` `tools/` `shared/versions.json` `shared/fingerprints/`（合并动作） | A 协调会话 | 别人要改就在 `outbox/` 里放 `PROPOSAL-*.md` |
| `outbox/fleet_<x>_2026_11/` | 机器 X | 别人只读 |
| `shared/fingerprints/<X>.json` | 机器 X | 一人一文件，互不覆盖 |
| `status/PROGRESS.md` | 所有人 | **只准末尾追加**；改别人那行 = 冲突 |
| `<cadRoot>/`（含 `ledger.sqlite3`） | 该机器自己 | 不进 git |

git 流：单机单分支 `main`，**push 前必须 `git pull --rebase`**；提交信息首行
`deliver(fleet_<x>_2026_11): n/100` 或 `plan(wave k): …`，第二行写 `rules_sha=<…>`。

## 3. 单件流程（顺序固定，任一 FAIL 不许进入下一步）

```
recall → new → design.md（先数到目标步数）→ build → validate → probe → dedupe → export → package → 回传
```

- `recall`（新增，强制）：`python3 tools/recall.py --machine <X>`，打印教训 + 记忆 + 版本握手 + 你的切片 + 未修缺陷。
- `design.md` 是"非逆向生成"的证据（2.4 真实可辨识 + 加分项设计说明），不是事后补的说明文。
- `package` **串行、一次一件**（N43/N44 未修期间的硬约束）。
- 回传物齐备才允许 push：`delivery/<批次>/`、`ledger.jsonl`、`fingerprints.delta.json`、`versions.json`、
  `REPORT.md`（**必须含 `LESSONS_ACK: <sha8>`**）、豁免文件。自己先跑 `merge_check`，`ACCEPT` 才 push。

## 4. 批次与波次（200,000 件口径）

规模：**500 个族谱 × 400 成员 = 200,000 件**；每台机器 100 族谱 / 40,000 件 / **20 波**，
每波 5 个族谱 = 2,000 件。件号 `(机器)_(族谱)_(序号)` 由 `tools/emit_members.py` 确定性重算，
**每波开工前先展开该波 intake**（这就是合同，交付要逐件对上）：

```sh
python3 tools/emit_members.py --machine B --wave 1 > intake/waves/fleet_b_2026_11_w1.json
```

在这个规模上，人肉/会话式生产撑不住，波次门槛之外还有三条硬前提（WORKFLOW 的规模化部分）：

1. **无人值守批处理**：pilot 实测 3–6 分钟/件、≈1.95 MB/件。200,000 件 ≈ 555 机日、≈390 GB。
   每台机器要能连续跑（`entry.py` 循环即可，不要经会话），断点靠台账幂等键续跑。
2. **判重必须分桶**：`merge_check`/`fingerprint_merge` 已按 (solids, faces, edges) 分桶
   （`exact`/`scaled_same` 都要求面边数相同，所以分桶无损）；桶内仍按出厂判定函数比。
   任何人新增判重路径，不许把它退回全量两两比较。
3. **底表会先长到十万级**：每台每波后 `fingerprint_merge` 增量并入，`registry_sync` 合成时
   只看自己已交付的部分；底表文件按机器分片不变。

开下一波的前提不是"上一波做完了"，而是：

1. 上一波（2,000 件）回传全部 `ACCEPT`；
2. A 机已把上一波指纹并进 `shared/fingerprints/<X>.json` 并 push（否则下一波看不到这些几何已被占用）；
3. `catalog_lint` 与 `registry_sync --check-only` 都 OK。

违反这条就是在下一波里做重复件 —— 白干且不退文件。

## 5. 收货（A 机的动作，每台每波一次）

```sh
cd handoff && git pull --rebase
python3 tools/merge_check.py --mine cad --theirs outbox/fleet_b_2026_11 --baseline shared/fingerprints
python3 tools/catalog_lint.py
python3 tools/fingerprint_merge.py --machine B      # 增量并进 shared/fingerprints/B.json
```

抽件复建（防"台账说过了、盘上不是那回事"）：随机抽 ≥10% 的件，
用交付的原生文件重建并与 STEP 比体积（≤1e-4）与外形（≤1e-3）。

## 6. 异常与升级

| 情况 | 生产会话该做 | 不许做 |
|---|---|---|
| `dedupe` 报 `exact`/`scaled_same` | 停下，把两边 pid 与指纹贴进 `outbox/…/PROPOSAL-*.md` | 自己判断"其实不一样"继续做 |
| 探针某参数修不过 | 停下回报（附参数名与扰动值） | 把该参数从 params 表删掉蒙过去 |
| 内存需要降到 1536 以下 | 停下回报 | 抬 `memCapMb` 硬闯 |
| 想改清单/合同 | 停下，等 A 机重签发 | 直接改 `catalog/` 或 `intake/` |
| 插件行为与工单不符 | 停下，把命令与原始报错贴进 `REPORT.md` | 绕过插件手工出图/出 STEP |

## 6.5 审查/门禁报告的使用约定

门禁**一次列出全部缺陷**（Round-10 之后修过一条：早退只报第一个错，导致"修一个再看一个"）。
生产会话必须把整份 blocking 清单清零再重跑，不许逐条试。

## 7. 教训闭环（本条是流程的心脏）

1. 每轮审计/收货暴露的问题，**当天**写进 `LESSONS.md`（带编号 + 强制方式：门禁 / 自检）。
2. `MEMORY.md` 的"现状"段随之更新，决策只追加不改写。
3. 下次生产由 `recall.py` 强制读取；回传带 `LESSONS_ACK`，`merge_check` 核对 sha。
4. 同一类问题第二次出现 → 必须升级为门禁（写代码挡），不许第三条"下次注意"。

## 8. 完成的定义

- **一件**：五阶段 OK + 交付目录齐 + `merge_check` ACCEPT + 指纹入底表。
- **一波**：20 件全部达到"一件"标准，且抽件复建通过。
- **一台**：批次 `reconcile` 无缺件/无多余/无重复 id，`counts_as_unique` 与买方判定差值有解释。
- **项目**：五台批次合并后总 unique 计数达到目标，且没有任何一对 `exact`/`scaled_same` 跨机重复。
