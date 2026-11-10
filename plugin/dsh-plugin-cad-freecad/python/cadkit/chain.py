"""Pure decisions for the CAD production chain.

No FreeCAD import. Host CPython (fleet_runner, registry_sync, pytest) and
freecadcmd both import this module. Geometry stays in fingerprint/validate;
this file only decides what the chain is allowed to do with evidence.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import sys
import tempfile


class RegistryShapeError(ValueError):
    """fingerprints.json is not a map of project id -> fingerprint."""


class OwnershipUnknown(RuntimeError):
    """Could not prove who owns a delivery folder. Destructive cleanup is forbidden."""


def normalize_registry(doc):
    """Return ``{project_id: fingerprint}``.

    Accepts the engine's flat map, the driver's envelope
    ``{"updated": ..., "fingerprints": {...}}``, and the baseline shape
    ``{"entries": {...}}``. Any other shape, or an entry without ``volume``,
    raises RegistryShapeError. Entries are never dropped silently.
    """
    if not isinstance(doc, dict):
        raise RegistryShapeError(
            "registry root is %s, want a JSON object" % type(doc).__name__)
    if isinstance(doc.get("fingerprints"), dict):
        body = doc["fingerprints"]
    elif isinstance(doc.get("entries"), dict):
        body = doc["entries"]
    else:
        bad = [k for k, v in doc.items() if not isinstance(v, dict)]
        if bad:
            raise RegistryShapeError(
                "registry shape is not {pid: fingerprint} and has no "
                "fingerprints/entries envelope; non-object keys: %s"
                % ",".join(str(k) for k in bad[:8]))
        body = doc
    out = {}
    missing = []
    for pid, fp in body.items():
        if not isinstance(fp, dict) or "volume" not in fp:
            missing.append(str(pid))
            continue
        out[str(pid)] = fp
    if missing:
        raise RegistryShapeError(
            "fingerprint entries missing volume (not dropped): %s"
            % ",".join(missing[:12]))
    return out


def atomic_merge_registry(path, project_id, fingerprint):
    """Insert one full fingerprint into the on-disk registry.

    The previous file is loaded and normalised first. The write replaces
    only this project id. It never rewrites the file from a smaller set
    (the ledger-delivered subset that used to wipe the registry).
    """
    if not isinstance(fingerprint, dict) or "volume" not in fingerprint:
        raise RegistryShapeError(
            "refusing to store a fingerprint without volume for %s" % project_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            current = normalize_registry(json.load(f))
    else:
        current = {}
    current[str(project_id)] = fingerprint
    fd, tmp = tempfile.mkstemp(prefix=".fp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(current)


def assemble_candidates(*sources):
    """Union fingerprint maps. Same id with a different volume is a conflict.

    Sources are applied in order; the first geometry for an id is the one
    dedupe compares against. A later disagreeing copy is not silently
    preferred and is not silently dropped: the id is listed in conflicts
    and the caller must fail closed.
    """
    out = {}
    conflicts = []
    for source in sources:
        if not source:
            continue
        for pid, fp in source.items():
            if not isinstance(fp, dict):
                conflicts.append(str(pid))
                continue
            prev = out.get(pid)
            if prev is None:
                out[pid] = fp
            elif metric_drift(prev, fp):
                # Beyond the r_metric tolerance this is a real disagreement.
                # Float noise and a same-revision rewrite are not conflicts;
                # a new revision must retire the old ledger row instead of
                # leaving both active forever.
                conflicts.append(str(pid))
    return out, conflicts


def remove_tree(path):
    """Delete a directory. Failures propagate; nothing is ignored.

    Lost-CAS cleanup used ``shutil.rmtree(..., ignore_errors=True)``, which
    reported a restore after the delete had failed.
    """
    import shutil
    shutil.rmtree(path)


def rename_noreplace(src, dst):
    """Rename ``src`` to ``dst`` only if ``dst`` does not exist.

    POSIX ``rename`` replaces an empty directory, so a first publish can
    clobber a slot created between the exists-check and the rename.
    Linux uses ``renameat2(RENAME_NOREPLACE)``. macOS uses
    ``renamex_np(RENAME_EXCL)``. Anywhere else this raises and the
    caller must not fall back to plain rename.
    """
    src_b = os.fsencode(os.fspath(src))
    dst_b = os.fsencode(os.fspath(dst))
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p,
                              ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        at_fdcwd = -100
        rename_noreplace_flag = 1
        rc = renameat2(at_fdcwd, src_b, at_fdcwd, dst_b, rename_noreplace_flag)
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
        raise OSError(err, os.strerror(err) if err else "renameat2 failed", dst)
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        rename_excl = 0x4
        rc = fn(src_b, dst_b, rename_excl)
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
        raise OSError(err, os.strerror(err) if err else "renamex_np failed", dst)
    raise OSError(
        "no atomic no-replace rename on %s; refusing to publish over "
        "a path that plain rename might replace" % sys.platform)


def merge_probe_windows(dest, incoming):
    """Merge one probe window into the accumulated map. FAIL sticks (N44).

    A later non-FAIL for a cell that already FAILed is ignored. A later
    FAIL replaces a PASS. Returns how many non-FAIL records were suppressed.
    """
    suppressed = 0
    if not isinstance(incoming, dict):
        return suppressed
    for cell, rec in incoming.items():
        prev = dest.get(cell)
        if isinstance(prev, dict) and prev.get("status") == "FAIL":
            if not (isinstance(rec, dict) and rec.get("status") == "FAIL"):
                suppressed += 1
                continue
        dest[cell] = rec
    return suppressed


def keep_cell_record(prev, rec):
    """Record to store for one cell inside a window (N45).

    A FAIL already stored for this cell is kept when a later DONE/PASS
    arrives. The caller must not overwrite it.
    """
    if isinstance(prev, dict) and prev.get("status") == "FAIL":
        if not (isinstance(rec, dict) and rec.get("status") == "FAIL"):
            return prev
    return rec


def ownership_after_read(*, read_error, on_disk, our_sha, winner_sha):
    """'unknown' | 'keep' | 'replace' after a lost publish CAS.

    ``read_error`` set means the folder or the ledger could not be read.
    That is unknown, not "no newer delivery". ``keep`` means a different
    delivered revision's bytes are on disk and must not be removed.
    ``replace`` means this attempt may restore its own trash or remove
    an unledgered first publication.
    """
    if read_error:
        return "unknown"
    if (on_disk and our_sha and on_disk != our_sha
            and winner_sha and winner_sha == on_disk):
        return "keep"
    return "replace"


def decide_folder_ownership(*, exists, sha_or_error, our_sha, query_winner):
    """Turn a disk read plus a ledger lookup into keep/replace/unknown.

    ``sha_or_error`` is the hex digest, or the exception raised while
    hashing. ``query_winner`` returns the delivered native sha or raises.
    Any failure becomes 'unknown'. Callers must not delete on 'unknown'.
    """
    if isinstance(sha_or_error, Exception):
        return ownership_after_read(
            read_error=str(sha_or_error), on_disk=None,
            our_sha=our_sha, winner_sha=None)
    on_disk = sha_or_error if exists else None
    winner = None
    if on_disk and our_sha and on_disk != our_sha:
        try:
            winner = query_winner()
        except Exception as exc:
            return ownership_after_read(
                read_error=str(exc), on_disk=on_disk,
                our_sha=our_sha, winner_sha=None)
    return ownership_after_read(
        read_error=None, on_disk=on_disk, our_sha=our_sha, winner_sha=winner)


def apply_lost_cas(decision, *, final, trash, rmtree, rename, fsync_dir=None):
    """Apply a lost-CAS cleanup. ``unknown`` and ``keep`` do not touch ``final``.

    Returns left-intact, kept-newer, restored, or removed-first.
    """
    if decision == "unknown":
        return "left-intact"
    if decision == "keep":
        return "kept-newer"
    if trash:
        rmtree(final)
        rename(trash, final)
        if fsync_dir is not None:
            fsync_dir(os.path.dirname(final))
        return "restored"
    rmtree(final)
    return "removed-first"


def sql_is_unscoped_mutation(sql):
    """Typo guard only: True when a DELETE or UPDATE has no WHERE word.

    This does not make the ledger safe. ``WHERE 1``, a WHERE hidden in a
    comment, and a CTE all pass. Real enforcement is the BEFORE DELETE /
    BEFORE UPDATE triggers installed by ``open_ledger``.
    """
    text = " ".join((sql or "").split()).strip().rstrip(";")
    if not re.match(r"(?i)^(DELETE|UPDATE)\b", text):
        return False
    return re.search(r"(?i)\bWHERE\b", text) is None


def assert_scoped_sql(sql):
    """Refuse a DELETE/UPDATE that forgot WHERE. Not a security boundary.

    See ``sql_is_unscoped_mutation``. Callers that need a guarantee use
    the schema triggers, which abort even for raw sqlite3.
    """
    if sql_is_unscoped_mutation(sql):
        raise ValueError(
            "unscoped DELETE/UPDATE refused (typo guard only): %s"
            % " ".join(sql.split())[:160])


TRANSIENT_CLASSES = {"timeout", "nojson", "rc", "spawn", "oserror"}


def job_transition(old_state, new_state, *, error_class=None,
                   supersede_fail=False, attempt=0, max_attempts=3):
    """Return ('write', state) or ('keep', reason).

    FAIL, WAIVED and INFEASIBLE are append-only for this input hash
    (this revision). A later OK/PASS/DONE does not erase them. Clearing
    takes a new revision (a different input hash, so a new row) or
    ``record_waiver`` with a reason. ``supersede_fail`` is ignored.

    A transient error (timeout, no JSON, spawn failure) is recorded as
    RETRY until the attempt budget is spent, and only then as FAIL.
    RETRY may still become OK. FAIL may not.
    """
    del supersede_fail  # accepted so old callers fail closed, not open
    new = (new_state or "").upper()
    old = (old_state or "").upper()
    if not new:
        return ("keep", "empty job state")
    # Sticky rows are not rewritten here. The only writers that may leave
    # FAIL are record_waiver and the inherited-FAIL re-review path, and
    # both of those are checked again by the job UPDATE trigger.
    if old in ("FAIL", "WAIVED", "INFEASIBLE"):
        return ("keep",
                "N45: %s sticks for this revision; new revision or waiver"
                % old)
    # Missing review receipts are not a design failure and are not sticky.
    if new == "PENDING_REVIEW":
        return ("write", "PENDING_REVIEW")
    if old == "PENDING_REVIEW" and new in ("OK", "PASS", "DONE", "RETRY"):
        return ("write", new)
    kind = (error_class or "").split(":")[0].lower()
    if new == "FAIL" and kind in TRANSIENT_CLASSES:
        used = int(attempt or 0) + 1
        if used < int(max_attempts):
            return ("write", "RETRY")
        return ("write", "FAIL")
    return ("write", new)


def verdict_append_only(records, required):
    """PASS only if every required cell has a PASS and no record is FAIL.

    Records are append-only (one per window observation). A later PASS
    does not remove an earlier FAIL. Missing cells are not PASS.
    Returns (ok, reason).
    """
    required = list(required or [])
    if not required:
        return False, "no required windows"
    fails = [r for r in (records or [])
             if isinstance(r, dict) and r.get("status") == "FAIL"]
    if fails:
        cells = []
        for r in fails:
            name = r.get("cell") or "?"
            if name not in cells:
                cells.append(name)
        return False, "FAIL in %d record(s): %s" % (
            len(fails), ",".join(cells[:6]))
    passed = set()
    for r in records or []:
        if (isinstance(r, dict) and r.get("status") == "PASS"
                and r.get("cell")):
            passed.add(r["cell"])
    missing = [c for c in required if c not in passed]
    if missing:
        return False, "missing PASS for %s" % ",".join(str(c) for c in missing[:6])
    return True, ""


def cleanup_decision(*, native_status, native_sha, our_sha, winner_sha,
                     query_error, final_exists):
    """'unknown' | 'keep' | 'replace' for a lost publish CAS (N43).

    ``native_status`` is 'absent' (definite ENOENT), 'ready' (hashed) or
    'io-error'. Only a definite absence of the whole delivery folder
    means there is no newer revision to protect. Any other I/O error,
    a folder with no readable native, or a ledger read error is unknown
    and the caller must not delete.
    """
    if native_status == "io-error" or query_error:
        return "unknown"
    if native_status == "absent" and not final_exists:
        return "replace"
    if native_status == "absent" and final_exists:
        return "unknown"
    if (native_sha and our_sha and native_sha != our_sha
            and winner_sha and winner_sha == native_sha):
        return "keep"
    if native_status == "ready":
        return "replace"
    return "unknown"


def errno_is_absent(exc):
    """True only for a definite file-not-found. Every other OSError is not."""
    if isinstance(exc, FileNotFoundError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOENT


def export_precondition(report, fcstd_sha):
    """None when export may run, else a fail-closed error string.

    Validate must have passed, and the receipt must be bound to this
    FCStd's sha256. A report that merely exists, or a PASS for a
    different file, does not authorise export.
    """
    if not isinstance(report, dict):
        return ("export refused: no validate report for this FCStd "
                "(run validate and require PASS)")
    if report.get("ok") is not True:
        return "export refused: validate has not PASS on this revision"
    bound = report.get("fcstd_sha256")
    if not bound or not fcstd_sha or bound != fcstd_sha:
        return ("export refused: validate receipt is not bound to this "
                "FCStd sha256")
    return None


def verify_delivery_hashes(checklist, folder, read_sha):
    """Re-hash every file named by the checklist. Missing hashes fail closed.

    Returns (ok, reason). A checklist that only says PASS, with no
    per-file digest, is a failure: swapping the FCStd would still look
    accepted.
    """
    if not isinstance(checklist, dict):
        return False, "checklist missing"
    files = checklist.get("files")
    if not isinstance(files, dict) or not files:
        return False, "checklist has no file hashes"
    for rel, digest in files.items():
        if (not isinstance(rel, str) or not rel
                or rel.startswith("/") or ".." in rel.split("/")):
            return False, "bad checklist path %s" % rel
        path = os.path.join(folder, rel)
        try:
            got = read_sha(path)
        except OSError as exc:
            return False, "unreadable %s (%s)" % (rel, exc)
        if got != digest:
            return False, "hash mismatch %s" % rel
    return True, ""


def evidence_is_accepted(ev, disk_hashes):
    """True only for a fleet_runner evidence file whose hashes still match.

    A ledger job row of OK is not evidence and must not be passed here.
    ``disk_hashes`` is a fresh read of the same keys stored on the file.
    """
    if not isinstance(ev, dict) or ev.get("accepted") is not True:
        return False
    if ev.get("source") != "fleet_runner":
        return False
    recorded = ev.get("hashes") or {}
    if not recorded:
        return False
    for key, want in recorded.items():
        if disk_hashes.get(key) != want:
            return False
    return True


def metric_drift(old_fp, new_fp, rel_tol=1e-12):
    """True when two fingerprints of the same bound source disagree.

    Volume tolerance follows the audit's r_metric floor (1e-12 relative).
    Step-count changes are drift even when volume matches. Missing
    fingerprints are not drift; the caller treats them as "not yet known".
    """
    if not isinstance(old_fp, dict) or not isinstance(new_fp, dict):
        return False
    if old_fp.get("steps") != new_fp.get("steps"):
        return True
    try:
        old_v = float(old_fp.get("volume"))
        new_v = float(new_fp.get("volume"))
    except (TypeError, ValueError):
        return False
    scale = max(abs(old_v), 1e-9)
    return abs(old_v - new_v) / scale > rel_tol
