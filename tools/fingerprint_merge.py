#!/usr/bin/env python3
"""Merge one machine's fingerprint delta into its own baseline file (A-side action).

The baseline is the fleet's only defence against spec 2.5 across machines, so
this refuses to merge anything that collides with an existing entry — including
the machine's OWN earlier parts. Merging a duplicate would make the registry
lie about how much unique geometry exists, which is exactly the thing we get
paid for.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plugin_source
_p, MODE = plugin_source.discover()
if _p:
    import engine_predicates
    try:
        _fp, MODE = engine_predicates.load(_p)
    except Exception as exc:                                    # noqa: BLE001
        _fp, MODE = None, "unavailable: %s" % exc
else:
    _fp = None


def entries_of(path):
    doc = json.load(open(path, encoding="utf-8"))
    return doc.get("entries", doc)


def collide(a, b):
    if abs(a["volume"] - b["volume"]) / max(a["volume"], b["volume"]) < 1e-6:
        return "exact"
    if _fp and _fp.scaled_same(a, b):
        return "scaled_same"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", required=True, choices=list("ABCDE"))
    ap.add_argument("--delta", required=True, help="outbox/<批次>/fingerprints.delta.json")
    ap.add_argument("--allow-partial", action="store_true",
                    help="只并入不冲突的部分（默认遇冲突整体拒绝）")
    a = ap.parse_args()

    if not _fp:
        print("拿不到出厂判定函数（%s），拒绝合并 —— 底表不能被猜" % MODE, file=sys.stderr)
        return 2

    delta = entries_of(a.delta)
    dest = os.path.join(REPO, "shared", "fingerprints", a.machine + ".json")
    own = entries_of(dest) if os.path.exists(dest) else {}

    pool = {}
    for f in glob.glob(os.path.join(REPO, "shared", "fingerprints", "*.json")):
        tag = os.path.splitext(os.path.basename(f))[0]
        for pid, fp in entries_of(f).items():
            if tag != a.machine:
                pool[tag + "/" + pid] = fp
    pool.update({pid: fp for pid, fp in own.items()})          # own history too

    dupes, added, skipped = [], {}, []
    for pid, fp in sorted(delta.items()):
        hit = None
        for other, ofp in pool.items():
            c = collide(fp, ofp)
            if c:
                hit = "%s ~ %s (%s)" % (pid, other, c)
                break
        if hit:
            dupes.append(hit)
            skipped.append(pid)
        else:
            added[pid] = fp
            pool[a.machine + "/" + pid] = fp                   # delta vs delta too

    doc = {"_comment": "机器 %s 的几何指纹底表（只有 %s 机能写；合并由 A 机 fingerprint_merge 执行）"
                      % (a.machine, a.machine),
           "machine": a.machine,
           "predicates": MODE,
           "entries": {**own, **added}}
    if dupes and not a.allow_partial:
        print("拒绝合并： delta 里有 %d 件与既有几何判重" % len(dupes))
        for d in dupes:
            print("   BLOCK  " + d)
        print("处理办法：那几件要么改拓扑重做，要么由裁决判定它们本来就该算同一件（写进 MEMORY 决策）。")
        return 1

    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, dest)
    print("并入 %s：%d 件（原有 %d，跳过判重 %d）；predicates=%s"
          % (os.path.relpath(dest, REPO), len(added), len(own), len(skipped), MODE))
    for d in dupes:
        print("   note   已跳过: " + d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
