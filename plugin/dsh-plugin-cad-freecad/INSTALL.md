# 安装到另一台设备

包：`local-dsh-plugin-cad-freecad-0.5.0.tgz`（npm 包格式，22 个文件 / ~100 KB，**不含任何机器专属路径**）。

## 前提

| 依赖 | 说明 |
|---|---|
| DSH 桌面版（支持插件的构建） | 插件靠 profile 的 `dsh.profile.bundles` + `dsh.bundle.patch` 装载 |
| **FreeCAD**（含命令行 `freecadcmd`） | 引擎跑在 `freecadcmd` 里，**不需要另外装 Python**（FreeCAD 自带） |
| 内存 | 默认单次任务 ≤ 2 GB、整个 freecadcmd 进程树 ≤ 6 GB；4 GB 空闲比较舒服 |
| npm registry 可达 | `dsh plugin add` 会把参数转给 pnpm，pnpm 会校验 profile 里其它依赖 |

`freecadcmd` 会被自动发现（探测顺序，逐个检查可执行位）：

```
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd     macOS 官方包
/Applications/FreeCAD.app/Contents/bin/freecadcmd
/Applications/FreeCAD 1.0.app/…、/Applications/FreeCAD 1.1.app/…
/usr/local/bin、/usr/bin、/opt/freecad/bin、/opt/homebrew/bin    Linux
C:/Program Files/FreeCAD 1.0|1.1/bin/freecadcmd.exe             Windows
再退到 PATH 里的 freecadcmd
```

## 安装

```sh
# 1) 放到稳定位置（pnpm 会按路径记进 profile）
mkdir -p ~/dsh-plugins && cp local-dsh-plugin-cad-freecad-0.5.0.tgz ~/dsh-plugins/

# 2) 装进 profile（名字换成你那套：desktop / default / …）
dsh plugin --profile desktop add ~/dsh-plugins/local-dsh-plugin-cad-freecad-0.5.0.tgz

# 3) 完全退出并重启 DSH（必须：Host 只在装载 Loader 行时扫一次 dsh.client）
```

装对了的话 `~/.dsh/profiles/desktop/package.json` 里会多一条
`"@local/dsh-plugin-cad-freecad": "file:…"`，并且 `dsh.profile.bundles` 里有它。
卸载：`dsh plugin --profile desktop remove @local/dsh-plugin-cad-freecad` 再重启。

## 装完自检（30 秒）

对 Agent 说一句 `cad_freecad memcheck`，返回值里必须能看到**实际用了什么**：

```
cad_root          解析出来的项目树（首次默认 ~/dsh-cad）
cad_root_source   arg | env | config | session-workspace | config-create | home-default
freecad_bin       实际调用的 freecadcmd
freecad_bin_source discovered | config | discovered-path
mem { cap_mb: 2048, … }
```

再跑一条完整链路确认渲染器可用（会新建一个项目，不动你已有数据）：

```
cad_freecad new      opts {"id":"smoke_test","title":"Smoke"}
cad_freecad build    project smoke_test
cad_freecad export   project smoke_test        # 应产出 build/*.step + build/renders/ 8 张 ≥2000px
```

侧边应用栏应出现「CAD 看板」图标（六棱柱）。

## 配置（可选，写在 **profile 层**，不要改插件）

`~/.dsh/profiles/<profile>/cordis.patch.yml` 追加（profile 层覆盖 bundle 层，`~` 会展开）：

```yaml
- id: cad-freecad
  name: "@local/dsh-plugin-cad-freecad"
  config:
    freecadBin: ~/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
    cadRoot: ~/work/cad        # 放这里才能让侧栏面板读到（见下）
    memCapMb: 1536             # 机器紧就降；引擎会据此缩小工作点并自动降级
    groupCapMb: 4096
    probeMaxWorkers: 1
```

`cadRoot` 解析优先级：`opts.root` → 环境变量 `CADKIT_ROOT` → `config.cadRoot`（**存在才用**）→
`<会话工作区>/cad`（存在就用，没有则用 config 创建）→ `~/dsh-cad`。
检查点：一个过时的绝对路径**不会**被静默采用（先查存在性），每次返回都带 `cad_root` 与来源。

## 常见问题

- **`freecadcmd not found (looked at: …)`** —— 按提示把真实路径写进 `config.freecadBin`（或环境变量
  `CADKIT_FREECAD_BIN`）。插件不会瞎猜一个能"看起来成功"的路径。
- **侧栏没有图标** —— 没重启（Host 缓存 `dsh.client` 扫描结果，关开插件也不清这个缓存）；或 profile 的
  `dsh.profile.bundles` 里没有这个包。
- **面板显示"读不到 board"** —— 工作区文件是**会话作用域**的：顶部选一个工作区等于 `cadRoot` 所在工作区的会话；
  或者先跑一次任意动作让引擎写出 `cad/mem/board.json`。想让面板有数据，`cadRoot` 应指向会话工作区下的 `cad/`。
- **`memory_exceeded` / 降级** —— 这是治理在起作用：结果里的 `degraded` 会写明降了什么（长边、超采样、三角面），
  需要更高清就降 `memCapMb` 以上的并发或分小批导出。
- **首次 `validate` 不过** —— `new` 生成的是脚手架（`build.py` 只有最小特征），`step_floor`/`real_object`
  会失败；把尺寸特征写进 `build.py` 后重跑，这是设计如此。

## 这套流水线会做什么

`new` → `build`（特征树 FCStd）→ `validate`（机器验收门）→ `probe`（逐个扰动驱动参数，证明真参数化）→
`export`（STEP AP214 + 8 张交付图 + 几何一致性自检）→ `package`（交付清单逐项硬校验，缺件不出货）→
`dedupe`（几何指纹查重）。全过程三层内存治理：预算定容 → 进程内看门狗（降级→`exit 86`）→ Host 进程组兜底。
