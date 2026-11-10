#!/usr/bin/env python3
"""双通道调度器：新件生产与旧件审查并行。

一条产线：族谱核（一张图纸）→ 波次 intake 里的参数变体 → build → validate →
probe → dedupe → export → package。本文件是唯一支持的入口；仓外的
``cad-b-prep/driver.py`` 不再使用。

包装在 N43 修好之后可以并行：每个零件一把锁，读不到交付物就放弃而不是删除，
只有确定的「文件不存在」才当作没有更新的修订。``--package-workers`` 默认 1，
直到新 tarball 在 FreeCAD 1.1.1 上验证过并发。旧的 ``.publisher.lock``
和按件锁互不排斥，发现旧锁被占用就停。
探针结果按窗口追加，任何一条 FAIL 都是否决。

断点续跑看 ``build/chain_evidence.json`` 里重新读过的哈希，不看台账里
历史的 job=OK。那 172 条回填不会被当成已验收。

  python3 tools/fleet_runner.py --root ../cad --batch fleet_a_2026_11 \\
      --intake intake/waves/fleet_a_2026_11_w1.json --stages build,validate,probe,export \\
      --workers 3 --review-batch pilot_2026_10 --review-every 6 --dry-run

断点续跑靠台账幂等键（已 OK 且 input_hash 未变的阶段直接跳过），所以崩了就重跑同一条命令。
"""
import argparse
import hashlib
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
import acceptance                                                    # noqa: E402
import family_freeze                                                 # noqa: E402
sys.path.insert(0, plugin_source.VENDORED)
from cadkit.chain import metric_drift                               # noqa: E402

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


# ---------------------------------------------------------------------------
# Stage recorder (fix 2026-10-31, A-line; B 机 08:30 报告的两处之外补第三处)
#
# 出厂引擎里 job 表只有 schema 和读写访问器，**没有任何写入方**：
# "崩了重跑、已 OK 阶段自动跳过" 的幂等键全靠这张表，不写它 resume 就是空话，
# merge_check 的"台账五阶段"门禁也永远拿不到 OK 行。pilot 批次当时用
# cad/tools/pilotctl.py 记账；这里按同一套键语义做成舰队版：
#   base = sha(project.yml) + sha(族谱模块) + rules_sha
#   build 输入含 build.py 自身；validate/probe/dedupe/export 含 FCStd；
#   package 含全部证据文件 + STEP + 8 渲染 + build.py（N7 语义）。
# ---------------------------------------------------------------------------

def _h(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:20]


def _sha(path):
    if not os.path.exists(path):
        return "NOFILE"
    hh = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            hh.update(c)
    return hh.hexdigest()


RENDER_VIEWS = ("front", "back", "left", "right", "top", "bottom",
                "iso_up", "iso_down")


class Recorder:
    def __init__(self, root, engine_py, rules):
        if engine_py not in sys.path:
            sys.path.insert(0, engine_py)
        from cadkit.ledger import open_ledger, job_state, job_record
        self.root, self.rules = root, rules
        self._open, self._state, self._rec = open_ledger, job_state, job_record

    def input_hash(self, pid, stage):
        pr = os.path.join(self.root, "projects", pid)
        try:
            fam = (json.load(open(os.path.join(pr, "project.yml"),
                                  encoding="utf-8")) or {}).get("family")
        except Exception:
            fam = None
        yml = _sha(os.path.join(pr, "project.yml"))
        famsha = _sha(os.path.join(self.root, "family", fam + ".py")) \
            if fam else "NOFAM"
        base = _h(yml, famsha, self.rules)
        bsrc = _sha(os.path.join(pr, "build.py"))
        bd = os.path.join(pr, "build")
        fc = _sha(os.path.join(bd, pid + ".FCStd"))
        if stage == "build":
            return _h(base, bsrc)
        if stage in ("validate", "probe", "dedupe", "export"):
            return _h(base, fc)
        if stage == "package":
            extra = "".join(_sha(os.path.join(bd, fn)) for fn in
                            ("report.json", "probe.json", "export_manifest.json"))
            for v in RENDER_VIEWS:
                extra += _sha(os.path.join(bd, "renders", "view_%s.png" % v))
            extra += _sha(os.path.join(bd, pid + ".step")) + bsrc
            extra += _sha(os.path.join(pr, "review", "receipts.json"))
            return _h(base, fc, extra)
        raise SystemExit("unknown stage " + stage)

    def state(self, pid, stage):
        conn = self._open(self.root)
        try:
            return self._state(conn, pid, stage,
                               self.input_hash(pid, stage), self.rules)
        finally:
            conn.close()

    def record(self, pid, stage, ok, error_class=None, max_attempts=3):
        conn = self._open(self.root)
        try:
            if error_class == "pending_review":
                state = "PENDING_REVIEW"
            else:
                state = "OK" if ok else "FAIL"
            return self._rec(conn, pid, stage, self.input_hash(pid, stage),
                             self.rules, state, error_class,
                             max_attempts=max_attempts)
        finally:
            conn.close()


