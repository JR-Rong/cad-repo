#!/usr/bin/env python3
"""Freeze a machine's family cores into git and refuse a drifted live tree.

The cores that actually generate geometry (enclosure_core, panelbox_core, …)
live on the production Mac under ~/cad-fleet/family and are not in this
repo. Until they are copied here, a rebuild cannot name the source that
made a part.

    python3 tools/family_freeze.py freeze --machine B --src ~/cad-fleet/family
    python3 tools/family_freeze.py check --machine B --live ~/cad-fleet/family

``freeze`` copies every ``*.py`` into ``family_src/<machine>/`` and writes
``MANIFEST.json`` (per-file sha256 plus an aggregate). It does not invent
bindings from family key to module; the owner fills ``binding.json`` once:

    {"in01": {"module": "enclosure_core.py"}, ...}

``check`` exits 0 when every live file matches the manifest, 2 when the
manifest is missing or the bytes differ. A missing manifest is not a
pass: the wave report must say the core is unfrozen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def machine_dir(machine, repo=REPO):
    return os.path.join(repo, "family_src", machine.upper())


def _py_files(directory):
    if not directory or not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory)
                  if n.endswith(".py") and not n.startswith("."))


def aggregate(files):
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode())
        h.update(b"\0")
        h.update(files[name].encode())
        h.update(b"\0")
    return h.hexdigest()


def write_manifest(dest, files):
    doc = {
        "files": files,
        "aggregate_sha256": aggregate(files),
        "note": "family cores frozen by tools/family_freeze.py; "
                "do not edit under a live fleet_runner",
    }
    path = os.path.join(dest, "MANIFEST.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return doc


def freeze(machine, src, repo=REPO):
    src = os.path.abspath(os.path.expanduser(src))
    names = _py_files(src)
    if not names:
        raise SystemExit("family freeze: no *.py under %s" % src)
    dest = machine_dir(machine, repo)
    os.makedirs(dest, exist_ok=True)
    files = {}
    for name in names:
        shutil.copy2(os.path.join(src, name), os.path.join(dest, name))
        files[name] = sha256_file(os.path.join(dest, name))
    binding = os.path.join(dest, "binding.json")
    if not os.path.exists(binding):
        with open(binding, "w", encoding="utf-8") as f:
            json.dump({
                "_comment": "Map catalog family key -> core filename. "
                            "Example: \"in01\": {\"module\": \"enclosure_core.py\"}",
            }, f, ensure_ascii=False, indent=1)
            f.write("\n")
    doc = write_manifest(dest, files)
    return dest, doc


def check(machine, live, repo=REPO):
    """Return (status, detail). status is ok | missing | mismatch."""
    dest = machine_dir(machine, repo)
    manifest_path = os.path.join(dest, "MANIFEST.json")
    if not os.path.isfile(manifest_path):
        return "missing", "no MANIFEST.json under %s" % dest
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    expected = manifest.get("files") or {}
    if not expected:
        return "missing", "manifest has no files"
    live = os.path.abspath(os.path.expanduser(live))
    problems = []
    for name, digest in sorted(expected.items()):
        path = os.path.join(live, name)
        if not os.path.isfile(path):
            problems.append("missing %s" % name)
            continue
        got = sha256_file(path)
        if got != digest:
            problems.append("%s live %s… != frozen %s…" % (name, got[:12], digest[:12]))
    extra = [n for n in _py_files(live) if n not in expected]
    for name in extra:
        problems.append("unfrozen extra %s" % name)
    if problems:
        return "mismatch", "; ".join(problems[:8])
    return "ok", manifest.get("aggregate_sha256") or aggregate(expected)


def binding_for(machine, family, repo=REPO):
    path = os.path.join(machine_dir(machine, repo), "binding.json")
    if not os.path.isfile(path):
        return None
    doc = json.load(open(path, encoding="utf-8"))
    row = doc.get(family)
    if not isinstance(row, dict) or not row.get("module"):
        return None
    module = row["module"]
    manifest_path = os.path.join(machine_dir(machine, repo), "MANIFEST.json")
    sha = None
    if os.path.isfile(manifest_path):
        files = json.load(open(manifest_path, encoding="utf-8")).get("files") or {}
        sha = files.get(module)
    return {"module": module, "sha256": sha}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fr = sub.add_parser("freeze")
    fr.add_argument("--machine", required=True)
    fr.add_argument("--src", required=True)
    ch = sub.add_parser("check")
    ch.add_argument("--machine", required=True)
    ch.add_argument("--live", required=True)
    a = ap.parse_args()
    if a.cmd == "freeze":
        dest, doc = freeze(a.machine, a.src)
        print("frozen %d cores -> %s aggregate %s" % (
            len(doc["files"]), dest, doc["aggregate_sha256"][:16]))
        return 0
    status, detail = check(a.machine, a.live)
    print("%s %s" % (status, detail))
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
