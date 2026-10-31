# 派发给 B / C / D / E（给人看的派发单）

A 线（本机的另一个会话）由 [briefs/BRIEF-FOR-A-LOCAL.txt](briefs/BRIEF-FOR-A-LOCAL.txt) 驱动；
其余四台机器各粘一份**开场指令**就能开工，**每台一份，别混用**。

开场指令一律现生成，不要复用上一次的文本（它带着当天的 `rules_sha`、`LESSONS_ACK` 和本波 5 个族谱）：

```sh
python3 tools/kickoff.py --all --write          # 生成 briefs/KICKOFF-A..E.txt（默认 wave 1，可 --wave N）
cat briefs/KICKOFF-C.txt                        # 整段贴进 C 的会话
```

| 目标机器 | 粘这个文件 | 批次 | 族谱 / 件数 | 类目 |
|---|---|---|---|---|
| A | `briefs/KICKOFF-A.txt`（本机另开会话，长版 `BRIEF-FOR-A-LOCAL.txt`） | `fleet_a_2026_11` | 100 / 40,000 | 电机与驱动附件、传动与减速机构 |
| B | `briefs/KICKOFF-B.txt`（长版 `BRIEF-FOR-B.txt`） | `fleet_b_2026_11` | 100 / 40,000 | 仪器与人机界面、消费电子与电气封装 |
| C | `briefs/KICKOFF-C.txt`（长版 `BRIEF-FOR-C.txt`） | `fleet_c_2026_11` | 100 / 40,000 | 工装夹具、检具量具与测量 |
| D | `briefs/KICKOFF-D.txt`（长版 `BRIEF-FOR-D.txt`） | `fleet_d_2026_11` | 100 / 40,000 | 结构与机架、输送仓储与物料 |
| E | `briefs/KICKOFF-E.txt`（长版 `BRIEF-FOR-E.txt`） | `fleet_e_2026_11` | 100 / 40,000 | 流体与气路、能源与热管理 |

每台各 **20 波**，每波 5 个族谱 = 2,000 件。**第一轮只下 wave 1**（2,000 件），
回传被 ACCEPT、指纹并入底表之后才给 wave 2；每波开工先 `tools/emit_members.py --machine X --wave N`。

## 每台机器的三个前置（缺一个就不要开工）

1. **能拉仓库**：`git clone git@github.com:JR-Rong/cad-repo.git cad-repo`（SSH key 已加到 JR-Rong，或改用 HTTPS + 令牌）。
2. **装了插件**：先跑 `cad_freecad memcheck` 看有没有反应；没有就装
   `releases/local-dsh-plugin-cad-freecad-0.5.0.tgz`（sha256 见 `shared/versions.json`），
   命令 `dsh plugin --profile desktop add <tgz>`，然后**完全退出并重启 DSH**（不重启侧栏图标不会出现，
   这是 Host 缓存 `dsh.client` 扫描结果导致的，不是插件坏了）。安装还会去 npm registry 校验 profile 里的其它依赖，
   完全离线的机器要先解决这一点。
3. **装了 FreeCAD 的 `freecadcmd`**：插件会自动发现（macOS 的 `FreeCAD.app/Contents/Resources/bin/freecadcmd`、
   Linux/Windows 常规安装位、再退到 PATH）。报 `freecadcmd not found (looked at: …)` 就把它列的候选路径核对一下，
   写进 profile 层 `cad_freecad` 配置的 `freecadBin`，**不要改插件里的 `cordis.patch.yml`**。

## 开工（粘完开场指令，它自己会跑这四步）

```sh
python3 tools/recall.py --machine C                  # 教训 + 记忆 + 错题本 + 握手 + 本波切片（盖 ACK）
python3 tools/registry_sync.py --root ~/cad-fleet    # 全局去重底表，非 0 就停下喊人
python3 tools/emit_members.py --machine C --wave 1 > intake/waves/fleet_c_2026_11_w1.json
python3 tools/fleet_runner.py --root ~/cad-fleet --batch fleet_c_2026_11 \
    --intake intake/waves/fleet_c_2026_11_w1.json --stages build,validate,probe,export --package \
    --workers 3 --review-batch fleet_c_2026_11 --machine C
```
第一条不可跳：REPORT.md 里没有 `LESSONS_ACK` 或它过期，收货直接 REJECT。
第四条不可用手写循环替代：`package` 必须单槽串行，且启动时会自校引擎握手。

`registry_sync` 是防重复的第一道闸：它把五台机器的几何底表合成本机 `~/cad-fleet/registry/fingerprints.json`，
并在底表内部已经存在 `exact`/`scaled_same` 时**返回非 0**。非 0 的时候不要开工，那说明清单出了问题。

## 怎么判断它干得对（不要听它自己说）

它自己会先跑 `tools/merge_check.py`，`ACCEPT` 才 push。你收到 push 后，在 A 机复核一遍：

```sh
cd handoff && git pull --rebase
python3 tools/merge_check.py --mine cad --theirs outbox/fleet_b_2026_11 --baseline shared/fingerprints
python3 tools/catalog_lint.py
```

`merge_check` 挡的是硬事实：intake 有但没交付、缺 FCStd/STEP、STEP 不是 AP214/AP242、渲染不足 8 张或长边 <2000px、
台账五阶段没走全、与任何一台机器的几何构成 `exact`/`scaled_same`。它用的是插件里出厂的判定函数
（`tools/engine_predicates.py` 注入 FreeCAD 桩），不是抄一份规则副本。

## 你要留意的三件事

- **进度看** `status/PROGRESS.md`（各机器末尾追加一行），**别在聊天里问它做到哪了** —— 那个答案不如文件可靠。
- **它喊人是正常的**：判重命中、探针修不过、要把 `memCapMb` 降到 1536 以下、想改清单，这四类情况工单里明确要求停下问人。
  绕过这些限制交出来的件，大概率是判重不计数或 2.4 退回。
- **别让它改 `shared/fingerprints/` 里别人的文件**（lint 会报，但 git 层面前提交还是得你把关）。
