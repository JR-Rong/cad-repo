#!/usr/bin/env python3
"""生成发给某台机器的开场指令（贴进它会话即用）。

存在的理由：手写的开场白必然过期（LESSONS_ACK、握手值、波次内容每周都在变）。
这份是从 `catalog/families.json` + `shared/versions.json` + `LESSONS.md` 现算的，
所以每次派发前先 `git pull && python3 tools/kickoff.py --machine C --write` 生成一份，
贴出去就是当天的真话。

  python3 tools/kickoff.py --machine C            # 打到终端，直接复制
  python3 tools/kickoff.py --all --write          # 生成 briefs/KICKOFF-A..E.txt
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load():
    cat = json.load(open(os.path.join(REPO, "catalog", "families.json"), encoding="utf-8"))
    ver = json.load(open(os.path.join(REPO, "shared", "versions.json"), encoding="utf-8"))
    lessons = open(os.path.join(REPO, "LESSONS.md"), "rb").read()
    dl = json.load(open(os.path.join(REPO, "catalog", "delivered.json"), encoding="utf-8"))
    return cat, ver, hashlib.sha256(lessons).hexdigest()[:8], dl


def slice_of(cat, machine, wave):
    fams = [f for f in cat["families"] if f["machine"] == machine and f["wave"] == wave]
    m = cat["machines"][machine]
    return fams, m


def render(machine, wave, cat, ver, ack, delivered_cnt):
    fams, m = slice_of(cat, machine, wave)
    if not fams:
        raise SystemExit("机器 %s 没有第 %d 波" % (machine, wave))
    letters = {"A": "（本机既有生产会话：用配置里的 cadRoot，不要改成 ~/cad-fleet）",
               "B": "（外来机：cadRoot 用 ~/cad-fleet）",
               "C": "（外来机：cadRoot 用 ~/cad-fleet）",
               "D": "（外来机：cadRoot 用 ~/cad-fleet）",
               "E": "（外来机：cadRoot 用 ~/cad-fleet）"}
    fam_rows = "\n".join(
        "     %s…001–…%03d  %s（%s 轴族 / %s，%d 件，结构轴组合 %d 种）"
        % (f["id_prefix"], f["members"], f["zh"], f["arch"], f["category"],
           f["members"], f["axis_space"]) for f in fams)
    root = ("<memcheck 回报里的 cad_root，别改成 ~/cad-fleet>" if machine == "A"
            else "~/cad-fleet")
    defects = "\n".join("     - %s：%s" % (k, v) for k, v in (ver.get("known_plugin_defects") or {}).items())
    return """任务：你是机器 {M}，执行批次 {B} 的第 {W} 波 —— {NF} 个族谱 × 400 成员 = {NP} 件
（你的总量：{MF} 个族谱 / {MP} 件 / {MW} 波。其它机器的批次一律不要碰。）{L}

批次全貌：500 个族谱 × 400 成员 = 200,000 件，A–E 各 100 族谱、各 20 波。
{NF} 个族谱的构成（本波就是这 {NF} 个，别的波次别自作主张往前跑）：
{FAMS}

━━━━━━━━━━ 版本握手（有一项对不上就停工上报，别自己找引擎）━━━━━━━━━━
  rules_sha        {RS}
  插件版本         {PV}   tarball {TB}
  tarball sha256   {TS}
  LESSONS_ACK      {ACK}      ← 你回传的 REPORT.md 必须带这一行，缺它或它过期 = 直接 REJECT
  校验：`cad_freecad memcheck` 与 `python3 tools/refresh_versions.py` 不该由你跑（A 机动作），
        但 memcheck 回报里的 rules_sha 必须等于上面这个值。
  未修的插件缺陷（生产时必须规避）：
{DEFECTS}

━━━━━━━━━━ 开工前（顺序固定，三条）━━━━━━━━━━
  0. git clone git@github.com:JR-Rong/cad-repo.git {ROOT_REPO} && cd {ROOT_REPO}
     安装插件包 handoff/releases/{TB}（装完必须重启 DSH，插件是加载期读配置的），
     确认 freecadcmd 可用（默认 /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd）。
  1. python3 tools/recall.py --machine {M}
       —— 打印教训全文 + 项目记忆 + 错题本未关闭项 + 本波切片；把里面的 LESSONS_ACK 抄进 REPORT.md。
  2. python3 tools/registry_sync.py --root {ROOT}
       —— 非 0 就停下喊人（它找不到判重函数现在会失败关闭，不会假装检查过）。
  3. mkdir -p intake/waves && python3 tools/emit_members.py --machine {M} --wave {W} \\
         > intake/waves/{B}_w{W}.json
       —— 这就是本波 {NP} 件的合同；此后任何一件都不能偏离它（件号 = 机器_族谱_三位序号，
          由清单确定性重算，别自己编号）。

━━━━━━━━━━ 生产（双通道并行，不要手写 shell 循环）━━━━━━━━━━
  python3 tools/fleet_runner.py --root {ROOT} --batch {B} \\
      --intake intake/waves/{B}_w{W}.json \\
      --stages build,validate,probe,export --package \\
      --workers 3 --mem-budget-mb 6144 --per-part-mb 600 \\
      --review-every 6 --review-interval-secs 900 --machine {M}{RVLINE}
{RVNOTE}
  · 它启动时会用**即将执行的引擎**自校 rules_sha，不一致直接 exit 2 —— 那是它在救你整波。
  · 断点续跑看 chain_evidence.json 的哈希，不看历史 job=OK。崩了重跑同一条命令。
  · `package` 用 `--package-workers`（默认 2，每件一把锁）。必须与四个阶段一起给。
  · 别自己拼 freecadcmd 命令行：freecadcmd 会把 `--action` 吃掉，参数得走 CADKIT_* 环境（调度器已处理）。
  · 判断阶段是否通过只看 `CADKIT>> {{"ok": true, "checks": [...]}}`：**引擎失败时退出码仍然是 0**。

