# 进度流水（append-only，各机器只在末尾追加自己那行）

格式：`时间 | 机器 | project id | 状态 | 一句实况`
状态：`立项` → `设计` → `建模` → `校验` → `探针` → `出图` → `打包` → `已交付`；异常写 `阻塞:<原因>`。

---

- 2026-10-31 03:15 | A | (all) | 立项 | 清单签发：61 件（待做 50 / 25 族谱 / 24 件 S 级），`catalog_lint` OK，五份合同与开场指令已推 main
- 2026-10-31 03:16 | A | (baseline) | 设计 | 全局去重底表按机器分文件（`shared/fingerprints/A.json` 10 件），`registry_sync` 写入本机 cadRoot 通过
- 2026-10-31 03:20 | A | a_endcap_enc | 设计 | 插件 `new` 成功（cad_root=config、freecad_bin=config）；design.md 写完：特征 83 + 约束步骤预算 ≈170 步（A 级），与 `a_endcap_fan` 的拓扑差异已量化；下一步 `cad/family/iec_endcap_ext.py`（新模块，不动已交付的 `iec_endcap.py`，避免 10 件已交付项的 input_hash 漂移）
