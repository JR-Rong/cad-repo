# 工单 B — 新品类族谱扩量（6 件）

批次 `fleet_b_2026_11` · 派工：A 机（本机，IEC 端盖族谱归属方） · 适用：B 机 macOS + FreeCAD 1.1 + 插件 0.5.0

## 0. 你的边界（越界就是白做）

- **只做**本工单三个族谱的 6 件；**不碰** `iec_endcap`、电机端盖、轴承座（pillow block）、油路块（port plate）、衬套 —— 那些是 A 机在产或已产，撞 2.5 判定谁都没好处。
- 全部**单件实体零件**（2.1 允许零件或装配体）。装配体的"引用完整"（2.3）另开批次，第一批不做。
- `cadRoot` 固定 `~/cad-fleet`，`source_url=internal`，批次名 `fleet_b_2026_11`（写进豁免文件的 scope）。
- 台账（`cad/ledger.sqlite3`）**只在你机器上**，不要往 git 里塞 sqlite；回传只走 `outbox/`（导出件 + 交付目录）。

## 1. 开工自检（三条都要回报给 A 机，不要闷头开工）

```sh
cad_freecad memcheck
```

回报里必须有：`cad_root`（应为 `/Users/<you>/cad-fleet`，来源 `env`/`config`）、`freecad_bin`、
`mem.cap_mb`（应为 2048）。然后对齐版本：

```sh
cat handoff/shared/versions.json      # A 机当时的 rules_sha / 插件版本 / FreeCAD 版本
cad_freecad status project <任一>      # 结果里的 rules_sha 必须与 shared/versions.json 相同
```

**`rules_sha` 不一致就先回报、不要自己改引擎**：规则不同 = 两边不在同一个验收口径下，
A 机会打新的 tarball（`shared/versions.json` 同步更新），你装完再开工。已经做了一半的件不用重做：
台账的阶段键含 rules_sha，规则一变那些阶段会自动变成待重跑。

导入去重底表（B 机第一次必做）：

```sh
mkdir -p ~/cad-fleet/registry
cp handoff/shared/fingerprints.json ~/cad-fleet/registry/fingerprints.json
```

## 2. 任务清单

每件都是"一个族谱生成器 + 一个 variant 开关表"，两两之间**拓扑必须不同**（孔/筋/槽/隔板的数量或有无），
纯缩放会被 2.5 判重复。目标分级 **A（100–199 步）为下限，能到 S（≥200 步）更好**。

| # | project id | 物件 | 必须出现的功能结构（写进 design.md 的特征清单，先数到目标步数再动手） | 与兄弟件的拓扑差异 |
|---|---|---|---|---|
| 1 | `b_snapenc120_r3` | 卡扣式仪器外壳 120×80×40（下壳） | 薄壁抽壳、周边卡扣 6、PCB 铜柱 4、底部散热筋 3、M3 法兰螺柱、线缆出口 2、定位销孔 2、沉台与止口 | 筋 3、**无**透光窗、卡扣 6 |
| 2 | `b_snapenc120_r4w` | 同族谱上壳（带数显窗） | 同上 + 数显透光窗（含窗框台阶与卡爪 4）、按键孔 3、加强筋 4、接地螺柱 1 | 筋 4、**有**窗与按键孔、卡扣 8、多接地螺柱 |
| 3 | `b_termblk_4p` | 螺钉式接线端子 4P（导轨卡装） | 壳体隔腔 4、螺钉孔 4 + 导体压板腔、进线喇叭口、导轨卡扣与导向斜面、标记牌槽、防手指绝缘壁、灭弧/散热槽 | 极数 4、**无**可拆分隔板 |
| 4 | `b_termblk_6p_s` | 同族谱 6P（带分隔板） | 极数 6 + 可拆分隔板槽 5 + 标记槽 + 侧端挡板 + 螺钉 6 | 极数 6、**有**分隔板与挡板 |
| 5 | `b_drilljig_4b` | 钻孔钻模（4 孔） | 底板 + 钻套 4（过盈台阶 + 衬套肩）+ 菱形定位销 2 + 支承钉 3 + 压板槽 + 排屑缺口 + 紧固螺塞 | 钻套 4、销 2、**无**快换夹紧 |
| 6 | `b_drilljig_6b` | 同族谱（6 孔，带快换夹紧） | 钻套 6 + 定位销 3 + 快换夹紧螺钉 2 + 排屑槽贯通 + 对刀块槽 | 钻套 6、销 3、**有**快换夹紧与对刀块槽 |

