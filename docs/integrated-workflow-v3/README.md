# FreeCAD 五机完整流程与交接包

这是完整流程文档与 210000 新零件规划，不是已融合的运行系统。原 100 新件及 8 参考件、DSH/CadKit 现状、统一流程、规则和失败边界均在主手册。五机目标各 42000，当前实际统一接受量未知，禁止通过参数网格、镜像、改名凑数。

仓库 https://github.com/JR-Rong/cad-repo ，本版调查代码基线 `bdaab3c1de90723fcd07ddae1edc75132b285a09`；文档置于 `docs/integrated-workflow-v3`。后续 main 变化需重新核查，不自动覆盖在途生产。原源树没有此次确认完整的 PartDesign 后端、全部 family_src 和 B 报告中的回填/打包脚本；应由持有人提供冻结版本。文档包不含 909 MB CAD 模型、SQLite、凭据或私有日志。

## 阅读顺序

- `FreeCAD_Fleet_Integrated_Handbook.zh.md` 和同名 Word：自包含主手册，48 章、5 附录；Markdown 用于命令复制，Word 用于阅读。
- `prompts/START_A.md` 至 `START_E.md`：五份独立启动提示词，先检查旧代码、任务、成品、在途、失败和人工待审，只补缺；默认 AUDIT_ONLY。
- `planning/family_ownership.csv`：原 500 key 的历史与新建议 owner；35 项迁移待确认，历史和在途不重写。
- `planning/family_extensions.csv`：每机 10 个待探索功能子族；全 50 项未建模，不保证独立。
- `planning/fleet_plan.json`：每机 42000、候选上限 70000、分阶段门槛，统一账本计数。
- `templates/`：统一合同、事件、检查、人工审查、盘存、接收和租约建议 schema；未部署。
- `evidence/`：102 条历史源码定位加本轮 F 索引、40/32 规则与 12 项 DSH 对照、公开盘存摘要和文档验证。

## 原流程后端资产取得与核对

**仅 clone 仓库不能运行原 100 件流程。** 此仓库有 CadKit 引擎，但不含完整原 PartDesign 后端。向原资产持有人取得以下两个普通 ZIP，来源位于 `${LEGACY_ARCHIVE_ROOT}/final_delivery_100_unique_new_8_references_20261014/standalone_two_ordinary_ZIPs`，也可从已封存 909477759 字节完整交付包中取出该子目录。没有提供公共下载 URL；不要猜网盘地址或从网络下载同名包。

- `FreeCAD_runtime_source_100new_8refs_complete_under8MB_20261014.zip`：5409515 字节，SHA256 `62ce275859f5798dfeeafe5ff527fe7d36410a873e27fef8cdda0d8da881d73a`。
- `FreeCAD_required_native_references4_100new_8refs_complete_under8MB_20261014.zip`：7003732 字节，SHA256 `139ab4e1a6d70c5724496bedfc7de906ad77eb0a39c7a547bc62c3a716a712fe`。

先用本机 SHA256 工具按字节校验（macOS 可用 `shasum -a 256 文件路径`），再解压到新的独立父目录，合并同名根 `FreeCAD_standalone_100new_8refs_20261014`。不要覆盖已有生产目录。取得完整树后，从便携根运行源码确认的 `python3 portable_workflow.py verify`；该操作只做 manifest/目录核对，不启动 CAD。它依赖 production_goal 和完整 JSON/引用资产，不能从这份文档包直接执行。正式运行另需匹配 FreeCAD/Python/Qt 运行时、显示渲染环境、引用根和可用磁盘；实际版本、路径配置和命令详见主手册第 4、5、8、9 章。历史目标锁定 100，不能直接改数字并声称支持 21 万。

