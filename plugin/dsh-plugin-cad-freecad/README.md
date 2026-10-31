# dsh-plugin-cad-freecad

FreeCAD 参数化 CAD 流水线插件：建模 → 机器验收门 → 可编辑性探针 → STEP/渲染导出 → 交付打包，
全程受内存治理约束；并带一个**侧边应用栏面板**（Client 插件）实时展示看板。

- Host 半：<kbd>cad_freecad</kbd> 工具（`index.js` + `python/entry.py` + `python/cadkit/*`）
- Client 半：`lib/client.js`（`package.json` → `dsh.client` + `exports["./client"]`）
- 设计与实测记录：`../dsh-cad-plugin-design.md`
- 装到另一台设备：[INSTALL.md](INSTALL.md)（离线 tarball 安装、自动发现、自检、配置层）

## 动作

| action | 作用 |
|---|---|
| `new` `build` | 建项目骨架 / 跑 `build.py` 生成特征树 FCStd |
| `validate` | 机器验收门（重建、约束、有效实体、尺寸、整洁、步数等级）→ `build/report.json` |
| `probe` | 逐个扰动驱动参数，证明真参数化 → `build/probe.json` |
| `export` | STEP AP214 + 8 张交付图 + 几何一致性自检 |
| `package` | 交付清单逐项硬校验后出交付夹 + 批次 CSV |
| `dedupe` | 几何指纹查重（可 `register=1` 入注册表） |
| `status` | 单项目状态摘要 |
| `memcheck` | 内存预算 + 下一次 export 的工作点（不碰几何） |
| `board` | 重建 ` <cadRoot>/mem/board.json` 面板快照并返回 |

每个结果都带 `mem` 块（上限 / 实时 RSS / 峰值 / `degraded` 降级明细）。内存治理三层（预算定容、
进程内看门狗、Host 进程组兜底）见设计文档 §11。

## 侧边应用栏面板

- 入口：应用栏 `CAD 看板`（slot `sidebar.panellist`，id `cad-freecad`），面板注册在根作用域键槽 `main`（同 id）。
- 数据：面板通过 `ctx.remote.workspaceFiles.read(sessionId, "cad/mem/board.json")` 读取；
  该快照由引擎在**每次动作后**重写（`entry.py: record_action/build_board`），面板 8 s 轮询、页面隐藏时暂停。
  顶部可选读取哪个会话（工作区文件是会话作用域的）。
- 动作按钮：`ctx.uiWorkspace.startSession(undefined, { prompt, whenDraftExists: "replace" })`
  —— 点一下得到一个已写好的提示草稿，而不是半截命令；无该服务时退回写剪贴板。
- `cadRoot` 默认 `<工作区>/cad`，所以 `BOARD_PATH` 是工作区相对路径；若你把 `cadRoot` 指到别处，
  改 `lib/client.js` 顶部这一行常量即可。

## 客户端 bundle 是手写的

桌面 Host 只服务**已构建**的 client bundle（`dsh-client-modules`：`dsh.client.platform:"web"` +
`exports["./client"]` 指向真实文件），本插件不引入构建链，所以 `lib/client.js` 本身就是构建产物：
`window.__ModuleLoader__.load({ id, factory })` 线格式，`require()` 只用基线模块表
（react / react-dom / react/jsx-runtime / @deepseek-ai/{cordis,dsh-client-store,
dsh-client-ui-slots,dsh-client-ui-primitives,dsh-client-ui-dockkit} / zod / @tanstack/react-virtual），
其余服务一律走 `dsh.client.inject` + `ctx.<service>`。要改用真构建链时，替换 `exports["./client"]` 即可。

## 测试

```sh
node test/client-board.test.mjs     # 声明校验 + 线格式 + 注册 + 真实看板渲染 + 读文件/派发实路
```

覆盖：`dsh.client` 通过 Host 侧两个校验函数；loader id 必须等于包名；`main` 键必须等于
`sidebar.panellist` id；用真实 `cad/mem/board.json` 渲染；空态/不可读/降级三种状态；
effect 实跑（断言只读最新会话的 board）；点按钮断言 `startSession` 收到的 prompt。

## 生效方式

改了 `index.js` / `lib/client.js` / `cordis.patch.yml` 需要**重启 DSH**（或在 Settings → Plugins 里
把本插件关掉再打开）：Host 在装载 Loader 行时扫描 `dsh.client` 并把 bundle 挂到 `/plugins`。
Python 引擎每次调用都是新进程，改完即生效。
