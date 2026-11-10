#!/usr/bin/env python3
"""Pre-flight memory load — mandatory before any generation run.

Prints the lesson list, project memory, the version handshake, the open
defects to route around, and this machine's slice of the 500-family plan —
then stamps a sha of LESSONS.md that must come back in REPORT.md as
LESSONS_ACK (merge_check rejects a stale or missing one). Reading is only
verifiable if it leaves a trace; that is the whole trick.
"""
import argparse
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def sha8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", required=True, choices=list("ABCDE"))
    ap.add_argument("--wave", type=int, default=None, help="只看某一波（默认最低未完成波）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    for rel in ("LESSONS.md", "MEMORY.md", "catalog/families.json"):
        if not os.path.exists(os.path.join(REPO, rel)):
            print("缺 %s —— 先 git pull" % rel, file=sys.stderr)
            return 2
    lessons = open(os.path.join(REPO, "LESSONS.md"), encoding="utf-8").read()
    memory = open(os.path.join(REPO, "MEMORY.md"), encoding="utf-8").read()
    doc = json.load(open(os.path.join(REPO, "catalog", "families.json"), encoding="utf-8"))
    versions = json.load(open(os.path.join(REPO, "shared", "versions.json"), encoding="utf-8"))

    import build_catalog as B
    fs = [f for f in B.load() if f["machine"] == a.machine]
    waves = sorted({f["wave"] for f in fs})
    wave = a.wave or waves[0]
    wf = [f for f in fs if f["wave"] == wave]
    next5 = [m["pid"] for m in B.members_of(wf[0])][:5] if wf else []

    baseline = {}
    for f in sorted(glob.glob(os.path.join(REPO, "shared", "fingerprints", "*.json"))):
        tag = os.path.splitext(os.path.basename(f))[0]
        baseline[tag] = len(json.load(open(f, encoding="utf-8")).get("entries", {}))

    lidx = {}
    lp = os.path.join(REPO, "lessons", "index.json")
    if os.path.exists(lp):
        lidx = json.load(open(lp, encoding="utf-8"))
    lopen = [v for v in lidx.values() if v.get("status") in ("open", "partial")]
    lopen.sort(key=lambda v: (v.get("force") != "必须做进门禁", v.get("severity", "P9"),
                             -v.get("count", 1)))
    out = {"lessons_ack": sha8(lessons),
           "lessons_open": len(lopen),
           "lessons_needs_gate": sum(1 for v in lopen if v.get("force") == "必须做进门禁"),
           "rules_sha_expected": versions.get("rules_sha"),
           "plugin": versions.get("engine_plugin_version"),
           "machine": a.machine, "batch": B.BATCH[a.machine],
           "your_families": len(fs), "your_parts": sum(f["members"] for f in fs),
           "wave": wave, "waves": waves,
           "wave_families": [{"family": f["key"], "object": f["zh"], "archetype": f["arch"],
                              "members": f["members"], "id_prefix": f["id_prefix"]} for f in wf],
           "next_5_pids": next5, "baseline": baseline,
           "open_defects": versions.get("known_plugin_defects", {}),
           "delivered_registered": len(json.load(open(os.path.join(REPO, "catalog",
                                     "delivered.json"), encoding="utf-8"))["delivered"])}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print("=" * 74)
    print("项目记忆加载 · 机器 %s · 批次 %s · 本波 wave %d" % (a.machine, out["batch"], wave))
    print("=" * 74)
    print("LESSONS_ACK: %s     ← 回传 REPORT.md 必须带这一行，merge_check 会核对" % out["lessons_ack"])
    print("版本握手: rules_sha=%s  插件=%s" % (out["rules_sha_expected"], out["plugin"]))
    print("去重底表: %s（合计 %d 件几何已被占用 + 已交付登记 %d 件）"
          % (", ".join("%s=%d" % kv for kv in sorted(baseline.items())),
             sum(baseline.values()), out["delivered_registered"]))
    if lopen:
        print("错题本: %d 条未关闭（其中 %d 条已升级为「必须做进门禁」）。最要命的 %d 条："
              % (len(lopen), out["lessons_needs_gate"], min(6, len(lopen))))
        for v in lopen[:6]:
            print("  - [%s%s] %s%s" % (v.get("severity"),
                                       " ×%d" % v["count"] if v.get("count", 1) > 1 else "",
                                       v.get("title", "")[:78],
                                       "  ← " + v["where"] if v.get("where") else ""))
    print("\n未修缺陷（生产时必须规避，见 WORKFLOW §3/§6）:")
    for k, v in out["open_defects"].items():
        print("  - %s：%s" % (k, v))
    print("\n你的量: %d 个族谱 / %d 件 / %d 波（每波 5 族谱 = %d 件）"
          % (out["your_families"], out["your_parts"], len(waves), len(wf) * (wf[0]["members"] if wf else 0)))
    print("本波族谱:")
    for f in wf:
        print("  - `%s…001–…%03d` %s（%s 轴族，%d 件）"
              % (f["id_prefix"], f["members"], f["object"] if "object" in f else f["zh"],
                 f["arch"], f["members"]))
    print("  展开本波 intake: python3 tools/emit_members.py --machine %s --wave %d "
          "> intake/waves/%s_w%d.json" % (a.machine, wave, out["batch"], wave))
    print("  接下来 5 件: %s" % ", ".join("`%s`" % p for p in out["next_5_pids"]))
    print("\n--- 教训全文（必读，不是目录）---")
    print(lessons.rstrip())
    print("\n--- 项目记忆（现状 + 最近决策）---")
    print(memory.split("## 决策记录")[0].split("## 现状", 1)[-1].strip()[:2200])
    print("\n最近 5 条决策:")
    for line in [l for l in memory.splitlines() if l.startswith("| 20")][-5:]:
        print("  " + line)
    print("\n--- 单件顺序 ---")
    print("  new → design.md（把拓扑签名落成特征数）→ build → validate → probe → dedupe → export → package")
    print("  生产只走 tools/fleet_runner.py（见 RUNBOOK.md）；push 前 git pull --rebase")
    print("\n发现新问题（自己踩的、审查抓的、买方退的）：")
    print("  python3 tools/lesson_add.py --machine %s --source self --severity P1 \\"
          " --title \"症状\" --where \"file:line 或件号\" --detail \"证据\"" % a.machine)
    print("  只写自己的 lessons/inbox/%s.jsonl；A 机 lesson_ingest 合并去重后 LESSONS_ACK 会变，"
          "必须重跑本命令" % a.machine)
    print("  旧件复检（与新件生产并行，别空等）：python3 tools/review_scan.py --root <cadRoot> "
          "--batch <旧批次> --to-lessons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