# b_in01 parts the 2026-11-01 binding audit marked BROKEN. They stay
# quarantined until a new revision's evidence hashes match. Nothing here
# deletes or rewrites their files.
QUARANTINE_PIDS = {
    "b_in01_082", "b_in01_097", "b_in01_112", "b_in01_121", "b_in01_125",
    "b_in01_128", "b_in01_132", "b_in01_136", "b_in01_140", "b_in01_144",
    "b_in01_148", "b_in01_154", "b_in01_158", "b_in01_161",
}


def append_run_header(log, *, backup_sha, previous_end_sha, run_id,
                      chain_anchor=None, chain_anchor_ok=None):
    """First line of this run, even when the log file already has older runs."""
    matches = None if previous_end_sha is None else previous_end_sha == backup_sha
    anchor = chain_anchor or {}
    rec = {
        "lane": "run_header",
        "run_id": run_id,
        "first_line_of_run": True,
        "ledger_backup_sha256": backup_sha,
        "previous_end_sha256": previous_end_sha,
        "ledger_matches_previous_end": matches,
        "chain_event_count": anchor.get("event_count"),
        "chain_head_hash": anchor.get("head_hash"),
        "chain_anchor_ok": chain_anchor_ok,
    }
    log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.flush()
    return rec


class AnchorCheckFailed(Exception):
    """The start-of-run anchor check did not finish. The run must not start."""


def _record_anchor_check_failure(root, exc):
    from cadkit.ledger import alarm_open, open_ledger, raise_alarm
    try:
        conn = open_ledger(root)
    except Exception:
        return
    try:
        if not alarm_open(conn, "anchor_unverifiable"):
            raise_alarm(conn, "anchor_unverifiable",
                        "anchor check failed: %s" % exc)
    finally:
        conn.close()


def enforce_anchor_check(root, repo, backup_sha, previous_end):
    """Compare anchors and the previous end sha. Fail closed.

    Returns ``(chain_doc, chain_ok)``. Any exception is recorded as
    ``anchor_unverifiable`` and re-raised as ``AnchorCheckFailed``.
    A failed ``git fetch`` raises the same alarm and also refuses to
    start: there is no origin anchor to compare.
    """
    from cadkit.ledger import (alarm_open, anchor_holds, chain_head,
                               note_anchor_state, open_ledger,
                               raise_alarm, read_chain_anchor)
    try:
        conn = open_ledger(root)
        try:
            chain_doc = read_chain_anchor(root) or chain_head(conn)
            chain_ok = anchor_holds(conn, read_chain_anchor(root))
            note_anchor_state(conn, root, repo=repo, backup_sha=backup_sha)
            if alarm_open(conn, "anchor_unverifiable"):
                raise AnchorCheckFailed(
                    "origin anchor could not be fetched; not starting")
            if (previous_end and backup_sha and previous_end != backup_sha
                    and not alarm_open(conn, "ledger_sha_mismatch")):
                raise_alarm(conn, "ledger_sha_mismatch",
                            "sqlite backup sha differs from ledger.end.sha")
            return chain_doc, chain_ok
        finally:
            conn.close()
    except AnchorCheckFailed:
        raise
    except Exception as exc:
        _record_anchor_check_failure(root, exc)
        raise AnchorCheckFailed(str(exc))