建议顺序：`1→3→5` 先把三个族谱各打通一件（族谱生成器可复用），再回来出 `2→4→6`。

## 3. 每件的动作序列（就是这个顺序，别自创）

```sh
# 立项（注意 opts 是 id + title，不是 template）
cad_freecad new  opts {"id":"b_snapenc120_r3","title":"Snap enclosure 120x80x40 (base)","grade":"A","unit":"mm","type":"part","series":"snap_enclosure","source_url":"internal","license":"Author-granted commercial redistribution"}
# 1) 先写 design.md：功能、关键尺寸来由、接口、特征清单（草图×特征数到目标步数）
# 2) 写族谱/参数 + build.py：params 工作表是唯一驱动源
cad_freecad build     project b_snapenc120_r3
cad_freecad validate  project b_snapenc120_r3     # 任一 FAIL 就回建模，别往下走
cad_freecad probe     project b_snapenc120_r3     # 逐参数 +1% 扰动，必须全 PASS
cad_freecad dedupe    project b_snapenc120_r3     # exact / scaled_same = 阻断，立刻停下报告
cad_freecad export    project b_snapenc120_r3     # STEP AP214 + 8 张 ≥2000px + 一致性自检
cad_freecad package   batch fleet_b_2026_11 project b_snapenc120_r3
python3 cad/tools/pilotctl.py record b_snapenc120_r3 package ok   # 台账阶段记录（若你复用了 pilotctl）
```

`package` 是逐项硬校验的：交付清单缺任一项就不出件（这是设计如此，不是 bug）。

## 4. 内存纪律（这台机器与 A 机同规则）

- 默认上限：单件 `memCapMb=2048`，进程树 `groupCapMb=6144`，探针并发 `probeMaxWorkers=2`。A 机实测单件 export 峰值 426–530 MB。
- **串行 package，不要并行**：A 机 Round-10 审计仍挂着两个未修缺陷 —— **N43(P1) 锁 CAS 清理在 ownership 读异常时可能毁掉更新的交付**、**N44(P2) 跨窗口合并里后到的 PASS 会抹掉先前有效的 FAIL**。两者都需要并发/交错才触发。所以：一次只跑一个 `package`，不要开多个会话同时发布，也不要手工合并两边的 sqlite 台账（合并只在 A 机做，你只回传导出）。
- 结果里出现 `degraded`（降长边/关超采样/降三角面）是正常的，但**交付图长边必须 ≥2000px**：被降到 2000 以下时不要打包，改小 `opts {"max_triangles":N}` 或把模型拆细再出图。

## 5. A 机踩过的坑，直接照做（能省你半天）

1. **关掉的特征组，它的参数不要留在 params 表里** —— 探针会逐格 +1% 扰动，惰性单元格必 FAIL。
2. **倒角/圆角紧跟它的目标边创建点**（生成后就做，别攒到树尾）：元素图引用随链长衰减，攒后面必现悬空引用。
3. **切平面 / 布尔用草图面时外移 ≥1mm**，别让共面参与布尔。
4. **族谱变体按真实机型逐参数取值**（螺钉规格、壁厚、筋数随机型变），杜绝"整个模型 ×k"——那是 2.5 的 `scaled_same`，会被阻断。
5. 步骤数**先从 design.md 数出来**再建模；`validate` 的 `step_floor` 不足直接 FAIL，也不许堆无意义几何（2.4 真实可辨识）。
6. 每个草图至少 1 个尺寸/几何约束（2.4 硬项，validator 会查）。
7. 渲染与 STEP 都由插件产出，**不要手改**：8 视图 / 白底 / 单色灰 / 无坐标轴 / 同比例是硬要求；STEP 必须由交付的原生文件直接导出（AP214）。

