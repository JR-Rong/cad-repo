#!/usr/bin/env python3
"""旧件审查通道：对已交付批次做机械复检，发现即入错题本。

存在的理由：生产往前跑的时候，旧件会因为**引擎/族谱变更、判重底表增长、交付物被误改**
而变得不再合规；这些问题靠"当时通过了"是看不见的。审查跑在独立通道上，与新件生产并行，
发现的东西用 `lesson_add.py` 记进错题本（A 机 ingest 后变成教训 + 门禁）。

  python3 tools/review_scan.py --root ../cad --batch pilot_2026_10 --sample 10
  python3 tools/review_scan.py --root ~/cad-fleet --batch fleet_b_2026_11 --to-lessons
"""
import argparse
import glob
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
STAGES = ["build", "validate", "probe", "export", "package"]
GRADE_FLOOR = {"S": 200, "A": 100, "B": 50, "C": 0}


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def step_profile(path):
    txt = open(path, encoding="utf-8", errors="ignore").read(4000)
    low = txt.lower()
    if "automotive_design" in low:
        return "AP214"
    if "config_control_design" in low:
        return "AP242"
    return "unknown-profile"


def rows(db, table):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("select * from %s" % table)]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def scan(root, batch, sample=None, machine="A", families_sha=None):
    db = os.path.join(root, "ledger.sqlite3")
    findings = []
    if not os.path.exists(db):
        return [{"machine": machine, "severity": "P1", "where": batch,
                 "title": "台账不存在，无法复检交付可追溯性", "detail": db}]
    models = {r.get("model_id"): r for r in rows(db, "model")}
    jobs = rows(db, "job")
    dels = [r for r in rows(db, "delivery") if r.get("batch_id") == batch
            and r.get("state") == "delivered"]
    if not dels:
        known = sorted({r.get("batch_id") for r in rows(db, "delivery") if r.get("batch_id")})
        findings.append({"machine": machine, "severity": "P2" if batch not in known else "P1",
                         "where": batch,
                         "title": ("批次 %s 在台账里没有 delivered 行（已知批次: %s）"
                                   % (batch, ", ".join(str(k) for k in known) or "无"))
                                  + ("；批次存在但全未交付" if batch in known else ""),
                         "detail": "台账 %s" % db})
        return findings
    targets = []
    for d in dels:
        m = models.get(d.get("model_id")) or {}
        pid = m.get("project_id") or d.get("project_id")
        if pid:
            targets.append((pid, m, d))
    if sample:
        targets = targets[:sample]
    by_pid = {}
    for j in jobs:
        by_pid.setdefault(j.get("project_id"), []).append(j)

    for pid, m, d in targets:
        pdir = os.path.join(root, "delivery", batch, pid)
        where = "%s/%s" % (batch, pid)
        js = by_pid.get(pid, [])
        ok = {j["stage"] for j in js if j.get("state") == "OK"}
        missing = [s for s in STAGES if s not in ok]
        if missing:
            findings.append({"machine": machine, "severity": "P1", "where": where,
                             "title": "已交付件台账缺 OK 阶段（复检不过）: " + ",".join(missing),
                             "detail": "阶段记录被删/被覆盖，或交付后重跑失败未回滚"})
        step = os.path.join(pdir, pid + ".step")
        if not os.path.exists(step):
            findings.append({"machine": machine, "severity": "P1", "where": where,
                             "title": "交付目录缺 STEP（2.3 必需项）", "detail": step})
        else:
            prof = step_profile(step)
            if prof not in ("AP214", "AP242"):
                findings.append({"machine": machine, "severity": "P1", "where": where,
                                 "title": "STEP 剖面不是 AP214/AP242: " + prof, "detail": step})
        rend = glob.glob(os.path.join(pdir, "renders", "*.png"))
        if len(rend) != 8:
            findings.append({"machine": machine, "severity": "P1", "where": where,
                             "title": "渲染张数不是 8 张: %d" % len(rend), "detail": pdir})
        small = []
        for r in rend:
            sz = png_size(r)
            if not sz or max(sz) < 2000:
                small.append(os.path.basename(r) + ("=%dx%d" % sz if sz else "=坏文件"))
        if small:
            findings.append({"machine": machine, "severity": "P1", "where": where,
                             "title": "渲染长边不足 2000px 或文件损坏: " + ",".join(small[:4]),
                             "detail": pdir})
        if not os.path.exists(os.path.join(pdir, pid + ".FCStd")):
            findings.append({"machine": machine, "severity": "P1", "where": where,
                             "title": "缺原生 FCStd（含特征历史）", "detail": pdir})
        dm = os.path.join(pdir, "design.md")
        if os.path.exists(dm):
            body = open(dm, encoding="utf-8").read()
            if len(body) < 400:
                findings.append({"machine": machine, "severity": "P2", "where": where,
                                 "title": "design.md 过短，像事后补的（非逆向生成证据不足）",
                                 "detail": "%d 字" % len(body)})
        if families_sha:
            stale = [j["stage"] for j in js if j.get("state") == "OK"
                     and j.get("input_hash") and j.get("input_hash") != families_sha.get(pid)]
            if stale and families_sha.get(pid):
                findings.append({"machine": machine, "severity": "P1", "where": where,
                                 "title": "族谱/模型变更后阶段 input_hash 已过期仍标已交付",
                                 "detail": "过期阶段: " + ",".join(stale)})
    return findings


def to_lessons(findings, machine):
    for f in findings:
        subprocess.run([sys.executable, os.path.join(HERE, "lesson_add.py"),
                        "--machine", f.get("machine", machine), "--source", "review_scan",
                        "--severity", f.get("severity", "P2"), "--title", f["title"][:160],
                        "--where", f.get("where", ""), "--detail", f.get("detail", "")[:300],
                        "--evidence", "tools/review_scan.py --batch %s" % f.get("where", "")],
                       capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="本机 cadRoot")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--sample", type=int, default=0, help="只检前 N 件（0=全量）")
    ap.add_argument("--machine", default="A")
    ap.add_argument("--to-lessons", action="store_true", help="发现即写入本机错题本收件箱")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    t0 = time.time()
    findings = scan(os.path.expanduser(a.root), a.batch, a.sample or None, a.machine)
    if a.to_lessons:
        to_lessons(findings, a.machine)
    if a.json:
        print(json.dumps({"batch": a.batch, "findings": findings,
                          "seconds": round(time.time() - t0, 1)}, ensure_ascii=False, indent=1))
    else:
        for f in findings:
            print("%-4s %-28s %s" % (f["severity"], f["where"], f["title"][:100]))
        print("复检 %s：%d 条发现（%.1fs）%s"
              % (a.batch, len(findings), time.time() - t0,
                 "；已写入错题本收件箱" if a.to_lessons and findings else ""))
    return 1 if any(f["severity"] in ("P0", "P1") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
