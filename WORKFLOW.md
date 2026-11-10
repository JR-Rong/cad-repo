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
- `package` 在 N43 门禁落地后可以 `--package-workers` 并行（每件一把锁）。读失败不得删除交付目录。细节见 `RUNBOOK.md`。
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

## 5.5 派发（开场指令）

派发一律现生成：`python3 tools/kickoff.py --machine <字母> --wave <当前波次> --write`，
把 `briefs/KICKOFF-<字母>.txt` 全文贴进那台机器的会话。理由：开场白带著 `rules_sha`、
`LESSONS_ACK` 和本波族谱，手写版本必然与仓库不一致（B 机第一次开工就是被这个坑住的）。
长版规则 `briefs/BRIEF-FOR-<字母>.txt` 仍然有效（全 20 波清单 + 规则全文）。

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

## 7. 错题本闭环（本条是流程的心脏）

发现问题只有四个出口，全都汇到 `lessons/inbox/<机器>.jsonl`：

| 来源 | 谁产生 | 怎么进错题本 |
|---|---|---|
| `audit` | 本机审查 agent（codex 类对抗审查）的 findings JSON | A 机 `python3 tools/lesson_ingest.py --audit cad/audit/findings_round11_*.json` |
| `gate` | 收货门禁 `merge_check` 的 blocking | A 机 `--gate /tmp/mc.json`（先 `merge_check … --json > /tmp/mc.json`） |
| `review_scan` | 旧件机械复检通道 | `python3 tools/review_scan.py --root <cadRoot> --batch <旧批次> --to-lessons` |
| `self` / `buyer` | 人踩的坑、买方退单 | `python3 tools/lesson_add.py --machine X --source self --severity P1 --title "症状" --where "file:line 或件号" --detail "证据"` |

**所有权**：各机只写 `lessons/inbox/<自己>.jsonl`（append-only，永不冲突）；
`LESSONS.md` 与 `lessons/index.json` **只有 A 机的 `lesson_ingest.py` 能写**。

三条硬规则写在代码里，不是写在文档里：
1. **同因合并** —— key = (`where`, 标题前 120 字) 的哈希，五台撞同一个坑只有一条教训；
2. **复发即升级** —— `复发` 只算**不同来源**再犯（同一份文件重放不计数）；`count ≥ 2`、`P0/P1`、
   或审计里标"部分通过" ⇒ 强制方式自动变 **「必须做进门禁」**，A 机必须把它变成 merge_check 的
   blocking 或插件校验，不许留成"下次注意"；
3. **ACK 轮换** —— 每次摄入都改 `LESSONS.md` ⇒ `LESSONS_ACK` 变 ⇒ 各机 REPORT.md 里的旧 ACK 失效，
   必须重跑 `recall.py` 重读才能再交付。这是设计，不是副作用。

**数据卫生（第四条，也是踩过之后加的）**：测试/演练产生的发现必须
`lesson_ingest.py --synthetic <文件>` 摄入 —— 留档为 `status=synthetic`，**不计复发、不升门禁、
不计入未关闭数**；任何机器不得伪造别机的 `lessons/inbox/<字母>.jsonl`，不得手改 `index.json`。
错题本里的"复发"必须是真发生过两次的事，否则升级就是编出来的。

实测（本机跑过）：摄入 round-10 审计（真）+ 自测挡单（标 synthetic）⇒ 22 条中真实 15 条
（11 条已升级为「必须做进门禁」）、留档 7 条、复发计数最大为 1；同一份文件重放三次不再计数。

## 8. 完成的定义

- **一件**：五阶段 OK + 交付目录齐 + `merge_check` ACCEPT + 指纹入底表。
- **一波**：20 件全部达到"一件"标准，且抽件复建通过。
- **一台**：批次 `reconcile` 无缺件/无多余/无重复 id，`counts_as_unique` 与买方判定差值有解释。
- **项目**：五台批次合并后总 unique 计数达到目标，且没有任何一对 `exact`/`scaled_same` 跨机重复。

## 9. 并行编排：新件生产与旧件审查同时跑

**为什么能并行**：五台机器天然不相交（族谱按机器切、`shared/fingerprints/<X>.json` 各写各的）；
单机内 `build/validate/probe/export` 是互相无共享状态的独立进程；旧件复检只读交付目录，很轻。

**为什么不能一味拉满**（三条约束已写进调度器）：
1. 单件峰值 RSS 430–530 MB，插件每进程 cap 2048 MB、**进程组 cap 6144 MB** ⇒
   并发 = `min(--workers, --mem-budget-mb / --per-part-mb)`，默认取 3 路；
2. **N43/N44 已是门禁**：`package` 按件加锁，`--package-workers` 默认 2，仍受内存预算约束。
   探针记录追加，后到的 PASS 不会抹掉先到的 FAIL；同一修订的台账 FAIL 同样粘滞。
3. `--package` 必须与 `build,validate,probe,export` 同时给出，否则调度器直接拒绝 ——
   跳过阶段直接包装等于用调度器绕过 §3 的顺序约束（这条是调度器自己踩出来的，见错题本）。

```
python3 tools/fleet_runner.py --root <cadRoot> --batch fleet_b_2026_11 \
    --intake intake/waves/fleet_b_2026_11_w1.json \
    --stages build,validate,probe,export --package \
    --workers 3 --mem-budget-mb 6144 \
    --review-batch fleet_b_2026_11 --review-root <上一波 cadRoot> --review-every 6 --review-sample 20
```

- **调用契约**：`freecadcmd <插件>/python/entry.py` + `CADKIT_ACTION/CADKIT_ROOT/CADKIT_PROJECT/CADKIT_OPTS`
  环境变量（`entry.py` 顶部 docstring 里那种 `--action` 命令行写法会被 FreeCAD 吃掉，别照抄）；
- **判定只看 JSON**：引擎失败时**退出码仍是 0**，只有 `CADKIT>> {"ok": …, "checks": […]}` 是真的，
  调度器因此解析 JSON 并把 `checks[]` 里任何 FAIL 记为不过（曾把 8 条"通过"记在零交付物上）；
- **握手前置自检**：启动时用即将执行的引擎算一次 `rules_sha` 与 `shared/versions.json` 比对，
  不一致直接 exit 2 —— 用错引擎生产的那一波整波返工；
- **断点续跑**看 `projects/<id>/build/chain_evidence.json` 里重新读过的哈希。
  台账 `job=OK` 单独不算跳过（2026-10-31 回填的 172 件必须重验）；
- **复检采样**：每完成 `--review-every` 件插一次旧件 `review_scan`（发现即写自己的 inbox）；
  全量复检留给每班一次，别和生产抢窗口；
- 日志 `status/runner_<batch>_<ts>.jsonl` 每行 `{lane,pid,stage,ok,seconds,degraded}`，
  随 REPORT 回传；A 机看 `lane` 是否交错判断"真并行"。

**禁止并行**：`package`（全局单槽）、同一件重复跑、`git push`（`pull --rebase` 后立刻 push）、
以及 `ledger.sqlite3`（不可手工合并、不可两个进程写同一 project）。
