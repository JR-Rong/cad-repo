#!/usr/bin/env python3
"""Lint the plan before anyone cuts metal: catalog ↔ intake ↔ briefs consistency.

Five machines working from one list fails in predictable ways, all of them
expensive to discover late:
  * two machines assigned the same family or the same topology intent
    (that is a spec-2.5 collision, and only one of them will be counted);
  * an intake manifest that drifted from the catalog (the contract B..E work
    against stops matching what A issued);
  * a project id that doesn't carry its machine prefix (cross-machine
    provenance and dedupe attribution both key off that prefix).

Exit 0 = plan is internally consistent.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SPEC_CATEGORIES = {"机械零件", "机电产品", "电子元器件封装", "结构件", "工装夹具", "消费品"}
BATCH = {"A": "fleet_a_2026_11", "B": "fleet_b_2026_11", "C": "fleet_c_2026_11",
         "D": "fleet_d_2026_11", "E": "fleet_e_2026_11"}


def main():
    doc = json.load(open(os.path.join(REPO, "catalog", "parts.json"), encoding="utf-8"))
    parts = doc["parts"]
    problems, notes = [], []
    seen_pid, seen_topo, fam_owner = {}, {}, {}

    planned = [p for p in parts if p["status"] == "planned"]
    for p in parts:
        pid, m = p["pid"], p["machine"]
        if pid in seen_pid:
            problems.append("project id 重复: %s（%s 与 %s）" % (pid, seen_pid[pid], m))
        seen_pid[pid] = m
        if m not in BATCH:
            problems.append("%s: 未知机器 %s" % (pid, m))
        if p["grade"] not in ("S", "A", "B", "C"):
            problems.append("%s: 目标分级 %r 不在 S/A/B/C" % (pid, p["grade"]))
        if p["category"] not in SPEC_CATEGORIES:
            problems.append("%s: 类目 %r 不在规格 2.1 列举范围内" % (pid, p["category"]))
        if p["status"] == "planned":
            if not pid.startswith(m.lower() + "_"):
                problems.append("%s: project id 前缀必须是 %s_（跨机溯源靠它）" % (pid, m.lower()))
            topo = re.sub(r"\s+", "", p["topo"])
            if len(topo) < 8:
                problems.append("%s: 没写清与兄弟件的拓扑差异（判重靠这句）" % pid)
            if topo in seen_topo:
                problems.append("拓扑描述与 %s 完全相同: %s / %s" % (seen_topo[topo], pid, topo[:40]))
            seen_topo[topo] = pid
            owner = fam_owner.setdefault(p["family"], m)
            if owner != m:
                problems.append("族谱 %s 同时分给了 %s 和 %s（必须整族归一台）" % (p["family"], owner, m))

    # intake must equal the catalog slice, exactly
    for m, batch in BATCH.items():
        want = sorted(p["pid"] for p in planned if p["machine"] == m)
        path = os.path.join(REPO, "intake", batch + ".json")
        if not want:
            if os.path.exists(path):
                problems.append("intake/%s 存在但清单里没有 %s 机的件" % (batch, m))
            continue
        if not os.path.exists(path):
            problems.append("缺 intake/%s.json（%s 机的合同）" % (batch, m))
            continue
        got = sorted(json.load(open(path, encoding="utf-8"))["parts"])
        if got != want:
            problems.append("intake/%s 与清单不一致: 多 %s / 少 %s"
                            % (batch, [g for g in got if g not in want] or "-",
                               [w for w in want if w not in got] or "-"))
        brief = os.path.join(REPO, "briefs", "BRIEF-FOR-%s.txt" % m)
        if not os.path.exists(brief):
            problems.append("缺 briefs/BRIEF-FOR-%s.txt" % m)
            continue
        bt = open(brief, encoding="utf-8").read()
        missing = [w for w in want if w not in bt]
        if missing:
            problems.append("BRIEF-FOR-%s.txt 少了 %d 件: %s" % (m, len(missing), ", ".join(missing[:4])))

    # fingerprints baseline files: one per machine, owned by that machine only
    for path in sorted(glob.glob(os.path.join(REPO, "shared", "fingerprints", "*.json"))):
        machine = os.path.splitext(os.path.basename(path))[0]
        if machine not in BATCH:
            problems.append("shared/fingerprints/%s 不属于任何机器（只允许 <A..E>.json）"
                            % os.path.basename(path))
            continue
        entries = json.load(open(path, encoding="utf-8")).get("entries", {})
        foreign = [pid for pid in entries
                   if re.match(r"^[a-e]_", pid) and not pid.startswith(machine.lower() + "_")]
        if foreign:
            problems.append("shared/fingerprints/%s.json 里有别人前缀的条目: %s"
                            % (machine, ", ".join(foreign[:4])))
        notes.append("底表 %s.json: %d 件" % (machine, len(entries)))

    print("清单: %d 件（待做 %d / 已交付 %d）；族谱 %d 个；机器 %d 台"
          % (len(parts), len(planned), len(parts) - len(planned),
             len({p["family"] for p in planned}), len(BATCH)))
    for n in notes:
        print("   note   " + n)
    for p in problems:
        print("   BLOCK  " + p)
    print("catalog_lint:", "OK" if not problems else "%d 个问题" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
