# FreeCAD 与 DeepSeek Harness 五机批量生产完整交接手册

版本 3.0　核查日期 2026 年 11 月 1 日　流程整合与 210000 新零件五机规划

本版完整保留 v2 的历史生产与看板整合细节，新增第 41 至 48 章五机分工、现有工作盘存、全局计数、阶段扩产及恢复协议。目标为 A 至 E 各 42000、合计 210000 个最终接受且全局去重的新零件，当前只是一项有条件的规划。五份独立启动提示词随包提供。本轮不启动机器、不运行 CAD、不修插件；原 v1/v2 保留。

第 1 至 40 章的“本次核查”指 v2 只读调查快照；第 41 至 48 章追加本轮仓库和本机核查。原始事实和失败未被重写成“已修复”。公开版使用可配置资产根，原证据只给 SHA 和相对定位，脱敏摘要明确标记 public_summary；这些摘要不冒充原私有日志。任何旧提示词中允许执行的操作，均受第 44 至 48 章及新五机提示词的先盘存和门禁要求约束。

这份完整手册把已经完成的 100 个新零件制作流程，与本机真实安装的 DeepSeek Harness CAD 看板、CadKit 引擎和批次调度代码放到同一份交接规范中。推荐复用 DSH 的调度入口、看板和事务发布框架，保留原 FreeCAD 流程的完整构造历史、独立硬审、功能负例与严格去重，通过显式适配层连接。

**整合设计尚未实施。** 本次只读核查源码、配置白名单和已有非敏感状态，生成新文档；没有修改插件、连接模型、提交任务、生成零件、启动服务或改写历史产物。旧 v1/v2 文档保持原样。下文明确区分“已核实源码”“当前磁盘配置”“历史运行证据”和“建议或需实现”，不能把设计方案作为已运行系统使用。

核查发现两个必须先解决的事实：同名 0.5.0 插件存在安装版和签发版分叉；两套 Native 构造与验证协议不兼容。DSH 看板目前主要展示文件快照，按钮生成会话草稿，不能据此声称已经有完整任务状态机、自动模型路由或本地 Qwen 五轮修复。推荐先看第 26 至 30 章掌握现状与统一流程，再按第 31 至 40 章实施；第 1 至 25 章完整保留原 100 新件流程的可复用技术细节与历史边界。

本包是文档和证据索引，不包含完整 CAD 引擎与模型安装资产。完整历史代码和必要 Native 引用的取得方式见第 2 章；DSH 实际代码路径及版本见第 26 章。S 编号定位原工程证据，D 编号定位 DSH 证据，所有路径与哈希在 evidence 中。模板明确标记为建议格式，不能直接当已注册配方或已部署协议执行。

## 1 先读结论与交接范围

1. 历史成果为 100 个新件加 8 个参考件。参考件是 P001 至 P005、IC001 至 IC003；不得算入新增交付数量。
2. 本次只读核对 ZIP、源码、JSON、原采购 PDF 和归档目录。没有生成新件、重开 FreeCAD、启动服务或修改历史产物。历史报告中的 PASS 不等于本次重新运行检查后的 PASS。
3. 当前用户口径是原创正向设计、不采用开源或公开现成零件、内部不重复、保留完整构造历史，交付 FCStd、STEP、八视图、完整所需引用和项目信息；项目信息不含来源 URL、许可证、作者字段。禁止使用开源零件数据不等于禁止使用 FreeCAD 或开源工具库。
4. 原采购 PDF 的 40 条规则和其中 32 条必选项保留原文。原文仍要求来源 URL 等字段；用户覆盖项单独记录，不能篡改 PDF 后声称原采购标准已全通过。
5. 当前队列执行 19 项内部技术检查，再独立去重。它不调用完整采购验收器的最终合规汇总。结构新颖性、原创过程、实际功能、图片可辨识性、图纸版式与买方库存结果有各自边界。
6. 全部零件是名义 CAD 几何。没有完成材料、工艺、配合公差、强度、压力、寿命等验证，`manufacturing_release` 必须保持 false。

推荐阅读顺序：第 2 至 5 章取得资产和环境；第 6 至 9 章理解 recipe 与单件流程；第 10 至 16 章执行验收；第 17 至 22 章处理批次与交付；第 23 至 25 章规划后续模型闭环并交给下一位 agent。40 项规则的完整映射在附录 A。

## 2 历史资产的位置与身份

### 2.1 权威交付包

历史业务根目录 B：

```text
${LEGACY_ARCHIVE_ROOT}
```

完整交付目录 D：

```text
B/final_delivery_100_unique_new_8_references_20261014
```

完整包 `B/FreeCAD_complete_100new_8refs_20261014.zip` 本次重新计算为 909477759 字节，SHA256 为：

```text
7f8523309b576fe78b1bb073a6f48aa29459b270c2a21458db28e488391b4325
```

ZIP 中有 5570 个文件条目；连目录条目共 5821 项。不要把文件数与 ZIP 全部 member 数混用。外层含嵌套 ZIP，仅数外层 `.py` 不能判断源码是否完整。[S01 S02]

### 2.2 运行源码和必需引用的两包结构

以下两个普通 ZIP 位于 `D/standalone_two_ordinary_ZIPs/`，应解压到同一父目录，合并同名根目录 `FreeCAD_standalone_100new_8refs_20261014`：

- `FreeCAD_runtime_source_100new_8refs_complete_under8MB_20261014.zip`，本次确认 5409515 字节、781 个条目，内含入口、生成器、108 个已接收 recipe、3 个另存挂起 recipe、完整图结构历史、采购 PDF 与报告。
- `FreeCAD_required_native_references4_100new_8refs_complete_under8MB_20261014.zip`，包含 EH006、EC006、EV008、EB008 四个完整 Native 引用。这四个是功能接口核验资产，不是“8 个保留参考样例”的另一种叫法，也不是装配体交付能力证明。

这两包不是分卷压缩，也不是增量补丁。源码包不含 FreeCAD 系统二进制，也不包含全部 108 件完整模型。每件完整模型在完整交付包中；用 `summaries/model_library_index_100new_8refs.csv` 或便携根中的 `summaries/model_library_index.csv` 查定位，不要凭文件名猜路径。[S02 S03]

### 2.3 新机器取得原流程后端

**仅 clone 仓库不能运行原 100 件流程。** 此仓库有 CadKit 引擎，但不含完整原 PartDesign 后端。向原资产持有人取得以下两个普通 ZIP，来源位于 `${LEGACY_ARCHIVE_ROOT}/final_delivery_100_unique_new_8_references_20261014/standalone_two_ordinary_ZIPs`，也可从已封存 909477759 字节完整交付包中取出该子目录。没有提供公共下载 URL；不要猜网盘地址或从网络下载同名包。

- `FreeCAD_runtime_source_100new_8refs_complete_under8MB_20261014.zip`：5409515 字节，SHA256 `62ce275859f5798dfeeafe5ff527fe7d36410a873e27fef8cdda0d8da881d73a`。
- `FreeCAD_required_native_references4_100new_8refs_complete_under8MB_20261014.zip`：7003732 字节，SHA256 `139ab4e1a6d70c5724496bedfc7de906ad77eb0a39c7a547bc62c3a716a712fe`。

先用本机 SHA256 工具按字节校验（macOS 可用 `shasum -a 256 文件路径`），再解压到新的独立父目录，合并同名根 `FreeCAD_standalone_100new_8refs_20261014`。不要覆盖已有生产目录。取得完整树后，从便携根运行源码确认的 `python3 portable_workflow.py verify`；该操作只做 manifest/目录核对，不启动 CAD。它依赖 production_goal 和完整 JSON/引用资产，不能从这份文档包直接执行。正式运行另需匹配 FreeCAD/Python/Qt 运行时、显示渲染环境、引用根和可用磁盘；实际版本、路径配置和命令详见主手册第 4、5、8、9 章。历史目标锁定 100，不能直接改数字并声称支持 21 万。

只读清点显示源码包含 144 个 Python 文件、未压缩合计 1071643 字节，包内没有 FCStd/STEP。候选最小迁移范围是入口、production_goal/current_delivery/run_queue、recipe 定义、checks、workflow_source/scripts 与 procurement_acceptance worker/validator，以及经核对必需的配置、recipe 和规则。完整运行还需要 JSON 目录、历史/基线索引和四个 Native 引用。144 个 Python 包含诊断与历史负例，不能随意裁掉检查器；纯 Python 体积不等于可运行最小闭包。本轮只报告此范围，未将其整包复制到公开仓库：全部数据依赖及第三方采购材料的公开传播来源未穷尽核实。保留原包身份和失败证据，后续源码引入应单独审阅，不能把二进制引用或采购 PDF 当用户原创源码发布。

### 2.4 本次核查副本

只读审计说明在：

```text
${LEGACY_AUDIT_ROOT}/FreeCAD_readonly_audit_20261018.txt
```

本手册源码定位使用审计副本 S：

```text
${LEGACY_SOURCE_ROOT}
```

本次将 S 中与源码 ZIP 同路径的 613 个文件逐字节哈希比较，未发现差异；这不等于审计副本包含源码 ZIP 的所有条目。搬迁时以两个完整 ZIP 为交接资产，不要把不完整审计副本当作运行安装包。本次盘点详见 `evidence/fresh_inventory.json`。

## 3 目录结构与文件职责

便携根 P 解压后的关键结构如下，个别补充证据仍需按包内 manifest 查找：

```text
P/
  portable_workflow.py
  package_manifest.json
  README.txt
  archive/history_index.json
  interface_reference_models/
  recorded_status/
  summaries/
  workflow/
    run_queue.py
    current_delivery.py
    production_goal.py
    production_goal_current.json
    user_current_profile_v1.json
    recipe_catalogue.json
    held_recipe_catalogue.json
    reference_index.json
    requirements.txt
    queue_*.json
    *_recipe_definition*.py
    render_selected.py
    checks/
    source_evidence/
    workflow_source/
      scripts/
      procurement_acceptance/
      reference/procurement_requirements.pdf
```

运行中的业务归档与完整交付目录不是同一布局：

```text
B/
  history_index.json
  accepted/<project_id>/
  work/<queue_id>/
    state.json
    events.jsonl
    STOP_REQUESTED                 仅请求停止时出现
    <item_id>_attempt_<n>/
      input.json
      manifest.json
      bound_input.json
      phases.json
      build.log validate.log render.log
      structure.log quality.log views.log
      quality.json views.json
      native/<project_id>/
      run/<project_id>/
  RUNNER_LOCK                      活动控制器持有时出现
  batch_information.csv
  batch_information.json
D/models/100_unique_new/<project_id>/
D/models/8_retained_references/<project_id>/
```

`run_queue.py` 在调用前就会创建部分目录，执行过程中还写入 workflow 根的 `production_goal_progress.json`。因此运行副本与归档根都必须可写；读取封存状态时只用只读动作。原文件中的绝对路径记录用于追溯，不保证搬迁后仍可打开。[S04 S05]

## 4 环境版本与依赖

历史 README 指定 FreeCAD 1.1.1 和 Python 3.11。108 件现有 `metadata.json` 的软件版本均为 1.1.1；历史 Native 报告保留 `App.Version()`。FreeCAD 自带 Python 的精确补丁号、OCCT、Qt、操作系统 build 和编译器完整组合本次未逐一核实，不能编造为可复现锁定环境。[S03 S11]

FreeCAD 侧脚本必须使用能导入 `FreeCAD`、`Part`、`Sketcher`、`TechDraw` 的运行时。历史默认路径为 `/Applications/FreeCAD.app/Contents/Resources/bin/python`。普通 `python3` 不是这一路径的替代品。

队列和绘图侧依赖来自 `workflow/requirements.txt`：

| 包 | 封存要求版本 |
|---|---|
| numpy | 2.4.4 |
| Pillow | 12.2.0 |
| ezdxf | 1.4.4 |
| PyMuPDF | 1.28.2 |
| pypdf | 6.18.1 |
| pydantic | 2.13.4 |
| fonttools | 4.65.0 |

这些是 CAD 工程的封存依赖，不是本手册生成环境的版本。建议新机器单独建立匹配环境，锁定实际安装包与渲染字体；本次没有安装、升级或验证这些 CAD 依赖。不能把“requirements 文件存在”写成“当前机器依赖安装成功”。

队列读取的配置如下，均已从源码核实：

| 配置名 | 用途与默认行为 |
|---|---|
| CAD_AGENT_FREECAD_PYTHON | FreeCAD Python；默认上述 macOS 路径 |
| CAD_AGENT_FREECAD_LIB | FreeCAD lib；默认运行时上两级目录下的 lib |
| CAD_QUEUE_PYTHON | 队列与图片 Python；默认当前 sys.executable |
| CAD_DATA_REPO_ROOT | 脚本资源根；便携入口设置为 workflow/workflow_source |
| CAD_INTERFACE_REFERENCE_ROOT | 四个 Native 接口参考的目录；便携入口设置 |
| CAD_DATA_OUTPUT_DIR | 当前 attempt 的 run 目录；由队列设置 |
| CAD_DATA_PLAN_FILE | 当前 attempt/input.json；由队列设置 |
| CAD_DATA_ATTACH_DEPENDENCIES | 队列固定为 1，启用表达式依赖 |
| CAD_DATA_EDIT_SCOPE | 队列固定为 nominal；完整独立编辑另在 quality 阶段 |
| FREECAD_USER_HOME | attempt 内独立 freecad_profile |
| QT_QPA_PLATFORM | 队列设 offscreen |
| PYTHONPATH | 队列设置 FreeCAD lib 与 Resources/Mod/PartDesign |
| PYTHONDONTWRITEBYTECODE | 便携入口和 CAD 子进程设 1 |

注意 `PYTHONPATH` 在队列子进程环境中被重新赋值，不是无条件追加当前值；新平台目录布局须实际核验。[S04 S05 S06]

## 5 源码入口与安全阅读顺序

整合说明：下面是原工程代码资产。DSH 安装版、仓库版和调度器的分工另见第 26 至 29 章；不能因入口名称相似就替换其执行/验收协议。

1. `portable_workflow.py`：外层包验证、目录清单、支持范围、历史或实时状态、停止、运行和恢复；先处理终止状态、挂起配方、100 件上限、已知 canonical 配方。
2. `run_queue.py`：参数预检、锁、阶段超时、attempt 记录、子进程、19 项检查调用、去重和接收归档。
3. `recipe_catalogue.json`：108 个已接收固定架构；`held_recipe_catalogue.json`：VH001、DP001、CY001，不能混回活动目录。
4. `workflow_source/scripts/build_pilot.py`：草图、特征、依赖表达式、构建、重开、初步 STEP 和投影；同目录 `native_support.py` 处理显示状态及投影曲线。
5. `render_selected.py` 与 `package_pilot.py`：八视图和可选工程图；`drawing_support.py` 提供图形转换支持。
6. `checks/native_internal_inspect.py`：独立重开、输入绑定、特征图、BRep 图、直接 STEP 导出见证、按 recipe 选择功能量规。
7. `checks/native_quality.py`：深层实体检查、最终贡献、独立尺寸编辑和草图关系编辑；`checks/contribution_proof.py` 与 workflow 根下的 `restoration_results.py` 是相关辅助逻辑。
8. `checks/verify_native_views.py`：独立 Native 网格下固定相机全像素复核；`graph_dedup.py`：两类标签图比较。
9. `current_delivery.py`：当前用户口径 19 项检查和净化后的项目元数据；`production_goal.py`：100 件全局上限。
10. `workflow_source/procurement_acceptance/`：原 PDF 的独立 40 项验收器；与当前队列口径分开。
11. `source_evidence/`：设计计划、专门功能协议、故障、有限修订、视觉审查、最终交付证据。对功能或修订有疑问时必须回读对应报告。

较老的 `workflow_source/scripts/run_workflow.py` 支持原型输入和原采购门禁，但不是最终 100 件生产主入口。其旧 Pydantic 输入只覆盖 Pad/Pocket 与矩形、圆、三角形，不能直接表达后续全部 polygon、Groove、datum 和表达式能力。`current_delivery.py` 的 `__main__` 也指向旧样本 `verify_existing()`；不能把直接运行它当作任意新件的命令行验收器。[S05 S07 S08 S09 S10 S11 S12 S13]

## 6 设计任务如何变成 recipe

### 6.1 先定义真实功能和结构差异

历史设计先明确物件名称、用途、工作空间、接口、材料连接、应连通的通道和必须隔离的腔体，再决定构造步骤。结构区别写进 `structural_difference`，并在进入批量前比较已有家族。选择一个新的功能架构，而不是给已有模型改长宽、颜色、镜像、孔径或名称。

每个候选应有功能合同：哪些空间必须空、哪些位置必须有材料、接口方向与名义尺寸是什么、通道怎样连通、哪两路不能串通、是否只是裸壳而非完整装配。合同应说明不能证明的性能，例如只提供快接件壳体，不声称已有密封件或耐压认证。[S14 S15]

原创过程应保存需求来源、设计草案、构造理由与版本链。内部结构去重无法自动证明没有借用世界上任何公开模型；不要把“哈希不同”或“模型说原创”当作来源证据。交付项目元数据不写 URL、许可证、作者，不妨碍内部保留技术审计来源。

### 6.2 recipe 数据结构

已实现 recipe 通常包含 `recipe_id`、`series_id`、`architecture_id`、`structure_requirements`、`functional_description`、`modeling_requirements`、`structural_difference`、`plan`，以及规划用途的 `functional_validation_plan` 等。准确字段以该条 catalogue 为准，不能把规划字段当成验证器已经执行的检查。

`plan` 是构造计划，至少有 `id`、`name`、`name_zh`、`purpose`、`operations`；可含 `revision`、`parameters`、`relations`、`datum_planes`。队列从已登记 recipe 深拷贝 plan，再设置项目 ID 和参数覆盖；队列项中的自然语言描述不会被自动翻译为 CAD 操作。

普通操作的源码字段为：

```json
{
  "id": "Base",
  "type": "pad",
  "plane": "XY",
  "offset_mm": 0,
  "profiles": [
    {"kind": "rectangle", "x": 0, "y": 0,
     "width": 40, "height": 30}
  ],
  "depth_mm": 4,
  "purpose": "Continuous base carrying the declared interfaces",
  "reversed": false
}
```

此示例仅解释 schema，只有一个操作，不满足 20 节点要求，也未登记量规，不能拿来当合格零件。圆使用 `x/y/radius`；三角形或多边形使用 `points`。所有轮廓在草图局部二维坐标中定义；`offset_mm` 是所在基准面的偏置，不是任意三维位姿。

### 6.3 新 recipe 的最小登记范围

后续新增架构至少需要：活动 catalogue 中的新条目、队列的精确结构字段、FAMILIES/ARCHITECTURES 对应元数据、实际输入与 Native 绑定支持、功能探针/路由检查、适用的图去重域以及图纸策略。新 `recipe_id` 未在 catalogue 实现时状态是 NEEDS_RECIPE；只添加名称或描述不能执行。即便能够建出几何，缺少独立功能检查仍不会通过接收。

