# 生产运行手册（一张图纸 → 一整族零件）

这里的「流水线」是 CAD 生产链，不是 CI。一张图纸在本仓库里是**族谱核**：一个 Python 模块，描述该族在五条结构轴上的做法。`emit_members.py` 把族谱展开成 400 个件号（每件一条不同的拓扑签名，不是整模缩放）。每件的 `build.py` 只负责加载**已冻结**的核并调用 `build(b, proj)`。后面的顺序固定：

```
recall → scaffold（project.yml / design.md / build.py）
      → build → validate → probe → dedupe → export → package
      → 验收报告（outbox/<批次>/）
```

唯一入口是 `tools/fleet_runner.py`。不要再跑仓外的 `cad-b-prep/driver.py`。不要给 `freecadcmd` 传 `--action`（它会把参数吃掉）；调度器走 `CADKIT_ACTION` 等环境变量。引擎失败时退出码经常仍是 0，通过与否只看 JSON 里的 `ok`，以及本手册的验收门，不看台账里历史的 `job=OK`。

FreeCAD：`/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`（1.1.1）。  
B 线 cadRoot：`~/cad-fleet`。A 线 cadRoot：`default-workspace/cad`。两条线不要用同一个进程、同一个台账。

## 0. 开工前（每台机器、每次改引擎之后）

```sh
cd /path/to/cad-repo
git pull --rebase
python3 tools/recall.py --machine B
```

`LESSONS_ACK` 以这次 recall 打印的为准。引擎源码变了，`rules_sha` 会变；`shared/versions.json` 里的值必须等于仓库里这份引擎。对不上时 `fleet_runner` 直接退出 2。不要拿 `releases/` 里的旧 0.5.0 包覆盖 `plugin/dsh-plugin-cad-freecad`。

冻结族谱核（核现在只在 Mac 上，仓库里默认没有这些 `.py`）：

```sh
python3 tools/family_freeze.py freeze --machine B --src ~/cad-fleet/family
# 编辑 family_src/B/binding.json，例如
#   "in01": {"module": "enclosure_core.py"}
python3 tools/family_freeze.py check --machine B --live ~/cad-fleet/family
```

`check` 不是 0 就不要开跑。没有 MANIFEST 时调度器会警告并允许跑，但这一波**不能 ACCEPT**（报告里核的状态是 UNFROZEN）。

把波次合同摊成项目目录（已有 `build.py` 的件默认不覆盖；要重写绑定用 `--force`，且只对你确定要重绑的族用）：

```sh
python3 tools/scaffold_wave.py --machine B --root ~/cad-fleet \
    --intake intake/waves/fleet_b_2026_11_w1.json
```

`design.md` 里必须把 `topo_signature` 写成具体特征数（有哪些筋、孔、槽）。只改尺寸档不算新设计。

## 1. B 线 wave 1（in01–in05，2000 件）

先看调度器会排什么。历史台账里那 172 条 `job=OK` **不会被跳过**：没有 `build/chain_evidence.json`、且哈希对得上当前 FCStd / STEP / 族核的件，一律重跑。

```sh
python3 tools/registry_sync.py --root ~/cad-fleet --machine B
# 非 0 就停。底表形状不对会直接退出，不再静默丢掉缺 volume 的条目。

python3 tools/fleet_runner.py --root ~/cad-fleet --batch fleet_b_2026_11 \
    --intake intake/waves/fleet_b_2026_11_w1.json \
    --stages build,validate,probe,dedupe,export --package \
    --package-workers 1 \
    --workers 3 --mem-budget-mb 6144 --per-part-mb 600 \
    --machine B --freecad-bin /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
```

