# outbox/ — B 机回传区

一个批次一个目录：`outbox/fleet_b_2026_11/`

```
delivery/fleet_b_2026_11/     交付批次目录原样（info.csv + 每件一个以 project_id 命名的文件夹）
ledger.jsonl                  你的台账导出（_table + 字段，见工单 §6 的脚本）
fingerprints.delta.json       只含本批次新增指纹：{"entries": {"b_xxx": {...}}}
waivers/fleet_b_internal.json 本批次内部来源豁免（scope.batches 写 fleet_b_2026_11）
versions.json                 你实际的 rules_sha / 插件版本 / tarball_sha256 / FreeCAD 版本
REPORT.md                     每条命令的 ok、降级明细、探针通过率、插件报错原文
```

## 推之前自己先过一遍门禁

A 机收货用的是**同一个脚本**，你自己先跑，省一轮往返：

```sh
python3 handoff/tools/merge_check.py --mine <你的 cadRoot> --theirs handoff/outbox/fleet_b_2026_11
```

`ACCEPT`（exit 0）才 push。它会挡：缺件 / 缺 STEP 或 FCStd / STEP 不是 AP214-AP242 /
渲染不足 8 张或长边 <2000px / 台账五阶段没走全 / 与 A 机底表或你自己件之间构成 `exact`、`scaled_same`。

## 不要提交进来的东西

- `ledger.sqlite3`（两边各一份，合并只在 A 机做；提交 sqlite 必冲突）
- 完整 `cad/projects/`（原生文件只在 `delivery/` 里给一份）
- 渲染图以外的临时产物、`__pycache__`、`mem/`、`audit/`
- `shared/fingerprints.json`（底表只由 A 机更新；你的增量写 `fingerprints.delta.json`）