当前功能检查通过 `PROBES` 与按 ID 分派的专门检查函数实现，不是一个通用插件注册表。后续 agent 要核实实际分派，而不能只写一份 `functional_validation_plan` 就声称功能受检。[S05 S07 S10 S14]

## 7 完整构造历史的建模规范

### 7.1 当前支持的几何域

一个 `PartDesign::Body`；材料特征是 `PartDesign::Pad`、`PartDesign::Pocket`、受限的 `PartDesign::Groove`。草图支持矩形、圆和用线段闭合的三角形/多边形。基准面可用 XY、XZ、YZ，或受支持的 `PartDesign::Plane` 加草图附着；部分结构通过表达式连接位置、长度、命名草图约束和角度。

Groove 只支持 `angle_deg=360`、草图 `V_Axis`、非 reversed，角度设置表达式 `360 deg`。它不是任意角度旋转特征或自由曲面工具。不要自行增加 Loft、任意 Sweep、导入网格或装配体，然后套用当前 PASS 口径。

### 7.2 草图与材料步骤

每个操作创建一个真实 Sketcher 草图和一个原生材料特征。圆有直径与中心约束；矩形有重合、水平、垂直、宽高与位置约束；多边形以重合线段和顶点定位形成闭环。至少一个约束是门槛；实际设计应尽量完整表达设计意图。质量报告会记录 fully_constrained，但当前 `SKETCH_CONSTRAINTS` 这一项本身只要求非空、约束数至少 1、被实际特征使用。

构建阶段每步 recompute，更新 Body.Tip，检查非空、有效、一个 solid、无特征错误。体积必须按意图增加或减少；`abs(delta)<1e-6 mm³` 会被构建器拒绝。全部依赖和参数应用后再次强制重建，逐步体积仍须有贡献。[S08]

不能用空草图、重复切削、不影响最后实体的步骤、容器、原点、线段数、约束数或图纸凑节点。完整历史意味着最终文件里保存可追溯的 Sketch/Feature 链，不是一个导入或冻结实体外面套几个名称。

### 7.3 参数依赖与坐标

与支撑高度关联的草图位置、穿透厚度、同轴孔径和锥座端点应通过表达式关联相同设计量；不要把应该一起变化的尺寸写成无关联常数。表达式示例：`CornerBase.Length`、`RearFlow_Sketch.Constraints.P0_Diameter / 2`。表达式含长度常数时遵守 FreeCAD 单位表达式语法，不能把负标量和长度相加后忽略单位错误。

XY 草图法向为 +Z；源码 XZ 使用绕 X 旋转 90 度，法向与直观命名不一定一致；YZ 使用指定旋转。Pad/Pocket 的 `reversed` 共同决定加料或去料方向。用独立 Native 读回验证真实位置、法向和范围，不仅靠肉眼猜。

参数覆盖仅允许独立草图命名约束或材料特征 Length。表达式驱动尺寸会被拒绝覆盖，应改它的独立驱动。输入计划在构建后根据实际 Native 约束和深度重新读回保存为 `operations.json`；后续绑定用这个执行计划，而不是已过时的最初参数声明。[S08 S10]

### 7.4 保存与显示状态

构建器保存 `model.FCStd`，保留真实 Document.xml 和 BRep。`native_support.save_presentation()` 在缺少 GuiDocument.xml 时补入可见性与相机，解决无界面保存后打开可能空白的表现；它不是重建历史，也不能把哑实体变成参数化模型。改动 GUI 展示状态会改变文件哈希，因此必须在正式质量绑定之前完成。[S09]

## 8 已核实的命令和使用前提

本章命令的参数与动作均在源码中存在，本次没有执行 CAD 生产命令。`$CAD_DOC_P` 表示完整两包合并后的便携根，`$CAD_DOC_PY` 表示配置好的队列 Python；变量名是文档说明，路径必须由接手者填入。不要把尖括号占位符直接运行。

### 8.1 便携只读检查

在便携根执行，或传入入口的绝对路径：

```bash
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" verify
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" support
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" inventory
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" status \
  --queue queue_oc001.json
```

`verify` 验证包 manifest 的字节、配方分区与计数；它使用包内 archive 基线，不是对任意 `--archive` 重做 CAD。`inventory` 读取封存清单，不会自动重新盘点模型。`status` 优先返回目标 archive 的实时 state；没有时返回 recorded_status，必须区分 `LIVE_ARCHIVE_STATE` 与 `RECORDED_DELIVERY_SNAPSHOT`。[S04]

### 8.2 历史运行与恢复接口

下列是真实接口说明，只能在经审查的新运行副本和明确授权批次中使用：

```bash
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" run \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE"
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" stop \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE"
"$CAD_DOC_PY" "$CAD_DOC_P/portable_workflow.py" resume \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE"
```

便携入口将相对 queue 路径解释为 `P/workflow/` 下路径。完成状态不重放；挂起 recipe 不开 CAD；100 件上限返回 `GLOBAL_NEW_TARGET_REACHED`；已知 canonical 配方返回 `KNOWN_CANONICAL_RECIPES_NOT_NEW`。即便换一个空 archive，最终便携基线已满 100，不能借此开始第 101 件。[S04 S06]

直接控制器的源码接口如下。它没有外层所有保护顺序，不能拿它绕过便携入口的已满额、挂起或历史重复检查：

```bash
"$CAD_DOC_PY" "$CAD_DOC_P/workflow/run_queue.py" \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE"
"$CAD_DOC_PY" "$CAD_DOC_P/workflow/run_queue.py" \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE" --status
"$CAD_DOC_PY" "$CAD_DOC_P/workflow/run_queue.py" \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE" --request-stop
"$CAD_DOC_PY" "$CAD_DOC_P/workflow/run_queue.py" \
  --queue "$CAD_DOC_QUEUE" --archive "$CAD_DOC_ARCHIVE" \
  --resume --clear-stop
```

直接控制器在无 history 时初始化为 `reference_index.json`，便携入口则可能复制完整包内 history；两者初始化语义不同。不要用直接控制器的八参考基线漏掉旧 100 件。队列进程退出 0 也不保证每件都通过：必须检查 state、每件 status 和 validation_summary。[S05]

### 8.3 历史集成队列的定位

`workflow/queue_integration.json` 是早期真实接口的集成样例，包含负深度失败、GB001 新构建、同名冲突、改名重复、GB002 缓存复用。它是测试资产，不是下一轮生产清单。原始直接入口是 `run_queue.py --queue ... --archive ...`，但最终封存包及现有历史不能为了演示而重跑；测试应使用隔离的受控副本和明确的回归目的。[S16]

## 9 单件从设计到接收的实际流水线

整合说明：本章六阶段是原后端的已实现流程。统一调度拟调用这些阶段并记录到统一证据契约，不能把整个旧控制器嵌套运行；统一端到端顺序见第 30 章。

### 9.1 预检和 attempt 建立

控制器读取 queue 和 catalogue，检查 queue_id 格式、item_id 唯一、project_id 格式、description/functional_description/structural_difference 非空、series_id 与 structure_requirements 精确等于登记 recipe。modeling_requirements 要求 mm、native_history=true、render_views=8、min_contributing_nodes 至少 20。高于 20 的请求会在接收前再次检查实际计数。

为每件建立 `<item_id>_attempt_<n>`，写输入列表、manifest，并创建 `_work/freecad_profile`。`manifest.json` 用 `probe_id=recipe_id` 把改后的项目 ID 绑定到对应功能检查器。缺 recipe、坏输入、同名目录、已有 claimed_name 都不会成为新件。

### 9.2 六个子进程阶段

以下占位符来自 `QueueRun.generate()` 的真实调用关系。FC 表示 FreeCAD Python，PY 表示队列 Python，WF 表示 workflow，A 表示 attempt，R 表示 A/run，ID 表示项目 ID。运行时环境由队列设置；单独拷贝一条命令不足以重现完整流程。

```text
build
  FC WF/workflow_source/scripts/build_pilot.py build ID
validate
  FC WF/workflow_source/scripts/build_pilot.py validate ID
render
  PY WF/render_selected.py --run R --project ID
structure
  FC WF/checks/native_internal_inspect.py
     --manifest A/manifest.json --input A/bound_input.json
     --output A/native --export-step
quality
  FC WF/checks/native_quality.py --native R/ID/model.FCStd
     --step R/ID/model.step --output A/quality.json --edits
views
  PY WF/checks/verify_native_views.py --manifest A/manifest.json
     --native-root A/native --output A/views.json
```

build 保存 FCStd、requirements、operations、参数、依赖、逐步 trace；validate 在独立打开的文档上检查并生成 STEP、原生网格、投影等初步证据。随后队列将执行后的 operations 写回输入，并生成 `{"mode":"structured","projects":[executed]}` 的 bound_input。

render 用已导出的 Native 网格做八视图，同时决定是否能生成通用二维图。structure 再独立重开 FCStd，创建实际图结构、绑定输入、做功能检查并重新直接导出最终 STEP。quality 读取最终 Native/STEP，做实体、贡献、独立编辑和恢复。views 使用独立 Native 网格重新渲染，逐像素比较实际八图。[S05 S08 S10 S11 S12]

### 9.3 汇总和接收

六阶段成功之后调用 `current_delivery.verify(...)`，产出 metadata 和 validation_summary。19 项全部 PASS 且请求节点数满足之后进入 `archive_part()`；它按历史索引做 canonical 和图结构去重。只有去重通过才复制关键证据、生成 delivery_manifest、临时接收、哈希核对、正式改名、追加历史、刷新批次表。

这一步技术 ACCEPTED 与最终视觉审查完成不是同一个判断。历史后期另有 `fully_qualified_unique_new` 和 `visual_pending_unique_new` 统计；视觉待审占用容量，但未记为完全合格。代码队列没有统一内置人工视觉审批状态机，不能把 source_evidence 的外部复核流程误说成每次自动 run_queue 都会执行。[S05 S17]

## 10 实际十九项检查

整合说明：这 19 项是原工程硬审，不等于 CadKit 的 12 个 validate ID。双方差异与联合门禁见第 33 章，任何转换必须保留原始证据及未支持状态。

下表按 `current_delivery.verify()` 调用顺序列出全部 19 项。检查失败由汇总统一标 FAIL；有些下层返回 UNVERIFIED，但因上层要求 `status == PASS` 仍阻止接收。不能只看 JSON 文件存在或子进程退出正常。[S07]

| 内部检查 ID | 实际判据和证据 |
|---|---|
| NATIVE_FRESH_BINDING | quality、关系编辑、图、输入绑定、STEP 见证、视图报告的 Native SHA 相同；读前后输入不变 |
| PARAMETRIC_HISTORY | 无 imported/frozen 与 unsupported；Native 材料特征数等于 operations 数 |
| INPUT_NATIVE_MATCH | 绑定报告 PASS，所绑定输入文件哈希与当前文件一致 |
| REFERENCES | 一个 Body，无 App::Link；只适用于当前单件模式 |
| STEP_DIRECT_AND_SCHEMA | 直接导出见证 PASS，见证 STEP SHA 匹配，schema 含 214 或 242 |
| GEOMETRY_MATCH | 独立质量和导出见证都满足体积相对差 ≤1e-4、各轴尺寸相对差 ≤1e-3 |
| REBUILD | 无 feature_errors、无检查器 error |
| GEOMETRY_VALID | valid、closed、一个 solid、正体积、最小面面积 >0、OCCT check(True) passed |
| DIMENSION_AND_SKETCH_DIMENSION_EDITS | 全部独立驱动无漏项或重复；每项 0.9975 和 1.0025 都通过且恢复 |
| SKETCH_GEOMETRIC_RELATION_EDITS | 关系编辑汇总 PASS，覆盖的草图集合等于质量报告草图集合 |
| SKETCH_CONSTRAINTS | 存在草图；每个非空、至少一个约束、用于真实材料特征 |
| TIDY | 未观察到 suppressed 和 unused_user_objects |
| UNITS_SCALE | requirements.unit 为 mm；1 ≤ max(size_mm) ≤ 20000 |
| CONTRIBUTING_NODES | candidate_node_count ≥20，且每个材料特征均贡献于最终实体 |
| FUNCTIONAL_COMPONENT | 该 recipe 的功能探针和适用专门检查汇总 PASS |
| EIGHT_NATIVE_RENDER_VIEWS | 实际 PNG、报告视图均为 8；每图 PASS；实际图片和 Native 网格 SHA 相符 |
| FORWARD_HISTORY | 操作和特征一一对应；每步有 purpose；没有导入或冻结对象 |
| METADATA | 必需用户口径字段非空；不写 source_url/license/author_or_origin |
| DELIVERY_NAMES | 文件名符合 ASCII 字母数字下划线连字符和普通扩展名点的正则 |

`DELIVERY_NAMES` 实际循环检查的是文件的 `p.name`，不是每一个目录名；queue/project ID 有另行正则。新交付仍应检查所有相对路径组件，不能声称这一项单独完整覆盖 PDF 所有路径要求。`TIDY` 也没有通用特征名乱码语义检测。这些属于已知覆盖边界，不应暗中包装成更强的能力。

## 11 STEP 往返与阈值的来源

最终 STEP 由 `native_internal_inspect.py --export-step` 从刚重开、强制重建的 Body 直接 `Part.export([body], output)` 得到，写出直接导出见证：Native SHA、STEP SHA、时间、FreeCAD 版本、schema、误差和 `direct_native_export_observed=true`。随后用 `Part.read` 重新读取 STEP 独立比较。质量检查器也独立读取并计算一遍；不是只相信生成器的说法。[S10 S11]

体积差公式为 `abs(Vnative - Vstep) / Vnative`；各轴尺寸差为 `abs(Lnative - Lstep) / Lnative`。这些是相对误差，不能写成绝对误差 0.0001 mm³ 或 0.001 mm。独立检查使用 `optimalBoundingBox(False, False)` 避免渲染三角网格污染设计外形范围。

| 要求 | 来源 | 当前落地与边界 |
|---|---|---|
| 体积差 ≤1e-4 | PDF 2.4 万分之一 | Native/STEP 相对体积误差 |
| 外形尺寸差 ≤1e-3 | PDF 2.4 千分之一 | 每轴包围盒长度相对误差 |
| ≥20 贡献节点 | PDF 2.4 | 内部候选计数；买方管线仍最终裁定 |
| 1 mm 至 20 m | PDF 2.4 | 工程解释为最大轴长在区间内，不是每轴至少 1 mm |
| 8 图且长边 ≥2000 | PDF 2.3 | 工程固定 2000×2000 和确定性相机协议 |
| 每驱动 ±0.25% | 工程诊断协议 | PDF 没规定扰动百分比 |
| 恢复体积差 <1e-6 mm³ | 工程诊断协议 | 不等于生产公差或制造精度 |
| 最终贡献 epsilon 1e-7 mm³ | 工程计数协议 | 区分真实保留贡献；不同于构建阶段 1e-6 |
| 像素允许差异数为 0 | 工程固定渲染协议 | 不是采购方批准的任意渲染器容差 |
| 局部量规 1e-6 或读回 1e-8 | 配方专门协议 | 各函数自己定义，不能泛化为通用壁厚/功能阈值 |

原 PDF、rules.json、rules.lock.json 应共同保留。PDF SHA256 为 `9f7333e75d83b2b4e4b7e22300dd4dee439aca0bfca0a3e4396bd0a3e6d2ae4a`；rules.json 的冻结 SHA256 为 `7bf6072e5d18874d01d1a111e4103a10ac3ef3c07613f678cc7a83f525e7f253`。[S18]

## 12 节点贡献与参数编辑检验

### 12.1 二十节点不等于二十次实体操作

当前算法对每个真实材料特征检查最终保留效果，再将有贡献特征及其使用的不同草图各计一次。因此十个贡献材料特征加十个使用草图可能达到 20；不能将一条草图中的二十条线计为二十节点，也不能一律将 20 节点误解为 20 次 Pad/Pocket。

Pad 的增加材料须在最终实体仍保留，且本步体积增加超过 epsilon。Pocket/Groove 的移除材料须在最终仍为空，且本步体积减少；使用严格的单调减料后缀证明或精确布尔求交计算。后续被完全填回、被完全删掉的步骤不应贡献。布尔异常不能被当作零体积继续计数。[S11 S19]

### 12.2 数值编辑范围

检查器枚举独立材料特征 Length、受支持基准面角度，以及独立的驱动草图尺寸/角度约束；已有表达式驱动的量单独记录，不重复当作独立驱动。每个独立值按原值乘 0.9975 与 1.0025，recompute 后须 valid、closed、一个 solid、正体积、无特征错误，然后恢复原值并全对象 touch 强制重建。

恢复要求无错误、有效、最终体积与名义值差小于 1e-6 mm³，并验证输入文件字节未变。乘法扰动对值为 0 的驱动仍是 0，不能声称它探索了零值附近所有变化。该协议验证有限工程编辑，没有证明任何任意值、任意组合、任意约束改动均能成功。

### 12.3 草图关系编辑

每个草图使用一个支持的关系变换：水平关系换等效平行；原点重合换两个轴上的 PointOnObject；交换 Coincident 的引用方向；轴上点关系换与原点对齐。编辑在事务中完成，随后 abortTransaction，恢复约束签名与体积。仅尺寸约束的草图由数值测试覆盖，报告 applicable=false；没有支持案例则为 UNVERIFIED。

当前输入和下层结果有受控恢复补充路径 `restoration_results.merge`。存在补充时记录 `validation/restoration_repair_binding.json`，不能删除原失败并把补充当作一开始通过。接手者要核对同一 Native SHA、原报告和修复证据的一致性。[S07 S11 S19]

## 13 功能检查与负例

### 13.1 功能检查必须独立于外观

通孔看起来存在不表示通道真连通；两个独立孔也可能在实体内部意外串通。历史实现使用实料/空腔点探针、完整功能包络、真实材料见证、通道网络与实际被移除空间的分组，部分 recipe 读取真实面的轴线、法向、尺寸和基准依赖。

检查空腔量规时先确认量规本身有效、连通且体积为正，再核对它与实体相交的材料体积。检查材料见证时判断本该保留的支撑、隔壁、根部是否缺料。通道隔离应结合实际 stock 与最终形状，不把原本就在空气里的工具重叠误判成内部泄漏。具体 stock 定义只在对应配方的适用结构内有效，不是任意加减料顺序都可以照搬。[S10 S20 S21]

### 13.2 每个新架构应保留的负例

以下是接手时建议沿用的检查设计原则，历史多个专门 recipe 已有相应实例，但并非所有 108 件都具有同样数量或类型的负例：