`--package-workers` 默认 **1**。开跑时本进程会用 `fcntl.flock` 独占 `delivery/<批次>/.publisher.lock` 直到退出。0.5.0 包里的 `ledger.py` 对同一个文件用的也是 `fcntl.flock`（不是 `lockf` / `fcntl.fcntl` 字节锁）。这两种锁在 Linux 上互不排斥，但仓库里能看到的旧引擎用的是 flock，所以占住 flock 就能挡住它。`driver.py` 不在本仓、也不在 0.5.0 包里，它的锁没办法在这里核对。如果进来时这把锁已经被占着，直接退出。在第 6 节的试验通过之前，不要把 `--package-workers` 加大。读交付目录时只有「文件确实不存在」才当作没有更新的修订；第一次放入用 `renameat2(RENAME_NOREPLACE)` / macOS `renamex_np(RENAME_EXCL)`。

包装会读 `projects/<件号>/review/receipts.json` 交给打包器，并把这份文件的 sha256 算进 package 的 input hash。文件不在，或回执不完整，记 **PENDING_REVIEW**，不是设计 FAIL，也不计入「一族 3 次设计 FAIL 就停族」。补上合格回执后同一件可以再跑。没有 `review_status=complete` 仍然不能 accepted。

每次实跑会在日志里写一行 `lane=run_header`（`first_line_of_run: true`）。这是**这一次运行的第一行**，不是整个日志文件的第一行（日志是追加的）。行里有本次开头的台账备份 sha256，以及链锚（事件条数和最后一条 `row_hash`）。跑完再写 `lane=run_footer`，同样带结束 sha256 和链锚，并写到两个固定文件里，不写进按分钟命名的日志名：

- `<cadRoot>/ledger.end.sha` — 上次 sqlite `.backup` 的 sha256。下次开跑拿**当前**备份和这个文件比。日志名带分钟，换一分钟就是另一个文件，从日志里读会永远读到空，等于没比。
- `<cadRoot>/ledger.chain.anchor` — 链头锚，在台账文件旁边，仍在这台机器上。内容是 `{event_count, head_event_id, head_hash}`。每次开跑核对「第 event_count 条事件的 `row_hash` 仍然是 head_hash」。**文件不在，不是第一次运行**：只要 event 里已经有行，或者链上已经有 `event_hash_backfill` 这条不能删的事件，缺文件就是 `chain_anchor_mismatch`。删掉这个文件再把整条链重算一遍，`verify_event_chain` 仍会通过，但开跑会告警。
- `status/ledger_anchor.json` — 同一份链头，写在**仓库**里，不在 cadRoot。字段是 `event_count`、`head_event_id`、`head_hash`、`ledger_end_sha`、`timestamp`。跑完会写这份工作区文件，**不会**自动提交。生产前必须单独推上去（见下面的命令）。下次开跑如果 `HEAD` 里已经有这份文件，会拿它再对一次链；对不上同样是 `chain_anchor_mismatch`。仓库里还没有这份文件时跳过这一次，不把「还没提交」当成篡改。

**上 Mac 后先做一次：** 同一份没有改过的台账，连续跑两次。两次的 sqlite `.backup` sha 必须相同。不相同的话，`ledger_sha_mismatch` 会误报（备份字节不稳定），先查这个，不要当成有人改了库。

新的空 cadRoot 第一次开跑也会记下 `chain_anchor_mismatch`：回填事件先落在链上，锚文件要等这次跑完才写出来。确认日志里的链头和刚写好的两个锚文件一致之后，用带原因的确认把这一条清掉，然后执行下面的推送。下一次运行起，缺锚或链被重算都会再次告警。

对不上时追加一条告警，**不会**在下次对上时自动清成 0。`schema_tampered`、`ledger_sha_mismatch`、`chain_anchor_mismatch`、`orphan_authorization` 都写在 `ledger_alarm`（只插入）并各有一条链上的 event。要清掉，必须写一条带原因的确认，而且确认行自己也要挂在这条 event 上。直接往 `ledger_alarm` 里插一行 ack，没有对应的 event，清不掉。

