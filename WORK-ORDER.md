# CAD 流水线通用工单（机器 A–E）

派工方：A 机 · 适用：macOS/Linux + FreeCAD 1.1 + 插件 0.5.0 ·
**你的件清单在 `catalog/CATALOG.md`（人读）与 `intake/fleet_<你的机器>_2026_11.json`（合同）**

## 0. 你的边界（越界就是白做）
- **只做**清单里分给你那台机器的前缀件（A=`a_`、B=`b_`…），别的机器在产同表别的族谱，你碰就是重复劳动 + 判重风险。
- 全部**单件实体零件**（2.1 允许零件或装配体）；装配体的"引用完整"（2.3）另开批次。
- `cadRoot` 固定 `~/cad-fleet`；`source_url=internal`；批次名 `fleet_<你的机器>_2026_11`（写进你自己的豁免文件 scope）。
- 台账 `<cadRoot>/ledger.sqlite3` **只在你机器上**：不进 git、不手工合并；回传只走 `outbox/` + `shared/fingerprints/<你的机器>.json`。
- **整族归一台**：族谱生成器脚本随交付走（bundle_extra），不要复制别人的族谱改两行交上来 —— 那是 2.5 的"同一来源/同一段代码改参数"。
## 1.## 1. 开工自检（三条都要回报给 A 机，不要闷头开工）

```sh
cad_freecad memcheck
```

回报里必须有：`cad_root`（应为 `<HOME>/cad-fleet`，来源 `env`/`config`）、`freecad_bin`、
`mem.cap_mb`（应为 2048）。然后对齐版本：

```sh
cat handoff/shared/versions.json      # A 机当时的 rules_sha / 插件版本 / FreeCAD 版本
cad_freecad status project <任一>      # 结果里的 rules_sha 必须与 shared/versions.json 相同
```

**`rules_sha` 不一致就先回报、不要自己改引擎**：规则不同 = 两边不在同一个验收口径下，
A 机会打新的 tarball（`shared/versions.json` 同步更新），你装完再开工。已经做了一半的件不用重做：
台账的阶段键含 rules_sha，规则一变那些阶段会自动变成待重跑。

导入去重底表（每台机器第一次必做）：

```sh
python3 tools/registry_sync.py --root ~/cad-fleet   # 合并五台机器的底表 + 内部判重预检
```

## 2. 你的件与开工三步

一句话：**`python3 tools/catalog_lint.py` 保证 50 件里没有任何两件同拓扑意图、没有一族被分给两台机器**；
你那 10 件的两两拓扑差异就写在清单最后一列，那是你的"怎么才算不重复"的答案，建模前照它设计特征清单。

开工三步：

```sh
git pull
python3 tools/catalog_lint.py                       # 计划本身是否自洽（A 机改清单后你才需要跑）
python3 tools/registry_sync.py --root ~/cad-fleet   # 把五台机器的全局去重底表同步进本机 registry
```

`registry_sync` 会在底表内部已经存在 `exact`/`scaled_same` 时**返回非 0 并拒绝静默通过** —— 那一刻别开工，先喊人。
## 3. 每件的动作序列（就是这个顺序，别自创）

```sh
# 立项（注意 opts 是 id + title，不是 template）
cad_freecad new  opts {"id":"<你的 project id>","name":"Snap enclosure 120x80x40 (base)","desc":"卡扣式仪器外壳下壳：薄壁抽壳、周边卡扣 6、PCB 铜柱 4、散热筋 3、线缆出口 2","grade":"A","unit":"mm","type":"part","series":"snap_enclosure","author":"internal:dsh-cad-agent-B","source_url":"internal","license":"Author-granted commercial redistribution"}
# 1) 先写 design.md：功能、关键尺寸来由、接口、特征清单（草图×特征数到目标步数）
# 2) 写族谱/参数 + build.py：params 工作表是唯一驱动源
cad_freecad build     project <你的 project id>
cad_freecad validate  project <你的 project id>     # 任一 FAIL 就回建模，别往下走
cad_freecad probe     project <你的 project id>     # 逐参数 +1% 扰动，必须全 PASS
cad_freecad dedupe    project <你的 project id>     # exact / scaled_same = 阻断，立刻停下报告
                                                 # （默认只查不登记；确认是新物件后才 opts {"register":true}）
cad_freecad export    project <你的 project id>     # STEP AP214 + 8 张 ≥2000px + 一致性自检
cad_freecad package   project <你的 project id> opts {"batch":"fleet_<x>_2026_11"}
python3 cad/tools/pilotctl.py record <你的 project id> package ok   # 台账阶段记录（若你复用了 pilotctl）
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

## 6. 回传（`outbox/fleet_<x>_2026_11/`，一次交付一个 commit）

```
outbox/fleet_<x>_2026_11/delivery/fleet_<x>_2026_11/  # 交付批次目录原样（含 info.csv + 每件文件夹）
outbox/fleet_<x>_2026_11/ledger.jsonl                  # 你的台账导出：见下方 SQL
outbox/fleet_<x>_2026_11/fingerprints.delta.json       # 本批次新增指纹（自己前缀那些）
outbox/fleet_<x>_2026_11/shared-fingerprints.json      # 同一个增量的底表形态，A 机收货后并入 shared/fingerprints/<X>.json
outbox/fleet_<x>_2026_11/waivers/fleet_<x>_internal.json # 你的内部来源豁免（scope 写你自己的批次）
outbox/fleet_<x>_2026_11/REPORT.md                     # 每条命令的 ok / 降级 / 探针通过率 / 遇到的插件报错
outbox/fleet_<x>_2026_11/versions.json                 # 你实际用的 rules_sha、插件版本、FreeCAD 版本、memcheck 输出
```

台账导出（只导本批次相关行，纯 stdlib）：

```sh
python3 - <<'PY'
import json, os, sqlite3
c = sqlite3.connect(os.path.expanduser("~/cad-fleet/ledger.sqlite3"))
out = open("outbox/fleet_<x>_2026_11/ledger.jsonl", "w")
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
python3 tools/merge_check.py --root ~/cad-fleet --theirs outbox/fleet_<x>_2026_11 --baseline shared/fingerprints
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
- 你想改 `catalog/parts.json` 或 `intake/*.json`（清单与合同是 A 机签发物，改动必须 A 机签字并重跑 lint）。
