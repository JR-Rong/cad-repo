#!/usr/bin/env python3
"""双通道调度器：新件生产与旧件审查并行，包装（package）严格串行。

为什么不能一味拉满：
  * 单件峰值 RSS ~430–530 MB（实测），插件每进程 cap 2048 MB、进程组 6144 MB；
  * N43(P1)/N44(P2) 都在并发发布/跨窗口合并上触发，出厂包未修 ⇒ `package` 必须独占；
  * 探针自带并发上限（probe worker 2）。
所以这里是**资源准入 + 单一 package 槽位**的池：生产跑 N 路 build/validate/probe/export，
审查通道同时跑（它只做只读复检，很轻），要 package 的件排队进那一个槽位。

  python3 tools/fleet_runner.py --root ../cad --batch fleet_a_2026_11 \\
      --intake intake/waves/fleet_a_2026_11_w1.json --stages build,validate,probe,export \\
      --workers 3 --review-batch pilot_2026_10 --review-every 6 --dry-run

断点续跑靠台账幂等键（已 OK 且 input_hash 未变的阶段直接跳过），所以崩了就重跑同一条命令。
"""
import argparse
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plugin_source                                                # noqa: E402

ENTRY = "entry.py"


def find_entry():
    path, label = plugin_source.discover()
    if not path:
        raise SystemExit("找不到插件引擎（%s）—— git pull 或 --plugin-python" % label)
    return os.path.join(path, ENTRY), path


def rules_sha_of(pydir):
    """出厂引擎自己的 rules_sha（宿主 python3 就能算，不用起 FreeCAD）。"""
    probe = ("import sys; sys.path.insert(0, %r); from cadkit.version import rules_sha; "
             "print(rules_sha())") % pydir
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or None


def find_freecad(explicit=None):
    """entry.py 的 shebang 就是 freecadcmd：宿主 python3 里没有 FreeCAD 模块。

    位点顺序：--freecad-bin → FREECAD_BIN → PATH → macOS 默认安装位。
    """
    cand = [explicit, os.environ.get("FREECAD_BIN"),
            shutil.which("freecadcmd"),
            "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"]
    for c in cand:
        if c and os.path.exists(c):
            return c
    raise SystemExit("找不到 freecadcmd（entry.py 必须在它下面跑；用 --freecad-bin 指路）。"
                     "记住：宿主 python3 跑 entry.py 只会得到 \"No module named 'FreeCAD'\"，"
                     "而它仍然退出码 0。")


def pending(root, intake, stages, limit=None):
    """Only queue what the ledger has not already accepted."""
    doc = json.load(open(intake, encoding="utf-8"))
    pids = doc.get("parts", [])
    db = os.path.join(root, "ledger.sqlite3")
    done = {}
    if os.path.exists(db):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        for pid, stage, state in con.execute("select project_id, stage, state from job"):
            done.setdefault(pid, {})[stage] = state
        con.close()
    out = []
    for pid in pids:
        todo = [s for s in stages if done.get(pid, {}).get(s) != "OK"]
        if todo:
            out.append((pid, todo))
    return out[:limit] if limit else out


def verdict(payload, stage, expect_rules=None):
    """只有引擎自己说 ok 才算过；checks[] 里任何 FAIL 都算不过。"""
    if not isinstance(payload, dict) or "ok" not in payload:
        return False, "无 CADKIT JSON 输出（阶段未真正执行）"
    got = ((payload.get("engine") or {}).get("rules_sha"))
    if expect_rules and got and got != expect_rules:
        return False, ("版本握手不一致：本阶段引擎 rules_sha=%s，签发值=%s "
                       "—— 整波停下，别用错引擎生产（见 shared/versions.json）"
                       % (got, expect_rules))
    bad = [c for c in (payload.get("checks") or [])
           if isinstance(c, dict) and str(c.get("status", "")).upper() not in
           ("PASS", "SKIP", "N/A", "")]
    if bad:
        return False, "checks FAIL: " + "; ".join(
            "%s(%s)" % (c.get("id"), str(c.get("detail", ""))[:60]) for c in bad[:3])
    for key in ("report", "probe", "gate"):
        sub = payload.get(key)
        if isinstance(sub, dict) and sub.get("passed") is False:
            return False, "%s.passed=false %s" % (key, json.dumps(
                sub.get("failures") or sub.get("failed") or "", ensure_ascii=False))[:180]
    if payload.get("passed") is False:
        return False, "%s.passed=false" % stage
    if not payload.get("ok"):
        for k in ("error", "reason", "failures", "failed", "blocking"):
            v = payload.get(k)
            if v:
                return False, ("%s=%s" % (k, json.dumps(v, ensure_ascii=False)
                                          if isinstance(v, (list, dict)) else v))[:220]
        return False, "ok=false（引擎未给出原因）"
    return True, ""