旧库第一次被这版打开，会因为新触发器还没装过而记下一次 `schema_tampered`。这条确认的原因**必须写明升级版本** `upgrade 0.5.1`（引擎版本，见 `shared/versions.json` 的 `engine_plugin_version`）。不带这几个字，确认会被拒绝：

```sh
python3 - << 'PY'
import os, sys
sys.path.insert(0, "plugin/dsh-plugin-cad-freecad/python")
from cadkit.ledger import acknowledge_alarm, open_ledger
conn = open_ledger(os.path.expanduser("~/cad-fleet"))
acknowledge_alarm(
    conn, "schema_tampered",
    "upgrade 0.5.1: 旧库第一次打开，新触发器已装上")
conn.close()
PY
```

`waiver`、`rules_rereview`、`review_signoff`、`quarantine_mark` 和告警确认，都必须和一条 event 写在同一个事务里，行上记下那条 event 的 `event_id` 和 `row_hash`。验收和 `alarm_open` 只认对得上的行（id、哈希、payload 内容和行一致）。对不上的行当作没有，并且记 `orphan_authorization`。用 sqlite3 命令行直接 INSERT 一条 waiver 或一条 ack，不会生效。这种行删不掉，所以确认过 `orphan_authorization` 之后，下次打开仍会因为该行还在而再次告警；它始终不会变成一次有效的豁免。

生产前，每次实跑结束后执行（这次运行本身不会 push）：

```sh
python3 tools/push_ledger_anchor.py
```

它只提交并推送 `status/ledger_anchor.json`。没推之前，下一轮如果 `HEAD` 里的锚还是旧的，链或结束 sha 对不上就会告警。这是上生产之前的必做步骤，不是可选项。

Python 里那个「有没有 WHERE」的检查只防手滑（`WHERE 1`、注释、CTE 都绕得过）。真正挡住的是触发器，而且只挡得住还装着这些触发器的库：`job` / `event` / `fingerprint` / `evidence_seal` / `waiver` / `rules_rereview` / `state_transition` / `ledger_alarm` 不能 DELETE，`event`、`waiver`、`rules_rereview`、`state_transition`、`ledger_alarm` 不能 UPDATE。`job` 可以 UPDATE，但改不了身份列。粘滞状态（FAIL / WAIVED / INFEASIBLE）只允许两条路：FAIL→WAIVED 或 FAIL→INFEASIBLE，且已经有对应的 `waiver` 行；以及 `error_class` 以 `inherited:` 开头的 FAIL→OK，且同一把钥匙上有带原因的 `rules_rereview`。别的都不行，包括 FAIL→PENDING_REVIEW 再改成 OK。粘滞状态下 `error_class` 不能改，`attempt` 只能增加不能减小。合法更新的 event 里记下改之前和改之后的 attempt、error_class。

`rules_rereview` 只解开 **n45_inherit** 记下来的 FAIL。设计 FAIL 只能靠 waiver。这个判断查 `state_transition`（只插入，按 project/stage/input/rules 建了索引），不再对每一件扫整张 event。

event 的 `row_hash` 只在迁移时补一次。补完会在链上写一条 `event_hash_backfill` 事件，这条事件不能删。之后谁把 `row_hash` 改成 NULL，再打开也不会重算，`verify_event_chain` 直接失败。写 event 时用 `BEGIN IMMEDIATE`，避免两个写者读到同一个链头再各写一条。有人能改数据库文件的话，可以拆掉触发器；`open_ledger` 会在补回触发器之前发现缺了哪一个，写下 `schema_tampered`，这一波不能 ACCEPT。触发器防的是手滑，不是防一个已经能写这个文件的人。机器旁边的锚文件和 `ledger.end.sha` 也能被同一个能改库的人改掉；仓库里已推送的 `status/ledger_anchor.json` 和运行日志里打印的链头是另外两份对照。

旧的 `event.kind=rules_rereview` 会在 `open_ledger` 时抄进 `rules_rereview` 表（`INSERT OR IGNORE`，可以重复跑）。也可以单独跑：