1. 往应畅通的通道或装配包络加入真实相连材料，检查必须拒绝阻挡。
2. 切掉支撑根部、隔板或锁止唇，材料见证必须拒绝缺料。
3. 将本应隔离的两路通过真实材料打通，空腔分组或隔离检查必须拒绝泄漏。
4. 改错接口方向、轴线或断开基准附着，应被实际读回和依赖检查拒绝。
5. 检查负例实体本身有效，避免只是因坏几何而“碰巧失败”。负例在内存或专用副本执行，不保存覆盖交付 FCStd。

负例的合格状态是 `PASS_EXPECTED_REJECTION`：错误构型被正确拒绝，不是错误构型被放行。新架构没有适用量规时保留未核验状态，不能让生成模型自己填写一个 PASS 文件。[S20 S21 S22]

### 13.3 真实故障与修订经验

**P001 肋板挡孔。** 原型支架曾出现肋板侵入安装接口空间的历史问题；最终 build_pilot 中 LeftRib/RightRib 位于 x=24/90，purpose 明确保留后安装包络。接手者要同时审查加筋与安装孔、工具进入空间；有孔和有肋板分别正确，不代表装配接口畅通。原始逐次故障细节应继续查旧工程 revision_contract，不能只根据最终造型推导失败次数。[S08 S23]

**TC001 空气中的刀具重叠。** 原 front/neighbours 工具交集为 735.1326809400116 mm³；限制到真实 stock 内后为 0。修订了这一对检查的适用范围，保留原始交集、11 组实际移除空腔隔离、完整包络和材料/负例。虽然数值门槛没变，语义适用范围发生了变化，必须如实披露，不能只说“阈值未改所以完全没改规则”。[S20]

**RC001 检查器继续执行。** 原 API 错误与后续隔离失败均保留；同一 Native 完成两次结构读回，没有新增 Native 构建。增加真实误钻负例：左闭合螺钉通道被打通至夹持开口，实际空腔从期望 6 组变 5 组，正确拒绝。最终 STEP 来自相同 Native 副本直接导出，原生成器 STEP 同时保留。[S21]

**RF001 量规自身断开。** 原键轴包络 valid 但有 2 个 solid，实体干涉为 0 仍失败。修订量规 x 起点 2.7→2.3 mm、宽度 1.4→1.8 mm，外边界仍 4.1 mm；补上诊断量规自身断口并增加真实空腔覆盖，不放宽单实体或无干涉条件。名义 CAD 计划未变，原失败保留。[S20]

**CA001 材料量规穿过设计空腔。** 原支撑量规在 U 形通道处缺 2.56 mm³，不是零件必然缺料；将下部量规路线移至实际通道后方的实体材料，并保留原活塞阻塞、真实串腔负例，增加真实后桥材料切除负例。仍要求材料量规一体和缺料 ≤1e-6。模型几何不变，但量规定义改变须披露。[S20]

**QC001 参数依赖缺失。** 锥座内半径端点独立变化产生第二实体。修订将两个端点关联到后部流道半径；原独立驱动 63 个，预期修订后 61 个，全部剩余独立驱动仍要重新受检。不能删掉失败测试而不声明尺寸变成从属量的设计理由。[S22]

**HS001 深度与内边缘依赖。** 历史记录包含 SelectorBody/UpperFlange 加 0.25%、LowerInput/UpperInput 减 0.25% 的失败，以及锥座端点加 0.25% 导致 2 solid 的诊断。修订输入深度/草图平面表达式并登记后续有效 recipe；最终以 accepted catalogue 为准，不要用修订提案 JSON 中 `FIXED_RECIPE_NOT_NATIVE_VALIDATED` 状态冒充最终通过。[S24]

**挂起处理。** VH001、DP001、CY001 未计入最终 100 件。CY 的真实 stock-minus-final 布尔问题等保留在失败证据里；最后 OC001 补足至 100。不能自动改宽容差、换低级几何测试、删除失败历史或重用挂起件名称冲数量。[S02 S25]

## 14 内部去重如何工作

整合说明：本章双图去重不能被 DSH 几何不变量指纹替代。完整历史多来源接入、SQLite 优先于 JSON 的缺口和统一计数见第 34 章。

历史索引含 8 条 reference 和 100 条 accepted，按 canonical_recipe_id 去重计数。遇同 canonical ID 直接 DUPLICATE，尺寸变体、镜像、重命名和重放不得另计。

新候选与当时此前全部 index 条目顺序比较：

- 特征图记录真实 Pad/Pocket/full360Groove、草图轮廓边类型、特征顺序和相对平行/垂直/斜交关系；去掉数值尺寸、名称和绝对方向。
- BRep 图记录标准化后面、边、顶点及其关联和解析类型；去掉数值几何与方向。当前支持面包括 Plane、Cylinder、Cone、受限规则环面 Toroid，边包括对应 Line/Circle/Ellipse/Hyperbola/BSplineCurve；这不意味着任意自由曲面模型受支持。
- 任一图同构就 HOLD_SAME_STRUCTURE；两图都证实不同才 PASS_DISTINCT_NATIVE_STRUCTURE。哈希用于绑定身份，不直接证明新颖性。
- 精确搜索最多 200000 状态或 10 秒；超时/不支持域为 UNVERIFIED，不放行。某些确实不同的设计也可能因保守图定义被挂起。

100 个新件共 5750 次历史比较，等于 `100×8 + 100×99/2`。首件比较 8 个参考；最后 OC001 比较 107 个此前模型。八参考之间没有包含在新增件的这组比较中。因此不能声称 108 件所有两两组合都穷举，也没有买方全部历史库存或公开模型数据库的比对证据。[S13 S26]

换新批次时必须延续旧 108 件图/BRep 证据，并追加新接收结果；不能只使用旧 reference_index 中的八件。若只调整图支持域，应保留原 UNVERIFIED、明确新增解析类型、验证尺寸和镜像重复仍被挡住；TB001 的环面支持补充就是针对检查域，不是新增模型生成。[S17]

## 15 八视图与二维工程图

### 15.1 八视图协议

固定八个 PNG 名称：front、back、left、right、top、bottom、iso_upper_45、iso_lower_45。尺寸 2000×2000，白底灰色、正交投影、无坐标轴网格水印、统一缩放；比例为 2000×0.8 除以网格包围盒对角线长度。[S12 S27]

| 文件名 | 相机方向向量 |
|---|---|
| front | 0 −1 0 |
| back | 0 1 0 |
| left | −1 0 0 |
| right | 1 0 0 |
| top | 0 0 1 |
| bottom | 0 0 −1 |
| iso_upper_45 | 1 −1 √2 |
| iso_lower_45 | 1 −1 −√2 |

验证器从独立重开的 BRep 用 0.10 mm tessellation 得到网格，重算中心、相机、光照、深度与像素；每张允许不同像素数为 0，并绑定图片与网格 SHA。quality.json 另有 0.18 mm 网格，不是固定视图复核采用的网格，不要混用。

这里的 ±45° 是固定工程解释。原采购 PDF 同时用了“45 度俯仰”和“等轴测”的措辞，严格等轴测定义的歧义仍由采购方确认，不能用内部像素 PASS 消除歧义。八图精确一致也不代替真人确认零件名、用途和细节可辨识性。

### 15.2 二维图实际生成策略

`render_selected.py` 在操作数 ≤20 且没有 Groove 时调用通用 `render_drawings(spec)`；否则写 `NOT_GENERATED_OPTIONAL`，需要专门可读布局。历史后期有单件补图和局部标注修订，不等于通用队列自动完成所有二维图。[S27]

图纸以 Native TechDraw 隐线投影和名义特征表为来源，通过 ezdxf 生成 DXF，再转 PDF，两个单页汇成 `drawing.pdf`。每个 DXF 保存后重开 audit，要求 errors=0；核对 PDF 页数，生成 preview。通常第一页投影、关键尺寸和接口，第二页完整特征/尺寸表。Groove 的轴与 360 度、长特征列表、斜面和复杂遮挡需要专门布局。

现有 93 套有 drawing.pdf 和 DXF：85 新件、8 参考。无二维图的 15 个新件是 BC001、BL001、CL001、CV001、EB001、EB002、EB006、EB008、EC006、EH006、EH008、EV008、JC001、VE001、VP001。PDF 第三章把图纸列为加分项，当前 19 项门禁不要求二维图齐全。[S01 S02]

DXF audit_errors=0 只能证明该库读取时未报告其支持的错误，不证明其他 CAD 已经编辑、保存和重开。历史 `downstream_cad_edit_save_reopen` 为 not_run。某些 PDF 文字已轮廓化，文本提取为空不代表没有标注，必须逐页看实际渲染。

### 15.3 人工视觉复核

看完整八图和每一页最终 PDF，核对没有空白、遮挡、截断、相互重叠、标注指错面、重复尺寸、特征遗漏、尺寸表超页或文字小到不可读。查图纸数值与 Native 实测及 operations 一致，查用途与结构一致，检查接口完整和没有为了凑节点堆无意义几何。

记录审查时间、方法、文件名和 Native/STEP/PNG/PDF 哈希、问题和修订说明。仅改图纸布局时先保护 Native/STEP/19 项报告和历史哈希，保留旧图，再重生成图纸、逐页复查、重绑清单。不能把图纸修订记成新零件，也不能未经渲染查看就标视觉 PASS。[S17 S28]

## 16 OC001 单件完整示例

OC001 是最后补位的新件，三个相互正交的 20×20 名义检具凹座，各有独立法向探孔，三面名义背板各 3 mm，共同角根加双地脚。不是含面板、探针或被检件的装配体，也没有校准正交度/平面度/重复精度证书。

实际配方共 13 操作：CornerBase、XDatumWall、YDatumWall、GroundFeet 四个 Pad；XPanelSeat、YPanelSeat、ZPanelSeat、XNormalProbe、YNormalProbe、ZNormalProbe、FootAxes、FootSeats、UnderLocator 九个 Pocket。每步有草图，历史报告计 26 贡献节点，60 独立驱动、120 次数值扰动、13 草图关系检查、107 次此前结构比较。[S14 S15 S28]

其依赖把竖墙、Z 座和地脚孔草图高度绑定到 CornerBase.Length，把 X/Y 探孔起点与长度绑定到对应墙的实际位置/厚度。功能核验关注三座及三个各自法向通道、三面背板、共同角根与地脚；负例包括堵 X 探孔和削去 Y 座背板。

`queue_oc001.json` 的实际超时预算为 build 240 秒、validate 240、render 180、structure 300、quality 1200、views 180，max_attempts=1、target_count=1。这不是所有模型的性能承诺，只是该历史任务的上限。已有最终两页图纸与逐页视觉证据；本次没有重新生成它。[S15 S28]

## 17 批处理控制与数量边界

### 17.1 队列字段的实际效力

队列核心字段是 `queue_id`、`target_count`、`max_attempts`、`timeouts_seconds`、`items`。每项包含 item_id、project_id、recipe_id、description、functional_description、structural_difference、series_id、structure_requirements、modeling_requirements、parameters，可有 use_verified_cache。

历史 JSON 还出现 `one_candidate_only`、`no_alternate_candidate_on_failure`、`global_target_unique_new`、`production_policy`、`expected_test_outcome` 等描述字段；当前控制器没有逐一读取执行它们。真正的全局政策来自 `production_goal.py` 和固定 policy 文件。接手者不能仅在 JSON 多写字段就声称行为已受程序约束。[S05 S06 S15 S16]

### 17.2 目标计数

`accepted_unique_new()` 对 kind=accepted 的 canonical 身份去重计数。reference、失败、重复、别名、参数变体不加新件数。队列目标会被全局剩余额度裁剪，恢复时加上本队列已接收数，避免重复启动多余件。

历史运行验证记录覆盖：99 件时只允许一个合格新增，达到 100 后 run/resume 不启动 Native；已有 100 件即使队列请求 500 也生成 0；已有完成队列返回 terminal；CY/VH/DP 挂起生成 0；封存文件和图/BRep 历史保留。99→100 控制器测试使用 mocked generation/archive，并非另外真实生成若干测试件。文档应分别标注模拟边界测试和真实历史完成状态。[S29]

### 17.3 技术状态与交付状态

控制器终态含 ACCEPTED、FAILED、DUPLICATE、NAME_COLLISION、NEEDS_RECIPE；队列终态可能是 TARGET_REACHED、QUEUE_EXHAUSTED、GLOBAL_NEW_TARGET_REACHED。STOPPED/INTERRUPTED 表示可调查后续跑。

业务上还需分清：设计完成、Native 已生成、技术检查通过、内部去重通过、视觉待审、完全合格、归档完整、已打包/已交付。历史后期报告维护视觉待审占容量和完全合格两套统计；最终为完全合格 100、视觉待审 0。接手实现应保留这些状态差异，不能将文件复制完成等同所有审查完成。

## 18 断点续跑与停止

队列获取 archive/RUNNER_LOCK，使用独占创建防并发；已有 PID 存活则拒绝另一个控制器，已退出才移除旧锁。发现锁异常时先核实 owner 与进程状态，不能为赶进度直接删锁。状态文件用临时文件替换写入，events.jsonl 追加阶段事件。[S05]

每阶段在 `phases.json` 记录 command_key、COMPLETE、输出哈希。command_key 含命令参数、queue_hash 与命令中直接 `.py` 脚本哈希。只有匹配且已登记输出仍存在、哈希相同才复用。

这里有需要主动管理的边界：没有递归哈希所有被 import 的模块和环境；首轮 render 在图目录尚不存在时，传入的 outputs 可能是空列表；build/structure 也只登记部分关键输出。因此“阶段可复用”不代表完整构建依赖缓存正确。源码、依赖、配置或辅助输出改变后，应新建明确 revision/queue_id 并重新验证受影响阶段，不能仅靠旧 COMPLETE 自动跳过。最终 19 项和文件清单仍必须绑定当前实际文件。

停止可用便携 stop 或直接 `--request-stop`，它写 STOP_REQUESTED。运行器在循环检查停止/超时，向子进程组 SIGTERM，最多等待 10 秒再 SIGKILL。默认阶段预算 600 秒，允许 1 至 1200 秒；信号 SIGINT/SIGTERM 也设置停止标记。

恢复前依次确认：历史 accepted 字节完整；state 的 queue_hash 等于原 queue；没有其他 runner；STOP 是有意请求而非不明中断；当前 attempt 目录与 last_attempt 一致；没有 `_ACCEPTING_...` 残留。队列内容改变必须使用新 queue_id。现存 terminal item 会被跳过，不能靠 resume 重新修复 FAILED；应另建有限修订任务并保留旧记录。

`reconcile_archives()` 核对 accepted 清单并修复“归档已改名、索引/状态尚未保存”的窄窗口。它不是通用数据库事务；发现 `_ACCEPTING_...` 或未记录 attempt 要人工检查，不会随意覆盖。其补写索引路径不一定恢复全部 canonical 元数据，非默认 project_id 的修复需要显式检查，不能静默把别名当新身份。

## 19 失败分类与真实重试机制

整合说明：本章描述历史重试。统一设计预算、DSH 阶段重跑及 probe 窗口恢复必须分别计数，实际恢复缺口和推荐协议见第 35 章。

| 失败类别 | 先读的证据 | 正确处理 |
|---|---|---|
| 预检或 NEEDS_RECIPE | state reason、queue、catalogue | 修正结构合同或实现新 recipe；没有 Native 生成不算建模失败 |
| 构建失败 | build.log、dependency_error.json、trace | 定位断实体、方向、空切削、单位/表达式错误；保留原 attempt |
| STEP 或绑定失败 | direct_step_export_witness、input_native_binding | 确认同一文件和导出来源，不使用陈旧 STEP |
| 实体或贡献失败 | quality.json、OCCT、feature list | 不降低实体/节点门槛；修模型或停件 |
| 编辑恢复失败 | finite_edit_test、关系编辑、恢复补充 | 区分真正独立量与设计从属量；保留原失败并重检 |
| 功能失败 | functional_interface_probes、量规与负例 | 区分模型错误和量规错误；修适用范围必须披露 |
| 图结构重复或未支持 | dedup_result、graphs、history | 重复不计数；不支持/超时保持未核验 |
| 视图失败 | views.json、实际 PNG、mesh SHA | 查尺寸、相机、网格、重渲染；不改像素容差掩盖问题 |
| 超时或停止 | events、阶段 log、state | 查资源/算法和停止原因，按原队列恢复或单独修订 |
| 归档完整性失败 | delivery_manifest、临时目录、index | 停止接收，保留全部现场，不能覆盖旧合格件 |
| 仅图纸问题 | drawing_checks、逐页预览、审查记录 | 保护 Native/STEP，修图重审重绑清单，不新增计数 |

最终 `run_queue.py` 的 max_attempts 只允许 1 至 2，失败后再次执行同一个 spec；没有模型推理和改图调用。某些队列显式只允许 1 次。人工/会话诊断后另建 bounded_revision 或 dependency_repair 队列是另一条修订链，必须连同原先尝试数统计，不能每换 queue_id 就把预算清零。

旧 `run_workflow.py` 最多 3 次的原型修复仅对 P001 至 P005 等已知依赖模式适用，不代表最终队列支持任意生成式修复。Qwen 五轮闭环不能通过把 max_attempts 从 2 改成 5 获得。[S05 S13 S20 S22 S24]

## 20 归档与最终打包

整合说明：新系统拟复用 DSH 事务发布框架，但其现有字段和参数表门禁不能直接接收全部旧模型；发布适配和元数据覆盖见第 32、36 章。

### 20.1 单件接收的原子边界

通过 19 项与去重后，复制 native_graphs、input_native_binding、direct_step_export_witness、functional_interface_probes 到单件 validation，保存 executed_plan，更新 queue/item/结构差异元数据；枚举交付文件生成 SHA 清单。复制到 `accepted/_ACCEPTING_<ID>`，逐文件哈希检查，再 rename 为 `accepted/<ID>`，最后追加 history、刷新 batch 表。[S05]

清单在生成时排除 drawing_preview，但应明确清单是否包括全部正式文件。清单本身不能包含自身的稳定哈希；完整外层交付可以另作总清单。任何后续图纸/metadata 改动都需更新相关 manifest，不能保留过时哈希宣称完整。

### 20.2 每件交付最低目录

```text
<project_id>/
  model.FCStd
  model.step
  requirements.json
  operations.json
  metadata.json
  validation_summary.json
  dedup_result.json
  delivery_manifest.json
  views/<八个固定名称>.png
  validation/
    native_graphs.json
    input_native_binding.json
    direct_step_export_witness.json
    functional_interface_probes.json
    executed_plan.json
  evidence/                       构建、参数、依赖、投影等
  drawing.pdf                     若已生成或本批次明确必需
  drawing_sheet_1.dxf
  drawing_sheet_2.dxf
```

完整交接还要提供质量/编辑报告、阶段日志和视觉记录的可达位置；某些原报告在 work/attempt 下，不能只带模型目录就丢掉它们。外部引用若存在必须齐全，但当前单 Body 无 App::Link 的门禁没有实现通用装配体引用遍历验收。

### 20.3 批次级交付

