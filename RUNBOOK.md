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

`--package-workers` 默认 **1**。按件锁和 0.5.0 / `driver.py` 的批级 `.publisher.lock` **互不排斥**。调度器若发现那个旧锁正被占用，会直接退出。在新 tarball 于 FreeCAD 1.1.1 上验证过并发之前，不要加大这个数。读交付目录时只有「文件确实不存在」才当作没有更新的修订；第一次放入用 `renameat2(RENAME_NOREPLACE)` / macOS `renamex_np(RENAME_EXCL)`，避免普通 `rename` 换掉一个空目录。

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

这 14 件在验收报告里标 `quarantined`，同一批字节重跑也不会变成 accepted。要离开隔离，必须同时满足：`~/cad-fleet/quarantine/<件号>.json` 里的 `input_sha` 和现在的构建输入哈希不同，并且有人签字（`sign_off.reviewer` + `sign_off.reason`）或写了 waiver。不要在台账里把它们标成 design-infeasible，除非你看过新几何、确认这个轴组合做不出来，并用上面的 waiver 写下原因。

隔离记录例：

```json
{"input_sha": "<隔离时的 build input_sha>",
 "sign_off": {"reviewer": "你的名字", "reason": "新修订已目检"}}
```

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
5. 第 2 节的 14 件有根因记录（见 quarantine 文件）；`b_in01_002` 的已交付包要用清单里的文件哈希再核一遍，对不上就重做，不要沿用旧检查表。
6. 人工复核是验收的一部分，不是可选项。包装阶段一律带 `CADKIT_REQUIRE_REVIEW=1`：没有 L1/L2 回执，`review_status` 不是 `complete`，件就不会 accepted。抽件还要在 FreeCAD 里打开，并按冻结核重跑 build，对比体积/步数（字节级 FCStd 哈希不必相等；体积相对差大于 1e-12 或步数变化会标 `recompute_drift`，不能验收）。
7. `ACCEPTANCE.json` 里按族计算 `accepted + quarantined + infeasible`。不等于 400，或者还有 unverified / needs_repair，结论就是 `NOT_ACCEPTED`。不要为了凑 400 改状态。

另外：验收要六段都 OK（build、validate、probe、dedupe、export、package），并且 FCStd、STEP、族核三个哈希都非空。`chain_evidence.json` 的 sha256 写在台账 `evidence_seal` 里；手改这份 JSON 对不上印章，就不算数。换了 `rules_sha` 不会洗掉旧 FAIL，除非对这一件写了 `record_rules_rereview` 的原因。

## 5. 仍然要人决定的事

- 族核历史版本（尤其 in01 丢过的中间代）找不回来。冻结的是**你现在磁盘上的那一版**。旧件不能声称由那一版生成，除非重跑对得上。
- 121 / 161 是修几何还是宣布 design-infeasible，看过新修订再写 waiver。不要凭这份分组提前宣布。
- 渲染长边和 STEP 往返容差仍然不是自动签字。人工复核已经是验收门：没有回执就不能 accepted。STEP 往返本身还要你在抽件时做。
- 全局发号/租约服务仍然没有。不要自造 `f3_` 新件号。
- 本机若装的是旧 tarball，而仓库引擎的 `rules_sha` 已经不同：以仓库 `plugin/` 为准，并让 `shared/versions.json` 跟上。