```sh
python3 - << 'PY'
import os, sys
sys.path.insert(0, "plugin/dsh-plugin-cad-freecad/python")
from cadkit.ledger import open_ledger, migrate_rules_rereview_from_events
conn = open_ledger(os.path.expanduser("~/cad-fleet"))
migrate_rules_rereview_from_events(conn)
conn.commit()
conn.close()
PY
```

同一条命令可以反复跑。已验收（证据文件里的哈希和磁盘一致）的阶段会跳过。超时 / 没有 JSON 这类瞬时失败最多试 `--max-attempts`（默认 3）次，记为 `RETRY`，还没变成粘滞 FAIL 之前可以被同一次修订上的成功盖过。一旦记成 FAIL，后来的 PASS **不能**抹掉它；要嘛换了字节（新的 input hash，即新修订），要嘛写明原因的豁免：

```sh
python3 - << 'PY'
import os, sys
sys.path.insert(0, "plugin/dsh-plugin-cad-freecad/python")
from cadkit.ledger import open_ledger, record_waiver
conn = open_ledger(os.path.expanduser("~/cad-fleet"))
record_waiver(conn, "b_in01_XXX", "validate", "<input_sha>", "<rules_sha>",
              "原因写清楚", kind="design-infeasible")
conn.close()
PY
```

同一族在一次运行里累计 3 个设计 FAIL，这一族余下的件会暂停，其他族继续。修好核并重新冻结之后，用 `--repair-failed` 再跑。

跑完（或 dry-run）会写：

```
outbox/fleet_b_2026_11/REPORT.md            含 LESSONS_ACK
outbox/fleet_b_2026_11/ACCEPTANCE.json      每件 accepted / quarantined / unverified / …
outbox/fleet_b_2026_11/fingerprints.delta.json   只含本次真正 accepted 的指纹
```

`VERDICT: ACCEPT` 只有在**每一件**都是 accepted 时才会出现。172 条旧账不会被写成 accepted。

收货（A 侧，等上面真的 ACCEPT 之后）：

```sh
python3 tools/merge_check.py --mine <A 的 cadRoot> \
    --theirs outbox/fleet_b_2026_11 --baseline shared/fingerprints
```

## 2. 十四件 BROKEN（全部是 b_in01）

件号：082、097、112、121、125、128、132、136、140、144、148、154、158、161。

不要删文件，不要改已经导出的 STEP 来“对上”清单。原件、日志、哈希都留着。分组在 `status/quarantine_b_in01.json`（`python3 tools/quarantine_b_in01.py` 可重算）：

| 组 | 件 | 含义 |
|---|---|---|
| 航空插座轴 | 082、112、125、128、132、136、140、144、148、154、158 | 拓扑签名的第 4 轴都是「三孔 + 航空插座座」。128–148 每隔 4 个件号，是因为壳体五轴按 (坐标和, 坐标) 排序后，这一轴停在该取值上、别的轴在变，**不是** sz 尺寸档（尺寸档长度是 8，这些件的尺寸并不相同）。审计上它们多半是旧 STEP 绑了旧 native，或 082 那种孤儿重导出。 |
| 原生校验失败 | 121、161 | 重建之后 validate 仍 FAIL（曲线/自交）。不在航空插座那一轴上。 |
| 097 单独 | 097 | 不是航空插座，也不是 121/161。现行 FCStd 是后重建的；STEP/manifest 仍认证重建前的 native（.FCBak 对得上）。留下的 FAIL 收据说的是 V2 的 trough/cbore/tab，V4 核已经删了这些对象。台账里那条 probe PASS 的 `receipt_sha` 是 `-`，没有文件哈希。保留原件，按新修订重跑，不要拿旧收据当结论。 |

这 14 件在验收报告里标 `quarantined`。隔离时的 build `input_sha` 由工具从台账读出并冻结，不能手写一份不同的哈希来放行：