新增件与参考件分目录；提供 CSV/JSON 批次表、项目名称/软件/版本/mm/零件数/特征数/描述/family/canonical/结构差异、总量与未通过清单、全部文件哈希、原失败与修订记录、当前规则版本与用户覆盖说明。数值范围、制造边界、缺少图纸须明确。

历史完整交付将源码、四 Native 引用、单件包、模型目录、报告和索引组合归档。`source_evidence/package_final100_standalone.py` 是当时封存专用脚本，含历史路径和规模，不是新批次通用打包 CLI。不要直接运行它重写旧包；新批次用独立 staging 目录，清单驱动打包、解包校验、核对所有引用，再移交。[S02 S30]

8 MB 普通包限制是历史交付通道的包装策略，不是几何或采购质量门槛。超过大小时不得裁去必要 Native 引用、BRep 图字段或失败证据而仍称完整；按完整资产分包并给合并说明，不用丢字段换“成功”。

## 21 移植到新机器的检查表

1. 取得两个完整普通 ZIP 和完整模型/历史资产，验证哈希与合并根；保留原包只读备份。
2. 新建独立运行工程和 archive，不能指向历史 B 或 D。先执行包 verify/support/inventory，只把它们当只读基线核验。
3. 核实 FreeCAD 1.1.1 的 Python/lib/Mod 路径、CPU 架构、模块导入、offscreen 能力和依赖安装版本；不假设 macOS 布局在 Windows/Linux 相同。
4. 配置 CAD_QUEUE_PYTHON、CAD_AGENT_FREECAD_PYTHON/LIB、CAD_DATA_REPO_ROOT、CAD_INTERFACE_REFERENCE_ROOT。确认四个完整 Native 引用能定位；检查旧绝对路径在新环境的重定位办法。
5. 保留完整 108 canonical 去重基线。把“历史去重库”和“新批次配额”分开设计，避免旧 accepted=100 直接把新批次算满，或为启动而漏掉旧历史。
6. 在新代码中显式版本化批次目标、停止条件、挂起集和规则；不修改旧 production_goal.py 或封存 manifest。本手册没有提供已实现的新批次适配器，接手者必须完成此改造并验证。
7. 用隔离的已知正负样例验证构造、STEP、编辑恢复、图去重、八视图、停止/恢复、同名冲突、超时与接收窗口。完整负例库随源码查找，不能只验证一个容易的模型。
8. 确认字体和渲染依赖一致；固定协议全像素检查对数值/渲染差异敏感。差异应定位，不能自动放宽容差。
9. 先做一个新架构的完整闭环并人工复核，再扩大队列。新量规与模型实现分开审查，模型生成方不得修改独立验收器。
10. 记录运行版本、源码 SHA、规则 SHA、环境指纹、批次参数、初始历史快照及每次 revision；确认输出目录可写且只作用于新工程。

没有核实的移植项包括 Windows/Linux 实际重放、FreeCAD 新版本兼容、任意多 Body/装配体、通用自由曲面、通用图纸排版，以及本地模型 endpoint 和视觉能力。不要把它们写入完成清单。

## 22 一件和一批的验收清单

### 22.1 单件完成前

- 原创正向设计过程有记录，功能与名称一致，结构区别不是尺寸/镜像/名称变化。
- recipe 在当前支持域，独立功能合同、量规、负例登记；没有未知能力默认为通过。
- 单 Body、完整真实历史、每个草图被使用、每个材料特征最终保留贡献，节点至少 20。
- 六阶段输出可追溯，19 项全部 PASS，Native/STEP/输入/视图证据哈希一致。
- 独立尺寸试验和关系编辑有完整覆盖与恢复，原输入字节未变。
- STEP 是最终 Native 的直接导出，schema 与相对误差合格。
- 与全部已有 canonical 历史比较，未出现 DUPLICATE 或 UNVERIFIED；数量更新只有一次。
- 八张实际图片逐张可读；适用二维图每页复核，标注、比例与实际模型一致。
- metadata 没有用户排除的三个字段；完整引用、清单与日志可达。
- 原失败/修订记录保留；只有经过实际检查的版本标记通过；manufacturing_release=false。

### 22.2 批次交付前

- 新增、参考、挂起、失败、重复、视觉待审、完全合格和已交付数量分别一致。
- 旧件哈希不变，无活动控制器/子进程/锁；达到目标后不会启动下一个候选。
- 新增历史和批次表覆盖所有正式接收件；没有临时接收目录混入交付。
- 包内源码、运行配置、原采购规则、用户覆盖、依赖、索引、必要引用齐全。
- 总包和各普通子包有字节数和 SHA；实际解包核对文件清单及所有引用。
- 可选图纸缺口明确；必需图纸若被新任务要求则作为新门禁，不能沿用历史可选口径漏交。
- 技术通过、模型审查、人工视觉、买方验收和制造放行分别记录，不互相替代。

## 23 Qwen 五轮闭环的后续需求

整合说明：本章保留后续 Qwen 需求，不是已实现功能。每次推理任务按难度路由到自有 API、DeepSeek 官方 API 或 Codex 的需求见第 29 章；二者需在 provider 配置中协调。

这一章是待实现设计，不是历史功能。用户后来希望本地 Qwen 产生方案，脚本硬审，不合格反馈修复，最多重试五次，再经人工复核。模型服务器地址、准确模型名、协议、鉴权、超时、是否能看图，以及第二层模型审查能力，本次均未读取或接通。

建议模型只产出受限结构化计划，不执行任意 Python。新增适配层验证 schema、支持域、单位和资源预算，再调用固定执行器。硬审不能由模型改写为通过；模型也不能修改检查器、降低阈值、清空历史或删除失败。

建议状态顺序为 GENERATE → BUILD → HARD_CHECK → REVIEW → PACKAGE → ACCEPTED；失败进入带明确 rule_id、实测值、阈值、文件哈希与日志定位的 REPAIR。第二层模型和人工视觉可提出修订，但必须绑定同一 Native/STEP/图片/PDF 版本。文本模型不能声称看过图片。

五次预算的建议明确为“包含第一轮在内，总生成/修订尝试最多 5 次”；这与自然语言“最多重试五次”可能有计数歧义，新实现应把语义写进配置和任务合同。第五次仍不合格则 SKIPPED，保留全部结果，继续其他候选。传输重连、读取进程状态、只读诊断不应伪装成新建模成功；真正改计划或重新生成须有新的 revision 哈希并消耗规定预算。

必须新增的实现包括：本地模型适配器、统一扩展 schema、独立功能合同/量规审核、修订预算跨队列追踪、人工复核状态、全文件证据绑定、新批次配额与累计历史分离。只改 max_attempts 或向旧 queue 写五轮字段不够。[S31]

## 24 原工作流接手提示词

本章保留原工作流提示词用于理解历史后端；整合任务优先使用第 40 章的完整统一提示词。下面的提示词用于启动下一批开发和生产。填好数量、路径、图纸要求与模型配置；没有明确生产授权时先完成新工程改造和可审查方案，不触碰旧批次。

```text
请先完整阅读 FreeCAD_Fleet_Integrated_Handbook.zh.md，
并核查 evidence/source_index.json、fresh_inventory.json 和完整源码资产。
目标：在新的工程与归档目录中制作 [数量] 个结构独立的新零件。
历史 100 新件加 8 参考件作为只读去重基线，不能重新计数。
旧 B/D、完整 ZIP、历史 state/events/Native/STEP 均不得修改。

使用固定可审查 recipe 与 FreeCAD 原生构造历史。
先实现独立新批次政策，将配额和累计去重历史分开。
不得通过清空历史、绕开便携保护或修改旧 100 件 cap 启动新生产。
每个候选先给功能合同、结构差异、构造计划和独立检查方案。
只在已支持的 Pad/Pocket/full360 V_Axis Groove 等域内执行；
超出支持域先报告并实现/审查检查器，不得默认为通过。

交付 FCStd、直接导出 AP214/AP242 STEP、八张 2000×2000
白底灰色固定视图、完整所需引用、项目元数据、批次表和哈希证据。
元数据不含 URL、许可证、作者。零件必须原创正向设计，
不使用公开现成/开源零件，不靠尺寸、缩放、镜像、改名凑数量。
二维 PDF/DXF 本批次要求：[必需或可选]。

19 项硬审、独立参数编辑恢复和全历史图去重须全部通过。
20 节点指有最终贡献的草图和特征，不是约束数或线段数。
功能检查包括真实材料/空腔、接口、连通/隔离和真实阻挡/泄漏负例。
修改检查适用范围必须披露，保留原失败并证明真正错误仍被拒绝。
不得降低门槛、删失败、让模型修改独立验收器或伪造 PASS。
每件技术通过后查看实际八图及每页图纸，再计完全合格。
保持制造放行 false，分别记录技术/视觉/买方验收状态。

如果使用本地 Qwen，先核实 endpoint/model/protocol 与视觉能力；
该闭环尚未实现，先开发适配器和受限 schema，再生产。
总尝试预算按本批次合同明确：[含初次共5次或其他明确规则]。
失败给规则ID、实测值、证据路径和修订原因；超过预算 SKIPPED。
所有 revision 有独立哈希，旧结果不可覆盖，失败不计新增。

先完成一个新架构的端到端样件与人工复核再扩大批次。
每批汇报候选/技术通过/待视觉/完全合格/挂起/失败/重复数量，
达到目标立即停止。完成后打包、解包验证、核对旧件 hash，
报告完整交付绝对路径、字节数、SHA、缺口和未核实能力。
```

## 25 原工程核查结论与已知限制

本次确认完整包 SHA/字节数、源码副本身份、108 件归档必需文件、历史 19/19 状态、864 个 PNG 文件、93 套图纸、5750 条新件历史比较记录，以及最终源码中的命令和阈值。没有重新执行 CAD 或验证每个旧模型在当前机器的新运行结果；未重算 5750 次图同构，也未做新的 108 件逐图视觉验收。

历史 profile 的 implementation_status 仍有早期 20 配方/12 新件说明，不能用它覆盖 accepted108 的真实目录和 history 计数。历史 final reports 中的全通过描述仅指各自口径；完整原 PDF 的 32 必选项仍有外部事实、用户覆盖与工程有限验证之间的缺口。

下一位 agent 可以直接复用这里的资产定位、命令接口、受支持建模方式、故障处理和验收清单；“继续生成下一批”需要在新工程完成批次政策和新架构登记。本文没有启动该改造或生产，也没有把 Qwen 服务器能力写成已完成。

## 26 DeepSeek Harness CAD 插件定位与证据

### 26.1 本次真正核查的是哪个插件

本机已安装的是 `@local/dsh-plugin-cad-freecad`，package.json 声明 0.5.0。桌面 profile 的依赖为 link，解析到 W 下的 `dsh-plugin-cad-freecad`。W 表示 `${DSH_WORKSPACE_ROOT}`；P 表示 `W/dsh-plugin-cad-freecad`；C 表示 `W/cad`；H 表示 `W/handoff`。它不同于此前讨论的 `@rjr/dsh-task-router`。本次没有把通用难度路由插件当作 CAD 看板，也没有读取凭据文件。[D01 D21]

DeepSeek Harness 应用包的版本字段是 `0.2.1-rc.5`；FreeCAD 应用包版本字段是 `1.1.1`。这是本机磁盘安装信息，不证明当前驻留进程已经加载这一版。profile 的 CAD 配置指定 FreeCAD.app 下的 freecadcmd、C 为 cadRoot、单进程 2048 MB、内存保留 768 MB、探针至多 2 个 worker、进程组 6144 MB、采样间隔 2000 ms。未读取其他服务的模型凭据；本次没有启动应用、调用 CAD 工具或新模型推理。[D21]

H 是包含批量调度、跨机收货与教训库的配套仓库，其 Git HEAD 为 `d56486c23e4f261a8f5343ceb024c1e8f49d5187`。`W/cad-repo` 也存在，但本次 rev-parse 返回 128，不能据其目录名认定为当前权威实现。接手者应使用已定位的 H 和实际安装的 P，并核对哈希，而不是在名称相近的目录之间混用。

### 26.2 同一个 0.5.0 实际存在两个引擎版本

| 资产 | 实际版本身份 | 已核实含义 |
|---|---|---|
| 桌面 link 指向的 P | rules_sha 64a95367ef997904 | 当前安装目标的 Python 引擎 |
| H/plugin 内的副本 | rules_sha 1d835a8f3d2ef4f1 | fleet_runner 默认优先发现的引擎 |
| H/shared/versions.json | 签发 1d835a8f3d2ef4f1 | 多机交接当前签发口径 |
| H/releases 内 0.5.0 tgz | SHA256 2c20ed9df517259616d3b22e4e8ce382ce491d7fef9e81adfdc3974b9f19a872 | 与签发值相同；23 个可比较包文件与 H 副本一致 |

两份源码至少有 INSTALL.md、index.js、packager.py、probe.py 四个文件不同。桌面版本包含 N43 所有权读取异常时保留现有交付的处理，以及 N44 跨探针窗口 FAIL 优先合并；仓库副本还保留旧的 results.update 合并。仅看 npm 版本 0.5.0 或入口文件存在无法识别这个分叉。[D06 D08 D16 D20 D21]

`rules_sha()` 是 cadkit 各 `.py` 文件按名称排序后的名称和字节，加 entry.py，取 SHA256 前 16 位；它不覆盖 JS Host、看板、fleet_runner、模型族脚本、配置、采购 PDF 和依赖环境。统一版本清单必须另外保存这些文件的完整 SHA256，不把一个短 rules_sha 当成完整供应链身份。[D10]

### 26.3 当前状态与历史运行证据

只读盘点得到：C 下有 14 个项目；看板快照也列 14 项、40 条最近动作、3 个交付目录批次，最后动作是 memcheck。复制 SQLite 主库及 WAL 到临时目录后查询，源文件复制前后哈希一致，副本 integrity_check 为 ok；账本有 10 个 model、10 个 revision、10 个 delivered 行，published 合计 10，counts 合计 9。190 条已交付 artifact 的大小与 SHA256 均与现存文件一致。目录里的批次数、项目数、已发布数和独立计数不是同一指标。[D21]

计数为 9 的直接原因已从账本核实：其中两件共享 cells=43、steps=151、solids=1 的粗分类，合计 counts=1；另八个单成员分类各计 1。零计数行的 counts_as 确实指向同类中一个已交付且计数为 1 的代表。这证明账本为何如此结算，不证明两件几何完全相同，也不能替代原双图去重或买方最终计数。文档不披露这些当前项目的业务名称。[D09 D21]

job 表共 70 行：build、validate、probe、export 各有 10 条 OK；package 有 10 条 OK 和 20 条 FAIL。它们按不同输入哈希保存，不能把历史失败删除，也不能把“存在任意 OK”当作当前输入已经通过。buyer_stock 为 0 行，说明没有从这份账本证明已接入买方外部库存。

历史 Round 11 审计针对 64a95367ef997904 给出 ACCEPTED、P0=0、P1=0、P2=1；N43/N44 已修，N45 未修。N45 是重复 CADKIT-DONE 帧可能让更早的真实 FAIL 在全局合并前丢失。审计通过不等于所有缺陷清零。统一生产门禁建议先修复并回归 N45，再使用该探针作为最终放行依据；本次没有修改代码或重跑审计。[D18]

H/status 的现存 runner 日志只有一次小型记录：5 行，4 行生产、1 行复检，其中 2 次生产成功、2 次失败。它不能证明 20 万件计划、跨机规模或无人值守生产已经落地。文档中的“500 族谱 × 400 成员”是规划，不是成果数量，也不自动满足我们的结构独立计数要求。[D13 D16 D21]

## 27 双方架构对照与整合取舍

| 能力 | 原 100 新件流程 | DSH 看板与配套代码 |
|---|---|---|
| 设计输入 | 会话规划固定 recipe，原生构造计划 | project.yml、build.py、family 模块；目录展开 intake |
| 构造历史 | 单 PartDesign Body，Pad/Pocket/Groove 等受支持域 | Part::Extrusion/Cut/MultiFuse 等图链，参数 Spreadsheet |
| 执行调度 | run_queue 六阶段、停止标志、队列锁、100 件边界 | Host 单次动作；H/fleet_runner 线程池与单个 package 消费者 |
| 硬审 | 19 项、独立 Native 输入绑定、真实贡献、双图去重 | 12 个 validate ID、参数表探针、打包时重新验收 |
| 功能验收 | 每架构专用量规与阻挡/泄漏等负例 | 密封圈带、螺栓装配路径等族规则；非任意零件功能证明 |
| 状态存储 | JSON state/events、accepted、history 索引 | SQLite job/revision/artifact/delivery/evidence，JSON 看板投影 |
| 人工审查 | 视觉待审与完全合格分开记录 | L1/L2 绑定回执；require_review 需显式开启 |
| 去重计数 | canonical 与 Native 特征图/BRep 图，新增独立计数 | 几何不变量与 cells/steps/solids 粗分类，delivered 可多于 counts |
| 交付 | FCStd+STEP+八图，可选二维图，用户限定元数据 | staging、事务账本、原子发布、CSV、完整交付清单 |

推荐只保留一套生产控制面：复用 DSH 的项目入口、看板展示、批次 intake、job 记录、发布账本及收货框架；把原流程的建模执行器和独立验收器作为可调用后端。不要让 fleet_runner 内再嵌一个完整 run_queue 长期同时负责配额、重试、锁和接收。两个控制器会各自判断成功、计数与恢复，形成双写冲突。整合后由一个调度器分配阶段，后端只返回绑定证据。[建议]

第一阶段推荐继续使用 `partdesign_recipe` 后端，保留原单 Body 和 19 项规则语义。DSH 自带的 `cadkit_part` 后端暂作为第二种构造能力：只有完成独立历史、贡献、功能和跨库去重适配后才纳入统一生产，不能把 Part:: 文件包进一个 Body 就声称兼容，更不能隐藏导入实体来凑完整历史。

这里的“复用”是工程设计，不是已接通事实。现有 fleet_runner 固定调用 CadKit entry.py；现有 CadKit strict package 要求 params 表探针，并调用其自身 Part:: 计数规则；原 current_delivery 则具有单 Body 与特定 Native 解析约束。至少需要一个阶段执行适配器、一个统一证据/验收适配器和一个已验证的发布接入口。文档没有提供已经实现的万能转换器。

## 28 看板的真实行为与状态可信度

客户端 `lib/client.js` 是手写的已构建 bundle，注册侧边栏 `cad-freecad` 及同名 main 面板。它通过 `workspaceFiles.read(sessionId, "cad/mem/board.json")` 读取所选会话工作区的文件；每 8 秒轮询，页面隐藏时暂停。cadRoot 若不在该会话的 cad 子目录，后端可能正常写文件而前端读不到。当前路径是常量，尚无完整的多根动态适配。[D02]