def read_part_ledger(root, pid, rules, stage_inputs, current_input):
    """Ledger facts for one part. Any read error is a FAIL, not a skip."""
    out = {"event_fail": False, "sealed": None, "ledger_input": None,
           "signoff": None, "error": None}
    try:
        from cadkit.ledger import (event_unwaived_fail, evidence_seal,
                                   open_ledger, quarantine_mark,
                                   review_signoff)
        conn = open_ledger(root)
        try:
            out["sealed"] = evidence_seal(conn, pid)
            mark = quarantine_mark(conn, pid)
            if mark:
                out["ledger_input"] = mark["input_sha"]
            if current_input:
                out["signoff"] = review_signoff(conn, pid, current_input)
            for stage_name, ih in stage_inputs:
                if ih and event_unwaived_fail(
                        conn, pid, stage_name, ih, rules):
                    out["event_fail"] = True
                    break
        finally:
            conn.close()
    except Exception as exc:
        out["event_fail"] = True
        out["sealed"] = None
        out["error"] = str(exc)
    return out


def previous_run_end_sha(log_path):
    """End sha from one jsonl log. The runner does not compare against this.

    Default log names include the minute, so a later run would open a
    different file and see no previous end. The comparison uses the fixed
    file ``<cadRoot>/ledger.end.sha``.
    """
    last = None
    if not log_path or not os.path.isfile(log_path):
        return None
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if doc.get("lane") == "run_footer" and doc.get("ledger_end_sha256"):
                last = doc["ledger_end_sha256"]
    return last


def _family_file(root, pid):
    yml = os.path.join(root, "projects", pid, "project.yml")
    module = None
    family = acceptance.family_key(pid)
    try:
        doc = json.load(open(yml, encoding="utf-8"))
        module = doc.get("family_module")
        family = doc.get("family") or family
    except (OSError, ValueError):
        doc = {}
    if not module and family:
        module = family + ".py" if not str(family).endswith(".py") else family
    path = os.path.join(root, "family", module) if module else None
    if path and not os.path.isfile(path):
        alt = os.environ.get("CADKIT_FAMILY_SRC")
        if alt:
            cand = os.path.join(os.path.expanduser(alt), os.path.basename(module))
            if os.path.isfile(cand):
                path = cand
    return family, path if path and os.path.isfile(path) else None


def pending(rec, intake, stages, limit=None, repair_failed=False,
            max_attempts=3, only=None):
    """Queue stages that do not have re-verified evidence for these bytes.

    A ledger job row of OK is not enough — the 172 backfilled b_in01
    parts have no chain_evidence.json and are queued again.
    """
    doc = json.load(open(intake, encoding="utf-8"))
    pids = list(doc.get("parts", []))
    if only:
        want = set(only)
        pids = [p for p in pids if p in want]
    out = []
    for pid in pids:
        ev = acceptance.load_evidence(rec.root, pid)
        ev_path = acceptance.evidence_path(rec.root, pid)
        if ev and os.path.isfile(ev_path):
            try:
                from cadkit.ledger import evidence_seal, open_ledger
                conn = open_ledger(rec.root)
                try:
                    sealed = evidence_seal(conn, pid)
                finally:
                    conn.close()
            except Exception:
                sealed = None
            if sealed != acceptance.sha256_file(ev_path):
                ev = None
        _fam, fam_path = _family_file(rec.root, pid)
        hashes = acceptance.disk_hashes(rec.root, pid, fam_path)
        todo = []
        for stage in stages:
            try:
                ih = rec.input_hash(pid, stage)
            except SystemExit:
                raise
            except Exception:
                ih = None
            if ih and acceptance.stage_skippable(ev, stage, ih, hashes):
                continue
            st = ((ev or {}).get("stages") or {}).get(stage) or {}
            if (not repair_failed and st.get("state") == "FAIL"
                    and acceptance.attempts_of(ev, stage) >= max_attempts):
                continue
            todo.append(stage)
        if todo:
            out.append((pid, todo))
    return out[:limit] if limit else out


