#!/usr/bin/env python3
"""Materialise part ids from the 500-family design space (deterministic).

The 200,000 rows are deliberately not committed: they are recomputed from
(machine, family, index). This turns a wave into the intake file the ledger and
merge_check actually compare against, so "what was ordered" stays a fact.

  tools/emit_members.py --machine A --wave 1 > intake/waves/fleet_a_2026_11_w1.json
  tools/emit_members.py --family md01 --csv
  tools/emit_members.py --machine E --list-families
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_catalog as B                                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", choices=B.MACHINES)
    ap.add_argument("--wave", type=int)
    ap.add_argument("--family")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--list-families", action="store_true")
    a = ap.parse_args()
    fs = B.load()

    if a.list_families:
        for f in fs:
            if not a.machine or f["machine"] == a.machine:
                print("%s %s w%-2d %-6s %4d 件  %s" % (f["machine"], f["key"], f["wave"],
                      f["arch"], f["members"], f["zh"]))
        return 0

    if a.family:
        sel = [f for f in fs if f["key"] == a.family]
    else:
        if not (a.machine and a.wave):
            raise SystemExit("需要 --machine + --wave，或 --family，或 --list-families")
        sel = [f for f in fs if f["machine"] == a.machine and f["wave"] == a.wave]
    if not sel:
        raise SystemExit("没有匹配族谱")

    rows = []
    for f in sel:
        rows += B.members_of(f)

    if a.csv:
        w = None
        import csv
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["project_id", "object", "category", "grade", "family", "size", "topo_signature"])
        for r in rows:
            w.writerow([r["pid"], r["object"], r["category"], r["grade"], r["family"],
                        r["size"], r["sig"]])
        return 0

    batch = "fleet_%s_2026_11" % (sel[0]["machine"].lower() if len({s["machine"] for s in sel}) == 1
                                  else "multi")
    doc = {"batch": batch, "wave": a.wave, "machine": sel[0]["machine"],
           "generated_by": "tools/emit_members.py",
           "families": [f["key"] for f in sel],
           "parts": [r["pid"] for r in rows],
           "detail": [{"project_id": r["pid"], "title": r["object"], "family": r["family"],
                       "category": r["category"], "grade": r["grade"], "unit": "mm",
                       "type": "part", "wave": r["wave"], "topo_signature": r["sig"],
                       "size": r["size"]} for r in rows],
           "notes": "成员的 topo_signature 必须落到 design.md 的具体特征数；等比缩放不算差异。"}
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    sys.stderr.write("emit: %d 件（族谱 %s）\n" % (len(rows), ",".join(f["key"] for f in sel)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