看板显示治理预算、最近动作、项目文件存在性、报告失败/WARN、探针摘要、渲染数量与尺寸、交付目录和 registry 是否存在。它不直接查询 SQLite，没有任务领取、依赖 DAG、强制人工审批、自动拆分或配额事务。按钮调用 startSession 生成 prompt 草稿，whenDraftExists 为 replace；没有该服务时复制文本。按钮不等于直接执行 CAD 动作，更不能把进入会话解释为任务已经提交成功。

entry.py 每个非 probe_worker 动作都会 record_action 并重建 board.json，因此 `status`、`memcheck` 和 `board` 也会写状态；Host 还可能创建 cadRoot。它们不是本次只读核查可以随意运行的诊断命令。本次直接读取已有文件，没有调用这些动作。[D03]

board.json 通过临时文件加 os.replace 整体写入，但 history.jsonl 采取读旧尾部再整文件替换，未形成多写者事务；最多保留 200 条，快照展示 40 条。因此看板历史不是审计事件总账。generated_at 和动作 ts 目前来自 `_stamp(os.devnull)`，不能可靠代表实际生成时间；应结合文件 mtime、账本与阶段结果判断新鲜度，统一适配时改为真实 UTC 时间及明确 source_revision。[D03]

建议看板增加由账本只读投影生成的 lifecycle_state、technical_state、visual_state、dedup_state、publish_state、quota_used、snapshot_age、active_revision 和 blockers。动作只能请求一次受控转移，不能让 UI 自行把 PASS 或 delivered 写回。过期快照显示“状态待刷新”；任何摘要必须能打开同一 revision 的证据，不能仅按项目名覆盖旧状态。[需实现]

## 29 任务创建 拆分与模型职责

CadKit 的 new 只建立 project.yml、build.py、design.md、requirements.md 骨架；默认 target_grade=A，需要至少 100 个贡献节点，最小骨架本来就不足。project.yml 虽以后缀 yml 命名，实际读取器采用 JSON。build 会 import 项目 build.py 并调用 `build(b, proj)`；这不是从文字自动生成 CAD，也不是对任意代码的隔离沙箱。[D03 D04]

H/tools/emit_members.py 按 machine、wave、family 从 catalog 确定性展开 project_id 和 detail，生成 parts 清单。它不创建全部 build.py、不执行设计、不证明 topo_signature 已落实为实体结构。fleet_runner 只消费已存在的项目路径；签发 intake 和在 C/batches 下建立用于 package 的同一份清单是两步，现有调度器不会自动把任意 --intake 复制成 batch 合同。[D12 D13]

统一 TaskContract 应先包含功能、接口、材料区域/空腔、结构差异、受支持后端、尺寸驱动关系、预期负例、图纸要求、历史基线和配额身份。模型或 agent 先提出设计计划，独立检查其是否在支持域，再登记不可变 revision。只有改变设计计划或修订模型才消耗设计尝试预算；读状态或网络重连不算重新设计。[需实现]

用户此前提出每次任务先分难度：低难度用自有服务器 API，中等用 DeepSeek 官方 API，困难任务用 Codex。核查的 CAD Host、entry.py 与 fleet_runner 没有这套模型选择调用，也没有本地 Qwen 自主修复循环；不得把 DSH 的名字视为路由已经接通。新的任务路由适配器应在每个需要推理的任务开始时写一条 RoutingDecision，记录复杂度、理由、能力需求、选用 provider/model 和预算；确定性 FreeCAD 构造与硬审不必消耗模型推理。[后续需求]

建议模型分工：低难度处理已有 schema 的填写、日志归类和已批准规则的元数据整理；中等处理支持域内的新配方规划与局部修订；困难处理新架构、跨通道功能、依赖关系、失败检查器适用性和整合代码审查。涉及视觉必须验证所选模型有图片能力，并提供实际图片；文本模型不能签署视觉已审。任何路由结果都不能授予生成模型修改独立检查器、清除历史或降低阈值的权限。

Qwen 与低难度自有 API 是否为同一服务尚未核实。实现时通过 provider 配置明确；本次未读取 endpoint、密钥、当前聊天模型或调用任何服务。模板只使用未配置的 provider 别名，不包含真实凭据。

## 30 统一端到端流程

以下为推荐整合流程，均需适配与验证后才能执行。原流程的已实现细节仍见第 6 至 22 章，DSH 的已实现部分见第 26 至 29 章。

1. 冻结批次合同：明确新增独立件目标、候选总数、图纸是否必需、模型预算、后端支持域和用户字段覆盖；旧 100 新件、8 参考件只做历史基线。
2. 完成环境和代码身份握手：绑定 Host、前端、引擎、执行器、检查器、族谱、配置和历史索引；选择并统一安装版与签发版，不能混用同名 0.5.0。
3. 规划候选并做初筛：先查历史 canonical/结构意图，再形成设计合同和独立功能验收方案；领取候选租约及配额预留，不因预留就计完全接受。
4. 任务难度判断：对本次设计或修订选择允许的模型；记录输入摘要哈希与路由理由。模型输出受限计划，生成器不接受任意系统命令。
5. 构建 Native：在新工程的 revision 目录执行已批准后端，逐步 recompute，保留真实草图/特征、表达式和构造日志；不写旧历史目录。
6. 技术验证：独立重开 Native，做输入绑定、STEP 往返、实体、节点真实贡献、参数编辑恢复、架构功能正负例；生成结构去重图和报告。
7. 内部去重：与完整 108 件和本批此前结果比较，并接入 DSH 当前有效交付及共享历史；任何来源不完整或解析不支持标记 UNVERIFIED，不自动通过。
8. 固定导出：生成八个指定视角和 manifest，按当前合同输出可选或必需二维图；技术报告与图片绑定同一 Native/STEP 版本。
9. 独立复核：L1 核查设计与证据，L2 实看八图及每页图纸；模型复核可辅助，最终人工复核状态独立记录。改图后重绑定，改几何后重做受影响技术检查。
10. 接收门禁：确认技术、功能、去重、视觉、字段覆盖、版本和配额都满足；一次事务把 reserved 转为 accepted_unique，失败释放预留但保存全部记录。
11. 事务交付：staging 全文件哈希核对，绑定证据与 revision，commit pending、受锁发布、核对磁盘、mark published，再生成 CSV 和看板投影。
12. 批次收货：按精确 intake 核对缺件、多件、重复、哈希和审查；保留原始失败、修订和差异；解包复验，提交完整文档与代码定位。达到目标停止领取候选，旧件哈希不变。

这条流程中“构建完成”“技术通过”“待视觉”“完全接受”“已发布”“已交付”“买方确认”分别有证据，不互相代替。仅发现 FCStd、报告 ok、交付目录或看板绿色均不能单独证明最终完成。

## 31 状态 字段与事件映射

### 31.1 状态应采用多维记录

| 统一阶段或状态 | 原工作流证据 | DSH 现状与适配点 |
|---|---|---|
| PLANNED | queue item 与 recipe | intake/project.yml；需增加合同版本 |
| RESERVED | 原计数和队列控制 | 无统一配额租约；需实现 |
| BUILDING | build 阶段事件 | build 动作；job 目前主要保存 OK/FAIL |
| TECHNICAL_PASSED | 19/19 加功能、图验证 | validate/probe/export 不是同义词；需联合证据 |
| DEDUP_PASSED | canonical 和双图比较 | fingerprint + 跨机底表；需桥接 108 历史 |
| VISUAL_PENDING | 占容量但未完全接受 | review_status pending；不得计 accepted_unique |
| ACCEPTED_UNIQUE | 完全合格新增 | 不能直接等同 delivery.counts；需合同级计数 |
| STAGED 与 PUBLISH_PENDING | _ACCEPTING 临时目录 | .staging 加 delivery pending |
| PUBLISHED | accepted 清单已绑定 | delivered 且 published=1，磁盘哈希吻合 |
| DELIVERED | 外部包已交付记录 | 文件夹 published 不等于用户已收到；另记 |
| FAILED HELD DUPLICATE | 终态和保留证据 | job FAIL、withdrawn/superseded 含义不同；需分类 |

同一件可以 technical_state=PASS、visual_state=PENDING、publish_state=NOT_STARTED。不要用一个绿色状态掩盖缺口。现有 DSH delivered 行中既有独立计数 1，也可能是同类成员计数 0；本次 10 个 delivered 而 counts 合计 9 是真实例子。[D09 D21]

### 31.2 字段映射与命名空间

原 project_id 映射到 DSH project_id，但新方案还要 tenant/buyer、batch、wave、candidate_id、canonical_id、revision_id 和 backend。原 history canonical 身份、DSH family/series、几何 structure_sha 不可互相冒充；它们表达不同概念。旧 P001 等参考身份应加 `legacy100:` 命名空间保留原值，新候选使用批次前缀避免跨批同名。

原 operations.json 的执行计划、requirements.json、input_native_binding、native_graphs 和功能量规报告进入 evidence_manifest 的独立条目，不只复制一个 validation_summary。DSH report/probe/export_manifest/review/receipts/repro_manifest 也保留原格式；统一 gate_manifest 引用其哈希与规则，不能把一个 ok 布尔值转换成 19 项 PASS。

现有 job 唯一键是 project_id、stage、input_sha、rules_sha，不含 batch，也没有统一设计尝试号；event 表有 delivered/info/published 等记录。特别是 commit_acceptance 先写 pending 时会插入名为 delivered 的事件，最终发布以 delivery.state、published 和实际文件为准，不以事件名字单独判定。[D09]

建议新增事件 TaskCreated、RevisionProposed、LeaseAcquired、StageStarted、StageFailed、EvidenceBound、ReviewRequested、ReviewRecorded、QuotaCommitted、PublishPending、Published、DeliveryConfirmed、Held、Withdrawn。每个事件带 event_id、真实 UTC、任务/版本/幂等键和 evidence_sha；重复事件不能造成重复计数。这些是拟议事件名，当前看板和账本没有全部实现。

## 32 执行适配层与输入输出契约

### 32.1 三个边界

调度适配层负责一次只派发一个确定阶段，处理租约、预算和进程组；执行后端负责用冻结计划生成产物；独立验收层负责读取冻结文件并作出结果。验收器的代码与规则版本不得由生成任务写入。发布层只消费完整验收包与人工回执，不能根据聊天文字直接入账。[需实现]

TaskContract 至少包括 schema_version、batch_id、candidate_id、canonical_id、backend、native_history_policy、functional_contract、structural_difference、drawing_policy、required_gate_ids、history_baseline_sha、quota_policy、attempt_policy 和 routing_policy。输入文件只引用本批工作目录和已批准只读资产；拒绝路径越界、未知规则 ID、未注册 recipe、隐式默认已通过。

StageRequest 包括 request_id、revision_id、stage、input_manifest_sha、executor_sha、checker_sha、timeout、memory_cap、output_dir 和 lease_token。StageResult 包括 clean_exit、exit_code、status、rules_version、input_digest、artifacts、checks、warnings、resource_usage、error_class 和 retryability。成功要求干净退出及结构化结果同时成立；dedupe 旧接口没有 ok 时须显式适配为结论，不能按 Python/FreeCAD 退出 0 自动通过。

### 32.2 原建模后端如何接入

第一阶段适配器调用原构建、结构解析、质量和视图脚本，复用第 8 至 12 章已核实接口，把原六阶段输出封装为 StageResult。不要为接入 DSH 改写 108 个 Native，也不要直接运行已满额封存入口；新工程要独立批次政策，历史图保持只读。旧 queue 的断点语义可作为测试基线，但正式统一调度不再双重维护 accepted 计数。[需实现]

原 Native 的独立驱动是 Sketch 约束及特征尺寸，DSH strict probe 主要认 params 表；原八图是 views 下的固定文件，DSH 是 renders/view_*.png；原 model.FCStd 与 DSH <project_id>.FCStd 名称不同。适配必须明确文件映射、语义与哈希，复制或重新命名后验证字节不变；不能为满足目录名就省略原报告，也不能为让 strict probe 通过而伪造 params 表。

现有 packager 会重新运行 CadKit validate 并强制部分元数据字段，无法直接接收所有原 PartDesign 模型。推荐抽取其事务发布和账本能力，新增受版本控制的验证 profile；该 profile 明确接受原独立验收的证据且仍验证 staged 副本。不要在原 packager 里加入“旧件全部跳过”的宽松例外。

### 32.3 必须绑定的内容

每个 revision 固定计划、构建源码、族谱/recipe、参数、Native、STEP、图片、图纸、功能量规、独立验收器、审查记录、配额合同及历史索引哈希。source/protocol/env 中任何会影响结果的变化都使对应缓存失效。只用文件名、时间戳或短项目名无法建立可信证据链。

本包的 templates/unified_task_contract.json、stage_result.json 和 unified_gate_manifest.json 是建议 schema 示例，均标记 PROPOSED_NOT_IMPLEMENTED；不是现有 CadKit 能直接接受的 opts，也不是已经安装的插件扩展。

## 33 联合验收门禁与功能负例

DSH validate 源码实际有 12 个检查 ID：rebuild_no_error、step_floor、sketch_constraints、geometry_valid、unit_scale、tidy、not_reverse_engineered、parametric_history、seal_land_clear、bolt_access、real_object、source_license。real_object 是 WARN 人工项；草图未全约束也可 WARN；总体 ok 只要求没有 FAIL。不能把这 12 项称为原采购 40 项或原工程 19 项已经全部通过。[D05]

| 验收维度 | 差异 | 统一推荐 |
|---|---|---|
| 历史和贡献 | 原逐步体积/保留贡献；DSH 主要按最终对象依赖闭包计节点 | 保留原真实贡献证明；Part:: 后端扩展必须独立验证 |
| 节点分级 | 原至少 20；DSH C≥20、B≥50、A≥100、S≥200 | 至少 20 不变；更高等级只有合同要求才强制，不等同任务模型难度 |
| 实体 | 原单 Body 单闭合正体积 solid | 第一期保留；装配/多实体另签支持域，不能隐式放开 |
| 数值编辑 | 原全部独立驱动 ±0.25% 并恢复；DSH 参数表通常 +1% | 原必需测试保留；DSH +1% 作额外证据，不替代双向覆盖 |
| 恢复 | 原体积绝对差 <1e-6 mm³；DSH 容差 max(1e-6,V×1e-6) | 按验证 profile 明确，不偷偷用较宽阈值覆盖原门槛 |
| STEP | 双方相对体积≤1e-4、包围盒轴长≤1e-3 | 从最终 Native 直接导出并独立读回，实际核验 AP214/AP242 |
| 八图 | 原固定 2000×2000 且独立全像素；DSH 默认 2400、内容启发式和 manifest | 首期保留原固定协议；不同渲染器另建基准，不互判像素失败 |
| 视觉 | DSH require_review 默认未强制 | 统一完整接受与发布前强制 L1/L2 加明确人工状态 |
| 来源字段 | 原用户排除 URL/许可证/作者；DSH package 要求非空 | 新发布 profile 显式覆盖，内部审计与对外交付字段分开 |

DSH probe 使用不可变快照、worker 复制、哈希复查、实际驱动单元覆盖检查，并在恢复时比较实体数和体积；这些机制可复用。但当前记录的 value 是扰动前 old，实际施加 old×(1+delta)，新统一结果应同时记录 old_value、applied_value、delta、方向和恢复量，不能只凭有一个 numeric value 就声称保存了实际施加值。[D06]

DSH 的密封圈带和螺栓装配包络检查有实际几何逻辑，仍主要围绕既有族谱及其 feature 声明。新通道类件要继承第 13 章的真实材料/空腔、连通/隔离、接口、阻挡、泄漏量规与负例。P001 肋板挡孔、TC/RC 工具重叠、RF 量规断开、CA 量规落空腔、QC/HS 参数依赖修复全部保留；适用范围修改必须披露原失败并证明真实错误仍会被拒绝。

grade、节点数、图像非白像素比例或通用 real_object WARN 不是功能证明。通用 12 项通过也不能自动接受未知结构。每个新架构需要独立正例和至少适用的堵塞、串腔/泄漏、接口缺失、真实材料不足负例；量规先自检闭合性与连通性，不能让坏量规掩盖坏模型。

联合门禁把必须规则的 FAIL、UNVERIFIED、MISSING 都视为阻断。WARN 只在规则明确定义为提示且对应人工关口已完成时可保留。L3 若仅是建议性审查要写清范围，不把缺失 L3 自动改成 PASS，也不能把 L1/L2 文本签名当作已实际看图。

## 34 历史接入 去重与配额

旧 108 件图索引、原 Native/STEP 哈希、引用身份和新/参考分类应只读导入为历史基线。DSH 的 10 个当前 delivered、共享各机底表及未来买方库存属于另外的来源。先比较源身份和文件哈希，建立 canonical_alias 映射；未核实跨库是否重复前不能简单相加为 118 个独立件，也不能把参考件转成新增。[需实现]

DSH fingerprint 采用体积、表面积组合、包围盒比例、实体/面/边数、步骤和参数单元数；exact 不是 BRep 字节相等或图同构。scaled_same 的源码容差包括紧致度 1.5%、轴比例偏差 0.5%、体积比例偏差 2% 等工程阈值。原双图去重和这些不变量并非同一个判据。统一方案先用不变量分桶，再用受支持 Native/BRep 图和必要几何核验作精确判定；分桶必须证明没有漏掉应比较的对象。[D10 S13]

存在一个必须补齐的接入缺口：check_and_register 在本地 SQLite 有 delivered fingerprint 行时优先使用账本，可能完全不读 registry/fingerprints.json；而 registry_sync 主要把共享底表写到这个 JSON。仅执行 registry_sync 不证明全局历史已进入本地生产去重。应实现明确的只读多来源并集或 import_reference 表，记录每个来源版本与覆盖数量，重复 identity 冲突直接阻断。[D10 D15]

跨机 merge_check 虽复用 scaled_same/related，但它自己的 exact 分支在同面/边/实体桶内按等体积判断；本地 fingerprint exact 还检查步骤和紧致度。因此当前“全部判据同源”声明并不完全成立。registry_sync 的等体积预检也有更宽误判范围，并且仍有全量两两循环。不能用这些启发式结论替代原双图历史，不能未经验证宣称十万或二十万规模性能已达标。[D10 D14 D15]

统一批次分开维护 candidate_count、reserved_count、technical_pass_count、visual_pending_count、accepted_unique_count、published_count、delivered_count、duplicate_count 和 held_count。配额采用新批次目标与租约事务；视觉待审占容量，只有技术、去重和人工视觉全部完成后转入 accepted_unique。DSH ledger 的 counts 目前按 cells/steps/solids 等价类选代表，并不等同原 canonical 独立架构计数，保留该指标作原始证据但不要直接用作新目标结算。[D09]

保留原 99→100 边界与达到目标后生成 0 的测试语义，改成新批次目标 N 的 N−1→N 与 N 后 0。旧 100 件已完成状态不变。H 文档的每波 2000 与“完成定义一波 20 件”存在文字矛盾，实际合同必须以冻结 intake 的精确清单为准；规模和数量都不能从说明文字自动扩大本次授权。

## 35 重试 幂等 锁与恢复