只读清点显示源码包含 144 个 Python 文件、未压缩合计 1071643 字节，包内没有 FCStd/STEP。候选最小迁移范围是入口、production_goal/current_delivery/run_queue、recipe 定义、checks、workflow_source/scripts 与 procurement_acceptance worker/validator，以及经核对必需的配置、recipe 和规则。完整运行还需要 JSON 目录、历史/基线索引和四个 Native 引用。144 个 Python 包含诊断与历史负例，不能随意裁掉检查器；纯 Python 体积不等于可运行最小闭包。本轮只报告此范围，未将其整包复制到公开仓库：全部数据依赖及第三方采购材料的公开传播来源未穷尽核实。保留原包身份和失败证据，后续源码引入应单独审阅，不能把二进制引用或采购 PDF 当用户原创源码发布。

## 五机范围

- A：电机与传动机械接口；96 个原目录入口加 10 个扩展入口，最终接受目标 42000。
- B：仪器与电子干式封装；111 个原目录入口加 10 个扩展入口，最终接受目标 42000。
- C：工装夹具与测量检具；103 个原目录入口加 10 个扩展入口，最终接受目标 42000。
- D：机架输送与物料结构；104 个原目录入口加 10 个扩展入口，最终接受目标 42000。
- E：流体与热管理；86 个原目录入口加 10 个扩展入口，最终接受目标 42000。

族数、架构数、配置数、最终接受数是不同量。全局总目标 210000，不把五台本地计数简单相加。原100+8只作去重基线；DSH现有件未自动加分。B报告的产量和过期STEP只能现场复核后分类。

## 外部资产与路径配置

复制 config/asset_roots.example.json 到你自己的非公开配置文件，填写实际绝对路径，不提交私有配置。根含义如下：

- REPO_ROOT：冻结代码仓库根；DOC_ROOT：本交接目录。
- LEGACY_ARCHIVE_ROOT：完整历史交付根，内含完整 ZIP 和 final_delivery_100_unique_new_8_references_20261014。
- LEGACY_SOURCE_ROOT：只读审计源码副本；正式安装须取得主手册第 2 章的两个完整普通 ZIP，不把此不完整审计副本当完整安装包。
- LEGACY_AUDIT_ROOT：只读审计说明所在根。
- LEGACY_V1_DOC_ROOT / LEGACY_V2_DOC_ROOT：原始审计证据的本地封存文档根，源索引中的原始哈希以此为准；公开版摘要不是其逐字节替代。
- DSH_WORKSPACE_ROOT：实际 DSH 工作区；含安装插件、cad 与 handoff，安装版和仓库版分开核验。

主手册 B/D/S/P/H/W 等短名仅在各章所定义上下文中使用，历史 P 为便携根，DSH P 为安装插件，不能跨章误用。运行变量 FREECAD_PYTHON、CAD_ROOT、工件存储路径和实际模型端点由部署者核实；不在文档里填写密钥。脚本中宏路径和历史命令是证据示例，使用前检查参数、配置和授权，不能照抄立即开跑。

以下是本包新写的只读核对脚本的真实 CLI，从本目录运行：

```bash
python3 tools/verify_documentation_assets.py --roots /path/to/private_asset_roots.json
```

它只读取文件和 SHA256，不调用 CAD，不写账本。所有 108 条 source 记录匹配时返回 0；缺根或缺文件为 UNKNOWN/MISSING，哈希不符为 MISMATCH，返回 1。即使核对成功也不意味着 CAD 技术门禁、N45 或全局适配已通过。首次复制根配置不填 null 时无法完成外部资产核对，这是预期阻断。

## 当前上线阻断

同版分叉、N45 FAIL 被 DONE 覆盖风险、PartDesign 与 Part:: 不兼容、strict/review 默认门禁、SQLite/JSON 去重基线分裂、恢复边界与族核心缺失仍需修复和证据。文档没有宣称已经解决。新候选先租约和全局登记，技术与功能硬审、实际人工八图复核后才接受；失败原记录永久保留。V1/V2 原档未改，历史产物未重做。

## 交付校验

deliverable_manifest.json 给出交付文件的字节数和 SHA256（不含其自身及外层 ZIP）。evidence/handbook_validation.json 是文档/引用/版面校验，不是零件验收证书。ZIP 同时包含 Word、主文、五提示词、规划和模板；QA PNG/PDF 与私有路径配置不交付。
