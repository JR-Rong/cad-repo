#!/usr/bin/env python3
"""Refresh handoff/shared/versions.json — the A/B version handshake.

Run on A machine right before pushing a work-order change: rules_sha moves
whenever the engine or rules change, and B machine's whole "same acceptance
basis" check keys off this file. B then compares it against its own memcheck
and status output; a mismatch means A owes B a new tarball, not that B should
patch its engine.
"""
import hashlib
import json
import os
import subprocess
import sys

WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PKG = os.path.join(WORKSPACE, "dsh-plugin-cad-freecad")
sys.path.insert(0, os.path.join(PKG, "python"))
from cadkit.version import rules_sha          # noqa: E402

BATCH = os.environ.get("HANDOFF_BATCH", "fleet_b_2026_11")
FREECAD = os.environ.get("FREECAD_BIN",
                         "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else "(missing)"


def main():
    plugin = json.load(open(os.path.join(PKG, "package.json")))
    tgz = os.path.join(WORKSPACE, "local-dsh-plugin-cad-freecad-%s.tgz" % plugin["version"])
    try:
        ver = subprocess.run([FREECAD, "--version"], capture_output=True, text=True,
                             timeout=180).stdout.strip().splitlines()[0]
    except Exception as exc:                    # noqa: BLE001
        ver = "unavailable: %s" % exc
    out = {
        "engine_plugin_version": plugin["version"],
        "tarball": os.path.basename(tgz),
        "tarball_sha256": sha(tgz),
        "rules_sha": rules_sha(),
        "family_iec_endcap_sha256": sha(os.path.join(WORKSPACE, "cad", "family", "iec_endcap.py")),
        "spec_pdf_sha256": sha(os.path.join(WORKSPACE, "cad", "reference",
                                            "procurement_requirements.pdf")),
        "freecad_version_local": ver[:120],
        "handoff_batch": BATCH,
        "role": "machine-A issues this; machine B echoes the same keys into its return",
    }
    dest = os.path.join(WORKSPACE, "handoff", "shared", "versions.json")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
