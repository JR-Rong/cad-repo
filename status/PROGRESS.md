# 进度流水（append-only，各机器只在末尾追加自己那行）

格式：`时间 | 机器 | project id | 状态 | 一句实况`
状态：`立项` → `设计` → `建模` → `校验` → `探针` → `出图` → `打包` → `已交付`；异常写 `阻塞:<原因>`。

---

- 2026-10-31 03:15 | A | (all) | 立项 | 清单签发：61 件（待做 50 / 25 族谱 / 24 件 S 级），`catalog_lint` OK，五份合同与开场指令已推 main
- 2026-10-31 03:16 | A | (baseline) | 设计 | 全局去重底表按机器分文件（`shared/fingerprints/A.json` 10 件），`registry_sync` 写入本机 cadRoot 通过
- 2026-10-31 03:20 | A | a_endcap_enc | 设计 | 插件 `new` 成功（cad_root=config、freecad_bin=config）；design.md 写完：特征 83 + 约束步骤预算 ≈170 步（A 级），与 `a_endcap_fan` 的拓扑差异已量化；下一步 `cad/family/iec_endcap_ext.py`（新模块，不动已交付的 `iec_endcap.py`，避免 10 件已交付项的 input_hash 漂移）
- 2026-10-31 03:34 | A | fleet_a_2026_11 | 立项 | **A 线所有权移交**：生产归本机另一个会话（粘 `briefs/BRIEF-FOR-A-LOCAL.txt`），协调会话从此不写 `cad/projects/a_*`、不写 `shared/fingerprints/A.json`、不跑 package —— 同一台机器同一份台账，两个进程同时生产就是在赌文件锁
- 2026-10-31 03:34 | A | (dispatch) | 设计 | 加 `DISPATCH.md`（B–E 派发单：三项前置、开工两条命令、以及"别听它自己说"的复核命令）

- 2026-10-31 03:31 | B | fleet_b_2026_11 | 阻塞:rules_sha 握手不一致 | B 机安装件与 releases/ 签名包逐字节一致（tarball sha256 与 versions.json 相符），但该引擎 version.rules_sha() 算得 **1d835a8f3d2ef4f1**，shared/versions.json 签发的是 **64a95367ef997904**；本机任何现存工件都复现不出 64a95…（refresh_versions.py 签名的 dev 检出 dsh-plugin-cad-freecad/python/cadkit 已无 .py 源文件）。按工单 §1/§7 停工等人：A 重打新 tarball 或按当前出厂包重签 versions.json 并 push，B 拉取复握手后开工。B 侧 memcheck 正常（cap 2048 / group 6144 / FreeCAD bin discovered）。
- 2026-10-31 03:33 | B | (baseline) | 设计 | 底表同步完成：10 件（A）→ ~/cad-fleet/registry/fingerprints.json；catalog_lint OK。两点上报：(1) registry_sync 只探测仓旁 dev 检出（本机该目录已清空），predicates=unavailable 仍静默返回 0 —— 它承诺的内部判重预检实际空跑；B 已用安装引擎补跑两两预检：45 对 0 exact / 0 scaled_same（3 对 related 为 A 同族件，不阻断）。建议 registry_sync 学 merge_check 加 CADKIT_PLUGIN_PY / profile 位点探测，并对 unavailable 显式报错。(2) B 机 cadRoot 已按工单写入 profile patch 层（cadRoot: ~/cad-fleet），插件加载期取配置需 DSH 重启才生效，重启前 B 以逐调用显式 root 参数等价执行并全程记 REPORT。
- 2026-10-31 03:48 | B | (prep) | 设计 | 停工期待握手期间完成非生产准备：10 件 design.md 草稿全部写完（特征/约束两级预算，A 级 148–164、S 级 215–230，同 A 机 a_endcap_enc 口径），同族两件拓扑差异按清单落为"有无/数量"级；params 表按变体裁剪方案已定（关闭特征零残留）。B 维持等待：releases/ 新包或 versions.json 重签任一到位即复握手开工；已挂 10 分钟轮询自查。
