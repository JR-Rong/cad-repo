#!/usr/bin/env python3
"""Sync the fleet-wide dedupe baseline into a local cadRoot registry.

Spec 2.5 is judged over the buyer's WHOLE library, but the plugin's `dedupe`
can only see one cadRoot. Five machines therefore need the union of everyone's
fingerprints before they start cutting metal — and each machine may only ever
write its own file (shared/fingerprints/<MACHINE>.json), so five agents pushing
can never clobber each other's baseline.

    python3 tools/registry_sync.py --root ~/cad-fleet [--machine B] [--check-only]

Writes <root>/registry/fingerprints.json in the plugin's plain {pid: fp} shape.
Refuses to run if the same project id exists twice with different geometry —
that is two machines building the same part, which must be settled by a human.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import engine_predicates
    _fp, MODE = engine_predicates.load(os.path.join(REPO, os.pardir,
                                 "dsh-plugin-cad-freecad", "python"))
    scaled_same, related = _fp.scaled_same, _fp.related
except Exception:                                             # noqa: BLE001
    try:
        _fp, MODE = engine_predicates.load(os.path.expanduser(
            "~/Documents/deepseek-harness/default-workspace/dsh-plugin-cad-freecad/python"))
        scaled_same, related = _fp.scaled_same, _fp.related
    except Exception:                                          # noqa: BLE001
        scaled_same = related = None
        MODE = "unavailable"


def load_files(dirpath):
    out = {}
    src = {}
    for path in sorted(glob.glob(os.path.join(dirpath, "*.json"))):
        machine = os.path.splitext(os.path.basename(path))[0]
        doc = json.load(open(path, encoding="utf-8"))
        entries = doc.get("entries", doc)
        for pid, fp in entries.items():
            if not isinstance(fp, dict) or "volume" not in fp:
                continue
            if pid in out and out[pid].get("structure_sha") != fp.get("structure_sha"):
                raise SystemExit("冲突：%s 同时出现在 %s 与 %s 且几何不同 —— 两台机器做了同一个 id，"
                                 "先人工裁定再同步" % (pid, src[pid], machine))
            out[pid] = fp
            src[pid] = machine
    return out, src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="本机 cadRoot（会写 <root>/registry/）")
    ap.add_argument("--repo", default=REPO, help="handoff 仓位置")
    ap.add_argument("--machine", default=None, help="本机代号，用于跳过自重叠检查")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    fdir = os.path.join(a.repo, "shared", "fingerprints")
    if not os.path.isdir(fdir):
        raise SystemExit("找不到 %s（先 git pull）" % fdir)
    entries, src = load_files(fdir)

    # cross-machine pre-check: flag duplicates that already exist in the baseline
    dupes, relatives = [], []
    if scaled_same:
        keys = sorted(entries)
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                fa, fb = entries[ka], entries[kb]
                try:
                    if abs(fa["volume"] - fb["volume"]) / max(fa["volume"], fb["volume"]) < 1e-6:
                        dupes.append("%s==%s(exact)" % (ka, kb))
                    elif scaled_same(fa, fb):
                        dupes.append("%s~%s(scaled_same)" % (ka, kb))
                    elif related(fa, fb):
                        relatives.append("%s~%s" % (ka, kb))
                except KeyError:
                    pass

    merged = dict(entries)
    local_path = os.path.join(a.root, "registry", "fingerprints.json")
    if os.path.exists(local_path):
        try:
            local = json.load(open(local_path, encoding="utf-8"))
            for pid, fp in local.items():
                merged.setdefault(pid, fp)
        except (ValueError, OSError) as exc:
            print("本机 registry 读不动（%s），用底表覆盖" % exc)

    print("底表: %d 件（来自 %s）； predicates=%s"
          % (len(entries), ",".join(sorted(set(src.values()))) or "-", MODE))
    if dupes:
        print("BLOCK 底表内部已有判重对:", "; ".join(dupes))
    for r in relatives[:5]:
        print("note   同系列相似形(不阻断):", r)
    if a.check_only:
        return 1 if dupes else 0
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    tmp = local_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, local_path)
    print("写入 %s：%d 件（含本机历史 %d 件）"
          % (local_path, len(merged), len(merged) - len(entries)))
    return 1 if dupes else 0


if __name__ == "__main__":
    sys.exit(main())
