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

HANDOFF = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKSPACE = os.path.abspath(os.path.join(HANDOFF, ".."))
import plugin_source                                            # noqa: E402
PKG = os.path.dirname(plugin_source.VENDORED)                   # 仓库内出厂引擎
sys.path.insert(0, plugin_source.VENDORED)
from cadkit.version import rules_sha          # noqa: E402


def dev_rules_sha():
    """A 机 dev 检出口径：只作对照记录，不是握手值（握手必须等于别机装的东西）。"""
    import subprocess
    dev = os.path.abspath(os.path.join(WORKSPACE, "dsh-plugin-cad-freecad", "python"))
    if not os.path.isdir(dev):
        return "(dev checkout 不存在)"
    probe = ("import sys; sys.path.insert(0, %r); from cadkit.version import rules_sha; "
             "print(rules_sha())") % dev
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    return r.stdout.strip() or "(不可用)"

BATCH = os.environ.get("HANDOFF_BATCH", "fleet_b_2026_11")
FREECAD = os.environ.get("FREECAD_BIN",
                         "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else "(missing)"


def main():
    plugin = json.load(open(os.path.join(PKG, "package.json")))
    name = "local-dsh-plugin-cad-freecad-%s.tgz" % plugin["version"]
    # 以仓库里发给别机的包为准；工作区那份只是打包现场
    tgz = os.path.join(HANDOFF, "releases", name)
    if not os.path.exists(tgz):
        tgz = os.path.join(WORKSPACE, name)
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
        "engine_source": "handoff/plugin/dsh-plugin-cad-freecad/python（与 releases/*.tgz 同源）",
        "rules_sha_dev_checkout": dev_rules_sha(),
        "family_iec_endcap_sha256": sha(os.path.join(WORKSPACE, "cad", "family", "iec_endcap.py")),
        "spec_pdf_sha256": sha(os.path.join(WORKSPACE, "cad", "reference",
                                            "procurement_requirements.pdf")),
        "freecad_version_local": ver[:120],
        "handoff_batch": BATCH,
        "known_plugin_defects": {
            "N43 (P1, 未修)": "packager.py 所有权读失败被当作“无更新者”，会删除/覆盖别人更新的交付"
                              " —— package 必须串行、一次一件",
            "N44 (P2, 未修)": "probe.py 跨窗口 results.update() 让后到 PASS 抹掉先前 FAIL"
                              " —— 同一件不要跨窗口重复上报，重跑整件",
            "N1/N2/N16/N18/N20/N23": "部分通过：异常路径与合并语义未收敛（与上面两条同源）"},
        "role": "machine-A issues this; machine B echoes the same keys into its return",
    }
    dest = os.path.join(WORKSPACE, "handoff", "shared", "versions.json")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