### 35.1 三种重试不能混计

设计修订是新计划/参数/构造版本；阶段重跑是在确定输入下重新执行 build/validate 等；probe 崩溃续跑是参数窗口内的恢复。旧 run_queue 最多 1 至 2 次同 spec 尝试，DSH fleet_runner 没有五轮模型修复；job.attempt 是同 project/stage/input/rules 键的写入次数，输入变了就变成另一行；probe 的窗口尝试上限与单元数有关。这些都不等于五次设计预算。[S05 D06 D09 D12]

建议 design_attempt_no 跨队列、跨机器、跨输入哈希累计；默认包含初次在内最多 5 个设计版本，具体定义写入合同。stage_attempt_no 和 probe_window_attempt 独立记录。超过设计预算进入 HELD/SKIPPED，保留所有失败；重连、状态读取和未执行的任务不耗设计预算。任何修检查器范围的变更必须独立审查，不由设计修订自动带过。[需实现]

### 35.2 已有恢复能力的边界

fleet_runner 的 Recorder 绑定 project.yml、family 源码、rules_sha；build 还绑定 build.py，其他常用阶段绑定 Native，package 再绑定报告、manifest、STEP、八图与 build.py。它会跳过精确输入键的 OK，但没有保存并重验各阶段全部输出哈希；package 输入键未包含全部审查回执、waiver、batch 合同等依赖。不能把“精确输入键”扩写成完整依赖闭包已经正确。[D12]

还有静态可见的恢复缺口：pending 只看 --stages，package 由生产线程结束后入独立队列；若 build/validate/probe/export 都已 OK，而 package 单独失败，重跑同一默认命令可能没有候选进入包装队列。默认阶段列表也没有 dedupe，实际去重主要依赖 package 内部预检。新适配必须显式把所有未完成必需阶段纳入依赖 DAG，禁止漏掉只剩包装的任务。

fleet_runner 目前先算 todo，再按用户提供的阶段顺序执行；仅检查 --package 时所需阶段集合包含关系，不强制正确顺序，也没有跨进程全局调度锁、项目租约或持久化暂停状态。其 --dry-run 之前会打开账本并做 schema 初始化/迁移，因此也不应当作纯只读命令。它直接启动 freecadcmd，绕过 index.js 的进程组采样与 kill；单消费者只保证同一次 runner 内包装串行，不等于全机器只有一个发布任务。[D12]

run_stage 会解析 JSON，但有结构化成功结果时没有把非零退出统一判为失败；Host index.js 则要求 result.ok 与 exit 0 同时成立。新适配要统一这个差异，并验证超时/停止能结束全部 probe 子进程，而不只杀父进程。这里是源码审阅发现的边界，本次没有新运行故障实验。

### 35.3 统一恢复协议

开始阶段前原子领取 project/revision 租约，写 RUNNING 与完整依赖哈希；完成后验证输出清单再写 OK。重启时先确认旧进程/租约、输入和输出都吻合，才能复用。人工视觉回执和配额合同变化只失效相关下游阶段；几何变化使导出、功能、去重和视觉同时失效。禁止用同项目不同窗口互相覆盖报告。[需实现]

暂停分为 STOP_REQUESTED、DRAINING、PAUSED；停止领取新候选，明确当前阶段是允许完成还是终止进程组，保存中断原因。恢复前检查 active lease、pending publication、staging、旧 delivery 所有权和配额余额；不知道磁盘属于谁时保持原文件并进入人工排查，不能按“未知就是不存在”清理。N43 的处理可复用，N45 的 FAIL 丢失必须作为验收负例保留。[D08 D18]

## 36 审查 账本 发布与交付

DSH package 的 strict 与 require_review 默认都不是强制开启，分别从 opts 或环境字符串是否等于 "1" 决定。二者不是同一个开关；只开 strict 仍不能保证人工回执齐全。统一 profile 应强制二者及原独立 gate manifest，禁止依赖外部 shell 环境碰巧开启。[D08]

record_approval.py 在实际复核后记录 reviewer、包含项目 ID 的 attestation、Native/contract/export manifest/family 哈希。gen_receipts.py 会复核图片与批准记录再生成 L1/L2；但当前脚本固定 pilot 批次和历史 reviewer/date/scope 文本，不是通用多批审核器。新实现应接收真实本次审查身份、时间和文件清单，不复制旧审查语句给新模型。[D17]

L1 负责设计合同、参数依赖、功能量规和原始失败审查；L2 必须实际看八图和每页图纸，并绑定图片及 PDF 哈希；人工最终复核还需单独记录完成者与结论。现有 adjudicate_review 的 L1/L2 哈希约束可作为基础，但一段 attestation 本身不能技术上证明某人已经看图。模型复核记录和人工复核记录应可区分。

发布复用 DSH 的 staging、fsync、SQLite 事务、发布锁、旧目录临时移位、原子 rename、磁盘归属复核和 mark_published。数据库事务与文件系统 rename 不是一个原子事务，必须保留 pending、恢复和拒绝路径，不能承诺任意崩溃下立即完全一致。event 中提前出现 delivered 不代表发布完成，CSV 和看板只能由最终有效账本与磁盘导出。[D09]

对外交付元数据继续遵守用户要求：不含 URL、许可证、作者。DSH 当前 INFO_FIELDS/REQUIRED_INFO 和 CSV 输出包含并要求这些字段，因此需要专门 delivery profile 和字段投影；不能先伪造 author/license 满足原 gate 再删除字段而保留旧 PASS。内部审查身份、规则覆盖和授权记录保留在审计证据，范围说明不等于公开项目作者字段。[D08]

最终每件保留 Native、STEP、八图、适用二维图、设计合同/构造计划、全部必要引用、独立硬审/功能/去重证据、审查回执和文件 manifest；每批保留 intake、规则/源码身份、全历史来源索引、状态/事件、失败与修订、配额统计、交付清单。便携 ZIP 可分源码/依赖引用/模型/证据，但解包合并必须完整可定位。仅交本手册不能当作交了运行系统。

## 37 核实过的 DSH 接口与副作用

本章是已核实的接口说明，本次没有执行下面的 CAD 或调度命令。需要在未来获授权的新工程里配置绝对路径，不能复制后指向 C 或旧 B 生产目录。

### 37.1 Host 工具实际参数

cad_freecad 接受 action、project、root、opts；root 是顶层参数，README 中写成 opts.root 的描述与 Host 读取方式不一致。action 枚举为 new、build、validate、probe、export、package、dedupe、status、memcheck、board。package 的 opts.batch 必填；dedupe 注册当前用字符串 register="1" 判断。Host 将 opts 转为环境 JSON，entry 再转为字符串形式参数，布尔值语义需要适配层规范化。[D01 D03]

例如 `strict_bop` 在入口经过 bool(string) 转换，字符串 "0" 并不是 Python False；strict/require_review 又按字符串等于 "1" 解析。统一 API 应使用真正受校验的 typed options，并给这些边界做负例，不能依赖自然语言“关闭/打开”的含糊传参。

### 37.2 单阶段可靠调用形态

FreeCAD 会先处理自己的 CLI 参数，配套调度器实际采用环境变量加 entry.py，而不是照抄 entry docstring 的自由 `--action` 写法。下例仅说明源码确认的调用协议；变量由接手者指向新工作副本：

```sh
CADKIT_ACTION=validate \
CADKIT_ROOT="$CAD_NEW_ROOT" \
CADKIT_PROJECT="$CAD_NEW_ID" \
CADKIT_OUT="$CAD_NEW_RESULT" \
CADKIT_OPTS='{}' \
"$CAD_FREECAD_BIN" "$CAD_ENGINE_ENTRY"
```

该动作会写 report 和看板，必须由执行器保存结果并同时检查干净退出及 JSON 结论。build、probe、export 等同理；不能把 CADKIT_OUT 指向历史报告。参数含凭据或用户任务正文时不写进 shell 日志，模型配置保持在受保护配置层。

### 37.3 既有批次调度接口

```sh
"$CAD_QUEUE_PY" "$CAD_HANDOFF/tools/fleet_runner.py" \
  --root "$CAD_NEW_ROOT" --batch "$CAD_NEW_BATCH" \
  --intake "$CAD_NEW_INTAKE" \
  --stages build,validate,probe,export \
  --workers 1 --expect-rules "$CAD_PINNED_RULES"
```

这是现有 CadKit fleet_runner 的接口，不是已实现的原 PartDesign 后端接入命令。`--package` 确实存在，但只有强制审查、字段 profile、版本与恢复缺口处理后才适合统一交付。fleet_runner 没有声明 --plugin-python；要改变其 find_entry 选择可用已支持的 CADKIT_PLUGIN_PY，但必须同时正确签发版本，不能为了通过握手任意换值。[D12]

`emit_members.py --machine A --wave 1` 是已有清单展开接口；`merge_check.py --mine ... --theirs ... --baseline ... --json` 是已有跨机收货接口；`registry_sync.py --root ... --check-only` 有只检查分支。它们都有第 34、35 章的适用边界，不是统一验收的替代。对新批次不要运行 hardcoded pilot 的 strict_pass 或 gen_receipts 脚本来覆盖历史。

## 38 接手 agent 的实施顺序与验收

下面是后续实施任务，不代表本次已经修改插件。先在独立工作副本开发，用小型正负例验证，再批量生产。

1. 固定来源：保存旧手册和旧 108 件全部哈希；核对 P/H/tgz 分叉；选定引擎版本并给 Host、前端、执行器、checker、family、PDF 和配置各自完整 SHA。处理 N45，重签发包与 versions，不能只改 package.json 版本号。
2. 冻结统一合同：确定结构独立计数、配额、图纸和五次预算语义；保留用户元数据覆盖；原 108 基线只读，DSH 既有库保持独立身份。
3. 实现阶段适配：先支持 partdesign_recipe 六阶段，不改变旧 Native；增加 typed request/result、工作目录隔离、进程组取消、完整输入/输出哈希与失败分类。
4. 接入联合门禁：原 19 项原样保留，增加 DSH 发布证据及人工状态；Part:: backend 必须另做支持域适配，不把不适用直接填 PASS。
5. 实现历史并集与配额事务：验证 SQLite 有数据时仍能看见外部/旧 108 基线；并发领取、视觉待审、重复退回与达到目标后的停止都可测试。
6. 修复恢复语义：只有 package 失败、输出被删、review 改变、版本漂移、阶段乱序、同项目多进程、非零退出但 JSON ok、worker 超时/孤儿等情况必须拒绝错误复用。
7. 实现审查与发布 profile：严格审查强制开启，实际视觉与回执绑定；对外交付排除三个字段；发布 CAS 不确定时不动现有文件，保证历史证据可追溯。
8. 扩展看板只读投影和受控动作：显示阶段、阻断、配额、待审、真实时间和过期状态；先验证按钮不会直接伪造完成，再做单件端到端运行。
9. 仅在一个新架构样件完成技术、视觉、发布、恢复和解包复验后扩大批次；每次扩大都保留历史基线和失败负例。

| 验收组 | 必须出现的真实证明 |
|---|---|
| 原历史保护 | 旧完整 ZIP、108 Native/STEP 和相关索引前后哈希不变 |
| 版本隔离 | 相同 0.5.0 不同源码被识别；未报告版本或签发不一致阻断 |
| 构造与门禁 | 真贡献不足、冻结实体、断实体、依赖断开、STEP 偏差全部失败 |
| 功能 | 堵孔、串腔、泄漏、材料不足、断开量规与误落空腔均被正确分类 |
| 探针 | 双向覆盖、恢复、N45 重复 DONE、FAIL 后 PASS、假 coverage 不可变绿 |
| 审查 | 缺 L1/L2、缺人工复核、替换图片/Native/图纸、旧签名全被拒绝 |
| 去重 | 旧库/本批/跨机/别名/改名/缩放/镜像覆盖；未知域保持 UNVERIFIED |
| 计数 | 参考不新增、同件修订不新增、待审占容量、N−1→N 只增 1、N 后生成 0 |
| 恢复 | 只剩 package 的失败可恢复；输入/输出/回执变化失效；中断不重复发布 |
| 发布 | 事务和 rename 间故障、旧/新竞争、未知归属、staging 残留有安全恢复 |
| 收货 | ledger 与实际哈希逐条一致；无缺件多件；精确视图名/有效 PNG/STEP schema 检查 |

验收报告分别写“本次执行”“读取历史结果”“仅源码支持”“尚未实现”。把测试脚本存在写成测试已跑、把一个 smoke 日志写成规模能力，均不得验收通过。

## 39 冲突裁决与少量待确认项

推荐裁决一：控制面和账本复用 DSH，第一期 Native 构造与技术接受保留原严格流程。原因是看板和发布设施已存在，而原功能负例与历史图证据最贴合用户 100 新件成果；两者通过显式 adapter 接口连接。

推荐裁决二：采用真正结构独立的 accepted_unique 作为批量目标，DSH 的目录数、family 参数成员数、delivered 和 counts 分别保留。不能为了达成大规模计划把只改尺寸的成员都算独立新件。优先导入只读 108 基线，跨库重复未核实前不合并数量。

推荐裁决三：选定已含 N43/N44 修复的代码作为候选整合基线，但先关闭 N45 并回归，重新冻结 Host/引擎/看板/runner 的完整版本；不能直接把安装版结果与旧发布包签发混在一起。继续保留单发布槽和同项目租约，避免因“已有锁”就取消全部并发边界。

推荐裁决四：严格审查、完整证据和用户元数据覆盖由不可变 profile 决定；性能降级可以减少并发、分块探针或等待资源，不能把输出长边降到 2000 以下后仍计合格，也不能减少独立参数覆盖来隐藏失败。

仍需在未来生产合同里明确三点：本批独立新增目标与二维图是否必需；“最多重试五次”采用五次总设计尝试还是初次加五次；Qwen 是否就是低难度自有 API，以及各 provider 的具体模型与视觉能力。文档提供默认建议，不把这些未核实配置写成已经连接。当前整理文档不需要修改既有系统来解决这些配置问题。

## 40 可复制的完整统一任务提示词

下面的提示词用于未来明确授权的实施任务，方括号需替换为该批真实合同。它不是本次启动生产的授权。

```text
先完整读取 FreeCAD_Fleet_Integrated_Handbook.zh.md 和 evidence 目录。
任务是在新工程中整合 DSH CAD 看板调度与历史 FreeCAD 严格验收，
随后按经确认的合同生产 [独立新增数量] 件，图纸要求 [必需/可选]。
本阶段授权范围为 [仅实现和验证/允许样件/允许批量]。

旧 FreeCAD_Agent_Handoff v1、旧 100 新件加 8 参考件、DSH 当前项目
与交付账本均保留；原目录只读，不覆盖，不清历史、不改旧 100 件 cap。
本手册不是运行安装包，先按第 2 和 26 章取得完整代码及必要 Native 引用。

先核实实际安装版与 handoff 发布版分叉，冻结完整哈希。
不要只看 0.5.0；确认 N43/N44 修复，处理 N45 重复 DONE 丢 FAIL，
再做回归与新版本签发。不要启动未授权服务或读取/传播凭据。

第一期采用 partdesign_recipe；复用旧建模和独立 19 项验收，
复用 DSH intake/job/账本/发布框架。实现显式阶段与证据 adapter，
不要让两个完整控制器同时重试、计数和接收。
CadKit Part:: 后端与原单 Body 不兼容的部分必须独立开发验证，
不得伪造 params、隐藏导入实体、把 N/A 填成 PASS 或跳过检查。

每个推理任务开始时做简单难度判断，按批准配置路由：
低难度自有 API，中等 DeepSeek 官方 API，困难 Codex；
本地 Qwen 的角色以本批配置为准。模型只产出受限方案和修订建议，
不能修改独立验收器、降低阈值、清除失败或签署未看过的图片。

每候选先写功能合同、真实结构差异、参数依赖、正例和负例。
至少 20 个最终贡献 sketch/feature 节点，不是 20 次任意操作。
保留 STEP 往返、参数双向编辑恢复、真实材料与空腔、通道连通隔离、
接口、阻挡和泄漏验收；修改检查适用范围必须披露并保留原失败。

把完整 108 历史、DSH 有效交付和共享基线显式接入同一去重查询。
防止本地 ledger 有行后忽略 registry；粗指纹不能代替双图判重。
参考、别名、尺寸变体、缩放、镜像与修订不自动增加独立件数。
视觉待审占容量，只有技术+功能+去重+实际视觉复核完成才计完全接受。

实现跨版本累计的设计预算 [含初次总共 5 次/明确其他语义]；
阶段重跑和 probe 窗口恢复独立记录，不以换 queue_id 重置设计预算。
完整依赖与输出哈希、项目租约、单发布槽、暂停排空和 package-only
恢复必须覆盖；退出非零即使 JSON ok 也不得记录成功。

严格审查与人工复核必需；回执绑定实际 Native、STEP、图片、图纸。
交付 FCStd、直接导出 AP214/AP242 STEP、固定八图、完整所需引用、
设计与验证证据、批次表、失败修订记录和全文件 manifest。
对外交付项目信息不含 URL、许可证、作者；内部审查证据保留。
制造放行保持 false，不宣称完成工艺/强度/安全认证。

先完成一个新架构端到端样件及第 38 章正负例，再按授权扩大。
每次报告区分源码支持、当前配置、历史运行证据和本次实际结果。
最终验证配额边界、旧件哈希、解包完整性，给出绝对路径、大小、
SHA256、已实现内容、未实现部分与明确失败，不能用看板绿色代替证据。
```


## 41 本轮入库范围、仓库身份与已有工作

本版在 v2 完整流程上新增五机规划和独立提示词，计划最终取得 **210000 个验收合格、全局去重的新零件**。规划不代表这些结构已经存在或可由现有脚本自动生成。本轮只提交文档、模板、规则映射和可重建证据索引，没有启动五机、运行生产、修复引擎或迁移生产账本。

唯一核实的目标仓库为 `https://github.com/JR-Rong/cad-repo`，owner 与已连接账户均为 JR-Rong；可见性为 public，连接具有 push 权限。本机原 handoff 的 main 工作区干净，停在 d56486c23e4f261a8f5343ceb024c1e8f49d5187；远端 main 在本次检查时为 bdaab3c1de90723fcd07ddae1edc75132b285a09。两个提交的差异仅是 status/PROGRESS.md 新增三行 B 机报告。新文档基于后者在独立分支准备，原生产工作区不切分支、不 pull、不改文件。[F01 F02]

新路径为 `docs/integrated-workflow-v3/`，旧 WORKFLOW、catalog、intake、briefs、plugin 和其他人提交全部保留。此目录是完整的流程说明和规划包；它没有把原执行后端嵌入 CadKit。原 100 件流程完整源码和四个必要 Native 引用仍须从第 2 章的封存资产取得。仓内有 CadKit 的 23 个发行文件、看板、fleet_runner、registry/收货工具及 500 项族谱目录；没有原 PartDesign 六阶段后端，也没有本次能够确认齐全的 `family_src/A` 至 `family_src/E`、各机实际 build.py、修复驱动器和成品证据。B 报告中的 backfill_ledger.py、package_wave1.py 是已有进度声明，检查的远端树中未包含这些文件。下一位 agent 必须先取得并冻结相关代码，不可自行猜实现。[F03]