单件顺序（任一 FAIL 不许进下一步，也不许跳步）：
  new → design.md → build → validate → probe → dedupe → export → package
  design.md 必须把该件 `topo_signature`（5 条结构轴取值）落成**具体特征数**：
  哪个特征存在/不存在、筋几条、孔几个、倒角几处 —— "同族只是尺寸不同"不算差异，
  等比缩放会被判 scaled_same 不计数（采购需求 2.5）。params 表是唯一驱动源，
  关掉其特征组的参数必须从表里删掉（探针逐格 +1% 扰动，惰性单元格必 FAIL）。

━━━━━━━━━━ 错题本（这是共享资产，不是可选项）━━━━━━━━━━
  当天发现当天记，一行命令：
    python3 tools/lesson_add.py --machine {M} --source self|audit|review_scan --severity P1 \\
        --title "症状" --where "file:line 或件号" --detail "证据（期望 vs 实测）"
  · 只写你自己的 lessons/inbox/{M}.jsonl，**不要改 LESSONS.md**，更不要伪造别机的 inbox；
  · A 机合并去重后 LESSONS_ACK 会轮换，你必须重跑 recall 才能继续交付；
  · 旧件也要被审：tools/review_scan.py --root {ROOT} --batch <你已交付的批次> --to-lessons

━━━━━━━━━━ 本波收尾（{NP} 件全部 package 之后）━━━━━━━━━━
  1. python3 tools/merge_check.py --mine {ROOT} --theirs outbox/{B} \\
         --baseline shared/fingerprints --intake intake/waves/{B}_w{W}.json
     —— 它会**一次列出全部 blocking**，把清单清零再 push，别修一个看一眼。
  2. 回传：outbox/{B}/（delivery 批次目录、ledger.jsonl、fingerprints.delta.json、versions.json、
     REPORT.md 含 LESSONS_ACK）、更新 shared/fingerprints/{M}.json（只这一个文件）、
     status/PROGRESS.md 末尾追加一行、intake/waves/{B}_w{W}.json 一起 push。
  3. push 前 git pull --rebase；`git push` 被拒就 rebase 后重推，冲突通常只在 PROGRESS.md（追加式，两边都留）。
  4. **上一波被 A 机 ACCEPT 且指纹并入底表之前，不许开下一波。**

立刻停下来喊人（别自己绕）：判重命中 / 探针修不过 / 需要把 memCapMb 降到 1536 以下 /
想改清单、族谱或 intake / 插件行为与工单不符 / 磁盘剩余不足 100 GB / 渲染长边掉到 2000px 以下。

全局事实（用来判断自己是不是跑偏）：已交付基线 {DEL} 件（不重复计数），
你的机器分界是 project id 前缀 `{ML}_`，全量清单与自洽性见 catalog/FAMILIES.md、
`python3 tools/catalog_lint.py`（A 机会天天跑它，你 pull 后跑一次也行）。
长版规则：WORKFLOW.md（角色/所有权/波次/收货）、WORK-ORDER.md（技术口径）、
briefs/BRIEF-FOR-{M}.txt（全 {MW} 波族谱清单）、briefs-common.txt（规则全文）。

生成时间 {NOW}（由 tools/kickoff.py 现算；换波次就重新生成一份，别复用旧文本）
""".format(M=machine, ML=machine.lower(), B=m["batch"], W=wave, NF=len(fams),
           NP=len(fams) * 400, MF=m["families"], MP=m["parts"], MW=len(m["waves"]),
           L=letters[machine], FAMS=fam_rows, RS=ver["rules_sha"], PV=ver["engine_plugin_version"],
           TB=ver["tarball"], TS=ver["tarball_sha256"][:24] + "…", ACK=ack, DEFECTS=defects,
           RVLINE=("" if wave == 1 else chr(10) + "      --review-batch " + m["batch"]),
           RVNOTE=("  · 第 1 波你还没有旧件，所以不加 --review-batch；从第 2 波起加上它"
                   "（同一批次号即可，台账里已 delivered 的那些就是你要审的旧件）。"
                   if wave == 1 else
                   "  · --review-batch 指向同一批次：复检只读台账里已 delivered 的行，"
                   "也就是你前面几波交付的东西。"),
           DEL=delivered_cnt,
           NOW=time.strftime("%Y-%m-%d %H:%M"), ROOT=root,
           ROOT_REPO=("~/cad-fleet-repo" if machine != "A" else "~/cad-fleet-repo-a"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", choices=list("ABCDE"))
    ap.add_argument("--wave", type=int, default=1)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true", help="写 briefs/KICKOFF-<机器>.txt")
    a = ap.parse_args()
    cat, ver, ack, dl = load()
    cnt = len(dl.get("delivered", []))
    targets = list("ABCDE") if a.all else [a.machine]
    if not targets:
        raise SystemExit("给 --machine 一个字母，或用 --all")
    for m in targets:
        text = render(m, a.wave, cat, ver, ack, cnt)
        if a.write:
            p = os.path.join(REPO, "briefs", "KICKOFF-%s.txt" % m)
            open(p, "w", encoding="utf-8").write(text)
            print("写入 %s（%d 字）" % (os.path.relpath(p, REPO), len(text)))
        else:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