def run_stage(freecad, entry, root, batch, pid, stage, mem_mb, timeout,
              expect_rules=None):
    # 参数只能走环境：freecadcmd 会先把 --xxx 当成自己的选项吃掉
    cmd = [freecad, entry]
    env = dict(os.environ)
    opts = {}
    if stage == "package":
        opts["batch"] = batch
    if stage == "export":
        opts["long_side"] = 2400
    env.update({"CADKIT_ACTION": stage, "CADKIT_ROOT": root, "CADKIT_PROJECT": pid,
                "CADKIT_MEM_MB": str(mem_mb), "CADKIT_OPTS": json.dumps(opts)})
    env.pop("CADKIT_OUT", None)
    if stage in ("validate", "probe", "export", "package"):
        env["CADKIT_OUT"] = os.path.join(root, "projects", pid, "build",
                                         "runner_%s.json" % stage)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"pid": pid, "stage": stage, "ok": False, "error": "timeout %ds" % timeout,
                "seconds": round(time.time() - t0, 1)}
    payload = None
    for line in reversed((p.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("CADKIT>>"):
            try:
                payload = json.loads(line[len("CADKIT>>"):].strip())
            except ValueError:
                payload = None
            break
    ok, err = verdict(payload, stage, expect_rules)
    if not ok and not err:
        err = ((p.stderr or p.stdout or "").strip().splitlines() or ["?"])[-1][:200]
    if payload is None and p.returncode != 0:
        err = "rc=%d %s" % (p.returncode, err)
    mem = (payload or {}).get("mem") or {}
    return {"pid": pid, "stage": stage, "ok": ok, "rc": p.returncode,
            "seconds": round(time.time() - t0, 1),
            "peak_mb": mem.get("peak_mb"), "degraded": bool(mem.get("degraded")),
            "error": None if ok else err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--intake", required=True, help="emit_members.py 生成的本波 intake")
    ap.add_argument("--stages", default="build,validate,probe,export")
    ap.add_argument("--package", action="store_true", help="本波结束后串行包装")
    ap.add_argument("--workers", type=int, default=3, help="生产并发（按内存预算取）")
    ap.add_argument("--mem-budget-mb", type=int, default=6144)
    ap.add_argument("--per-part-mb", type=int, default=600,
                    help="准入用的单件内存预留（实测峰值 430–530MB），不是引擎 cap")
    ap.add_argument("--stage-mem-mb", type=int, default=2048,
                    help="传给引擎的每进程 cap（CADKIT_MEM_MB，默认与出厂一致 2048）")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--review-batch", default=None, help="同时复检的旧批次")
    ap.add_argument("--review-root", default=None, help="旧批次所在 cadRoot（默认同生产 root）")
    ap.add_argument("--review-every", type=int, default=6, help="每完成 N 件插一次旧件复检")
    ap.add_argument("--review-interval-secs", type=int, default=900,
                    help="心跳复检：即使一件都没完成，也要隔这段时间审一次旧件")
    ap.add_argument("--review-sample", type=int, default=20)
    ap.add_argument("--machine", default="A")
    ap.add_argument("--log", default=None)
    ap.add_argument("--freecad-bin", default=None,
                    help="freecadcmd 路径（默认 FREECAD_BIN → PATH → /Applications/FreeCAD.app/…）")
    ap.add_argument("--expect-rules", default=None,
                    help="签发的 rules_sha（默认读 shared/versions.json），每个阶段自查一次")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.expect_rules is None:
        vj = os.path.join(REPO, "shared", "versions.json")
        if os.path.exists(vj):
            a.expect_rules = (json.load(open(vj, encoding="utf-8")) or {}).get("rules_sha")
    stages = [x.strip() for x in a.stages.split(",") if x.strip()]
    if a.package:
        need_chain = {"build", "validate", "probe", "export"}
        if not need_chain <= set(stages):
            raise SystemExit("--package 要求 --stages 覆盖 %s（当前 %s）：跳过阶段直接包装，"
                             "等于让调度器替人绕过工单 §3 的顺序约束"
                             % ("/".join(sorted(need_chain)), ",".join(stages)))
    entry, engine = find_entry()
    freecad = find_freecad(a.freecad_bin)
    if a.expect_rules:
        got = rules_sha_of(engine)
        if got and got != a.expect_rules:
            print("BLOCK 引擎版本握手不一致：%s 算得 rules_sha=%s，签发值=%s"
                  % (engine, got, a.expect_rules))
            print("      整波停下：用错引擎生产 = 这一波全部返工。"
                  "要么换回签发的那个包（releases/*.tgz），要么请 A 机重签 shared/versions.json。")
            return 2
        print("握手自检：%s ✓（与签发值一致）" % (got or "引擎未报告 rules_sha"))
    root = os.path.abspath(os.path.expanduser(a.root))
    allowed = max(1, min(a.workers, a.mem_budget_mb // a.per_part_mb))
    todo = pending(root, a.intake, stages)
    print("引擎 %s（%s）\n本波待跑 %d 件（阶段 %s）；生产并发 %d（内存预算 %dMB / 单件 %dMB），package %s"
          % (engine, os.path.basename(freecad), len(todo), ",".join(stages), allowed,
             a.mem_budget_mb, a.per_part_mb,
             "串行单槽" if a.package else "本次不做"))
    if a.dry_run:
        for pid, t in todo[:5]:
            print("  %s: %s" % (pid, ",".join(t)))
        print("dry-run，未执行")
        return 0

    q = queue.Queue()
    for pid, t in todo:
        q.put((pid, t))
    pkg_q = queue.Queue()
    log = open(a.log or os.path.join(REPO, "status", "runner_%s_%s.jsonl"
                                     % (a.batch, time.strftime("%Y%m%d_%H%M"))), "a",
               encoding="utf-8")
    lock = threading.Lock()
    stop = threading.Event()
    stats = {"ok": 0, "fail": 0, "reviews": 0, "packaged": 0, "done_parts": 0}

    def emit(rec):
        with lock:
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()

    def worker(n):
        while not stop.is_set():
            try:
                pid, t = q.get_nowait()
            except queue.Empty:
                return
            for stage in t:
                r = run_stage(freecad, entry, root, a.batch, pid, stage,
                              a.stage_mem_mb, a.timeout, a.expect_rules)
                r["lane"] = "produce"
                r["worker"] = n
                emit(r)
                with lock:
                    stats["ok" if r["ok"] else "fail"] += 1
                    print("%-7s [%d] %-18s %-8s %s%s" % ("ok" if r["ok"] else "FAIL", n, pid,
                          stage, "%5.1fs" % r["seconds"], "  " + str(r["error"])[:90] if not r["ok"]
                          else ("  degraded" if r["degraded"] else "")), flush=True)
                if not r["ok"]:
                    break                        # 任一 FAIL 不再往下走（工单 §3）
            else:
                with lock:
                    stats["done_parts"] += 1
                    if a.package:
                        pkg_q.put(pid)          # 单一消费者 ⇒ 包装天然串行
                    if a.review_batch and stats["done_parts"] % a.review_every == 0:
                        review_q.put_nowait(stats["done_parts"])   # 与 package 无关
            q.task_done()

    review_q = queue.Queue()
    review_thread = None
    if a.review_batch:
        def do_reviews():
            last = time.time()
            beat = max(1, a.review_interval_secs)
            while not stop.is_set():
                n = None
                try:
                    n = review_q.get(timeout=1)
                except queue.Empty:
                    if time.time() - last < beat:
                        continue
                    n = "heartbeat"          # 一件都没完成也要按时间复检旧件
                last = time.time()
                p = subprocess.run([sys.executable, os.path.join(HERE, "review_scan.py"),
                                    "--root", os.path.expanduser(a.review_root or a.root),
                                    "--batch", a.review_batch, "--machine", a.machine,
                                    "--sample", str(a.review_sample), "--to-lessons"],
                                   capture_output=True, text=True)
                with lock:
                    stats["reviews"] += 1
                rec = {"lane": "review", "after_parts": n, "rc": p.returncode,
                     "root": os.path.expanduser(a.review_root or a.root),
                       "tail": (p.stdout or "").strip().splitlines()[-1:], "at": time.strftime("%H:%M:%S")}
                emit(rec)
                print("  旧件复检(%s): %s" % (a.review_batch, " ".join(rec["tail"])), flush=True)
        review_thread = threading.Thread(target=do_reviews, daemon=True)
        review_thread.start()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(allowed)]
    for t in threads:
        t.start()
    pkg_thread = None
    if a.package:
        def do_package():
            while not stop.is_set():
                try:
                    pid = pkg_q.get(timeout=2)
                except queue.Empty:
                    if all(not t.is_alive() for t in threads) and pkg_q.empty():
                        return
                    continue
                try:
                    r = run_stage(freecad, entry, root, a.batch, pid, "package",
                                  a.stage_mem_mb, a.timeout, a.expect_rules)
                finally:
                    pkg_q.task_done()      # 没有这句，pkg_q.join() 永远返回不了（实测挂死）
                r["lane"] = "package"
                emit(r)
                with lock:
                    stats["packaged" if r["ok"] else "fail"] += 1
                    print("PACKAGE %-18s %s%s" % (pid, "ok" if r["ok"] else "FAIL",
                          "  " + str(r["error"])[:90] if not r["ok"] else ""), flush=True)
        pkg_thread = threading.Thread(target=do_package, daemon=True)
        pkg_thread.start()

    for t in threads:
        t.join()
    # 生产线程结束后必须把包装队列排空再停：半途 stop 会静默丢掉交付，
    # 在 20 万件规模上就是"台账说做了 2 万件、盘上只有 1.9 万件"。
    if pkg_thread:
        pkg_q.join()
    stop.set()
    for t in (review_thread, pkg_thread):
        if t:
            t.join(timeout=120)
    print("完成：件 %d（含 %d 次旧件复检），阶段 ok=%d fail=%d，package=%d；日志 %s"
          % (stats["done_parts"], stats["reviews"], stats["ok"], stats["fail"],
             stats["packaged"], os.path.relpath(log.name, REPO)))
    return 1 if stats["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
