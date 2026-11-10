#!/usr/bin/env python3
"""Group the 14 BROKEN b_in01 parts by the catalog's parameter axes.

This does not open a CAD file, delete an export, or rewrite a ledger.
It only names which design-space coordinate those indices share, so a
repair rebuilds them as a new revision instead of patching the old STEP.

The indices look periodic (128, 132, … 148) because ``members_of`` walks
the 壳体 axis product sorted by (sum, tuple), and axis 3 has four cable-hole
values. That stride is not one size on the sz ladder (the ladder length
is 8). Eleven of the fourteen sit on axis 3 =「三孔 + 航空插座座」.
b_in01_121 and b_in01_161 do not: the binding audit already separated
them as native validate FAILs. They are quarantined with the others and
are not marked design-infeasible from this table — that label needs an
explicit waiver reason after a human looks at the rebuilt geometry.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_catalog as B  # noqa: E402

PIDS = [
    "b_in01_082", "b_in01_097", "b_in01_112", "b_in01_121", "b_in01_125",
    "b_in01_128", "b_in01_132", "b_in01_136", "b_in01_140", "b_in01_144",
    "b_in01_148", "b_in01_154", "b_in01_158", "b_in01_161",
]
NATIVE_FAIL = {"b_in01_121", "b_in01_161"}
ORPHAN_EXPORT = {"b_in01_082"}
# 097 is the only BROKEN index that is neither the aviation-connector
# cluster nor a native validate failure. Binding audit: STEP/manifest
# still certify the pre-rebuild native (FCBak sha matches the manifest),
# the surviving FAIL receipt names V2 objects the V4 core deleted
# (trough/cbore/tab), and the ledger probe PASS has receipt_sha='-'.
ROOT_097 = (
    "不是航空插座轴，也不是 121/161 那种重建后仍 validate FAIL。"
    "绑定审计：现行 FCStd 是后重建的，STEP 与 manifest 仍认证重建前的 native"
    "（.FCBak 与 manifest.fcstd_sha 一致，112 则对不上）。"
    "留下的 FAIL 收据描述的是 V2 特征 trough/cbore/tab，V4 核已删掉这些对象，"
    "收据说的修订不在当前文件里。台账 probe 行 receipt_sha='-'，"
    "那条 PASS 没有文件哈希。处理：保留原件，按新修订重跑 validate→export，"
    "不要用这条旧 FAIL 或占位回执当结论。"
)


def rows_for():
    fam = next(f for f in B.load() if f["key"] == "in01" and f["machine"] == "B")
    members = {m["pid"]: m for m in B.members_of(fam)}
    axes = B.ARCHETYPES[fam["arch"]]
    out = []
    for pid in PIDS:
        member = members[pid]
        parts = member["sig"].split("；")
        out.append({
            "project_id": pid,
            "index": int(pid.rsplit("_", 1)[1]),
            "size": member["size"],
            "topo_signature": member["sig"],
            "axes": [{"axis": i, "value": parts[i]} for i in range(len(parts))],
            "audit_class": (
                "native_validate_fail" if pid in NATIVE_FAIL
                else "orphan_reexport" if pid in ORPHAN_EXPORT
                else "stale_export"),
            "root_cause": (
                ROOT_097 if pid == "b_in01_097"
                else "native validate FAIL on the rebuilt FCStd "
                     "(121 InvalidCurveOnSurface, 161 SelfIntersect); "
                     "not the aviation-connector axis"
                if pid in NATIVE_FAIL
                else "orphan STEP: on-disk STEP matches no recorded native"
                if pid in ORPHAN_EXPORT
                else "stale export on the aviation-connector axis: "
                     "STEP/manifest still bind an older native"),
            "action": "rebuild as a new revision through the full chain; "
                      "do not delete or patch the existing FCStd/STEP",
        })
    return fam, axes, out


def main():
    fam, axes, rows = rows_for()
    cable = "三孔 + 航空插座座"
    on_cable = [r["project_id"] for r in rows
                if any(a["value"] == cable for a in r["axes"])]
    doc = {
        "family": "in01",
        "arch": fam["arch"],
        "axis_names": [a[0] + " …" for a in axes],
        "do_not_delete": True,
        "design_infeasible": [],
        "note": "No part is marked design-infeasible here. 121 and 161 "
                "failed validate on the rebuilt native; the other twelve "
                "are evidence-chain breaks (stale or orphan STEP). "
                "A waiver requires an explicit reason in the ledger.",
        "shared_axis": {
            "axis": 3,
            "value": cable,
            "pids": on_cable,
            "count": len(on_cable),
            "why_it_looks_periodic": "128..148 step 4, plus 154 and 158, "
                                     "are neighbours in the sorted 5-axis "
                                     "product while axis 3 stays on the "
                                     "aviation-connector value. Size is not "
                                     "constant (sz ladder modulo 8).",
        },
        "parts": rows,
    }
    dest = os.path.join(REPO, "status", "quarantine_b_in01.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote %s (%d parts, %d on %s)" % (dest, len(rows), len(on_cable), cable))
    return 0


def mark(root, pid):
    """Freeze this part's build input_sha from the ledger. No hash argument."""
    sys.path.insert(0, os.path.join(REPO, "plugin", "dsh-plugin-cad-freecad", "python"))
    from cadkit.ledger import mark_quarantine_from_ledger, open_ledger
    conn = open_ledger(os.path.abspath(os.path.expanduser(root)))
    try:
        result = mark_quarantine_from_ledger(conn, pid)
    finally:
        conn.close()
    print("%s input_sha %s applied=%s" % (
        pid, result["input_sha"], result["applied"]))
    return 0


def signoff(root, pid, input_sha, reviewer, reason):
    sys.path.insert(0, os.path.join(REPO, "plugin", "dsh-plugin-cad-freecad", "python"))
    from cadkit.ledger import open_ledger, record_signoff
    conn = open_ledger(os.path.abspath(os.path.expanduser(root)))
    try:
        record_signoff(conn, pid, input_sha, reviewer, reason)
    finally:
        conn.close()
    print("sign-off %s %s" % (pid, input_sha))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mark":
        if len(sys.argv) != 4:
            raise SystemExit("usage: quarantine_b_in01.py mark <cadRoot> <pid>")
        sys.exit(mark(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == "signoff":
        if len(sys.argv) != 7:
            raise SystemExit(
                "usage: quarantine_b_in01.py signoff <cadRoot> <pid> "
                "<new-input-sha> <reviewer> <reason>")
        sys.exit(signoff(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
                         sys.argv[6]))
    sys.exit(main())