本仓库是公开仓库，所以本次新增材料只包含流程、摘要和定位索引。本机绝对路径改为可配置资产根，凭据、SQLite 原库、私有日志、会话正文、909 MB 历史模型包和 QA 页面不上传。Word 与完整 ZIP 作为附件交付；Git 中保存可审阅的 Markdown、JSON、CSV、只读索引核对脚本及 Word，避免把模型包当源代码提交。原 v1/v2 独立文件保持不变。

### 41.1 已有状态的证据等级

| 来源 | 本次能确认的事实 | 不能据此推断的内容 |
|---|---|---|
| 原 FreeCAD 封存批次 | 100 新件、8 参考件，历史验收和哈希链 | 不能自动加到新批次配额，也未与 DSH 全库交叉去重 |
| A 本机文件及 SQLite 副本 | 14 项目；10 delivered、published 10、counts 9；70 job 中 20 条 package FAIL 历史保留 | 不等于 14 或 10 个满足统一门禁的新架构 |
| 仓库 catalog/delivered.json | 11 条登记，其中包括参考交付条目 | 不等于本机账本 11 delivered，不等于 11 个新增接受数 |
| shared/fingerprints | 检查时只有 A.json，10 条 | 不能声称已覆盖 B–E 全部历史和在途 |
| B 最新仓库报告 | 170+ 处理进度、172 件回填排练、169 指纹、92/173 过期 STEP 补导中；121/161 仍失败 | 未访问 B 物理机；这些是 self-report，不是最终发布/人工接受数 |
| C、D、E | 有合同和分工文件 | 实际机器、任务、产物、在途和失败数量均 UNKNOWN |

本次 A 数据来自复制主库及 WAL 后的只读查询，复制前后哈希一致，副本完整性检查通过。B 的 096 复活、19 件复验通过、6 件打包排练等只记录为仓库报告；其真实性和版本绑定须在 B 现场核验。不能用 A 本机没有某路径来断言 B 文件丢失，也不能用共享目录缺少 C/D/E 文件来断言三机尚未开工。[F01 F02]

## 42 从原 500 族谱到五机明确职责

原目录把 10 个领域、500 个物件概念分给五机，每机 100 项；每项按通用结构轴展开 400 个配置，总共 200000 个 ID。`catalog_lint.py` 能核对 ID、同族文本签名、清单和范围一致性，却没有构造 200000 个 Native，也没有证明这些配置跨族、跨机的图结构独立。`面板` 实际只有四条轴，其他轴族通常五条；不能把旧说明中的“五条轴”当作每个族的实际保证。不同物件名复用同一八类轴矩阵尤其需要全局结构审查。[F04]

本版继续沿用原 500 个 key 和其历史映射，不重编号或删除旧记录。新增 `planning/family_ownership.csv` 对全部 500 项逐条标注旧 owner、建议 owner、功能边界及迁移状态；`planning/family_extensions.csv` 提供 50 个候选功能子族（每机 10 个），用于探索替补空间。550 个目录条目仍只是规划入口，既不是 550 个已证明独立架构，更不是乘以固定成员数就合格。

| 机器 | 主责功能域 | 明确排除及交接边界 |
|---|---|---|
| A | 电机、传动的承载、轴向/径向定位、扭矩接口、机械防护与装拆结构；保留 md/tr 主线 | 独立流体分配/排泄给 E；通用机架吊装给 D；干式电子封装给 B；不得复制厂商标准件 |
| B | 仪器、人机界面、消费电子与电气设备的干式封装、连接器定位、线缆隔离、检修接口 | 工件测试定位座给 C；以换热为主的基座/罩给 E；承载机架给 D；不得把外壳说成完整仪器 |
| C | 加工/装配工装与测量检具，直接决定工件基准、夹紧、测头可达性或试验接口的实体 | 储存量具的盒座/收纳架给 D；通用仪器外壳给 B；独立阀路给 E；夹持座不冒称执行器总成 |
| D | 机架、连接、设备支撑、物料导向/搬运/仓储、非测量型支座与安装保护 | 精密工件基准给 C；传动张紧核心给 A；介质通路与热交换给 E；支架不自动声称承重认证 |
| E | 流体路由、隔离、密封接口、排放、换热通道与热管理的几何结构 | 干式电气盒给 B；纯安装支架给 D；独立传动接口给 A；不声称阀性能、耐压或热性能已认证 |

归属看主要功能和验收合同，不看名称中的“座、壳、板”。跨域一体件只允许一个主责 owner，另一机提供接口或独立检查意见；一件不能拆成五个计数。例：电机水冷承载端盖由 A 主责、E 核对内部流路；独立冷板由 E 主责；同一个 CAD 改名为“电机冷板”不能换 owner 再算一件。

本次建议转移 35 项目录职责（已按 CSV 校验），包括 A 的 tr24/tr25/tr42/tr43 转 E、md42 转 D；B 的 ce33 转 C、ce34/ce38 转 E；D 的 st08 转 A，st14/st15/st36/st37 转 C；C 的 ga23/ga24 转 D；E 的干式电气封装组转 B、通用支撑组及 fl32 转 D、fl47 干式变送器壳转 B。`en02 光伏接线盒` 与 `ce26` 是名称和主用途重叠的规划入口，统一由 B 做同域排查；这不是仅凭名称判定两份既有 CAD 已重复。

这些是新规划建议，不会立即夺取已有工作。所有转移先标记 `PENDING_OWNER_ACK`；旧 owner 仍负责其已领取 revision，完成暂停/排空、清点产物和失败、双方登记后才转移未领取候选。旧 ID、prefix、历史 owner 和哈希永久保留，不能改名迁移后重复计数。B 当前 in01–in05 主线不因本次重写而更名或全量重跑。

## 43 210000 接受配额与候选余量

本规划将新批次目标明确设为 **210000 个 accepted_unique_new**，A–E 暂各承担 42000。它大于 200000，且不靠把原 108 或 catalog 的 11 条登记加上去凑“20w+”。已存在的本轮候选可以经身份核对、统一门禁和全局接收后抵扣所属机剩余额度；已经属于旧基线的件只参与去重，不再计新增。

| 机器 | 最终接受目标 | 候选审查预算上限 | 起步验收批次 |
|---|---:|---:|---|
| A | 42000 | 70000 | 先 1，再累计 5、25、100 |
| B | 42000 | 70000 | 先盘存和修补，再补足相同门槛 |
| C | 42000 | 70000 | 先 1，再累计 5、25、100 |
| D | 42000 | 70000 | 先 1，再累计 5、25、100 |
| E | 42000 | 70000 | 先 1，再累计 5、25、100 |
| 合计 | 210000 | 350000 | 全局唯一接收后汇总 |

70000 是候选探索的预算上限，不是命令一次展开 70000 文件，更不是保证全部通过。按 60% 的假设最终接受率，350000 候选才对应 210000；这个比例是预算情景，当前没有实测支持。若实测只有 40%，达到目标需要约 525000 候选，已超本计划上限，应复核可行性和增补新功能子族，不得默默多跑或降低去重标准。

必须分开六个量：目录族数、通过设计审查的结构架构数、参数配置数、Native 已构造数、技术通过数、最终全局接受数。一个架构的尺寸/镜像/配色/文件名配置可以有很多个，但本合同只接受经全局结构证据确认独立的新零件。原 400 成员不继承“必定计数”的承诺，也不统一改成 420 成员。每族先提出可验证功能差异和架构证据，再决定实际可接收成员。

探索差异必须改变合理的载荷路径、约束/装拆关系、端口角色、通道连通/隔离、工件定位或真实接口，并有功能正负例。仅添无用途筋条、孔阵或“填充段”凑 A/S 步数，不是可接受的数量来源。原报告里 finx/填充段只能作为已知建模与复验风险保留，新方案不能照搬为凑数方法。最低 20 个真实贡献 sketch/feature 节点仍保留；没有合同要求时，不为了 A/S 标签强加 100/200 节点。

配额可在后续明确批准的计划 revision 中按有效产出率调整，但总目标和接受口径不能变化。每次调整记录从哪台转多少、未领取候选、既有接受数、所有权确认和新计划哈希。不得在五机本地分别宣布 42000 后简单相加；唯一可信总数来自同一全局接收账本对 canonical 身份去重后的合计。

## 44 开始任何机器任务前的盘存与续作

每台独立启动提示词先执行同一盘存协议。第一阶段默认 `AUDIT_ONLY`：读 git 状态/commit、安装包和实际引擎哈希、当前配置白名单、已有合同/任务/项目/成品/在途/失败/人工待审/registry/发布记录。不要调用有副作用的 cad_freecad status、memcheck、board，也不要把 fleet_runner --dry-run 当纯只读。不能读取或回显凭据。

先确定机器身份、实际 CAD_ROOT、其他会话和 runner 是否活跃、生产目录所有权及锁的 holder。只读检查进程存活和锁元数据，不凭锁文件旧就删除。发现活跃任务时不再启动第二个 runner；记录现有任务边界和下一安全检查点，避免修改其 family/core/规则源码。若无法证明进程或发布目录归属，记 UNKNOWN 并暂停接管。

盘存表至少包含 machine、project/canonical/alias、batch、revision、intake、源码/规则/输入/Native/STEP/八图/图纸哈希、job 各阶段状态、失败原记录、review 状态、published、global acceptance、lease、最后可信事件与证据位置。SQLite 先复制主库和 WAL，再在副本只读核对；源文件在复制中变化则丢弃快照重试或标记不一致，不让检查过程迁移生产数据库。

对每条记录分流：完整且版本绑定一致的已接受件只导入历史并复核缺口；已技术通过但缺视觉者只补人工复核；只剩 package 或发布恢复者只排对应阶段；输出过期或缺失者按依赖失效重做必要阶段；设计/功能失败者保留原 revision 后进入有预算的修订；无法证明状态者 UNKNOWN，不能按“缺 job 行=从未做过”重跑全部。

B 尤其要核对仓库报告中的 2000 项 scaffold、in01–in05 核心、驱动产生的 drv 记录、回填脚本、过期 STEP 清单、REWORK 清单和仍活跃的 runner。172 件回填排练不是对所有 revision 的真实通过证明；回填必须验证同一输入、源码、规则、产物和原始阶段结果，标注证据来源。STEP 的 CLOSED_SHELL 数可作为诊断信号，不是通用单实体判定器；最终仍用 STEP 导入、有效闭合实体、相对体积/包围盒和 Native 哈希绑定判定。

每机先提交 `machine_inventory` 摘要和差异表，再形成 `resume_plan`：列明跳过、复核、补导、补审、续打包、设计修复和新候选各多少，UNKNOWN 单列。首次启动的机器也必须先找旧记录，不能预设为空。未访问远端机器的协调 agent 只能填写来源未核实，不能用本机路径存在性代替现场盘存。

## 45 复用现有登记和调度的最小协作机制

优先复用现有 `shared/fingerprints/<machine>.json`、intake、fleet_runner、SQLite job/delivery 和单槽发布框架，不新建平行生产控制器。为满足五机全局独立计数，只增补版本化的全局接收索引、候选领取记录及统一验收适配；实现和并发验证仍是上线前任务。

每机只提交自己的证据清单与 registry delta。A 的协调角色作为初期唯一全局接收写入方，A 的生产角色也只能按普通生产者提交，不能自行绕过复核。收货顺序为：验证完整依赖哈希和人工状态 → 合并原 108、DSH 有效历史、五机已接受与在途候选的身份并集 → 运行适用的全局精确去重 → 按唯一 canonical 执行一次接收 → 写全局索引 revision → 回执生产者。共享 JSON 作为不可变 revision 的发布投影，不能靠五机随意互相覆盖达到强一致。

当前本地 fingerprint 的 SQLite 优先逻辑会忽略 JSON 基线，registry_sync 也可能只更新 JSON；因此必须先实现并验证同一次查询使用全来源并集。粗几何指纹只能用于安全筛选，不能直接替换原双图判断。候选分桶必须证明不会漏掉缩放、镜像、改名或跨族重复；宁可进入 UNKNOWN/UNVERIFIED，不把新命名当独立结构。[D09 D10 D15]

旧 ID 永久不变，新候选 ID 建议形如 `f3_a_md01_000001`，revision 另记 `r0001`；全局 canonical ID 一旦分配不因机主转移改变。目录归属 key 与原 family key、旧前缀通过 alias 表关联；改 ID 只能解决命名冲突，不能解决几何重复。新 50 项子族使用 ax/bx/cx/dx/ex key，先检查全局占用再登记，未登记不得直接生产。

领取记录绑定 candidate、owner、revision、token、有效期、源码/合同 SHA。A 单写者接收领取申请并签发租约；实际实现可延用现有仓库清单和小型账本，无须新增复杂服务。单独写一个 lock.json 或一次 git push 本身并不能证明租约原子性；必须通过竞争负例，确保同候选只有一个有效 owner。没有在线接收方或基线过期时，可在已有租约范围内保留候选，但不做最终接受计数，且受在途上限限制。

视觉待审和在途 reserved 占容量，只有技术、功能、全局去重和实际人工视觉全部完成才转 accepted_unique_new。发布是独立状态，拷贝 outbox 或推送 Git 不等于验收。每机余额是本规划 42000 减去属于同一批次、已被全局接收的不同 canonical 数；基线件、重复、失败和修订均不新增。达到全局 210000 后停止新领取；保持在途排空政策并验证 N−1→N 只增 1、N 后新生成 0。

## 46 上线阻断、失败预算与扩产门槛

以下任一项缺少绑定当前版本的证据，五机提示词必须停在盘存/实现计划阶段，不能宣称可以无人值守开跑：安装版与仓库发行版分叉未处理；N45 重复 DONE 可覆盖早先 FAIL；PartDesign 与 Part:: 验收不兼容；strict 与 require_review 未作为明确必需门禁；原独立检查后端/Native 引用或族核心缺失；全局历史并集不完整；复验恢复不能识别 package-only、过期输出或未知 lease。

N45 的处理按 artifact/revision 和原始失败证据进行：同一次 revision、参数 cell、窗口中曾出现有效 FAIL，后来的 PASS、空 DONE 或其他 cell 的 DONE 不得抹除它。保持原始 frame/窗口证据、去重后的事件身份与失败优先合并。只有经过真实修复的新 revision、完整重验和明确关闭记录才能解除失败。N43/N44 在安装版的修复不等于旧发行包也具备，N44 也没有自动修好 N45。本次不修代码、不改历史审计结论。[D06 D08 D18]

设计预算建议含首次共 5 次，总预算跨队列、跨机器和输入哈希追踪；原用户“最多重试五次”的语义在生产合同确认前不得装作已确定。阶段重跑、网络重连和 probe 窗口重试分别计数。第五次设计仍失败则 HELD，保留失败，不改名重新领一个候选清零预算。模型路由按每次推理任务简单判断：低难度自有 API、中等 DeepSeek 官方、高难度 Codex；这一路由及本地 Qwen 角色仍需配置和实现，确定性 CAD/硬审无需额外模型调用。

| 阶段 | 最大新增接受规模 | 放行证据 |
|---|---:|---|
| G0 盘存与准备 | 0 | 五机各自盘存；版本、来源、基线和阻断清单齐全 |
| G1 单件闭环 | 每机 1 | 同一 revision 的构造、19 项、功能正负例、全局去重、实际八图审查、发布/恢复通过 |
| G2 跨结构小试 | 每机累计 5 | 至少覆盖两个不同主功能子族，所有负例有效；不得用五个尺寸代替 |
| G3 稳定性试验 | 每机累计 25，再到 100 | 输出过期、只剩 package、双 writer、断点、重复 DONE、缺审查均正确阻断或恢复 |
| G4 受限批次 | 每机单批最多 500 | 实测成功率/资源/人工审查能力；全局登记无积压；每次回传完整证据 |
| G5 扩产 | 条件满足后单波最多 2000 | 前两波持续合格，所有来源全局合并；每个新族仍先过单件验证 |

这些是上限而非启动命令，不能把旧 emit_members 的 2000 项展开等同于允许立刻生产。B 已有产物按盘存结果抵扣相应验证阶段的工作量，只有证据真实达到门槛才允许进入下一阶段。跨版本更换引擎、模型族核心或检查器后，失效阶段重验，新版本重新取得扩产资格。

初始并发建议每机一个模型构建任务，probe 子 worker 按实测内存限制，package 每份生产根全机单槽；验证稳定后再提高。不得照抄旧 workers=3 到未知机器。未知锁/重复发布、数据哈希异常、漏失 FAIL、缺基线任一出现即停止新领取；同一根因连续 3 次失败即暂停受影响族调查，不等到 12 连炸。它们是新增推荐停机条件，不是已经部署的现有参数。

人工视觉积压初始上限建议每机 25 件，超过后暂停新构建，优先补审；模型只能辅助，不能用文本回执替代实际看图。预留磁盘至少覆盖剩余小批次估算、当前在途和一次完整备份；以试点 p95 峰值内存配置并发，OOM/子进程未回收就降并发调查。降并发不能伴随降图像分辨率、缩减参数覆盖或跳功能检查。

## 47 规模可行性与交付边界

现有事实支持“有一条小规模流程和已有部分产物”，不足以证明 21 万个结构独立架构可达。最大的约束是结构新颖性、独立功能检查、全局去重和人工复核，不只是 FreeCAD 运行速度。旧通用轴矩阵会产生大量配置和相似外形；必须先测每族可接受独立数，耗尽该族后转向新功能架构，不能用更密的参数网格补缺。

210000 件之间全量两两比较为 22049895000 对，另需比较历史基线。现有两两遍历不具备已验证的这一规模性能；可在原图/指纹规则前增加有证明的粗筛与缓存，但须保留必要候选对，测漏检和跨桶重复负例。50 个扩展子族也未经过实际生成，不能声称为数量提供保证。

仅八视图就有 1680000 张。若每件完整交付平均 10 MB，则 210000 件约 2.1 TB，尚不含失败、revision、备份和 350000 候选探索。若每件机器时间 2/5/10 分钟，五机各单任务理想持续运行分别约 58/146/292 天，未计失败、停机、全局去重和审查；这是算术情景，不是交期承诺。人工每件 30/120 秒分别需要约 1750/7000 小时，也未计返修。应在首批 100 件实测后重估计划。

Git 保存规则、源码、合同、摘要、指纹索引和可审核变更；大规模 Native/STEP/PNG/图纸、失败日志和账本备份应由独立版本化工件存储保管，登记实际 URI/相对路径和 SHA，不能把 21 万件模型直接灌入此公开源码仓库。本次未配置或开通任何存储服务。最终零件项目元数据依用户要求不含 URL、许可证、作者；内部审计来源与外部字段投影分开，不用假字段骗过旧 package。