```sh
python3 tools/quarantine_b_in01.py mark ~/cad-fleet b_in01_097
```

台账里还没有这条 build job 时，命令会拒绝，不会编一个哈希。新修订的 build input hash 变了之后，签字写进台账（同样不能事后改）：

```sh
python3 tools/quarantine_b_in01.py signoff ~/cad-fleet b_in01_097 \
    <新的 build input_sha> "你的名字" "新修订已目检"
```

两边都成立才离开隔离。手改 `quarantine/<件号>.json` 不算数。不要把它们标成 design-infeasible，除非看过新几何并写了 waiver。

只重跑这 14 件：

```sh
python3 tools/fleet_runner.py --root ~/cad-fleet --batch fleet_b_2026_11 \
    --intake intake/waves/fleet_b_2026_11_w1.json \
    --stages build,validate,probe,dedupe,export --package \
    --package-workers 1 --machine B --repair-failed \
    --only b_in01_082,b_in01_097,b_in01_112,b_in01_121,b_in01_125,b_in01_128,b_in01_132,b_in01_136,b_in01_140,b_in01_144,b_in01_148,b_in01_154,b_in01_158,b_in01_161 \
    --freecad-bin /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
```

121 和 161 若核本身不改，重跑会再次 FAIL，并且这次的 FAIL 会粘住。先改核、冻结、再跑。

## 3. A 线 wave 1，然后 C / D / E

A 线换 root，不要和 B 同时写一个台账：

```sh
python3 tools/recall.py --machine A
python3 tools/family_freeze.py freeze --machine A --src <A 的 cadRoot>/family
python3 tools/scaffold_wave.py --machine A --root <.../default-workspace/cad> \
    --intake intake/waves/fleet_a_2026_11_w1.json
python3 tools/fleet_runner.py --root <.../default-workspace/cad> \
    --batch fleet_a_2026_11 --intake intake/waves/fleet_a_2026_11_w1.json \
    --stages build,validate,probe,dedupe,export --package \
    --package-workers 1 --machine A \
    --freecad-bin /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
```

C / D / E 的 wave-1 合同用同一条展开命令生成（仓库里现在也有生成好的文件，以命令为准，可以重算）：

```sh
python3 tools/emit_members.py --machine C --wave 1 > intake/waves/fleet_c_2026_11_w1.json
python3 tools/emit_members.py --machine D --wave 1 > intake/waves/fleet_d_2026_11_w1.json
python3 tools/emit_members.py --machine E --wave 1 > intake/waves/fleet_e_2026_11_w1.json
```

这三台如果没有自己的 `~/cad-fleet`，就不要在 B 的目录里跑它们。每台：自己的 cadRoot、自己的 `family_src/<机器>/`、自己的 ledger。波次文件里的族是 fx01–fx05、st01–st05、fl01–fl05。

上一波没有 ACCEPT、指纹没有并进 `shared/fingerprints/<机器>.json` 之前，不要开 wave 2。

## 4. 一波怎样才算通过

下面七条都要成立。缺一条，`ACCEPTANCE.json` 就应该保持 `NOT_ACCEPTED`。C/D/E 用同一套，把族名和 cadRoot 换掉即可。