## 6. 回传（`outbox/fleet_b_2026_11/`，一次交付一个 commit）

```
outbox/fleet_b_2026_11/delivery/fleet_b_2026_11/     # 交付批次目录原样（含 info.csv + 每件文件夹）
outbox/fleet_b_2026_11/ledger.jsonl                  # 你的台账导出：见下方 SQL
outbox/fleet_b_2026_11/fingerprints.delta.json       # 本批次新增的指纹条目（B 机 project_id 那些）
outbox/fleet_b_2026_11/waivers/fleet_b_internal.json # 你的内部来源豁免（scope 写 fleet_b_2026_11）
outbox/fleet_b_2026_11/REPORT.md                     # 每条命令的 ok / 降级 / 探针通过率 / 遇到的插件报错
outbox/fleet_b_2026_11/versions.json                 # 你实际用的 rules_sha、插件版本、FreeCAD 版本、memcheck 输出
```

台账导出（只导本批次相关行，纯 stdlib）：

```sh
python3 - <<'PY'
import json, os, sqlite3
c = sqlite3.connect(os.path.expanduser("~/cad-fleet/ledger.sqlite3"))
out = open("outbox/fleet_b_2026_11/ledger.jsonl", "w")
for table in ("model", "revision", "job", "evidence", "delivery", "fingerprint", "artifact"):
    cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % table)]
    for row in c.execute("SELECT * FROM %s" % table):
        out.write(json.dumps({"_table": table, **dict(zip(cols, [str(x) for x in row]))},
                             ensure_ascii=False) + "\n")
out.close()
PY
```

## 6.5 推之前自己先过门禁（同一个脚本，省一轮往返）

```sh
python3 handoff/tools/merge_check.py --mine ~/cad-fleet --theirs handoff/outbox/fleet_b_2026_11
```

`ACCEPT`（exit 0）才 push。它挡：缺件、缺 FCStd/STEP、STEP 非 AP214/AP242、渲染不足 8 张或长边 <2000px、
台账五阶段没走全、以及与 A 机底表或你自己件之间构成 `exact`/`scaled_same`。
判定函数直接 import 插件里的 `cadkit.fingerprint`（`tools/engine_predicates.py` 注入 FreeCAD 桩，
所以 host python3 就能跑，不需要 FreeCAD 环境）—— 不是把规则抄一遍，抄的那份会漂。
门禁会自己找 cadkit：本仓旁边的 `dsh-plugin-cad-freecad/python`，找不到就扫
`~/.dsh/profiles/*/node_modules/@local/dsh-plugin-cad-freecad/python`（你装插件后的实际位置）；
都没有就 **fail closed**（报"拿不到 cadkit.fingerprint，跨机去重未执行"），这时用
`--plugin-python <含 cadkit 的目录>` 或 `export CADKIT_PLUGIN_PY=…` 指过去。它绝不会在拿不到判定函数时放行为 ACCEPT。

## 7. 立刻停下来问人的条件

- `rules_sha` 或插件版本与 `shared/versions.json` 不一致；
- `dedupe` 报 `exact` / `scaled_same`（不管对谁）；
- `probe` 有参数修不过（先回报，不要靠关掉该参数蒙过去）；
- 需要 `memCapMb` 降到 1536 以下才能过（说明几何规模失控，A 机有现成处理办法）；
- 你想改 `intake/fleet_b_2026_11.json`（清单是合同，改动必须 A 机签字）。