def verdict(payload, stage, expect_rules=None):
    """只有引擎自己说 ok 才算过；checks[] 里任何 FAIL 都算不过。

    修（B 机 08:30 报告①，代码复核成立）：checks 里的 WARN 是引擎设计好的
    "人工终审提示位"（validate.py:552 real_object 恒为 WARN），引擎门禁与
    packager 都按 WARN 放行；旧白名单没有它，fleet 模式下 validate 会全灭。
    WARN 计入日志但**不**算不过。

    修（去重阶段）：act_dedupe 的返回没有 ok 键，只有 exact/scaled 命中列表；
    工单 §7 的口径是 exact/scaled_same 阻断、series/related 不阻断 ——
    与 act_package 的 pre-gate（possible_exact + scaled∩series）同语义。
    """
    if not isinstance(payload, dict) or "ok" not in payload:
        if stage == "dedupe" and isinstance(payload, dict) and (
                "exact" in payload or "similar_series" in payload):
            hits = list(payload.get("possible_exact") or [])
            sc = set(payload.get("scaled") or [])
            hits += [m for m in (payload.get("similar_series") or []) if m in sc]
            if hits:
                return False, ("dedupe 命中 exact/scaled_same: %s —— 工单 §7：停下喊人"
                               % ",".join(hits[:5]))
            return True, ""
        return False, "无 CADKIT JSON 输出（阶段未真正执行）"
    got = ((payload.get("engine") or {}).get("rules_sha"))
    if expect_rules and got and got != expect_rules:
        return False, ("版本握手不一致：本阶段引擎 rules_sha=%s，签发值=%s "
                       "—— 整波停下，别用错引擎生产（见 shared/versions.json）"
                       % (got, expect_rules))
    bad = [c for c in (payload.get("checks") or [])
           if isinstance(c, dict) and str(c.get("status", "")).upper() not in
           ("PASS", "SKIP", "N/A", "", "WARN")]
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


def _warn_ids(payload):
    return [c.get("id") for c in ((payload or {}).get("checks") or [])
            if isinstance(c, dict)
            and str(c.get("status", "")).upper() == "WARN"]