1. 这套引擎改动经过审阅并合进主分支。合之前不要用它给买方计数。
2. 172 件全部重新算哈希（FCStd、STEP、族核、指纹）。注册表和台账只追加，不整表删除、不用磁盘回填冒充当时的校验。
3. 172 件在修好的判定上重跑 validate 和 probe。任一窗口记录是 FAIL，整件就是 FAIL；后来的 PASS 作废不了它。
4. 用修好的指纹重跑 dedupe（候选 = 已交付 ∪ 本波已生成 ∪ 五机底表，缺 volume 或信封形状直接失败，不再把注册表缩成一行）。
5. 第 2 节的 14 件有根因记录。**097 仍是「暂时闭合」**：要在 Mac 的 FreeCAD 1.1.1 上打开当前 FCStd，确认已经没有 trough/cbore/tab，再对一下当前 FCStd、`.FCBak` 和 `manifest.fcstd_sha`，把这三份哈希存档。做完之前不要把 097 写成已经结案。`b_in01_002` 的已交付包要用清单里的文件哈希再核一遍，对不上就重做。
6. 人工复核是验收的一部分，不是可选项。包装阶段一律带 `CADKIT_REQUIRE_REVIEW=1`：没有 L1/L2 回执，`review_status` 不是 `complete`，件就不会 accepted。抽件还要在 FreeCAD 里打开，并按冻结核重跑 build，对比体积/步数（字节级 FCStd 哈希不必相等；体积相对差大于 1e-12 或步数变化会标 `recompute_drift`，不能验收）。
7. `ACCEPTANCE.json` 里按族计算 `accepted + quarantined + infeasible`。不等于 400，或者还有 unverified / needs_repair，结论就是 `NOT_ACCEPTED`。不要为了凑 400 改状态。

另外：验收要六段都 OK（build、validate、probe、dedupe、export、package），并且 FCStd、STEP、族核三个哈希都非空。`chain_evidence.json` 的 sha256 写在台账 `evidence_seal` 里；手改这份 JSON 对不上印章，就不算数。换了 `rules_sha` 不会洗掉旧 FAIL，除非对这一件写了 `record_rules_rereview` 的原因。

## 5. 仍然要人决定的事

- 族核历史版本（尤其 in01 丢过的中间代）找不回来。冻结的是**你现在磁盘上的那一版**。旧件不能声称由那一版生成，除非重跑对得上。
- 121 / 161 是修几何还是宣布 design-infeasible，看过新修订再写 waiver。不要凭这份分组提前宣布。
- 渲染长边和 STEP 往返容差仍然不是自动签字。人工复核已经是验收门：没有回执就不能 accepted。STEP 往返本身还要你在抽件时做。
- 全局发号/租约服务仍然没有。不要自造 `f3_` 新件号。
- 本机若装的是旧 tarball，而仓库引擎的 `rules_sha` 已经不同：以仓库 `plugin/` 为准，并让 `shared/versions.json` 跟上。

## 6. 上生产之前在 Mac 上做的两件事（云端没有 FreeCAD 1.1.1，这里不做）

**并发试验通过之前，`--package-workers` 保持 1。**

1. 两路包装试验：大约 20 件，`--package-workers 2`，其中一件的件号故意提交两次。日志要能证明走的是 `renamex_np`（macOS）那条不覆盖的改名；目录里不能剩下 `.stale.` / `.superseded.`；每一件的台账和磁盘要对得上。试验进行到一半，用**正在安装的那份 0.5.0 引擎**对同一个 cadRoot、同一个批次再发一次 package。它必须堵在 `.publisher.lock` 上，不能把文件发出去。试验收尾再跑一次 `verify_event_chain`，必须为真（两个包装线程同时写 event，链不能分叉）：

```sh
python3 - << 'PY'
import os, sys
sys.path.insert(0, "plugin/dsh-plugin-cad-freecad/python")
from cadkit.ledger import open_ledger, verify_event_chain
conn = open_ledger(os.path.expanduser("~/cad-fleet"))
print("verify_event_chain", verify_event_chain(conn))
assert verify_event_chain(conn)
conn.close()
PY
```

这几条都成立，才可以把 `--package-workers` 调到大于 1。另外对同一份没改过的台账连续跑两次，`<cadRoot>/ledger.end.sha` 和两次 `.backup` 的 sha 必须一致，否则会误报。
2. 097 结案：用 1.1.1 打开当前 FCStd，确认没有 trough/cbore/tab；比较当前 FCStd、`.FCBak`、`manifest.fcstd_sha` 三份哈希并归档。在此之前，门禁 5 对 097 保持「暂时闭合」。