每波交付必须能重建：冻结代码/依赖/配置白名单、功能合同、intake、原始失败、全局基线 revision、模型与八图及适用二维图、人工视觉记录、规则映射和 manifest。报告清楚区分 planned、generated、technical_pass、visual_pending、accepted_unique、published、delivered；未知数据以 null/UNKNOWN 表示，不写成 0。最终接收也不意味着制造放行，保持 manufacturing_release=false。

## 48 五份独立提示词和接手顺序

`prompts/START_A.md` 至 `START_E.md` 是五份可单独交给机器的完整提示词。每份都包含目标、原有/新分工、排除边界、盘存与来源版本、阻断门禁、复验续作、唯一 ID、全局登记去重、设计预算、扩产和停机条件，不依赖阅读另一台提示词才能知道关键限制。完整 500 项与 50 项扩展分配分别在 planning CSV；每份提示词也列出属于本机的全部目录 key 与名称。

使用顺序：先把本目录及完整代码资产放到机器可访问位置；指定 MACHINE、REPO_ROOT、CAD_ROOT、LEGACY_SOURCE_ROOT、LEGACY_ARCHIVE_ROOT、实际插件路径和批准的任务模式；先执行盘存阶段。未明示允许样件/批量的情况下，提示词默认只读盘存并输出 resume_plan。准备这些提示词不等于本次远程启动五机，也不授权自动改变正在运行的 B 机产线。

仓库交接验收包括：500 个旧 key 各一次、50 个扩展 key 唯一、主责唯一、所有权迁移保留历史；每机 42000、候选上限 70000、全局目标 210000；五份提示词均包含关键门禁；本机绝对路径和凭据不入仓；全部文档引用能解析到冻结版本或明确外部资产；Word 全页检查；提交只新增本目录，push 后远端分支 SHA 与实际提交一致。下一个 agent 的第一份报告应是事实盘存和阻断清单，不是直接开始吐 2000 个模型。


## 附录 A 四十项采购规则与当前流程映射

下表逐一映射冻结 rules.json 的 40 个 ID。32 项必选加 8 项加分；这里说明覆盖位置和缺口，不产生新的采购合规 PASS。来源字段被用户覆盖时，仍保留原规则身份。

| PDF规则 ID | 必选 | 原文位置 | 当前覆盖与缺口 |
|---|---|---|---|
| SCOPE_REAL | 是 | 第1页 2.1 | 功能合同 人工复核；只能证明有限名义几何；真实物件和完整性需外部语义证据 |
| SOFTWARE | 是 | 第1页 2.1 | METADATA 原生读取；观察 FreeCAD 1.1.1；创作版本与读取版本应分别追溯 |
| PARAM_HISTORY | 是 | 第1页 2.1 | PARAMETRIC_HISTORY；原生特征草图及无冻结检查；限支持域 |
| SOURCE_ACCESS | 是 | 第1页 2.2 | 用户覆盖；当前交付不要求来源URL；原PDF要求仍保留，不宣称原规则通过 |
| SOURCE_MATCH | 是 | 第1页 2.2 | 用户覆盖与人工来源记录；没有网页匹配自动证明；不应伪造来源 |
| LICENSE | 是 | 第1页 2.2 | 用户覆盖；当前不交许可证字段；不等于自动证明任意数据的权利来源 |
| NATIVE | 是 | 第2页 2.3 | PARAMETRIC_HISTORY FORWARD_HISTORY；完整Native历史与输入绑定；FCStd本身的ZIP容器不等于压缩历史 |
| ASSEMBLY_REFS | 是 | 第2页 2.3 | REFERENCES；仅一个Body且无App::Link；装配体验收未实现 |
| STEP | 是 | 第2页 2.3 | STEP_DIRECT_AND_SCHEMA；重开Native直接导出并绑定SHA；AP214或AP242 |
| VIEW_COUNT | 是 | 第2页 2.3 | EIGHT_NATIVE_RENDER_VIEWS；固定8个PNG并按名称与内容复核 |
| VIEW_STYLE | 是 | 第2页 2.3 | EIGHT_NATIVE_RENDER_VIEWS；在固定白灰渲染协议内全像素一致，不是通用图像语义理解 |
| VIEW_SIZE | 是 | 第2页 2.3 | EIGHT_NATIVE_RENDER_VIEWS；固定2000×2000，比原文长边≥2000更具体 |
| VIEW_POSE_CONTENT | 是 | 第2页 2.3 | EIGHT_NATIVE_RENDER_VIEWS；使用±45度俯仰；与严格等轴测定义歧义仍需买方确认 |
| VIEW_SCALE | 是 | 第2页 2.3 | EIGHT_NATIVE_RENDER_VIEWS；共同正交比例、独立Native网格全像素校验 |
| METADATA | 是 | 第2页 2.3 | METADATA；按当前用户口径不含URL许可证作者，不能算原PDF全部字段齐全 |
| PROJECT_FOLDERS | 是 | 第2页 2.3 | 队列和归档；按project_id目录；另核对metadata和批次覆盖 |
| BATCH_TABLE | 是 | 第2页 2.3 | batch_table；生成CSV和JSON；字段遵从用户覆盖，非原PDF全部字段 |
| FILE_NAMES | 是 | 第2页 2.3 | DELIVERY_NAMES 与ID预检；当前单项只检查文件basename；新交付另查所有相对目录组件 |
| GEOMETRY_MATCH | 是 | 第2页 2.4 | GEOMETRY_MATCH；原生与STEP相对体积≤1e-4，每轴相对长度≤1e-3 |
| REBUILD | 是 | 第2页 2.4 | REBUILD；独立重开和全对象强制重建，无特征失败 |
| EDITABLE | 是 | 第2页 2.4 | 两项EDIT检查；全部独立数值有限扰动加每草图一个关系案例；不能证明任意编辑 |
| SKETCH_CONSTRAINTS | 是 | 第2页 2.4 | SKETCH_CONSTRAINTS；非空且至少1约束并用于材料特征；并非该项保证全约束 |
| GEOMETRY_VALID | 是 | 第2页 2.4 | GEOMETRY_VALID；单实体闭合正体积、正面面积、深层OCCT；无通用壁厚制造认证 |
| UNITS_SCALE | 是 | 第2页 2.4 | UNITS_SCALE；mm及最大包围盒轴长1至20000；真实尺度对应仍需语义核查 |
| TIDY | 是 | 第2页 2.4 | TIDY SKETCH_CONSTRAINTS；抑制和未用对象检查；无通用乱码语义判定 |
| REAL_RECOGNIZABLE | 是 | 第2页 2.4 | FUNCTIONAL_COMPONENT 人工复核；专门量规提供有限功能证据；名称真实完整需人工判断 |
| FORWARD_DESIGN | 是 | 第2页 2.4 | FORWARD_HISTORY 过程证据；没有导入冻结并有purpose；完整来源过程仍需追溯 |
| NODE_MIN | 是 | 第2页 2.4 | CONTRIBUTING_NODES；实际保留贡献的特征和草图≥20；不是买方计数认证 |
| BUYER_COUNT | 是 | 第2页 2.4 | 外部缺口；无买方管线结果，不能由内部候选计数替代 |
| BATCH_DEDUP | 是 | 第3页 2.5 | canonical检查 双图去重；内部支持域保守判定；新件与此前历史共5750对 |
| FAMILY_ID | 是 | 第3页 2.5 | METADATA 队列精确匹配；family和architecture等记录；仍应人工核对系列划分 |
| INVENTORY_DEDUP | 是 | 第3页 2.5 | 外部缺口；内部108历史不等于买方全部库存或公开参考库 |
| BONUS_REQUIREMENTS | 加分 | 第3页 3 | requirements operations；有名义需求和设计计划；不自动等于真实客户原始规格 |
| BONUS_DRAWING | 加分 | 第3页 3 | 可选图纸与视觉审查；93套含PDF和DXF，15新件缺图；非19项必选门禁 |
| BONUS_PHOTO | 加分 | 第3页 3 | 未提供通用证明；渲染图不能当实物照片 |
| BONUS_DESIGN_NOTE | 加分 | 第3页 3 | purpose relations 源规划；保留意图和名义尺寸理由；外部规格来由另核 |
| BONUS_OPENSCAD | 加分 | 第3页 3 | 未提供通用交付；本批是FreeCAD，不以缺SCAD阻止当前必选通过 |
| BONUS_AUTHOR_RENDER | 加分 | 第3页 3 | 独立加分缺口；八张规定灰图不自动证明作者高质量渲染加分 |
| BONUS_PARAMETRIC | 加分 | 第3页 3 | Native ExpressionEngine；实际有表达式依赖；逐件以真实Native证据为准 |
| BONUS_CONFIG | 加分 | 第3页 3 | 不增加数量；没有通用多配置交付能力；即使有仍计一件 |

## 附录 B 技术证据索引

S 为本次核查的只读源码副本根，B 为历史业务根，D 为完整交付目录，路径定义见第 2 章。JSON 定位使用键名，Python 定位使用函数名；`evidence/source_index.json` 提供资产根及相对路径、字节数、SHA256 和源码 ZIP 一致性，便于接手者验证。

### S01 本次只读盘点

- `${LEGACY_V1_DOC_ROOT}/evidence/fresh_inventory.json`



### S02 最终交付说明与格式清单

- `D/summaries/FINAL100_PROJECT_NOTES.txt`

- `D/summaries/final100_format_completeness.json`



### S03 便携说明与依赖

- `S/README.txt`

- `S/workflow/requirements.txt`



### S04 便携入口

- `S/portable_workflow.py`



### S05 队列控制器

- `S/workflow/run_queue.py`



### S06 生产上限

- `S/workflow/production_goal.py`

- `S/workflow/production_goal_current.json`



### S07 当前十九项门禁

- `S/workflow/current_delivery.py`

- `S/workflow/user_current_profile_v1.json`



### S08 原生构造器

- `S/workflow/workflow_source/scripts/build_pilot.py`



### S09 原生显示与投影支持

- `S/workflow/workflow_source/scripts/native_support.py`



### S10 独立原生绑定与功能分派

- `S/workflow/checks/native_internal_inspect.py`



### S11 独立质量和参数编辑

- `S/workflow/checks/native_quality.py`



### S12 固定视图复核

- `S/workflow/checks/verify_native_views.py`



### S13 图去重与旧原型入口

- `S/workflow/checks/graph_dedup.py`

- `S/workflow/workflow_source/scripts/run_workflow.py`



### S14 最后一件设计配方

- `S/workflow/oc001_recipe_definition.py`



### S15 最后一件队列

- `S/workflow/queue_oc001.json`



### S16 早期集成测试队列

- `S/workflow/queue_integration.json`



### S17 视觉完成和解析域补充的历史记录

- `S/workflow/source_evidence/batch9_tb_cb_report.json`

- `S/workflow/source_evidence/batch10_rj_vc_completion_report.json`



### S18 原采购规则及锁定来源

- `S/workflow/workflow_source/procurement_acceptance/rules.json`

- `S/workflow/workflow_source/procurement_acceptance/rules.lock.json`

- `S/workflow/workflow_source/reference/procurement_requirements.pdf`

- `S/workflow/workflow_source/procurement_acceptance/README.md`



### S19 贡献证明和恢复补充绑定

- `S/workflow/checks/contribution_proof.py`

- `S/workflow/restoration_results.py`



### S20 TC RF CA 原失败和检查修订

- `S/workflow/source_evidence/batch6_failure_revision_register.json`

- `S/workflow/source_evidence/batch5_jc_tc_report.json`



### S21 RC 原失败和真实串腔负例

- `S/workflow/source_evidence/batch8_cg_rc_report.json`



### S22 QC 参数依赖修复

- `S/workflow/source_evidence/batch7_vr_qc_report.json`

- `S/workflow/source_evidence/batch7_failure_register.json`



### S23 P001 肋板修订合同

- `S/workflow/workflow_source/revision_contract.json`

- `S/workflow/workflow_source/README.md`



### S24 HS 原失败和修订记录

- `S/workflow/source_evidence/hs001_native_failure_depth_dependency_revision.json`

- `S/workflow/source_evidence/hs001_frozen_independent_inner_edge_edit_diagnosis.json`

- `S/workflow/source_evidence/hs001_revisionC_recipe.json`

- `S/workflow/source_evidence/batch9_hs_aw_report.json`



### S25 挂起和最终失败留存

- `S/workflow/held_recipe_catalogue.json`

- `S/workflow/source_evidence/cy001_bounded_void_failure_held_checkpoint.json`

- `D/summaries/final100_original_failure_register.json`



### S26 累计历史与当前配方目录

- `B/history_index.json`

- `S/workflow/recipe_catalogue.json`



### S27 图纸与八图生成

- `S/workflow/render_selected.py`

- `S/workflow/workflow_source/scripts/package_pilot.py`

- `S/workflow/workflow_source/scripts/drawing_support.py`



### S28 OC001 最终技术及视觉结果

- `S/workflow/source_evidence/oc001_final_report.json`

- `S/workflow/source_evidence/oc001_completion_report.json`

- `D/models/100_unique_new/OC001/validation_summary.json`

- `D/models/100_unique_new/OC001/dedup_result.json`



### S29 停止恢复和最终打包运行记录

- `D/summaries/final100_runtime_validation.json`



### S30 历史专用封存脚本

- `S/workflow/source_evidence/package_final100_standalone.py`



### S31 前次只读审计与未实现需求

- `${LEGACY_AUDIT_ROOT}/FreeCAD_readonly_audit_20261018.txt`




## 附录 C 随附文件的用途

- `templates/queue_schema_example.json`：按真实字段给出占位模板，尚未注册 recipe，不能直接生产。
- `templates/review_record_template.json`：后续人工与模型复核记录建议格式，不是现成控制器 schema。
- `templates/failure_feedback_template.json`：后续生成式修复建议格式，不是已运行 Qwen 协议。
- `evidence/pdf_rule_mapping.csv`：完整 40 条映射，便于 agent 程序读取。
- `evidence/fresh_inventory.json`：本次只读盘点，不是新 CAD 验收报告。
- `evidence/source_index.json`：本手册全部 S 引用的路径与哈希。
- `evidence/pdf_rule_inventory.json` 与 `procurement_pdf_text.txt`：冻结规则副本及原 PDF 提取文本（技术引用，不是新的用户元数据要求）。
- `evidence/handbook_validation.json`：本交接文档的内容与引用检查结果，不是零件验收证书。

Word 版本与本 Markdown 同源生成，供人工阅读；程序化命令复制和后续 agent 使用优先阅读 Markdown。完整工程仍须从第 2 章所列 ZIP 与模型资产取得。

## 附录 D DSH 技术证据索引

P、H、C、W 的资产根映射见第 26 章和本版 README。以下文件仅作为技术证据；没有把用户会话正文、凭据或 SQLite 原始库打入文档包。`evidence/dsh_source_index.json` 保存可配置资产根、相对路径、字节数、SHA256、行数和 Python 顶层函数定位。

### D01 已安装插件身份

- `P/package.json`
- `P/index.js`
- `P/cordis.patch.yml`

### D02 看板客户端

- `P/lib/client.js`
- `P/locale/zh.json`

### D03 动作分派和快照

- `P/python/entry.py`
- `P/python/cadkit/project.py`

### D04 构造与参数表

- `P/python/cadkit/docctx.py`
- `P/python/cadkit/contract.py`

### D05 机器验收

- `P/python/cadkit/validate.py`

### D06 参数探针

- `P/python/cadkit/probe.py`

### D07 导出与渲染

- `P/python/cadkit/exporter.py`
- `P/python/cadkit/render.py`

### D08 交付包装

- `P/python/cadkit/packager.py`

### D09 事务账本

- `P/python/cadkit/ledger.py`

### D10 指纹与版本

- `P/python/cadkit/fingerprint.py`
- `P/python/cadkit/version.py`

### D11 内存治理

- `P/python/cadkit/mem.py`

### D12 批处理调度

- `H/tools/fleet_runner.py`
- `H/tools/plugin_source.py`

### D13 任务拆分

- `H/tools/emit_members.py`
- `H/tools/build_catalog.py`
- `H/tools/families_500.py`
- `H/tools/kickoff.py`

### D14 收货与旧件复检

- `H/tools/merge_check.py`
- `H/tools/review_scan.py`
- `H/tools/engine_predicates.py`

### D15 共享历史与教训

- `H/tools/registry_sync.py`
- `H/tools/fingerprint_merge.py`
- `H/tools/lesson_ingest.py`
- `H/tools/lesson_add.py`

### D16 签发版本与批量协作文档

- `H/shared/versions.json`
- `H/WORKFLOW.md`
- `H/plugin/README.md`

### D17 审查记录与试点入口

- `C/tools/record_approval.py`
- `C/tools/gen_receipts.py`
- `C/tools/pilotctl.py`
- `C/tools/strict_pass.sh`

### D18 第十一轮历史审计

- `C/audit/findings_round11_2026_10_31.json`
- `C/audit/review_round11_brief.md`

### D19 安装和设计说明

- `P/README.md`
- `P/INSTALL.md`
- `W/dsh-cad-plugin-design.md`

### D20 仓库副本

- `H/plugin/dsh-plugin-cad-freecad/package.json`
- `H/plugin/dsh-plugin-cad-freecad/python/cadkit/probe.py`
- `H/plugin/dsh-plugin-cad-freecad/python/cadkit/packager.py`

### D21 本次 DSH 只读盘点

- `${LEGACY_V2_DOC_ROOT}/evidence/dsh_readonly_inventory.json`

随附统一 JSON 模板均为实施建议，带 PROPOSED_NOT_IMPLEMENTED 标记；CSV 提供状态映射建议或规则对应索引，不代表执行结果。Word 版本供阅读，命令与路径复制优先使用 Markdown；本机 qa 目录中的中间渲染不属于交付物。

## 附录 E 本轮仓库和五机证据

F01 为 evidence/repository_investigation.json，记录已连接仓库身份、本机 A 盘存与远端机器的 UNKNOWN 边界。F02 为冻结提交的 status/PROGRESS.md；B 的数字仅为 self-report，不能充当实物接受证据。F03 为 evidence/repository_asset_inventory.json，列出当时仓库全部文件大小与 SHA，不包含生产模型。F04 为 catalog/families.json、tools/build_catalog.py 和 tools/catalog_lint.py，支撑旧 500 条与配置枚举边界。完整定位在 evidence/fleet_source_index.json。

公开版通过 root_key + relative_path + SHA256 重建引用。config/asset_roots.example.json 列所需根，tools/verify_documentation_assets.py 仅核对文件，不导入 CAD/插件，不启动服务；缺少外部资产报告 MISSING/UNKNOWN，不能把仅文档当完整可运行系统。README 给出该脚本的实际用法。新模板都是 PROPOSED_NOT_IMPLEMENTED，需实现并回归后才能接入现有 runner。
