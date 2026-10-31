#!/usr/bin/env python3
"""Locate a cadkit/ engine that matches what the machines actually run.

Order: explicit --plugin-python -> CADKIT_PLUGIN_PY -> the engine vendored in
this repo (byte-identical with releases/*.tgz, so it exists on every machine
after a pull) -> A's dev checkout next to the workspace -> the installed
profile copy. Nothing here may hard-code a home directory.
"""
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HANDOFF = os.path.dirname(HERE)
VENDORED = os.path.join(HANDOFF, "plugin", "dsh-plugin-cad-freecad", "python")


def candidates(explicit=None):
    cands = []
    if explicit:
        cands.append(("--plugin-python", explicit))
    if os.environ.get("CADKIT_PLUGIN_PY"):
        cands.append(("CADKIT_PLUGIN_PY", os.environ["CADKIT_PLUGIN_PY"]))
    cands.append(("repo plugin/（与出厂包同源）", VENDORED))
    cands.append(("dev checkout", os.path.join(HANDOFF, os.pardir, "dsh-plugin-cad-freecad", "python")))
    for pat in ("@local/dsh-plugin-cad-freecad/python", "**/@local/dsh-plugin-cad-freecad/python"):
        for p in sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".dsh", "profiles",
                                               "*", "node_modules", pat), recursive=True)):
            cands.append(("已安装插件", p))
    return cands


def discover(explicit=None):
    """Return (python_dir, source_label) or (None, why)."""
    tried = []
    for label, c in candidates(explicit):
        if not c:
            continue
        c = os.path.abspath(c)
        tried.append(label)
        if os.path.exists(os.path.join(c, "cadkit", "fingerprint.py")):
            return c, label
    return None, "没找到 cadkit/（试过: %s）—— git pull 应带下 plugin/dsh-plugin-cad-freecad" % ", ".join(tried)
