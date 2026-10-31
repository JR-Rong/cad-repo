#!/usr/bin/env python3
"""Plan self-consistency gate for the 500-family / 200,000-part catalog.

Runs the FULL expansion (500 × 400 = 200,000 members) and fails on anything
that would cost a machine real parts later: duplicate project ids, two members
of a family sharing one topology signature, a family planned beyond its axis
space, a brief/intake that disagrees with the catalog, a fingerprint baseline
file carrying another machine's ids.
"""
import csv
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_catalog as B                                          # noqa: E402

SPEC_CATEGORIES = {"机械零件", "机电产品", "电子元器件封装", "结构件", "工装夹具", "消费品"}
errs, warns = [], []


def need(cond, msg):
    if not cond:
        errs.append(msg)


def main():
    fam_p = os.path.join(ROOT, "catalog", "families.json")
    if not os.path.exists(fam_p):
        print("先跑 tools/build_catalog.py", file=sys.stderr)
        return 2
    doc = json.load(open(fam_p, encoding="utf-8"))
    fs = doc["families"]
    need(len(fs) == 500, "族谱数 %d != 500" % len(fs))
    need(len({f["key"] for f in fs}) == len(fs), "族谱 key 重复")
    per = Counter(f["machine"] for f in fs)
    for m in B.MACHINES:
        need(per[m] == 100, "机器 %s 族谱数 %d != 100" % (m, per[m]))

    for f in fs:
        need(f["arch"] in B.ARCHETYPES, "%s 轴族未定义: %s" % (f["key"], f["arch"]))
        need(f["category"] in SPEC_CATEGORIES, "%s 类目不在规格 2.1: %s" % (f["key"], f["category"]))
        need(f["id_prefix"].startswith(f["machine"].lower() + "_"),
             "%s 前缀与机器不符: %s" % (f["key"], f["id_prefix"]))
        need(f["axis_space"] > f["members"],
             "%s 设计轴组合 %d 未超出成员数 %d（没有余量就不能排下一波）"
             % (f["key"], f["axis_space"], f["members"]))
        need(1 <= f["wave"] <= 20, "%s 波次越界: %s" % (f["key"], f["wave"]))

    # full expansion: ids unique, signatures unique inside a family
    seen_pid, total = set(), 0
    for f in fs:
        ms = B.members_of(f)
        need(len(ms) == f["members"], "%s 成员数不符" % f["key"])
        sigs = Counter(m["sig"] for m in ms)
        dup = [k for k, v in sigs.items() if v > 1]
        if dup:
            errs.append("%s 同族签名重复 %d 个，例：%s" % (f["key"], len(dup), dup[0][:60]))
        for m in ms:
            need(m["pid"].startswith(f["machine"].lower() + "_"), "%s 前缀错误" % m["pid"])
            if m["pid"] in seen_pid:
                errs.append("project id 重复: " + m["pid"])
                break
            seen_pid.add(m["pid"])
            total += 1
    need(total == doc["total_parts"], "展开件数 %d != total_parts %d" % (total, doc["total_parts"]))
    need(total >= 200000, "总量 %d < 20 万目标" % total)

    # manifest sha agreement (the committed digest must match recomputation)
    with open(os.path.join(ROOT, "catalog", "manifest.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            f = next(x for x in fs if x["key"] == row["family"])
            need(row["ids_sha256_16"] == B.sha_members(f), "%s manifest 摘要与重算不一致" % row["family"])

    # intake + briefs must carry every family of their machine
    for m in B.MACHINES:
        mine = [f["key"] for f in fs if f["machine"] == m]
        batch = B.BATCH[m]
        i = json.load(open(os.path.join(ROOT, "intake", batch + ".json"), encoding="utf-8"))
        need(sorted(x["family"] for x in i["families"]) == sorted(mine), "%s intake 族谱与清单不符" % batch)
        need(i["quota"]["parts"] == len(mine) * B.PER_FAMILY, "%s intake 件数不符" % batch)
        brief = open(os.path.join(ROOT, "briefs", "BRIEF-FOR-%s.txt" % m), encoding="utf-8").read()
        miss = [k for k in mine if "`%s_%s_" % (m.lower(), k) not in brief]
        need(not miss, "%s brief 缺族谱: %s" % (batch, miss[:4]))

    # wave layout: every wave of a machine has exactly FAMILIES_PER_WAVE families
    for m in B.MACHINES:
        waves = defaultdict(int)
        for f in fs:
            if f["machine"] == m:
                waves[f["wave"]] += 1
        for w, n in sorted(waves.items()):
            need(n == B.FAMILIES_PER_WAVE, "%s wave %d 族谱数 %d != %d"
                 % (B.BATCH[m], w, n, B.FAMILIES_PER_WAVE))

    # delivered registry
    dl = json.load(open(os.path.join(ROOT, "catalog", "delivered.json"), encoding="utf-8"))["delivered"]
    need(len({d["pid"] for d in dl}) == len(dl), "已交付 pid 重复")
    for d in dl:
        need(d["pid"] not in seen_pid, "已交付 pid 与计划件冲突: " + d["pid"])

    # cross-file: fingerprint baselines
    fp_dir = os.path.join(ROOT, "shared", "fingerprints")
    for name in sorted(os.listdir(fp_dir)) if os.path.isdir(fp_dir) else []:
        if not name.endswith(".json"):
            continue
        tag = name[:-5]
        need(tag in B.MACHINES, "指纹文件 %s 文件名必须是机器名" % name)
        for pid in json.load(open(os.path.join(fp_dir, name), encoding="utf-8")).get("entries", {}):
            foreign = [x.lower() + "_" for x in B.MACHINES if x != tag]
            if any(pid.startswith(p) for p in foreign):
                errs.append("%s.json 含别家 id: %s" % (tag, pid))

    for w in warns:
        print("warn   " + w)
    if errs:
        for e in errs[:40]:
            print("FAIL   " + e)
        print("catalog_lint: %d 项不通过" % len(errs))
        return 1
    print("catalog_lint: OK —— %d 族谱 / %d 件（每台 %d 族谱 × %d 件，20 波），"
          "全部成员拓扑签名唯一" % (len(fs), total, per["A"], B.PER_FAMILY))
    return 0


if __name__ == "__main__":
    sys.exit(main())
