# cad-repo — CAD 数据采购 · 五机协作仓

一台机器做不动的量：采购说明按**有效建模步骤**分级计价（S ≥200、A 100–199、B 50–99），
件数是钱；但规格 2.5 的判重是**按买方全部库存自动比对**的，机器越多越容易互相撞车。
所以这个仓不是"共享文件夹"，而是一份**签发过的分工合同** + 一张**全局几何底表** + 一道**收货门禁**。

| 机器 | 批次 | 类目 | 族谱 | 待做 | S 级 |
|---|---|---|---|---|---|
| **A（签发方）** | `fleet_a_2026_11` | 机电产品 | iec_endcap_ext, servo_rear_cover, drive_enclosure, inverter_panel, motor_mount | 10 | 4 |
| **B** | `fleet_b_2026_11` | 消费品 / 机电产品 / 电子元器件封装 | snap_enclosure, terminal_block, handheld_meter_case, panel_box, instrument_hood | 10 | 4 |
| **C** | `fleet_c_2026_11` | 工装夹具 | turn_fixture_base, weld_facing_plate, press_fixture, air_gauge_check, flip_fixture | 10 | 4 |
| **D** | `fleet_d_2026_11` | 结构件 | alu_corner_bracket, motor_bracket, machine_foot, cantilever_arm, access_door_frame | 10 | 3 |
| **E** | `fleet_e_2026_11` | 机械零件（流体/气路壳体） | gear_pump_housing, solenoid_valve_body, filter_housing, pneumatic_manifold, metering_pump_head | 10 | 9 |

**50 件 / 25 个族谱 / 24 件目标 S 级**；已交付 11 件（A 机 `pilot_2026_10` 10 件 + 参考件 1 件）也在清单里，
状态 `delivered`，作用是让所有人看得见"这些几何已经被占"。完整表格：[catalog/CATALOG.md](catalog/CATALOG.md)。

## 清单是怎么筛的（为什么没有轴承座、联轴器、气缸）

采购说明 2.5 明确不收"标准件库、厂商参考设计、教程范例"，且判重结果以买方为准、**只判不计数**。
所以进清单的只有**几何由我方设计决定**的物件：壳体、罩、支架、工装、检具、阀体、泵体。
每台机器一类工业场景（电机附件 / 仪器外壳 / 工装夹具 / 结构机架 / 流体壳体），
**族谱整族归一台、project id 带机器前缀**，同族两件必须换拓扑（孔/筋/槽/隔板数量或有无）——
纯缩放不是第二件零件，是一件重复件。计划本身的自洽性由 `tools/catalog_lint.py` 强制。

## 各机器开工（三条命令）

派发细节（前置检查、怎么复核它干得对）见 [DISPATCH.md](DISPATCH.md)。

```sh
git clone git@github.com:JR-Rong/cad-repo.git cad-repo && cd cad-repo
python3 tools/registry_sync.py --root ~/cad-fleet     # 同步全局去重底表（非 0 就停下喊人）
cat briefs/BRIEF-FOR-B.txt            # 换成你的字母；这段直接粘进那台机器的 DSH 会话
```

A 线由本机另一个会话独占执行，粘 `briefs/BRIEF-FOR-A-LOCAL.txt`（用本机既有 cadRoot，不是 `~/cad-fleet`）；
`briefs/BRIEF-FOR-A.txt` 是"A 跑在另一台机器上"时才用的版本。

没装插件的机器：`releases/local-dsh-plugin-cad-freecad-0.5.0.tgz`（sha256 见 `shared/versions.json`），
安装三步见 `releases/INSTALL.md`；装完**必须完全退出并重启** DSH，侧栏图标才会出现。

## 规则与不变量

- **一台机器一份台账**：`<cadRoot>/ledger.sqlite3` 不进 git、不手工合并；合并只在 A 机做。
- **底表一人一文件**：`shared/fingerprints/<你的字母>.json` 只有你能写，别人只读 —— 五台机器 push 也不会互相覆盖。
- **`package` 串行**：A 机 Round-10 审计挂着两个未修缺陷（N43 P1 锁 CAS 清理在 ownership 读异常时可能毁掉更新的交付、
  N44 P2 跨窗口 PASS 抹掉先前 FAIL），都需要并发才触发；并发发布等于在别人的交付件上赌运气。
- **版本握手**：`shared/versions.json` 里的 `rules_sha` 不一致就停工等 A 机重新签发，
  不要自己改引擎（`python3 tools/refresh_versions.py` 是 A 机的动作）。

## 目录

```
WORK-ORDER.md              通用工单（边界/动作序列/内存纪律/踩过的坑/回传/停下条件）
catalog/parts.json         零件总清单（机器可读，唯一事实源）
catalog/CATALOG.md         同一份清单的人读表格
intake/fleet_<x>_2026_11.json   各机器合同（pilotctl.py reconcile 认的格式）
briefs/BRIEF-FOR-<X>.txt   各机器开场指令（由 catalog 生成，不许手改）
shared/fingerprints/<X>.json    全局去重底表，按机器分文件
shared/versions.json       版本握手（rules_sha / tarball sha / FreeCAD / 规格 PDF sha）
shared/spec-extract.txt    采购说明全文（两边读同一份字）
shared/waiver-template.json     内部来源豁免模板（scope 写自己的批次）
releases/                  插件离线包 + 安装说明（新机器用）
outbox/<批次>/              回传区（交付目录 + 台账导出 + 指纹增量 + REPORT + 版本）
status/PROGRESS.md         进度流水（append-only，各机器末尾追加一行）
tools/build_catalog.py     清单源表 → parts.json / CATALOG.md / intake / briefs（改清单后重跑）
tools/catalog_lint.py      计划自洽性门禁（同族多机、id 前缀、拓扑重复、合同漂移）
tools/registry_sync.py     底表 → 本机 registry，并做跨机判重预检
tools/merge_check.py       收货门禁（缺件/缺 STEP/AP214/渲染 2000px/五阶段/跨机判重）
tools/refresh_versions.py  A 机重签版本握手
tools/engine_predicates.py 直接 import 出厂判定函数（注入 FreeCAD 桩，host python3 可跑）
```

## 改清单的流程（只有 A 机）

`tools/build_catalog.py` 改表 → 重跑 → `python3 tools/catalog_lint.py` 必须 OK →
`python3 tools/refresh_versions.py` → commit + push。别的机器 pull 后 lint 一下就知道自己的件有没有变。