def run_stage(freecad, entry, root, batch, pid, stage, mem_mb, timeout,
              expect_rules=None):
    # 参数只能走环境：freecadcmd 会先把 --xxx 当成自己的选项吃掉
    cmd = [freecad, entry]
    env = dict(os.environ)
    opts = {}
    if stage == "package":
        opts["batch"] = batch
        receipt = os.path.join(root, "projects", pid, "review", "receipts.json")
        if not os.path.isfile(receipt):
            return {"pid": pid, "stage": stage, "ok": False,
                    "error": "PENDING_REVIEW: review/receipts.json missing",
                    "pending_review": True, "rc": None, "seconds": 0.0,
                    "degraded": False, "warns": []}
        opts["review"] = "@" + os.path.abspath(receipt)
    if stage == "export":
        opts["long_side"] = 2400
    if stage == "dedupe":
        opts["register"] = "1"
    env.update({"CADKIT_ACTION": stage, "CADKIT_ROOT": root, "CADKIT_PROJECT": pid,
                "CADKIT_MEM_MB": str(mem_mb), "CADKIT_OPTS": json.dumps(opts)})
    # 修（B 机 08:30 报告②，代码复核成立）：FreeCAD 的进度条与 CADKIT JSON
    # 共享同一 stdout 行流，40KB 块边界可以把 JSON 拦腰截断 —— 而 entry.py
    # 对**所有** action 都支持 --out（结果文件由引擎自己落盘，不过 stdout）。
    # 旧版只给 validate/probe/export/package 设了 CADKIT_OUT，build 裸奔；
    # 现在全阶段都落文件、优先读文件，stdout 只剩兜底。旧文件先删，
    # 防上一轮的陈旧结果冒充本轮执行。
    out = os.path.join(root, "projects", pid, "build", "runner_%s.json" % stage)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        os.remove(out)
    except OSError:
        pass
    env["CADKIT_OUT"] = out
    # Gate 6: acceptance requires a human review. Package refuses without
    # L1/L2 receipts even if the operator left CADKIT_REQUIRE_REVIEW unset.
    if stage == "package":
        env["CADKIT_REQUIRE_REVIEW"] = "1"
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"pid": pid, "stage": stage, "ok": False, "error": "timeout %ds" % timeout,
                "seconds": round(time.time() - t0, 1)}
    payload = None
    try:
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        payload = None
    if payload is None:  # 兜底：stdout 的 CADKIT>> 行（可能被 spinner 污染）
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
            "warns": _warn_ids(payload),
            "error": None if ok else err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--intake", required=True, help="emit_members.py 生成的本波 intake")
    ap.add_argument("--stages", default="build,validate,probe,export")
    ap.add_argument("--package", action="store_true",
                    help="阶段通过后包装。N43 已 fail-closed，可与 --package-workers 并行")
    ap.add_argument("--package-workers", type=int, default=1,
                    help="并行包装数。默认 1，直到 1.1.1 上的新 tarball 验证过并发")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="同一修订上瞬时失败的重试上限；设计 FAIL 不因后来的 PASS 被抹掉")
    ap.add_argument("--repair-failed", action="store_true",
                    help="已耗尽次数的 FAIL 再排队（新修订或人工修复之后）")
    ap.add_argument("--only", default=None,
                    help="只跑这些件号，逗号分隔（例如 14 件隔离重修）")
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
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 件（试点/分批用；续跑语义不变）")
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
    fam_status, fam_detail = "missing", "no manifest"
    if a.machine:
        fam_status, fam_detail = family_freeze.check(
            a.machine, os.path.join(root, "family"))
        if fam_status == "mismatch":
            print("BLOCK 族谱核与 family_src/%s 不一致：%s" % (a.machine, fam_detail))
            print("      先 tools/family_freeze.py，或把活核恢复成冻结字节。不要边跑边改核。")
            return 2
        if fam_status != "ok":
            print("WARN 族谱核未冻结（%s）。本波可以跑，验收不能 ACCEPT。" % fam_detail)
    rec = Recorder(root, engine, a.expect_rules or rules_sha_of(engine))
    only = [p.strip() for p in a.only.split(",")] if a.only else None
    todo = pending(rec, a.intake, stages, a.limit,
                   repair_failed=a.repair_failed, max_attempts=a.max_attempts,
                   only=only)
    print("引擎 %s（%s）\n本波待跑 %d 件（阶段 %s）；生产并发 %d（内存预算 %dMB / 单件 %dMB），package %s"
          % (engine, os.path.basename(freecad), len(todo), ",".join(stages), allowed,
             a.mem_budget_mb, a.per_part_mb,
             ("并行 %d" % a.package_workers) if a.package else "本次不做"))
    def wave_rows():
        doc = json.load(open(a.intake, encoding="utf-8"))
        rows = []
        for pid in doc.get("parts", []):
            ev = acceptance.load_evidence(root, pid)
            _fam, fam_path = _family_file(root, pid)
            hashes = acceptance.disk_hashes(root, pid, fam_path)
            fp = None
            if ev:
                for st in (ev.get("stages") or {}).values():
                    if isinstance(st, dict) and st.get("fingerprint"):
                        fp = st["fingerprint"]
            current_input = (((ev or {}).get("stages") or {}).get("build") or {}
                             ).get("input_sha")
            file_sha = None
            sealed = None
            ledger_input = None
            signoff = None
            ev_path = acceptance.evidence_path(root, pid)
            if os.path.isfile(ev_path):
                file_sha = acceptance.sha256_file(ev_path)
            stage_inputs = []
            for stage_name, st in ((ev or {}).get("stages") or {}).items():
                stage_inputs.append((stage_name, (st or {}).get("input_sha")))
            flags = read_part_ledger(
                root, pid, rec.rules, stage_inputs, current_input)
            sealed = flags["sealed"]
            ledger_input = flags["ledger_input"]
            signoff = flags["signoff"]
            event_fail = flags["event_fail"]
            if flags["error"]:
                status, why = ("needs_repair",
                               "ledger check failed: %s" % flags["error"])
            else:
                status, why = acceptance.classify_part(
                    pid, ev, hashes,
                    quarantined=pid in QUARANTINE_PIDS or bool(ledger_input),
                    ledger_input_sha=ledger_input,
                    current_input_sha=current_input,
                    signoff=signoff,
                    file_sha=file_sha, sealed_sha=sealed,
                    event_unwaived_fail=event_fail)
            if status == "accepted" and fam_status != "ok":
                status, why = "unverified", "family core unfrozen"
            rows.append({"project_id": pid, "status": status,
                         "family": acceptance.family_key(pid),
                         "fingerprint": fp if status == "accepted" else None,
                         "detail": why or (ev or {}).get("accept_reason")})
        return rows

    def emit_report():
        blockers = []
        try:
            from cadkit.ledger import ledger_acceptance_blocks, open_ledger
            conn = open_ledger(root)
            try:
                blockers = ledger_acceptance_blocks(conn)
            finally:
                conn.close()
        except Exception as exc:
            blockers = ["ledger_unreadable: %s" % exc]
        paths = acceptance.write_wave_report(
            REPO, a.batch, wave_rows(), intake=a.intake, root=root,
            extra={"family_core": fam_status, "family_detail": fam_detail,
                   "quarantine_pids": sorted(QUARANTINE_PIDS),
                   "ledger_blockers": blockers,
                   "note": "accepted + quarantined + infeasible should equal "
                           "400 per family before the next wave"})
        print("验收报告 %s  VERDICT %s" % (paths["report"], paths["verdict"]))
        return paths

    if a.dry_run:
        for pid, t in todo[:5]:
            print("  %s: %s" % (pid, ",".join(t)))
        print("dry-run，未执行")
        emit_report()
        return 0

    from cadkit.ledger import backup_ledger, hold_legacy_publisher_lock
    held_lock = hold_legacy_publisher_lock(root, a.batch)
    if held_lock is None:
        print("BLOCK 旧引擎正在占用 delivery/%s/.publisher.lock" % a.batch)
        print("      0.5.0 tarball / driver.py 用同一把批锁。本进程会一直占着它，"
              "所以旧引擎中途启动会被挡住。先停掉已经在跑的旧进程。")
        return 2
    import atexit
    atexit.register(os.close, held_lock)

    q = queue.Queue()
    for pid, t in todo:
        q.put((pid, t))
    pkg_q = queue.Queue()
    log = open(a.log or os.path.join(REPO, "status", "runner_%s_%s.jsonl"
                                     % (a.batch, time.strftime("%Y%m%d_%H%M"))), "a",
               encoding="utf-8")
    from cadkit.ledger import read_end_sha
    snap = backup_ledger(root)
    backup_sha = snap[1] if snap else None
    previous_end = read_end_sha(root)
    try:
        chain_doc, chain_ok = enforce_anchor_check(
            root, REPO, backup_sha, previous_end)
    except AnchorCheckFailed as exc:
        print("BLOCK 台账锚核对失败，本次不启动：%s" % exc)
        log.close()
        return 2
    header = append_run_header(
        log, backup_sha=backup_sha, previous_end_sha=previous_end,
        run_id=time.strftime("%Y%m%d_%H%M%S") + "-%d" % os.getpid(),
        chain_anchor=chain_doc, chain_anchor_ok=chain_ok)
    if snap:
        print("台账快照 %s sha256 %s" % (snap[0], snap[1]))
    if chain_doc:
        print("链锚 %s 条 head %s 核对 %s" % (
            chain_doc.get("event_count"), chain_doc.get("head_hash"),
            chain_ok))
    if header["ledger_matches_previous_end"] is False:
        print("WARN 台账和上次运行结束时的快照不一致")
    lock = threading.Lock()
    stop = threading.Event()
    stats = {"ok": 0, "fail": 0, "reviews": 0, "packaged": 0, "done_parts": 0}
    paused = set()
    design_fails = {}

    def remember(pid, stage, r):
        """Ledger + evidence. A sticky FAIL beats a later engine PASS."""
        klass = None if r.get("ok") else acceptance.classify_failure(r.get("error"))
        stored = rec.record(pid, stage, r.get("ok"), klass,
                            max_attempts=a.max_attempts)
        if r.get("ok") and isinstance(stored, dict) and not stored.get("applied"):
            r["ok"] = False
            r["error"] = stored.get("reason") or "N45 sticky FAIL"
            klass = "n45_sticky"
        fam, fam_path = _family_file(root, pid)
        hashes = acceptance.disk_hashes(root, pid, fam_path)
        ev = acceptance.load_evidence(root, pid) or acceptance.fresh_evidence(
            pid, fam, hashes.get("family_core_sha256"), rec.rules)
        payload = None
        outp = os.path.join(root, "projects", pid, "build",
                            "runner_%s.json" % stage)
        try:
            payload = json.load(open(outp, encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        fp = None
        drift = False
        if isinstance(payload, dict):
            raw = payload.get("fingerprint")
            if isinstance(raw, dict) and "volume" in raw:
                fp = raw
            elif isinstance(raw, dict) and isinstance(raw.get("fingerprint"), dict):
                fp = raw["fingerprint"]
        prev_fp = None
        for st in (ev.get("stages") or {}).values():
            if isinstance(st, dict) and isinstance(st.get("fingerprint"), dict):
                prev_fp = st["fingerprint"]
        if (fp and prev_fp and hashes.get("family_core_sha256")
                and ev.get("family_core_sha256") == hashes.get("family_core_sha256")):
            drift = metric_drift(prev_fp, fp)
        try:
            ih = rec.input_hash(pid, stage)
        except Exception:
            ih = None
        if stage == "package" and isinstance(payload, dict):
            ev["review_status"] = payload.get("review_status") or "pending"
        acceptance.note_stage(
            ev, stage, ok=r.get("ok"), input_sha=ih, error_class=klass,
            hashes=hashes, fingerprint=fp, max_attempts=a.max_attempts,
            drift=drift)
        want = list(stages) + (["package"] if a.package else [])
        acceptance.mark_acceptance(ev, hashes, want)
        if fam_status != "ok":
            ev["accepted"] = False
            ev["accept_reason"] = "family core unfrozen"
        path = acceptance.save_evidence(root, pid, ev)
        conn = rec._open(root)
        try:
            from cadkit.ledger import seal_evidence
            seal_evidence(conn, pid, acceptance.sha256_file(path))
        finally:
            conn.close()
        if acceptance.counts_toward_family_pause(klass):
            design_fails[fam] = design_fails.get(fam, 0) + 1
            if design_fails[fam] >= 3:
                paused.add(fam)
        return r

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
            if acceptance.family_key(pid) in paused:
                emit({"lane": "produce", "pid": pid, "ok": False,
                      "error": "family paused after 3 design FAILs"})
                q.task_done()
                continue
            for stage in t:
                r = run_stage(freecad, entry, root, a.batch, pid, stage,
                              a.stage_mem_mb, a.timeout, a.expect_rules)
                r["lane"] = "produce"
                r["worker"] = n
                with lock:
                    remember(pid, stage, r)
                    stats["ok" if r["ok"] else "fail"] += 1
                emit(r)
                print("%-7s [%d] %-18s %-8s %s%s" % (
                    "ok" if r["ok"] else "FAIL", n, pid, stage,
                    "%5.1fs" % r["seconds"],
                    "  " + str(r["error"])[:90] if not r["ok"]
                    else ("  degraded" if r["degraded"] else "")), flush=True)
                if not r["ok"]:
                    break                        # 任一 FAIL 不再往下走（工单 §3）
            else:
                with lock:
                    stats["done_parts"] += 1
                    if a.package:
                        pkg_q.put(pid)
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
    pkg_threads = []
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
                with lock:
                    remember(pid, "package", r)
                    stats["packaged" if r["ok"] else "fail"] += 1
                    print("PACKAGE %-18s %s%s" % (pid, "ok" if r["ok"] else "FAIL",
                          "  " + str(r["error"])[:90] if not r["ok"] else ""), flush=True)
                emit(r)
        n_pkg = max(1, a.package_workers)
        for _i in range(n_pkg):
            th = threading.Thread(target=do_package, daemon=True)
            th.start()
            pkg_threads.append(th)

    for t in threads:
        t.join()
    # 生产线程结束后必须把包装队列排空再停：半途 stop 会静默丢掉交付，
    # 在 20 万件规模上就是"台账说做了 2 万件、盘上只有 1.9 万件"。
    if pkg_threads:
        pkg_q.join()
    stop.set()
    if review_thread:
        review_thread.join(timeout=120)
    for t in pkg_threads:
        t.join(timeout=120)
    emit_report()
    end = backup_ledger(root)
    end_sha = end[1] if end else None
    from cadkit.ledger import (open_ledger, write_chain_anchor, write_end_sha,
                               write_status_anchor)
    if end_sha:
        write_end_sha(root, end_sha)
    anchor = None
    try:
        conn = open_ledger(root)
        try:
            anchor = write_chain_anchor(root, conn)
            write_status_anchor(REPO, conn, end_sha)
        finally:
            conn.close()
    except Exception as exc:
        print("WARN 链锚没有写上：%s" % exc)
    else:
        print("仓内锚已写入 status/ledger_anchor.json。"
              "生产前要单独执行 python3 tools/push_ledger_anchor.py "
              "把它提交并推上去。这次运行不会自己 push。")
    log.write(json.dumps({
        "lane": "run_footer",
        "run_id": header["run_id"],
        "ledger_end_sha256": end_sha,
        "chain_event_count": None if not anchor else anchor.get("event_count"),
        "chain_head_hash": None if not anchor else anchor.get("head_hash"),
    }, ensure_ascii=False) + "\n")
    log.flush()
    if anchor:
        print("链锚已写入 %s  event_count=%s head=%s" % (
            os.path.join(root, "ledger.chain.anchor"),
            anchor.get("event_count"), anchor.get("head_hash")))
    print("完成：件 %d（含 %d 次旧件复检），阶段 ok=%d fail=%d，package=%d；日志 %s"
          % (stats["done_parts"], stats["reviews"], stats["ok"], stats["fail"],
             stats["packaged"], os.path.relpath(log.name, REPO)))
    return 1 if stats["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
