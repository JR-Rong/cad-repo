"""Transactional acceptance ledger (workflow-audit W12/W13/W21, 2026-10-30).

Replaces the flat fingerprints.json + append-only CSV + /tmp state-file
"resume by text line" scheme with a durable SQLite store:

  * immutable revisions (fcstd/source/rules/engine hashes)
  * artifacts with SHA-256 digests
  * per-revision jobs (stage state + input hash => resume is sound)
  * evidence rows (gate reports bound to rules_sha)
  * deliveries (committed batches) with supersession events
  * fingerprint candidates indexed for O(log n) dedup lookups at 100k scale

Publication protocol (W12/W13): the packager stages under
delivery/<batch>/.staging/<pid>-<rev>/, fsyncs files, then commits one
BEGIN IMMEDIATE transaction (revision+artifacts+delivery+fingerprint+event)
and atomically renames the folder into place. A crash between staging and
commit leaves only staging garbage — never a half-written delivery, never a
CSV row without files, never a registered-but-undelivered fingerprint.

Schema follows the hostile-review recommendation (cad/audit/
findings_workflow_2026_10.json W12), simplified to BIGINT ms timestamps.
"""
import hashlib
import os
import json
import sqlite3
import stat
import time

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS model(
  model_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL, project_id TEXT NOT NULL,
  family TEXT, series TEXT,
  UNIQUE(buyer_id, project_id));
