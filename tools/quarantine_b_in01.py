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


if __name__ == "__main__":
    sys.exit(main())
