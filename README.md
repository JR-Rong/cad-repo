# CAD 双机协同仓（fleet handoff）

两台机器共用一套**验收规则**和一张**全局去重底表**，各自独立生产、各自独立台账，靠这个仓库交接。

| 角色 | 机器 | 批次 | 负责族谱 | 台账 |
|---|---|---|---|---|
| A（本机） | macOS · FreeCAD 1.1.1 | `pilot_2026_10`（已交付 10/10）、`demo_2026_10` | `iec_endcap`（IEC 电机端盖） | `cad/ledger.sqlite3` 在 A 机，不入库 |
| B（远端） | macOS · FreeCAD 1.1 · 插件 0.5.0 | `fleet_b_2026_11`（6 件） | `snap_enclosure` / `terminal_block` / `drill_jig` | 自己的 `cad/ledger.sqlite3` |

**为什么按品类切开**：采购说明 2.5 只认"不同物件"。两台机器做同一族谱的不同机座号，等于把 2.5 的判定风险乘二；
做**不同类目**则结构上不可能撞重。所以 B 机不碰 `iec_endcap`，A 机不碰 B 的三个新族谱。

## 仓库布局

```
WORK-ORDER-B.md            B 机的工单（先读这个）
intake/fleet_b_2026_11.json    B 机的件清单（合同）；pilotctl.py reconcile 认这个格式
shared/fingerprints.json   全局去重底表：A 机已产出的全部几何不变量（B 机开工前必须导入）
shared/versions.json       对齐用：插件版本、rules_sha、FreeCAD 版本、规格摘要 sha
shared/spec-extract.txt    采购说明全文（从 PDF 抽出，避免两边读到的版本不同）
shared/waiver-template.json    内部来源豁免（B 机新建自己的批次豁免文件照此填）
outbox/                    B 机回传的地方（交付批次 + 台账导出 + 指纹增量 + 收据）
tools/merge_check.py       A 机收货时用：跨机去重 + 台账对账 + 缺件检查
```

## 两边的 git 约定

- `main` 只放**工单、件清单、底表、工具**；A 机是唯一能改 `WORK-ORDER-*.md` 与 `shared/fingerprints.json` 的人。
- B 机在自己的分支上工作（分支名 = 批次名 `fleet_b_2026_11`），产物只写进 `outbox/`。
- 谁都不许改对方目录：A 机不写 `outbox/` 里 B 机的文件，B 机不写 `intake/`（发现清单有问题就在 outbox 里提 issue 文件）。
- 每次交付一个 commit，commit message 第一行 `deliver(fleet_b_2026_11): 6/6`，第二行写 `rules_sha`。

## 一次完整往返

```sh
# B 机（第一次）
git clone <你的仓库> handoff && cd handoff
python3 -c "import json,shutil,os; os.makedirs('~/cad-fleet/registry',exist_ok=True); \
  shutil.copy('shared/fingerprints.json','~/cad-fleet/registry/fingerprints.json')"   # 导入去重底表
# 然后按 WORK-ORDER_B.md 干活；cadRoot 固定 ~/cad-fleet（见工单）

# B 机（每件完成）
git add outbox && git commit -m "deliver(fleet_b_2026_11): n/6" && git push

# A 机（收货）
python3 handoff/tools/merge_check.py --mine cad --theirs handoff/outbox/fleet_b_2026_11
```