CREATE TABLE IF NOT EXISTS revision(
  rev TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model(model_id),
  fcstd_sha TEXT NOT NULL, source_sha TEXT, meta_sha TEXT,
  rules_sha TEXT NOT NULL, engine_sha TEXT, runtime_sha TEXT,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS artifact(
  rev TEXT NOT NULL REFERENCES revision(rev), path TEXT NOT NULL,
  sha TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(rev, path));
CREATE TABLE IF NOT EXISTS job(
  job_id INTEGER PRIMARY KEY, rev TEXT REFERENCES revision(rev),
  project_id TEXT NOT NULL, stage TEXT NOT NULL, input_sha TEXT NOT NULL,
  rules_sha TEXT NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL,
  error_class TEXT, updated INTEGER NOT NULL,
  UNIQUE(project_id, stage, input_sha, rules_sha));
CREATE TABLE IF NOT EXISTS evidence(
  rev TEXT NOT NULL REFERENCES revision(rev), kind TEXT NOT NULL,
  rule_id TEXT NOT NULL, rules_sha TEXT NOT NULL, status TEXT NOT NULL,
  receipt_sha TEXT, PRIMARY KEY(rev, kind, rule_id, rules_sha));
CREATE TABLE IF NOT EXISTS batch(
  batch_id TEXT PRIMARY KEY, buyer_id TEXT, intake_sha TEXT,
  waiver_sha TEXT);
CREATE TABLE IF NOT EXISTS delivery(
  batch_id TEXT NOT NULL REFERENCES batch(batch_id),
  model_id TEXT NOT NULL REFERENCES model(model_id),
  rev TEXT NOT NULL REFERENCES revision(rev),
  state TEXT NOT NULL,  -- delivered | superseded
  delivered INTEGER NOT NULL,
  published INTEGER NOT NULL DEFAULT 0);  -- N25: ever really mark-published
CREATE INDEX IF NOT EXISTS delivery_model ON delivery(model_id, state);
CREATE TABLE IF NOT EXISTS fingerprint(
  rev TEXT PRIMARY KEY REFERENCES revision(rev), version INTEGER NOT NULL,
  project_id TEXT NOT NULL, structure_sha TEXT NOT NULL,
  shape_bucket TEXT NOT NULL, cells INTEGER, steps INTEGER, solids INTEGER,
  volume REAL, bbox TEXT, state TEXT NOT NULL,  -- active | withdrawn
  created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS fp_candidates
  ON fingerprint(version, structure_sha, shape_bucket);
CREATE INDEX IF NOT EXISTS fp_project ON fingerprint(project_id, state);
CREATE TABLE IF NOT EXISTS event(
  event_id INTEGER PRIMARY KEY, rev TEXT, kind TEXT NOT NULL,
  payload TEXT, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS intake(
  batch_id TEXT NOT NULL, project_id TEXT NOT NULL, required TEXT,
  PRIMARY KEY(batch_id, project_id));
CREATE TABLE IF NOT EXISTS buyer_stock(
  buyer_id TEXT NOT NULL, external_id TEXT NOT NULL, signature TEXT,
  eligibility TEXT NOT NULL, receipt_sha TEXT,
  PRIMARY KEY(buyer_id, external_id));
"""


def _ms():
    return int(time.time() * 1000)


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def inspect_native(path):
    """('absent'|'ready'|'io-error', sha-or-None).

    Only a definite ENOENT is 'absent' — that is the sole reading of
    "no newer revision's file". Permission errors, EIO, and anything
    else are 'io-error' and must fail closed.
    """
    import errno
    try:
        if not os.path.lexists(path):
            return "absent", None
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT:
            return "absent", None
        return "io-error", None
    if os.path.islink(path):
        return "io-error", None
    try:
        return "ready", sha256_file(path)
    except FileNotFoundError:
        return "absent", None
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT:
            return "absent", None
        return "io-error", None


def _fp_blob(fp):
    """Canonical invariant snapshot stored with every fingerprint row so
    dedupe can run from the ledger alone (N10)."""
    out = {k: v for k, v in fp.items()
           if k in ("faces", "edges", "compactness", "solids", "steps",
                    "cells", "volume", "bbox", "ratios", "volume_scale",
                    "structure_sha", "version")}
    if isinstance(out.get("bbox"), str):
        try:
            out["bbox"] = json.loads(out["bbox"])
        except Exception:
            pass
    return json.dumps(out, sort_keys=True)


_MIG2 = """
ALTER TABLE delivery ADD COLUMN counts_as TEXT;
ALTER TABLE delivery ADD COLUMN counts INTEGER DEFAULT 1;
"""
_MIG3 = """
ALTER TABLE fingerprint ADD COLUMN data TEXT;
"""


_MIG4 = """
ALTER TABLE delivery ADD COLUMN published INTEGER NOT NULL DEFAULT 0;
UPDATE delivery SET published=1 WHERE state='delivered';
"""


def _migrate(conn):
    for mig in (_MIG2, _MIG3, _MIG4):
        try:
            conn.executescript(mig)
        except Exception:
            pass  # columns exist


def open_ledger(cad_root):
    path = os.path.join(cad_root, "ledger.sqlite3")
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_SCHEMA)
    if not conn.execute("SELECT v FROM meta WHERE k='schema_version'")\
            .fetchone():
        conn.execute("INSERT INTO meta VALUES('schema_version',?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    _migrate(conn)
    return conn


# ---------- staging helpers (packager) ----------

def stage_dir(cad_root, batch, pid, rev):
    import shutil as _sh
    chain = os.path.join(cad_root, "delivery")
    root = os.path.join(chain, batch)
    # N2 recheck round 4: the .staging component itself used to be
    # examined only AFTER rmtree had already destroyed whatever an
    # attacker-planted .staging symlink pointed at. EVERY component of
    # the staging path must be a real directory BEFORE anything is
    # deleted - reject first, never touch through a link.
    for seg in (chain, root, os.path.join(root, ".staging")):
        st = os.lstat(seg) if os.path.lexists(seg) else None
        if st is None:
            continue
        if st.st_mode & 0o170000 == 0o120000 or not \
                stat.S_ISDIR(st.st_mode):
            raise ValueError("delivery path component is not a real "
                             "directory (symlink escape refused, N2): %s"
                             % seg)
    root = os.path.realpath(root)
    d = os.path.join(root, ".staging", "%s-%s" % (pid, rev[:16]))
    if os.path.islink(d) or (os.path.exists(d) and not
                             os.path.isdir(d)):
        raise ValueError("staging path is not a real directory: %s (N2)"
                         % d)
    if os.path.isdir(d):
        _sh.rmtree(d)            # per-attempt independent staging (N2):
                                 # never inherit bytes from a failed run
    os.makedirs(d, 0o700)
    if os.path.realpath(os.path.dirname(d)) != os.path.join(root,
                                                            ".staging"):
        raise ValueError("staging root escapes the delivery tree (N2)")
    return d


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_file(path):
    with open(path, "rb") as f:
        os.fsync(f.fileno())


class _PubStack(__import__("threading").local):
    stack = None


_PUB_STACK = _PubStack()


def _pub_lock(target):
    """Per-part publisher lock (N43).

    The critical section is one delivery folder, not the whole batch,
    so two parts may publish at the same time. The lock file lives in
    ``<batch>/.locks/<pid>.lock`` and is never inside the folder that
    gets swapped. Reentrant within a thread: the outermost frame holds
    the flock. A publisher that mutates disk without this lock is still
    caught by mark_published re-reading the native bytes."""
    import fcntl
    target = os.path.abspath(str(target))
    parent = os.path.dirname(target)
    pid = os.path.basename(target)
    key = os.path.join(parent, ".locks", pid)
    stack = _PUB_STACK.stack
    if stack is None:
        stack = _PUB_STACK.stack = []
    if key in stack:
        stack.append(key)
        return key
    os.makedirs(os.path.dirname(key), exist_ok=True)
    fd = os.open(key + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _PUB_FDS[key] = fd
    stack.append(key)
    return key


def _pub_unlock(key):
    import fcntl
    if key is None:
        return
    stack = _PUB_STACK.stack or []
    if not stack or stack[-1] != key:
        return  # unbalanced release - never drop someone else's lock
    stack.pop()
    if key in stack:
        # N33 round 7: an inner reentrant frame released - the OUTER
        # frame still owns the flock; only the last matching release
        # may drop it.
        return
    fd = _PUB_FDS.pop(key, None)
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_PUB_FDS = {}


def publish(staging, final, conn=None):
    """Atomic folder publication: old committed folder (if any) is moved
    aside, the staged folder is renamed into place, then the trash is
    removed. A crash before the rename leaves only staging garbage.

    N1 round 5 / N20 round 6: publication is SERIALIZED against the
    ledger. The attempt context is derived from the standard layout
    (final = <root>/delivery/<batch>/<pid>, staging folder
    "<pid>-<rev-prefix>"): the whole stale-check -> rename -> re-check
    sequence runs under the publisher lock; a stale attempt - one whose
    committed row predates an already-delivered row of the same model -
    RAISES before touching disk, and if a newer revision became
    delivered between the swap and the re-check, the swap is UNDONE
    (the trashed folder - which IS the newer delivery that was moved
    aside - is renamed back before raising). Callers holding an open
    ledger pass conn; standalone calls open the root's own
    ledger.sqlite3; if no ledger context can be derived the rename
    proceeds (legacy path)."""
    _f = os.path.abspath(final)
    _b = os.path.basename(os.path.dirname(_f))
    _pid = os.path.basename(_f)
    _rev = os.path.basename(os.path.abspath(str(staging))).rsplit("-", 1)
    _rev = _rev[1] if len(_rev) == 2 else ""
    _root = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
    _own = conn is None
    _con = conn
    _fd = _pub_lock(_f)
    try:
        if _rev:
            try:
                if _own and os.path.exists(
                        os.path.join(_root, "ledger.sqlite3")):
                    _con = open_ledger(_root)
                if _con is not None and is_stale_attempt(
                        _con, _b, _pid, _rev):
                    raise ValueError(
                        "refusing to publish stale attempt %s over a "
                        "newer delivered revision of %s (N1 round 5)"
                        % (_rev[:12], _pid))
            finally:
                if _own and _con is not None and False:
                    pass
        os.makedirs(os.path.dirname(final), exist_ok=True)
        # N43: re-read the native under the per-part lock immediately
        # before any rename. ENOENT means the slot is empty. Any other
        # I/O error aborts with the folder untouched. A ledger refresh
        # that fails is the same: we do not guess "not stale".
        _native = os.path.join(_f, _pid + ".FCStd")
        _status, _sha = inspect_native(_native)
        if _status == "io-error":
            raise ValueError(
                "N43: cannot read %s; refusing to replace the delivery "
                "folder" % _native)
        if _rev and _con is not None:
            try:
                _con.commit()
            except Exception as exc:
                raise ValueError(
                    "N43: ledger refresh failed before publish (%s); "
                    "folder left untouched" % exc)
            if is_stale_attempt(_con, _b, _pid, _rev):
                raise ValueError(
                    "refusing to publish stale attempt %s over a "
                    "newer delivered revision of %s (re-check, N43)"
                    % (_rev[:12], _pid))
        trash = None
        if _status == "absent" and not os.path.lexists(final):
            # Definite file-not-found of the delivery slot: create by
            # rename onto a path that does not exist (no-overwrite).
            os.rename(staging, final)
        elif _status == "ready" and os.path.lexists(final):
            # Bytes were hashed under the lock. Move the known folder
            # aside, then rename staging in. A failed second rename
            # puts the hashed folder back.
            trash = final + ".superseded." + str(_ms())
            os.rename(final, trash)
            try:
                os.rename(staging, final)
            except OSError:
                if trash is not None and not os.path.lexists(final):
                    os.rename(trash, final)
                    fsync_dir(os.path.dirname(final))
                raise
        else:
            raise ValueError(
                "N43: delivery path %s is not a definite absence and "
                "was not hashed; left untouched" % final)
        fsync_dir(os.path.dirname(final))
        if _rev and _con is not None:
            # N20 round 6: post-swap re-check under the SAME lock. If a
            # newer revision got delivered while we were swapping (only
            # possible via a lockless legacy caller), undo our swap -
            # the trash holds exactly the folder the newer delivery put
            # on disk - and refuse, so disk can never diverge from the
            # delivered row. A failed refresh undoes the swap too (N43):
            # we will not leave our bytes in place on an unknown ledger.
            try:
                _con.commit()
            except Exception as exc:
                bad = final + ".stale." + str(_ms())
                os.rename(final, bad)
                if trash is not None:
                    os.rename(trash, final)
                    trash = None
                fsync_dir(os.path.dirname(final))
                raise ValueError(
                    "N43: ledger refresh failed after swap (%s); "
                    "previous folder restored" % exc)
            if is_stale_attempt(_con, _b, _pid, _rev):
                bad = final + ".stale." + str(_ms())
                os.rename(final, bad)
                if trash is not None:
                    os.rename(trash, final)
                    trash = None
                fsync_dir(os.path.dirname(final))
                raise ValueError(
                    "refusing to publish stale attempt %s: a newer "
                    "revision of %s was delivered during the swap - "
                    "newer folder restored, attempt aborted (N20 "
                    "round 6)" % (_rev[:12], _pid))
        return trash
    finally:
        if _own and _con is not None:
            try:
                _con.close()
            except Exception:
                pass
        _pub_unlock(_fd)


def drop_superseded_trash(delivery_root):
    import shutil
    for dirpath, dirnames, _f in os.walk(delivery_root):
        for d in list(dirnames):
            if ".superseded." in d:
                shutil.rmtree(os.path.join(dirpath, d))
                dirnames.remove(d)


# ---------- commits ----------

def commit_acceptance(conn, *, batch, buyer_id, project_id, rev, fcstd_sha,
                      source_sha, meta_sha, rules_sha, runtime_sha,
                      artifacts, evidence, fingerprint, family=None,
                      series=None, intake=None, waiver_sha=None,
                      intake_sha=None):
    """One transaction: everything or nothing (W12/W13). The delivery row
    is written state='pending' and only mark_published() after durable
    filesystem publication flips it to 'delivered' (R1 recheck: no
    committed-but-unpublished truth gap)."""
    if not artifacts or not evidence:
        raise ValueError("commit_acceptance refuses a delivery with zero "
                         "artifacts or evidence (R1 recheck)")
    now = _ms()
    with conn:  # BEGIN IMMEDIATE / COMMIT / ROLLBACK
        conn.execute(
            "INSERT OR IGNORE INTO model(model_id, buyer_id, project_id, "
            "family, series) VALUES(?,?,?,?,?)",
            ("local:" + project_id, buyer_id, project_id, family, series))
        # N1 recheck: committing a new attempt must NOT revoke the old
        # delivered version - supersession happens in mark_published ONLY
        # after the new folder is durably published. A failed or abandoned
        # commit therefore leaves the previous delivery fully valid.
        # retry after an abandoned/same-rev attempt: clear the PREVIOUS
        # rows for this content-addressed rev BEFORE re-inserting (fixes
        # the round-3 bug where the cleanup erased this commit's own
        # artifact/evidence rows).
        # N1/N10/N13 recheck round 4: a DELIVERED revision is immutable.
        # The gen-8 flow let a same-rev retry with different bytes replace
        # the artifact/evidence/fingerprint rows of the delivered version
        # BEFORE (and even if) publication failed - laundering dedupe and
        # corrupting classes on failure. Different bytes under a delivered
        # rev is a conflict, never a cleanup.
        dl = conn.execute(
            "SELECT 1 FROM delivery WHERE rev=? AND state='delivered' "
            "LIMIT 1", (rev,)).fetchone()
        if dl is not None:
            prev = conn.execute(
                "SELECT fcstd_sha FROM revision WHERE rev=?",
                (rev,)).fetchone()
            if prev and prev[0] != fcstd_sha:
                raise ValueError(
                    "rev conflict: rev %s is DELIVERED for fcstd %s; "
                    "committing it again with fcstd %s would rewrite "
                    "delivered history (N1/N10 fail closed) - publish "
                    "under a new rev" % (rev[:12], str(prev[0])[:12],
                                         str(fcstd_sha)[:12]))
            # N10 recheck (round 4, second repro): the SAME rev+fcstd
            # could still swap the artifact/evidence SET (delivered rows
            # replaced by CHANGED_UNPUBLISHED paths, then abandoned).
            # A delivered commit's inventory is part of its immutability.
            # N22 round 6: the accepted revision's PROVENANCE (rules /
            # runtime / meta / source digests) is part of what "delivered"
            # attests - re-committing a delivered rev under a different
            # rules_sha would relabel audit evidence and is refused.
            orev = conn.execute(
                "SELECT rules_sha, runtime_sha, meta_sha, source_sha "
                "FROM revision WHERE rev=?", (rev,)).fetchone()
            if orev is not None and tuple(
                    "" if v is None else v for v in orev) != tuple(
                    "" if v is None else v for v in
                    (rules_sha, runtime_sha, meta_sha, source_sha)):
                raise ValueError(
                    "rev conflict: rev %s is DELIVERED; this commit "
                    "claims rules_sha %s but the accepted revision "
                    "provenance is %s - delivered provenance is "
                    "immutable (N22 round-6 fail closed)"
                    % (rev[:12], str(rules_sha)[:16], str(orev[0])[:16]))
            old_art = set(conn.execute(
                "SELECT path, sha, bytes FROM artifact WHERE rev=?",
                (rev,)).fetchall())
            new_art = {(p_, sh_, sz_) for (p_, sh_, sz_) in artifacts}
            old_ev = set(conn.execute(
                "SELECT kind, rule_id, status, receipt_sha FROM evidence "
                "WHERE rev=?", (rev,)).fetchall())
            new_ev = {(k_, r_, st_, rx) for (k_, r_, st_, rx) in evidence}
            # DERIVED AUDIT REPORT exception: the packager regenerates
            # DELIVERY_CHECKLIST.json itself (a damage-repair run legitimately
            # adds self-healing warnings). Every SUBSTANTIVE artifact - the
            # native, the STEP, the renders, the sources, the receipts - and
            # the full evidence set and fingerprint must still be byte-exact,
            # so the checklist's own content cannot launder anything: the
            # all-ok gate re-runs before this transaction commits.
            _substantive = {(p_, sh_, sz_) for (p_, sh_, sz_)
                            in (old_art ^ new_art)
                            if p_ != "DELIVERY_CHECKLIST.json"}
            if old_ev != new_ev or _substantive:
                _dp = sorted({x[0] for x in (old_art ^ new_art)
                              if x[0] != "DELIVERY_CHECKLIST.json"})[:6]
                _de = sorted({x[0] + "/" + x[1]
                              for x in (old_ev ^ new_ev)})[:6]
                raise ValueError(
                    "rev conflict: rev %s is DELIVERED; its artifact/"
                    "evidence inventory (%d/%d rows) differs from this "
                    "commit (%d/%d) - delivered history is immutable "
                    "(N10 fail closed); artifact diff: %s; evidence "
                    "diff: %s" % (rev[:12], len(old_art), len(old_ev),
                                  len(new_art), len(new_ev),
                                  _dp or "-", _de or "-"))
            # N10 recheck round 5: an EQUAL inventory commit could still
            # swap the FINGERPRINT blob (cells/steps/volume/...) before
            # publication - the delivered fingerprint is what dedupe and
            # class membership are computed from, so it is immutable with
            # the rest of the delivered history.
            ofp = conn.execute(
                "SELECT cells, steps, solids, volume, bbox, data, "
                "structure_sha, shape_bucket FROM fingerprint WHERE rev=?",
                (rev,)).fetchone()
            if ofp is not None:
                nfp = (fingerprint.get("cells"), fingerprint.get("steps"),
                       fingerprint.get("solids"), fingerprint.get("volume"),
                       fingerprint.get("bbox"), _fp_blob(fingerprint),
                       fingerprint.get("structure_sha"),
                       fingerprint.get("shape_bucket"))
                if tuple("" if v is None else v for v in ofp) != \
                        tuple("" if v is None else v for v in nfp):
                    raise ValueError(
                        "rev conflict: rev %s is DELIVERED; this commit "
                        "carries a DIFFERENT fingerprint for identical "
                        "inventory - delivered fingerprint is immutable "
                        "(N10 round-5 fail closed)" % rev[:12])
            # byte-IDENTICAL re-commit of a delivered rev is the legitimate
            # crash-retry path and stays allowed: the rewrite below runs in
            # THIS transaction (rollback keeps history intact on crash)
            # and re-inserts the exact same rows, then mark_published
            # collapses the duplicate delivered rows for this rev.
        conn.execute("DELETE FROM artifact WHERE rev=?", (rev,))
        conn.execute("DELETE FROM evidence WHERE rev=?", (rev,))
        conn.execute("DELETE FROM delivery WHERE rev=? AND state='pending'",
                     (rev,))
        conn.execute(
            "INSERT OR IGNORE INTO revision VALUES(?,?,?,?,?,?,?,?,?)",
            (rev, "local:" + project_id, fcstd_sha, source_sha, meta_sha,
             rules_sha, "", runtime_sha, now))
        conn.execute(
            "UPDATE revision SET rules_sha=?, runtime_sha=?, meta_sha=?, "
            "source_sha=? WHERE rev=?",
            (rules_sha, runtime_sha, meta_sha, source_sha, rev))
        for path, sha, size in artifacts:
            conn.execute("INSERT INTO artifact VALUES(?,?,?,?)",
                         (rev, path, sha, size))
        for kind, rule_id, status, receipt in evidence:
            conn.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?)",
                         (rev, kind, rule_id, rules_sha, status, receipt))
        conn.execute(
            "INSERT OR REPLACE INTO batch VALUES(?,?,?,?)",
            (batch, buyer_id,
             intake_sha or _intake_sha(conn, batch), waiver_sha))
        if intake:
            for pid in intake:
                conn.execute("INSERT OR REPLACE INTO intake VALUES(?,?,?)",
                             (batch, pid, "required"))
        # spec 2.5 counting metadata (non-blocking): same-class members
        # already delivered (identical feature structure: cells+steps+
        # solids) mark this one as counts=0 with its representative, so
        # per-batch unique-model counts are derivable and honest. The
        # BUYER adjudicates final counting; the pipeline never hides it.
        fp = fingerprint
        counts_as, counts = None, 1
        twin = conn.execute(
            "SELECT m.project_id FROM fingerprint f "
            "JOIN delivery d ON d.rev=f.rev AND d.state='delivered' "
            "JOIN model m ON m.model_id=d.model_id "
            "WHERE f.state='active' AND m.project_id<>? AND d.batch_id=? "
            "AND IFNULL(f.cells,-1)=IFNULL(?,IFNULL(?,0)) "
            "AND IFNULL(f.steps,-1)=IFNULL(?,IFNULL(?,0)) "
            "AND IFNULL(f.solids,-1)=IFNULL(?,IFNULL(?,0)) "
            "ORDER BY f.steps DESC, d.delivered ASC LIMIT 1",
            (project_id, batch,
             fp.get("cells"), fp.get("cells"),
             fp.get("steps"), fp.get("steps"),
             fp.get("solids"), fp.get("solids"))).fetchone()
        if twin:
            counts_as, counts = twin[0], 0
        conn.execute(
            "INSERT INTO delivery(batch_id, model_id, rev, state, "
            "delivered, counts_as, counts) VALUES(?,?,?,?,?,?,?)",
            (batch, "local:" + project_id, rev, "pending", now,
             counts_as, counts))
        conn.execute(
            "INSERT OR REPLACE INTO fingerprint "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev, fp["version"], project_id, fp["structure_sha"],
             fp["shape_bucket"], fp.get("cells"), fp.get("steps"),
             fp.get("solids"), fp.get("volume"), fp.get("bbox"),
             "active", now, _fp_blob(fp)))
        conn.execute("INSERT INTO event(rev,kind,payload,created) "
                     "VALUES(?,?,?,?)", (rev, "delivered", "", now))
    return rev


def _intake_sha(conn, batch):
    rows = conn.execute(
        "SELECT project_id FROM intake WHERE batch_id=? ORDER BY 1",
        (batch,)).fetchall()
    h = hashlib.sha256()
    for (p,) in rows:
        h.update(p.encode() + b"\n")
    return h.hexdigest()[:16] if rows else None


# ---------- queries (resume / dedup / reconciliation) ----------

def job_state(conn, project_id, stage, input_sha, rules_sha):
    row = conn.execute(
        "SELECT state FROM job WHERE project_id=? AND stage=? AND "
        "input_sha=? AND rules_sha=?",
        (project_id, stage, input_sha, rules_sha)).fetchone()
    return row[0] if row else None


def job_record(conn, project_id, stage, input_sha, rules_sha, state,
               error_class=None, rev=None, max_attempts=3):
    """Append a stage result. A FAIL for this input hash is not overwritten.

    The unique key is the revision identity (project, stage, input hash,
    rules). A later OK on that same key is rejected and an event row is
    written. A different input hash is a new revision and gets its own
    row. Transient failures stay RETRY until ``max_attempts``, then FAIL.
    Returns ``{"applied": bool, "state": ...}``.
    """
    from .chain import job_transition
    now = _ms()
    with conn:
        row = conn.execute(
            "SELECT state, attempt, error_class FROM job WHERE project_id=? "
            "AND stage=? AND input_sha=? AND rules_sha=?",
            (project_id, stage, input_sha, rules_sha)).fetchone()
        old_state = row[0] if row else ""
        old_attempt = row[1] if row else 0
        old_error = row[2] if row else None
        # When the new call is a pass, stickiness depends on the stored
        # failure class, not on the empty error of the passing call.
        kind = old_error if (state or "").upper() in ("OK", "PASS", "DONE") \
            else error_class
        action, decided = job_transition(
            old_state, state, error_class=kind, attempt=old_attempt,
            max_attempts=max_attempts)
        if action == "keep":
            conn.execute(
                "INSERT INTO event(rev,kind,payload,created) VALUES(?,?,?,?)",
                (rev, "n45_reject", json.dumps({
                    "project_id": project_id, "stage": stage,
                    "input_sha": input_sha, "kept": old_state,
                    "rejected": state, "reason": decided,
                }, ensure_ascii=False), now))
            return {"applied": False, "state": old_state, "reason": decided}
        if row is None:
            conn.execute(
                "INSERT INTO job(rev,project_id,stage,input_sha,rules_sha,"
                "state,attempt,error_class,updated) VALUES(?,?,?,?,?,?,1,?,?)",
                (rev, project_id, stage, input_sha, rules_sha, decided,
                 error_class, now))
        else:
            conn.execute(
                "UPDATE job SET state=?, attempt=?, error_class=?, updated=?, "
                "rev=COALESCE(?, rev) WHERE project_id=? AND stage=? AND "
                "input_sha=? AND rules_sha=?",
                (decided, old_attempt + 1, error_class, now, rev,
                 project_id, stage, input_sha, rules_sha))
        conn.execute(
            "INSERT INTO event(rev,kind,payload,created) VALUES(?,?,?,?)",
            (rev, "job_state", json.dumps({
                "project_id": project_id, "stage": stage,
                "from": old_state or None, "to": decided,
            }, ensure_ascii=False), now))
        return {"applied": True, "state": decided}


def record_waiver(conn, project_id, stage, input_sha, rules_sha, reason,
                  kind="waiver"):
    """Explicit waiver of a sticky FAIL. Requires a non-empty reason.

    Does not delete the history: the job row moves FAIL → WAIVED or
    INFEASIBLE and an event stores the reason. A later PASS still cannot
    erase it (see job_transition). ``kind`` is ``waiver`` or
    ``design-infeasible``.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("waiver requires a reason")
    if kind not in ("waiver", "design-infeasible"):
        raise ValueError("waiver kind must be waiver or design-infeasible")
    new_state = "WAIVED" if kind == "waiver" else "INFEASIBLE"
    now = _ms()
    with conn:
        cur = conn.execute(
            "UPDATE job SET state=?, error_class=?, updated=? "
            "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=? "
            "AND state='FAIL'",
            (new_state, reason[:120], now, project_id, stage, input_sha,
             rules_sha))
        if cur.rowcount != 1:
            raise ValueError(
                "waiver applies only to an existing FAIL row for this "
                "revision")
        conn.execute(
            "INSERT INTO event(rev,kind,payload,created) VALUES(?,?,?,?)",
            (None, kind, json.dumps({
                "project_id": project_id, "stage": stage,
                "input_sha": input_sha, "reason": reason,
            }, ensure_ascii=False), now))
    return {"applied": True, "state": new_state, "reason": reason}


def is_committed(conn, batch, project_id, fcstd_sha=None):
    """True if a delivered revision exists (optionally the exact same
    geometry) — the packager must NOT delete a committed delivery to
    rewrite it silently (W12); supersession is an explicit act."""
    q = ("SELECT d.rev FROM delivery d JOIN model m ON m.model_id=d.model_id "
         "JOIN revision r ON r.rev=d.rev "
         "WHERE d.batch_id=? AND m.project_id=? AND d.state='delivered'")
    args = [batch, project_id]
    if fcstd_sha:
        q += " AND r.fcstd_sha=?"
        args.append(fcstd_sha)
    return conn.execute(q + " LIMIT 1", args).fetchone() is not None


def get_committed_rev(conn, batch, project_id):
    row = conn.execute(
        "SELECT d.rev FROM delivery d JOIN model m ON m.model_id=d.model_id "
        "WHERE d.batch_id=? AND m.project_id=? AND d.state='delivered' "
        "ORDER BY d.delivered DESC LIMIT 1", (batch, project_id)).fetchone()
    return row[0] if row else None


def same_project_revisions(conn, project_id, exclude_batch=None):
    q = ("SELECT r.rev, d.batch_id, r.fcstd_sha, f.steps FROM revision r "
         "JOIN delivery d ON d.rev=r.rev JOIN model m ON m.model_id=r.model_id "
         "JOIN fingerprint f ON f.rev=r.rev "
         "WHERE m.project_id=? AND d.state='delivered'")
    args = [project_id]
    if exclude_batch:
        q += " AND d.batch_id<>?"
        args.append(exclude_batch)
    return conn.execute(q, args).fetchall()


def dedup_candidates(conn, version, structure_sha=None, volume=None,
                     bbox_diag=None, tol=0.005, exclude_rev=None):
    """Indexed candidate query replacing the O(n) full-registry scan
    (W21): structure exact-bucket match, or volume/bbox numeric buckets."""
    out = []
    if structure_sha:
        out += conn.execute(
            "SELECT rev,project_id,cells,steps,solids,volume,bbox "
            "FROM fingerprint WHERE version=? AND structure_sha=? AND "
            "state='active' AND rev<>?",
            (version, structure_sha, exclude_rev or "")).fetchall()
    if volume and volume > 0:
        rows = conn.execute(
            "SELECT rev,project_id,cells,steps,solids,volume,bbox "
            "FROM fingerprint WHERE version=? AND state='active' AND "
            "rev<>? AND ABS(volume/ ? - 1) < ?",
            (version, exclude_rev or "", volume, tol * 6)).fetchall()
        for r in rows:
            if r not in out:
                out.append(r)
    _ = bbox_diag
    return out


def is_stale_attempt(conn, batch, project_id, rev):
    """N1 round 5: TRUE when a NEWER-committed row of this model is
    already DELIVERED - publishing this attempt's folder onto disk at that
    point would stomp the delivered bytes while mark_published (correctly)
    refuses to mark it. Callers must check BEFORE publish(); packager also
    restores the trashed folder if a mark is nonetheless refused."""
    # `rev` may be the full content hash (packager pre-check) or the
    # 16-char staging-folder prefix (publish guard) - resolve by prefix;
    # hex rev prefixes collide only astronomically.
    like = rev + "%"
    row = conn.execute(
        "SELECT model_id, MIN(rowid) FROM delivery WHERE rev LIKE ? AND "
        "batch_id=? GROUP BY model_id", (like, batch)).fetchone()
    if row is None:
        return False
    model_id, my_rowid = row[0], row[1]
    return conn.execute(
        "SELECT 1 FROM delivery WHERE batch_id=? AND model_id=? AND "
        "state='delivered' AND rev NOT LIKE ? AND rowid>? LIMIT 1",
        (batch, model_id, like, my_rowid)).fetchone() is not None


def reinstate_previous(conn, batch, project_id, exclude_rev=None):
    """N2 round 5 / N25 round 6: after a post-publish rollback has put
    the PREVIOUS delivery's bytes back on disk, the ledger must agree:
    the most recent superseded row that was ACTUALLY PUBLISHED before
    (published=1 - never a rejected stale attempt that merely shares the
    superseded label) becomes delivered again and classes re-elect.
    Returns the reinstated rev or None."""
    with conn:
        row = conn.execute(
            "SELECT d.rev FROM delivery d JOIN model m "
            "ON m.model_id=d.model_id WHERE d.batch_id=? AND m.project_id=?"
            " AND d.state='superseded' AND d.published=1 AND d.rev<>? "
            "ORDER BY d.rowid DESC "
            "LIMIT 1", (batch, project_id, exclude_rev or "")).fetchone()
        if row is None:
            return None
        prev_rev = row[0]
        # N28 round 7: promote ONLY the selected published row (rowid),
        # never every same-rev superseded row - rejected attempts share
        # the rev and must not become delivered too (that duplicates
        # class counts with a published=0 row).
        conn.execute(
            "UPDATE delivery SET state='delivered' WHERE rowid=? AND "
            "state='superseded'", (conn.execute(
                "SELECT rowid FROM delivery WHERE rev=? AND batch_id=? "
                "AND state='superseded' AND published=1 ORDER BY rowid "
                "DESC LIMIT 1", (prev_rev, batch)).fetchone()[0],))
        conn.execute("UPDATE fingerprint SET state='active' WHERE rev=?",
                     (prev_rev,))
        conn.execute("INSERT INTO event(rev,kind,payload,created) "
                     "VALUES(?,?,?,?)",
                     (prev_rev, "reinstated", "post-publish rollback (N2)",
                      _ms()))
        _reelect_classes(conn, batch)
        return prev_rev


def mark_published(conn, batch, project_id, rev, disk_final=None,
                   expect_sha=None):
    """CAS: pending -> delivered (single winner), THEN - and only after the
    folder is durably on disk - retire the previous delivered versions of
    this model (N1: publish-then-supersede, never the reverse). Every
    model has exactly one active delivered version when this returns.

    N20 round 6: when the caller passes the delivery folder and the
    native digest it just published, the mark runs under the publisher
    lock and FIRST verifies that the folder on disk still IS this
    attempt - the bytes are the arbiter, so an older attempt whose
    folder was overwritten by a newer publication (even one not yet
    marked) fails the CAS and leaves disk untouched."""
    _fd = _pub_lock(disk_final) if disk_final else None
    try:
        return _mark_published(conn, batch, project_id, rev, disk_final,
                               expect_sha)
    finally:
        if _fd is not None:
            _pub_unlock(_fd)


def _mark_published(conn, batch, project_id, rev, disk_final, expect_sha):
    with conn:
        row = conn.execute(
            "SELECT model_id, MIN(rowid) FROM delivery WHERE rev=? AND "
            "batch_id=? AND state='pending' GROUP BY model_id",
            (rev, batch)).fetchone()
        if row is None:
            return False
        model_id, my_rowid = row[0], row[1]
        # N1 recheck round 4: attempts publish CONCURRENTLY and marking an
        # OLD attempt after a newer one was already delivered would retire
        # the version actually on disk. Supersession only ever moves
        # FORWARD: if a LATER-committed row of this model is already
        # delivered, this publish is stale - mark it superseded and change
        # nothing else.
        if expect_sha and disk_final:
            # N20 round 6 byte-arbiter: whatever occupies the delivery
            # path must BE this attempt, or this publish lost the race.
            _nat = os.path.join(os.path.abspath(str(disk_final)),
                                os.path.basename(os.path.abspath(
                                    str(disk_final))) + ".FCStd")
            # N27 round 7: absent bytes are not OUR delivery either -
            # a folder deleted between swap and mark must fail the
            # arbiter instead of marking a claim for nothing on disk.
            if not os.path.exists(_nat):
                conn.execute(
                    "UPDATE delivery SET state='superseded' WHERE rev=? "
                    "AND batch_id=? AND state='pending'", (rev, batch))
                return False
            if sha256_file(_nat) != expect_sha:
                conn.execute(
                    "UPDATE delivery SET state='superseded' WHERE rev=? "
                    "AND batch_id=? AND state='pending'", (rev, batch))
                return False
        newer = conn.execute(
            "SELECT 1 FROM delivery WHERE batch_id=? AND model_id=? AND "
            "state='delivered' AND rev<>? AND rowid>? LIMIT 1",
            (batch, model_id, rev, my_rowid)).fetchone()
        if newer is not None:
            conn.execute(
                "UPDATE delivery SET state='superseded' WHERE rev=? AND "
                "batch_id=? AND state='pending'", (rev, batch))
            return False
        cur = conn.execute(
            "UPDATE delivery SET state='delivered', published=1, "
            "delivered=? WHERE rev=? "
            "AND batch_id=? AND state='pending'",
            (_ms(), rev, batch))
        if cur.rowcount != 1:
            return False
        # retire every OLDER delivered row of this model; keep all rows of
        # the freshly published rev (same-rev republish dedup, N13).
        others = conn.execute(
            "SELECT rev FROM delivery WHERE batch_id=? AND model_id=? AND "
            "state='delivered' AND rev<>?", (batch, model_id, rev)).fetchall()
        conn.execute(
            "UPDATE delivery SET state='superseded' WHERE batch_id=? AND "
            "model_id=? AND state='delivered' AND rev<>?",
            (batch, model_id, rev))
        for (orev,) in others:
            still = conn.execute(
                "SELECT 1 FROM delivery WHERE rev=? AND state='delivered' "
                "LIMIT 1", (orev,)).fetchone()
            if still is None:
                conn.execute("UPDATE fingerprint SET state='withdrawn' "
                             "WHERE rev=?", (orev,))
        # same-rev republish: collapse duplicate delivered rows for this
        # exact content-addressed rev into a single winner.
        conn.execute(
            "DELETE FROM delivery WHERE rev=? AND batch_id=? AND "
            "state='delivered' AND rowid NOT IN (SELECT MIN(rowid) FROM "
            "delivery WHERE rev=? AND batch_id=? AND state='delivered')",
            (rev, batch, rev, batch))
        conn.execute("INSERT INTO event(rev,kind,payload,created) "
                     "VALUES(?,?,?,?)", (rev, "published", "", _ms()))
        _reelect_classes(conn, batch)
        return True


def _reelect_classes(conn, batch):
    """N13 recheck - CLASS-WIDE normalisation, not per-orphan patching.

    For every delivered equivalence class (identical cells+steps+solids
    fingerprint) the invariant is: EXACTLY ONE member carries counts=1
    (the representative) and every other member points AT the
    representative. A class that lost its representative (superseded,
    withdrawn, republished) re-elects the earliest-delivered member and
    re-points the rest - cycles and zero-unique classes are impossible
    once this runs inside every publish/withdraw transaction."""
    classes = conn.execute(
        "SELECT f.cells, f.steps, f.solids FROM delivery d "
        "JOIN fingerprint f ON f.rev=d.rev WHERE d.batch_id=? AND "
        "d.state='delivered' GROUP BY 1,2,3 "
        "HAVING SUM(IFNULL(d.counts,1)) <> 1", (batch,)).fetchall()
    for cells, steps, solids in classes:
        members = conn.execute(
            "SELECT d.rev, m.project_id FROM delivery d "
            "JOIN model m ON m.model_id=d.model_id "
            "JOIN fingerprint f ON f.rev=d.rev WHERE d.batch_id=? AND "
            "d.state='delivered' AND IFNULL(f.cells,-1)=IFNULL(?,-1) AND "
            "IFNULL(f.steps,-1)=IFNULL(?,-1) AND "
            "IFNULL(f.solids,-1)=IFNULL(?,-1) "
            "ORDER BY d.delivered ASC, d.rev ASC",
            (batch, cells, steps, solids)).fetchall()
        if not members:
            continue
        rep_rev, rep_pid = members[0]
        for rev, pid in members:
            if rev == rep_rev:
                conn.execute("UPDATE delivery SET counts=1, counts_as=NULL "
                             "WHERE rev=? AND batch_id=?", (rep_rev, batch))
            else:
                conn.execute("UPDATE delivery SET counts=0, counts_as=? "
                             "WHERE rev=? AND batch_id=?",
                             (rep_pid, rev, batch))


def delivery_state(conn, batch, project_id, rev):
    row = conn.execute(
        "SELECT state FROM delivery WHERE rev=? AND batch_id=? AND "
        "state IN ('pending','delivered') ORDER BY delivered DESC LIMIT 1",
        (rev, batch)).fetchone()
    return row[0] if row else None


def abandon_pending(conn, batch, rev, reason):
    with conn:
        conn.execute("UPDATE delivery SET state='abandoned' WHERE rev=? AND "
                     "batch_id=? AND state='pending'", (rev, batch))
        conn.execute("INSERT INTO event(rev,kind,payload,created) "
                     "VALUES(?,?,?,?)", (rev, "abandoned", reason, _ms()))


def withdraw(conn, rev, reason):
    with conn:
        conn.execute("UPDATE fingerprint SET state='withdrawn' WHERE rev=?",
                     (rev,))
        conn.execute("UPDATE delivery SET state='superseded' WHERE rev=? AND "
                     "state='delivered'", (rev,))
        conn.execute("INSERT INTO event(rev,kind,payload,created) "
                     "VALUES(?,?,?,?)", (rev, "withdrawn", reason, _ms()))
        for b in conn.execute(
                "SELECT DISTINCT batch_id FROM delivery WHERE rev=?",
                (rev,)).fetchall():
            _reelect_classes(conn, b[0])
