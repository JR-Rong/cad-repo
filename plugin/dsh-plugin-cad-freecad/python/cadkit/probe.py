"""Editability probe (purchase spec 2.4 "editable").

Primary mode drives the params sheet: each driving parameter cell is perturbed
by a small relative delta, the whole model is force-recomputed, and we verify
(a) no object errors and (b) the parameter actually influences the model
(solid volume moves). The cell is restored and the exact original volume must
return.

Robustness: OCC can *segfault* on pathological perturbations (broken
topological-name links deep in a dress-up chain). A crash must fail one
parameter, not the whole probe, so each perturbation runs in a child
freecadcmd process; the orchestrator restarts from the next cell after a
crash and records "CRASH" for the offending one. Models with no params sheet
fall back to perturbing sketch dimensions (single process).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

FREECADCMD = os.environ.get(
    "CADKIT_BIN", "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")


def _states(doc):
    bad = []
    for o in doc.Objects:
        st = list(o.State)
        if any(s in st for s in ("Invalid", "Error")) or "Touched" in st:
            bad.append(o.Name)
    return bad


def _total_volume(doc):
    from .validate import final_objects
    v = 0.0
    for o in final_objects(doc):
        try:
            v += abs(o.Shape.Volume)
        except Exception:
            return None
    return v


def _metric(doc):
    """Effect fingerprint: (final volume, final centroid, summed bbox centers
    of every shaped object). Pure translations keep volume and even centroid
    (symmetric patterns) but always move intermediate feature/cutter bboxes."""
    from .validate import final_objects
    v = 0.0
    cx = cy = cz = 0.0
    for o in final_objects(doc):
        try:
            vol = abs(o.Shape.Volume)
            c = o.Shape.CenterOfGravity
        except Exception:
            return None
        v += vol
        cx += c.x * vol
        cy += c.y * vol
        cz += c.z * vol
    if v <= 0:
        return None
    bx = by = bz = 0.0
    ds = ext = 0.0
    for o in doc.Objects:
        try:
            bb = o.Shape.BoundBox
        except Exception:
            continue
        bx += bb.Center.x
        by += bb.Center.y
        bz += bb.Center.z
        # anti-cancellation: symmetric patterns move pairwise so Σcenters and
        # centroid cancel out; |c| and extent sums cannot cancel for a bite.
        ds += (bb.Center.x ** 2 + bb.Center.y ** 2 + bb.Center.z ** 2) ** 0.5
        ext += bb.XLength + bb.YLength + bb.ZLength
    return v, cx / v, cy / v, cz / v, bx, by, bz, ds, ext


# ---------------------------------------------------------------------------
# in-process worker: one perturbation step, one CADKIT-STEP>> line per param
# ---------------------------------------------------------------------------
def _split_addr(a):
    import re as _r
    m = _r.match(r"([A-Za-z]+)(\d+)", a or "")
    if not m:
        return None, None
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))


def _addr(col, row):
    name = ""
    while col:
        col, rem = divmod(col - 1, 26)
        name = chr(65 + rem) + name
    return "%s%d" % (name, row)


def driving_cells(sh):
    """N5 recheck: the driving-parameter inventory is the ENTIRE used
    rectangle of the sheet (getUsedRange), not a heuristic column walk
    with a blank-run cutoff. Sparse drivers (A300 after 297 blank rows)
    and formula-valued aliases (resolved via Sheet.get) are included;
    every aliased cell whose resolved value is numeric is a parameter
    the probe must perturb. Shared verbatim by probe and packager so the
    two inventories can never drift apart again."""
    try:
        ur = sh.getUsedRange()
    except Exception:
        ur = None
    cells = []
    if ur and len(ur) == 2:
        c0, r0 = _split_addr(ur[0])
        c1, r1 = _split_addr(ur[1])
        if c0 and c1:
            if c0 > c1:
                c0, c1 = c1, c0
            if r0 > r1:
                r0, r1 = r1, r0
            # N5 recheck: NO silent truncation of the used rectangle -
            # a driver at A5000 was dropped by the old 4096-row cap while
            # both probe and packager agreed on the WRONG list. Refuse
            # (never guess) beyond the sanity ceiling.
            if (c1 - c0 + 1) > 512 or (r1 - r0 + 1) > 65536:
                raise ValueError("sheet used-range too large to audit: "
                                 "%dx%d at %s..%s" % (c1 - c0 + 1,
                                 r1 - r0 + 1, ur[0], ur[1]))
            for col in range(c0, c1 + 1):
                for row in range(r0, r1 + 1):
                    cells.append(_addr(col, row))
    if not cells:                       # defensive fallback: legacy scan
        cells = ["A%d" % r for r in range(1, 1025)]
    out = []
    for addr in cells:
        try:
            alias = sh.getAlias(addr)
            if not alias:
                continue
            val = float(sh.get(addr))
        except Exception:
            continue                    # blank / text / unresolvable
        out.append((addr, val))
    seen, uniq = set(), []
    for a, v in out:
        if a not in seen:
            seen.add(a)
            uniq.append((a, v))
    return uniq


def _aliases(doc):
    sh = doc.getObject("params")
    if sh is None:
        return None
    return sh, driving_cells(sh)


def probe_worker(path, start=0, limit=40, delta=0.01):
    """Perturb cells start..limit sequentially, emitting one CADKIT-STEP>>
    line per completed step (crash loses only the in-flight cell) plus a
    final CADKIT-DONE>> aggregate (authoritative; FreeCAD progress output can
    splice onto the same physical line as STEP markers)."""
    import FreeCAD as App
    # N5 recheck: close the worker's own check->open window. The snapshot
    # bytes are read ONCE, hash-verified against the parent's declared
    # snapshot sha, written into this worker's private 0700 temp file and
    # THAT copy is opened. A file replaced after our hash can therefore
    # never be the model the cells were perturbed on.
    want_sha = os.environ.get("CADKIT_PROBE_SNAP_SHA")
    if want_sha:
        import hashlib, tempfile, shutil as _sh2
        with open(path, "rb") as _f:
            _data = _f.read()
        if hashlib.sha256(_data).hexdigest() != want_sha:
            raise ValueError("probe snapshot hash mismatch at worker open "
                             "(tampered mid-run, N5)")
        _wd = tempfile.mkdtemp(prefix="cadkit-worker-")
        try:
            os.chmod(_wd, 0o700)
        except Exception:
            pass
        _pin = os.path.join(_wd, os.path.basename(path))
        with open(_pin, "wb") as _f:
            _f.write(_data)
            _f.flush()
            os.fsync(_f.fileno())
        path = _pin
    doc = App.openDocument(path)
    allrecs = []
    try:
        doc.recompute(None, True, True)
        sa = _aliases(doc)
        if sa is None:
            print("CADKIT-STEP>>" + json.dumps({"cell": "__nosheet__", "status": "NOSHEET"}), flush=True)
            return
        sh, aliases = sa
        print("CADKIT-ROWS>>%d" % len(aliases), flush=True)
        # N5(r4) scale fix: the orchestrator plans spans over LIST
        # INDICES, not row numbers - on a sparse sheet (driver at A5000)
        # row-based resumes/skips probed the wrong cells and the backfill
        # invented "A3" style names for empty rows. Publishing the full
        # address list lets the orchestrator track real cells.
        try:
            print("CADKIT-ALIASES>>" + json.dumps(
                [a for a, _v in aliases]), flush=True)
        except Exception:
            pass
        from .validate import final_objects as _finals
        for idx, (cell, old) in enumerate(aliases):
            if idx < start:
                continue
            if idx >= start + limit:
                break
            rec = {"cell": cell, "idx": idx, "value": old,
                   "status": "PASS", "note": ""}
            try:
                doc.recompute(None, True, True)
                base = _metric(doc)
                base_solids = 0
                for o in _finals(doc):
                    base_solids += len(o.Shape.Solids)
                sh.set(cell, "%.10f" % (old * (1.0 + delta)))
                doc.recompute(None, True, True)
                bad = _states(doc)
                v = _metric(doc)
                if not bad:
                    # W6 (workflow audit): the EDITED solid must also pass a
                    # shape validity check — a rebuild without error states
                    # is weaker than a valid result.
                    for o in _finals(doc):
                        try:
                            o.Shape.check(False)
                        except Exception:
                            bad.append("edited-solid-invalid:" + o.Name)
                            break
                if bad or v is None or v[0] <= 0:
                    rec.update(status="FAIL",
                               note="rebuild failed: " + (",".join(bad[:5]) or "invalid solid"))
                elif base and _moved(base, v) is None:
                    # W5/W6: an inert cell is NOT "success with warning" —
                    # the parameter does not drive geometry (broken
                    # expression). Fail closed; family docs promise every
                    # sheet cell is wired through expressions.
                    rec.update(status="FAIL",
                               note="inert parameter: perturbation produced "
                                    "no geometric change (unwired cell?)")
                sh.set(cell, "%.10f" % old)
                doc.recompute(None, True, True)
                bad2 = _states(doc)
                v2 = _metric(doc)
                n2 = 0
                for o in _finals(doc):
                    try:
                        n2 += len(o.Shape.Solids)
                    except Exception:
                        n2 = -1
                        break
                if bad2 or v2 is None or base is None or n2 != base_solids or \
                        abs(v2[0] - base[0]) > max(1e-6, base[0] * 1e-6):
                    rec.update(status="FAIL", note=(rec["note"] + " | restore mismatch"
                              "(solids %d vs %d)" % (n2, base_solids)).strip(" |"))
            except Exception as e:
                rec.update(status="FAIL", note="exception: " + str(e)[:140])
                try:
                    sh.set(cell, "%.10f" % old)
                    doc.recompute(None, True, True)
                except Exception:
                    pass
            allrecs.append(rec)
            print("CADKIT-STEP>>" + json.dumps(rec), flush=True)
        print("CADKIT-DONE>>" + json.dumps(allrecs), flush=True)
    finally:
        App.closeDocument(doc.Name)


def _moved(a, b, tol=1e-7):
    """Return a human tag of what changed, or None if nothing did."""
    if abs(b[0] - a[0]) > max(abs(a[0]) * 1e-6, 1e-6):
        return "volume"
    for i, label in ((1, "cx"), (2, "cy"), (3, "cz"), (4, "bx"), (5, "by"),
                     (6, "bz"), (7, "radial"), (8, "extent")):
        if abs(b[i] - a[i]) > 1e-4:
            return label
    return None


# ---------------------------------------------------------------------------
# orchestrator (runs in the outer freecadcmd, spawns children)
# ---------------------------------------------------------------------------
def _calibrate_worker_mb(path):
    """Open + fully recompute the model once here, so the memory one worker
    needs is measured, not guessed. Returns MB (floor 200)."""
    import FreeCAD as App

    from . import mem
    doc = None
    try:
        doc = App.openDocument(path)
        doc.recompute(None, True, True)
    except Exception:
        pass
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:
                pass
    import gc
    gc.collect()
    return max(200.0, mem.rss_mb() * 1.6 + 80.0)


def plan_workers(max_steps, cap=None, per_worker_mb=None, requested=None):
    """Concurrency from the memory budget (see cadkit/mem.py).

    The historical default was 6 simultaneous freecadcmd processes, which on
    a 16 GB host put several GB into the pipeline for one part. Workers are
    now derived: cap * 0.85 / measured-per-worker, clamped by the configured
    ceiling, by how much memory the OS actually has free, and by the number
    of parameters left to probe.
    """
    from . import mem

    cap = cap or mem.cap_mb()
    ceiling = mem.probe_max_workers()
    per = per_worker_mb or 400.0
    n_budget = int(cap * 0.85 / per)
    avail = mem.avail_mb()
    n_os = max(1, int((avail - mem.floor_mb()) / per)) if avail else 10 ** 6
    want = ceiling if requested is None else min(int(requested), ceiling)
    # W16 (workflow audit): do not force a worker into a budget that cannot
    # hold it — 0 allowed workers is an INFEASIBLE job, refused upstream.
    n = min(want, n_budget, n_os, max(1, max_steps))
    if n <= 0:
        return 0, {"per_worker_mb": round(per, 1), "ceiling": ceiling,
                   "budget_allows": n_budget, "os_allows": n_os,
                   "refused": "memory budget allows 0 workers (cap %.0fMB, "
                              "%.0fMB/worker)" % (cap, per)}
    notes = []
    if requested and n < int(requested):
        notes.append("probe workers %d -> %d (cap %.0fMB, %.0fMB/worker, "
                     "%s MB available)" % (int(requested), n, cap, per,
                                           "n/a" if avail is None else int(avail)))
        mem.degrade(notes[-1])
    elif n < ceiling:
        mem.degrade("probe workers capped at %d of %d (cap %.0fMB, %.0fMB/worker)"
                    % (n, ceiling, cap, per))
    return n, {"per_worker_mb": round(per, 1), "ceiling": ceiling,
               "budget_allows": n_budget, "os_allows": n_os}


def _wait_for_memory(log=None):
    """Block (briefly) while the host is below the memory floor, so probe
    spans never pile up on top of an already-paging machine."""
    import time

    from . import mem
    waited = 0
    while not mem.memory_ok():
        if waited == 0 and log is not None:
            log("memory below floor %.0fMB — holding probe spans" % mem.floor_mb())
            mem.degrade("probe paused until %.0fMB free (host below floor)"
                        % mem.floor_mb())
        if waited >= 180:
            return False
        time.sleep(5)
        waited += 5
    return True


def probe_project(proj, limit=40, delta=0.01, workers=None):
    """Crash-isolated, PARALLEL editability probe: the parameter rows are
    split into contiguous spans probed by independent freecadcmd children
    (each opens its own in-memory copy, perturbs, restores, never saves —
    spans cannot interfere). A crash loses only the in-flight cell and its
    span restarts from the next row.

    Concurrency is derived from the memory budget (see plan_workers), not
    hard-coded."""
    from . import mem

    path = proj.fcstd_path

    # W7 (workflow audit): workers must probe ONE immutable, content-addressed
    # snapshot — and the source must be byte-identical when they finish.
    # Previously workers re-opened the mutable project path while the hash
    # was taken afterwards, so a rebuild mid-probe could bind fresh hashes to
    # mixed old results.
    import shutil as _sh
    import tempfile as _tf
    import hashlib as _hashlib

    def _sha(p):
        h = _hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()

    _snapdir = _tf.mkdtemp(prefix="cadkit-probe-")
    _snap = os.path.join(_snapdir, os.path.basename(path))
    _sh.copy2(path, _snap)
    _src_sha0 = _sha(path)
    _snap_sha = _sha(_snap)
    try:
        os.chmod(_snapdir, 0o700)   # exclusive: no other reader/writer
    except Exception:
        pass
    path = _snap
    entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "entry.py")
    base_env = dict(os.environ)
    base_env.pop("CADKIT_OUT", None)
    # THE children are single-span WORKERS. This used to be missing: they
    # inherited CADKIT_ACTION=probe from the parent environment, re-entered
    # probe_project, and every level spawned another full set of freecadcmd
    # processes until fork()/memory gave out — an accidental fork bomb that
    # also made every cell report "NOT_PROBED".
    base_env["CADKIT_ACTION"] = "probe_worker"
    base_env["CADKIT_ORCHESTRATOR"] = "1"
    base_env["CADKIT_MEM_MB"] = str(mem.cap_mb())     # children self-govern too
    # R4 (recheck): workers must probe the IMMUTABLE SNAPSHOT, not the
    # mutable project file the orchestrator merely copied from. The child
    # resolves this exact path; the report's snapshot_sha ties the result.
    base_env["CADKIT_PROBE_FCSTD"] = _snap
    base_env["CADKIT_PROBE_SNAP_SHA"] = _snap_sha
    # R6: the PROJECT LAYOUT defines the root. An ambient CADKIT_ROOT
    # inherited from a test harness or a previous CLI stage must not
    # point the workers at a different tree (they then "find" no sheet
    # and the whole probe collapses to probed=0).
    _derived = os.path.abspath(os.path.join(proj.root, os.pardir,
                                            os.pardir))
    cad_root = _derived if os.path.isdir(
        os.path.join(_derived, "projects")) else (
        base_env.get("CADKIT_ROOT") or _derived)
    base_env["CADKIT_ROOT"] = cad_root
    max_steps = max(1, limit)
    total = [None]
    addrs = [None]          # driving-cell address list (from any worker)
    abnormal = [0]          # worker exits that died nonzero AFTER finishing
                            # their window (teardown crash, not a cell fault)

    viol = [0]   # N29/N30 round 7: protocol-violating worker records
    _gidx = {}   # N39 round 9: GLOBAL index -> cell agreement (run_span
                 # local by_idx lets bootstrap A1@0 and tail A5000@0
                 # coexist; the orchestrator must not double-book an
                 # index across windows)

    def _put_rec(rec, results, by_idx):
        # N30 round 7: a STEP line that parses to null/list/number, or
        # lacks a string cell, is a PROTOCOL violation - never trust it
        # and never let it crash the orchestrator.
        if not isinstance(rec, dict) or not isinstance(rec.get("cell"),
                                                       str):
            viol[0] += 1
            return
        # N36 round 8: a dict WITHOUT a usable status would KeyError at
        # report assembly (details r["status"]) - refuse it at intake.
        if rec.get("status") not in ("PASS", "FAIL"):
            viol[0] += 1
            return
        # N42 round 9: an accepted field must not poison report
        # assembly later (CRASH substring scans choke on null/42).
        if "note" in rec and not isinstance(rec["note"], str):
            viol[0] += 1
            return
        # N29 round 7: with the driving inventory known, a record must
        # name a REAL cell; the index must sit inside the authoritative
        # list. Out-of-range idx values (99999, tail cells wearing the
        # wrong index) are discarded and counted - they fabricate
        # coverage otherwise.
        if addrs[0] is not None:
            if rec["cell"] not in addrs[0]:
                viol[0] += 1
                return
            _i = rec.get("idx")
            if isinstance(_i, int) and not 0 <= _i < len(addrs[0]):
                viol[0] += 1
                return
        # N23 round 6: transcript records are NEVER last-write-wins.
        # A FAIL for a cell is trusted over a later duplicate PASS (and
        # an equal duplicate is harmless); progress accounting uses the
        # SET of validated indices, not the last record seen, so a
        # REVERSED transcript cannot fake window completion or hide a
        # nonzero exit behind an early-looking last line.
        prev = results.get(rec["cell"])
        if prev is not None and prev.get("status") == "FAIL" \
                and rec.get("status") == "PASS":
            return
        idx = _idx_of(rec, addrs[0])
        # N35 round 8 / N39 round 9: two DIFFERENT cells claiming one
        # index is a collision - same-window AND across windows. The
        # same cell repeating its own index stays harmless. First claim
        # wins; the late rival is counted, never merged.
        if idx is not None:
            if idx in by_idx and by_idx[idx].get("cell") != rec["cell"]:
                viol[0] += 1
                return
            if _gidx.get(idx, rec["cell"]) != rec["cell"]:
                viol[0] += 1
                return
            if idx not in _gidx:
                _gidx[idx] = rec["cell"]
        results[rec["cell"]] = rec
        if idx is not None:
            by_idx[idx] = rec

    def harvest(text, tail, results, base, by_idx):
        # N18 round 5: resume tracking starts at the window's FIRST index
        # (lo-1); a harvest that saw zero records must NOT look like one
        # cell completed (the old 0-init misattributed an immediate crash
        # to A2 while A1 silently became NOT_PROBED).
        # N23 round 6: completion is the CONTIGUOUS prefix over the index
        # set - out-of-order records mark their own slot but only fill a
        # gap when every lower index has been proven done.
        done = None
        for line in text.splitlines():
            za = line.rfind("CADKIT-ALIASES>>")
            if za >= 0:
                try:
                    _al = json.loads(line[za + 16:])
                except Exception:
                    _al = None
                # N41 round 9: the payload must be a list of cell-name
                # strings; 7 or [{"cell":...}] used to crash len()/set()
                # downstream. N40 round 9: the FIRST announced inventory
                # is authoritative - a later worker cannot ERASE or
                # replace it to fake smaller (or full) coverage.
                if (isinstance(_al, list)
                        and all(isinstance(a, str) for a in _al)):
                    if addrs[0] is None:
                        addrs[0] = _al
                    elif addrs[0] != _al:
                        viol[0] += 1
                else:
                    viol[0] += 1
                continue
            k = line.rfind("CADKIT-ROWS>>")
            if k >= 0:
                try:
                    _v = int(line[k + 13:].split("<")[0].strip().split()[0])
                except Exception:
                    _v = -1
                # Round-8 (absurd-total probe): a worker CLAIMING a
                # billion driving cells must not make the orchestrator
                # plan a billion windows - the claim is protocol
                # insanity, counted as a violation and ignored.
                if addrs[0] is not None:
                    pass  # the ALIASED inventory is authoritative;
                          # an absurd or different claim is a lie that
                          # cannot change planning - ignore it
                elif 0 < _v <= 1_000_000:
                    if total[0] is None or _v >= total[0]:
                        total[0] = _v
                    else:
                        # N40 round 9: a LATER smaller claim cannot
                        # erase the established expectation either.
                        viol[0] += 1
                elif _v != -1:
                    viol[0] += 1
                continue
            j = line.rfind("CADKIT-DONE>>")
            if j >= 0:
                try:
                    done = json.loads(line[j + 13:])
                except Exception:
                    pass
                continue
            i = line.rfind("CADKIT-STEP>>")
            if i >= 0:
                try:
                    rec = json.loads(line[i + 13:])
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("status") == "NOSHEET":
                    tail.append("NOSHEET")
                    continue
                _put_rec(rec, results, by_idx)
        if done is not None:
            if isinstance(done, list):
                for rec in done:
                    _put_rec(rec, results, by_idx)
            else:
                viol[0] += 1
        start = base
        while (start + 1) in by_idx:
            start += 1
        return start

    def run_span(lo, hi):
        """Probe driving-cell LIST INDICES lo..hi inclusive (N5 scale fix:
        index-based, so sparse sheets cannot probe or backfill the wrong
        cell). Returns (results, nosheet)."""
        env = dict(base_env)
        results = {}
        by_idx = {}            # index -> record (contiguous-prefix proof)
        start = lo - 1
        attempts = 0
        while start < hi and attempts <= (hi - lo) + 3:
            attempts += 1
            env["CADKIT_OPTS"] = json.dumps({
                "delta": delta, "limit": hi - start, "start": start + 1})
            tail = []
            try:
                if not _wait_for_memory():
                    # W16 (workflow audit): a memory-wait failure is honoured
                    # — the span stops and its remaining cells backfill as
                    # NOT_PROBED (FAIL), instead of pushing another worker
                    # past the cap.
                    mem.degrade("memory budget refused probe worker start "
                                "after 180s wait — span aborted (W16)")
                    return results, False
                proc = subprocess.Popen(
                    [FREECADCMD, entry], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    universal_newlines=True)
                out, _ = proc.communicate(timeout=3600)
                rc = proc.returncode
            except OSError as e:
                mem.degrade("probe worker failed to start (%s) — host out of "
                            "memory/process slots" % e)
                return results, False
            except subprocess.TimeoutExpired:
                # N14 recheck round 4: kill without reap left a zombie and
                # dropped the partial transcript; kill, reap, harvest.
                proc.kill()
                try:
                    out, _ = proc.communicate(timeout=60)
                except Exception:
                    out = ""
                rc = -99
            new_start = harvest(out or "", tail, results, start, by_idx)
            if "NOSHEET" in tail:
                return results, True
            if new_start > start:
                start = new_start
            if rc == 0 or start >= hi:
                # N18 round 5: a nonzero exit AFTER the whole window
                # completed is a teardown anomaly, not evidence that the
                # NEXT window's first cell crashed - old code recorded a
                # CRASH for it that the next span's PASS then erased
                # (crashes=0 was a lie either way). Account honestly.
                if rc != 0 and start >= hi:
                    abnormal[0] += 1
                break
            _k = addrs[0]
            # N18 round 5: never invent an address; if the driving list
            # was never published the real cell is UNKNOWN and the record
            # says so (the strict packaging gate fails closed on unknown
            # cells - honest failure beats a fabricated A3).
            cell = (_k[start + 1] if _k is not None and
                    start + 1 < len(_k)
                    else "__unknown_idx_%d__" % (start + 1))
            if cell not in results:
                results[cell] = {"cell": cell, "status": "FAIL",
                                 "note": "CRASH during perturbation (OCC segfault) — "
                                         "model not robust to editing this parameter"}
            start = start + 1
        return results, False

    # ---- concurrency: derived from the memory budget, measured on this model
    per_worker = _calibrate_worker_mb(path)
    nspans, why = plan_workers(max_steps, per_worker_mb=per_worker,
                               requested=workers)
    if nspans <= 0:
        _sh.rmtree(_snapdir, ignore_errors=True)
        from . import version as _ver
        rep = dict(_ver.stamp())
        rep.update({"ok": False, "mode": "params-sheet", "probe_version": 5,
                    "refused": why.get("refused", "infeasible memory budget"),
                    "fcstd_sha256": _snap_sha, "details": [], "probed": 0,
                    "failed": 0})
        return rep
    size = (max_steps + nspans - 1) // nspans
    # N5 scale fix (post-review): coverage must be EXACT, not the old
    # slice-overshoot accident. One bootstrap span (3 cells) publishes the
    # authoritative driving-cell address list; the remaining driver
    # indices are then planned over the REAL list length - sparse sheets
    # (A5000) and tails (A41-A43) are covered with true names. If even
    # the bootstrap dies, plan over the caller's limit and let the
    # backfill fail closed.
    results = {}
    nosheet = False
    boot_hi = min(2, max_steps - 1)
    boot, nosheet = run_span(0, boot_hi)
    results.update(boot)
    if not nosheet:
        n_total = len(addrs[0]) if addrs[0] else 0
        if n_total:
            # N24 round 6: the bootstrap-published driving list is the
            # AUTHORITATIVE inventory size. A caller limit ABOVE the real
            # inventory must not spawn ghost tail windows for indices
            # that do not exist (they became fabricated crashes when the
            # last real worker exited nonzero after finishing its true
            # cells).
            max_steps = n_total
        # N19 round 5: the memory/worker BUDGET selected before bootstrap
        # is authoritative - re-chunk over the discovered inventory but
        # never exceed nspans concurrent workers.
        size = (max_steps + nspans - 1) // nspans
        spans = [(a, min(max_steps - 1, a + size - 1))
                 for a in range(boot_hi + 1, max_steps, size)]
        if spans:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(
                    max_workers=min(nspans, len(spans))) as ex:
                for res_part, ns in ex.map(lambda t: run_span(*t), spans):
                    results.update(res_part)
                    nosheet = nosheet or ns
    if nosheet and not results:
        _sh.rmtree(_snapdir, ignore_errors=True)
        return _fallback_sketch_probe(proj, delta, limit)
    known = addrs[0]
    if known is None:
        # N18 round 5: NO address list ever arrived - the real inventory
        # is UNKNOWN; do not fabricate A1..An names. Backfill only up to
        # the row COUNT any worker reported, under explicit unknown
        # labels (the strict gate rejects these - fail closed, honestly).
        n_rows = total[0] if total[0] else len(results)
        known = ["__unknown_idx_%d__" % i for i in range(n_rows)]
    # N23 round 6: a driving list with DUPLICATE names is corrupt
    # inventory - refuse instead of quietly probing fewer cells than
    # the sheet declares.
    dupes = len(known) - len(set(known))
    n_rows = len(known)
    unprobed = 0
    for cell in known:
        if cell not in results:
            unprobed += 1
            results[cell] = {"cell": cell, "status": "FAIL",
                             "note": "NOT_PROBED: worker exited before reaching this parameter"}
    details = [results[k] for k in sorted(results, key=_row_of)]
    n_fail = sum(1 for r in details if r["status"] == "FAIL")
    # W7: the source must not have changed while the workers ran; N5:
    # and the SNAPSHOT the workers actually opened must still be the exact
    # bytes that were probed (re-verified before teardown).
    src_moved = _sha(proj.fcstd_path) != _src_sha0
    snap_final = _sha(_snap) if os.path.exists(_snap) else ""
    snap_ok = snap_final == _snap_sha
    ok = bool(details) and n_fail == 0 and not src_moved and snap_ok \
        and viol[0] == 0 \
        and dupes == 0 and len(details) >= n_rows
    _sh.rmtree(_snapdir, ignore_errors=True)
    from . import version as _ver
    report = dict(_ver.stamp())
    report.update({
        "ok": ok, "mode": "params-sheet", "probe_version": 5,
        "fcstd_sha256": _snap_sha,
        "source_moved_during_probe": src_moved,
        "snapshot_sha256": _snap_sha,
        "snapshot_final_ok": snap_ok,
        "probed": len(details), "workers": nspans,
        "worker_abnormal_exits": abnormal[0],
        "protocol_violations": viol[0],
        "cells_expected": n_rows,
        "coverage": "single +%.0f%% delta on every sheet cell %s" %
                    (delta * 100, "(full)" if len(details) >= n_rows
                     else "(PARTIAL — rejected)"),
        "mem": mem.status(why),
        "failed": n_fail,
        "crashes": sum(1 for r in details if "CRASH" in r.get("note", "")),
        "details": details,
        "note": "perturbed %.0f%% of %d driving parameters in isolated "
                "workers against immutable snapshot" % (delta * 100,
                                                        len(details))})
    return report


def _idx_of(rec, addrs):
    """Resume position in the driving-cell LIST (N5 scale fix). Workers
    report their absolute list index; older records fall back to the
    address map or the row-number heuristic."""
    i = rec.get("idx")
    if isinstance(i, int):
        return i
    a = rec.get("cell")
    if addrs and a in addrs:
        return addrs.index(a)
    return max(_row_of(a) - 1, 0)


def _row_of(cell):
    try:
        return int(cell[1:])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# fallback: no params sheet -> perturb sketch dimensions
# ---------------------------------------------------------------------------
def _fallback_sketch_probe(proj, delta=0.01, limit=40):
    import FreeCAD as App
    doc = App.openDocument(proj.fcstd_path)
    results = []
    try:
        doc.recompute(None, True, True)
        base_vol = _total_volume(doc)
        for sk, ci, c in _dim_constraints(doc, limit):
            name = "%s#%d %s=%s" % (sk.Name, ci, c.Type, round(c.Value, 4))
            old = c.Value
            rec = {"cell": name, "status": "PASS", "note": ""}
            try:
                sk.setDatum(ci, App.Units.Quantity("%.10f mm" % (old * (1.0 + delta))))
                doc.recompute(None, True, True)
                bad = _states(doc)
                v = _total_volume(doc)
                if bad or v is None or v <= 0:
                    rec.update(status="FAIL", note="rebuild failed: " +
                               (",".join(bad[:5]) or "invalid solid"))
                sk.setDatum(ci, App.Units.Quantity("%.10f mm" % old))
                doc.recompute(None, True, True)
                bad2 = _states(doc)
                v2 = _total_volume(doc)
                if bad2 or v2 is None or base_vol is None or \
                        abs(v2 - base_vol) > max(1e-6, base_vol * 1e-9):
                    rec.update(status="FAIL", note="restore mismatch/error")
            except Exception as e:
                rec.update(status="FAIL", note="exception: " + str(e)[:140])
                doc.recompute(None, True, True)
            results.append(rec)
        n_fail = sum(1 for r in results if r["status"] == "FAIL")
        ok = bool(results) and n_fail == 0
        from . import version as _ver
        from .ledger import sha256_file as _sha256
        rep = dict(_ver.stamp())
        rep.update({"ok": ok, "mode": "sketch-constraints", "probe_version": 5,
                    "legacy_mode": True,
                    "fcstd_sha256": _sha256(proj.fcstd_path),
                    "cells_expected": len(results),
                    "probed": len(results),
                    "failed": n_fail, "details": results,
                    "note": "no params sheet: perturbed %.0f%% of %d sketch "
                            "dimensions (legacy mode: strict packaging "
                            "requires a params sheet)"
                            % (delta * 100, len(results))})
        return rep
    finally:
        App.closeDocument(doc.Name)


def _dim_constraints(doc, limit):
    cands = []
    for o in doc.Objects:
        if o.TypeId != "Sketcher::SketchObject":
            continue
        for i, c in enumerate(o.Constraints):
            if c.Type in ("Radius", "Diameter", "Distance", "DistanceX",
                          "DistanceY", "DistanceXYZ") and c.Value and abs(c.Value) > 1e-9:
                cands.append((o, i, c))
    seen, picked = set(), []
    for o, i, c in cands:
        if o.Name in seen:
            continue
        seen.add(o.Name)
        picked.append((o, i, c))
        if len(picked) >= limit:
            return picked
    for c_ in cands:
        if len(picked) >= limit:
            break
        if c_ not in picked:
            picked.append(c_)
    return picked
