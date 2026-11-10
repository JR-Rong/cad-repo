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
  payload TEXT, created INTEGER NOT NULL,
  prev_hash TEXT, row_hash TEXT);
CREATE TABLE IF NOT EXISTS intake(
  batch_id TEXT NOT NULL, project_id TEXT NOT NULL, required TEXT,
  PRIMARY KEY(batch_id, project_id));
CREATE TABLE IF NOT EXISTS evidence_seal(
  project_id TEXT PRIMARY KEY,
  sha TEXT NOT NULL,
  updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rules_rereview(
  project_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  input_sha TEXT NOT NULL,
  rules_sha TEXT NOT NULL,
  reason TEXT NOT NULL,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT,
  PRIMARY KEY(project_id, stage, input_sha, rules_sha));
CREATE TABLE IF NOT EXISTS quarantine_mark(
  project_id TEXT PRIMARY KEY,
  input_sha TEXT NOT NULL,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT);
CREATE TABLE IF NOT EXISTS review_signoff(
  project_id TEXT NOT NULL,
  input_sha TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT,
  PRIMARY KEY(project_id, input_sha));
-- Append-only enforcement. The Python WHERE-clause check is only a typo
-- guard (WHERE 1, comments and CTEs slip through). These triggers fire for
-- every client, including raw sqlite3. Fingerprint retirement is an UPDATE
-- of state to 'withdrawn', which these triggers allow. DELETE is not.
CREATE TRIGGER IF NOT EXISTS job_no_delete
BEFORE DELETE ON job
BEGIN
  SELECT RAISE(ABORT, 'job rows cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS event_no_delete
BEFORE DELETE ON event
BEGIN
  SELECT RAISE(ABORT, 'event rows cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS event_no_update
BEFORE UPDATE ON event
BEGIN
  SELECT RAISE(ABORT, 'event rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS fingerprint_no_delete
BEFORE DELETE ON fingerprint
BEGIN
  SELECT RAISE(ABORT, 'fingerprint rows cannot be deleted; set state=withdrawn');
END;
CREATE TRIGGER IF NOT EXISTS evidence_seal_no_delete
BEFORE DELETE ON evidence_seal
BEGIN
  SELECT RAISE(ABORT, 'evidence_seal rows cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS quarantine_mark_no_update
BEFORE UPDATE ON quarantine_mark
BEGIN
  SELECT RAISE(ABORT, 'quarantine input_sha is frozen at record time');
END;
CREATE TRIGGER IF NOT EXISTS quarantine_mark_no_delete
BEFORE DELETE ON quarantine_mark
BEGIN
  SELECT RAISE(ABORT, 'quarantine marks cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS review_signoff_no_update
BEFORE UPDATE ON review_signoff
BEGIN
  SELECT RAISE(ABORT, 'review sign-off cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS review_signoff_no_delete
BEFORE DELETE ON review_signoff
BEGIN
  SELECT RAISE(ABORT, 'review sign-off cannot be deleted');
END;
CREATE TABLE IF NOT EXISTS waiver(
  project_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  input_sha TEXT NOT NULL,
  rules_sha TEXT NOT NULL,
  kind TEXT NOT NULL,
  reason TEXT NOT NULL,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT,
  PRIMARY KEY(project_id, stage, input_sha, rules_sha, kind));
CREATE TRIGGER IF NOT EXISTS waiver_no_update
BEFORE UPDATE ON waiver
BEGIN
  SELECT RAISE(ABORT, 'waiver rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS waiver_no_delete
BEFORE DELETE ON waiver
BEGIN
  SELECT RAISE(ABORT, 'waiver rows cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS rules_rereview_no_update
BEFORE UPDATE ON rules_rereview
BEGIN
  SELECT RAISE(ABORT, 'rules_rereview rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS rules_rereview_no_delete
BEFORE DELETE ON rules_rereview
BEGIN
  SELECT RAISE(ABORT, 'rules_rereview rows cannot be deleted');
END;
-- Indexed, insert-only copy of job/inherit transitions. Acceptance reads
-- this instead of json_extract-scanning event. The event row is still
-- the hash-chained log; this table is the lookup.
CREATE TABLE IF NOT EXISTS state_transition(
  transition_id INTEGER PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  input_sha TEXT NOT NULL,
  rules_sha TEXT NOT NULL,
  old_state TEXT,
  new_state TEXT NOT NULL,
  old_attempt INTEGER,
  new_attempt INTEGER,
  old_error TEXT,
  new_error TEXT,
  source TEXT NOT NULL,
  created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS state_transition_lookup
  ON state_transition(project_id, stage, input_sha, rules_sha, new_state);
CREATE TRIGGER IF NOT EXISTS state_transition_no_update
BEFORE UPDATE ON state_transition
BEGIN
  SELECT RAISE(ABORT, 'state_transition rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS state_transition_no_delete
BEFORE DELETE ON state_transition
BEGIN
  SELECT RAISE(ABORT, 'state_transition rows cannot be deleted');
END;
-- Alarms are append-only. action is 'raise' or 'ack'. A later ack with a
-- non-empty reason is the only way to clear one. Nothing writes these
-- back to 0.
CREATE TABLE IF NOT EXISTS ledger_alarm(
  alarm_id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT);
CREATE INDEX IF NOT EXISTS ledger_alarm_kind
  ON ledger_alarm(kind, alarm_id);
CREATE TRIGGER IF NOT EXISTS ledger_alarm_no_update
BEFORE UPDATE ON ledger_alarm
BEGIN
  SELECT RAISE(ABORT, 'ledger_alarm rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS ledger_alarm_no_delete
BEFORE DELETE ON ledger_alarm
BEGIN
  SELECT RAISE(ABORT, 'ledger_alarm rows cannot be deleted');
END;
-- Per-row acknowledgement of an authorization row that does not match
-- the chain. The row stays unauthorized. A raw insert here is itself
-- an orphan until a chain event names this ack_id.
CREATE TABLE IF NOT EXISTS orphan_ack(
  ack_id INTEGER PRIMARY KEY,
  src_table TEXT NOT NULL,
  src_rowid INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created INTEGER NOT NULL,
  event_id INTEGER,
  content_hash TEXT);
CREATE TRIGGER IF NOT EXISTS orphan_ack_no_update
BEFORE UPDATE ON orphan_ack
BEGIN
  SELECT RAISE(ABORT, 'orphan_ack rows cannot be rewritten');
END;
CREATE TRIGGER IF NOT EXISTS orphan_ack_no_delete
BEFORE DELETE ON orphan_ack
BEGIN
  SELECT RAISE(ABORT, 'orphan_ack rows cannot be deleted');
END;
CREATE TABLE IF NOT EXISTS buyer_stock(
  buyer_id TEXT NOT NULL, external_id TEXT NOT NULL, signature TEXT,
  eligibility TEXT NOT NULL, receipt_sha TEXT,
  PRIMARY KEY(buyer_id, external_id));
"""

# Reinstalled on every open so a ledger created by an older engine cannot
# keep the trigger that treated any rules_rereview row as permission to
# turn a design FAIL into OK. Names are checked before this runs; a missing
# name is an alarm. A same-named body is replaced with this one.
_JOB_TRIGGERS = """
DROP TRIGGER IF EXISTS job_update_guard;
DROP TRIGGER IF EXISTS job_update_event;
CREATE TRIGGER job_update_guard
BEFORE UPDATE ON job
BEGIN
  SELECT RAISE(ABORT, 'job identity columns are immutable')
  WHERE OLD.project_id IS NOT NEW.project_id
     OR OLD.stage IS NOT NEW.stage
     OR OLD.input_sha IS NOT NEW.input_sha
     OR OLD.rules_sha IS NOT NEW.rules_sha;
  SELECT RAISE(ABORT, 'attempt may only increase')
  WHERE NEW.attempt < OLD.attempt;
  SELECT RAISE(ABORT, 'error_class is immutable in a sticky state')
  WHERE OLD.state IN ('FAIL', 'WAIVED', 'INFEASIBLE')
    AND OLD.error_class IS NOT NEW.error_class;
  SELECT RAISE(ABORT, 'sticky job state has no legal transition')
  WHERE OLD.state IN ('FAIL', 'WAIVED', 'INFEASIBLE')
    AND NOT (
      (
        OLD.state = 'FAIL'
        AND NEW.state IN ('WAIVED', 'INFEASIBLE')
        AND EXISTS (
          SELECT 1 FROM waiver w
          JOIN event e ON e.event_id = w.event_id
            AND e.row_hash = w.content_hash
          WHERE w.project_id = OLD.project_id AND w.stage = OLD.stage
            AND w.input_sha = OLD.input_sha AND w.rules_sha = OLD.rules_sha
            AND w.kind = CASE NEW.state
                           WHEN 'WAIVED' THEN 'waiver'
                           ELSE 'design-infeasible' END
            AND e.kind = w.kind
            AND e.row_hash = cadkit_event_hash(
                  IFNULL(e.prev_hash, ''), IFNULL(e.rev, ''), e.kind,
                  IFNULL(e.payload, ''), e.created)
            AND json_extract(e.payload, '$.project_id') = w.project_id
            AND json_extract(e.payload, '$.stage') = w.stage
            AND json_extract(e.payload, '$.input_sha') = w.input_sha
            AND json_extract(e.payload, '$.rules_sha') = w.rules_sha
            AND json_extract(e.payload, '$.kind') = w.kind
            AND json_extract(e.payload, '$.reason') = w.reason)
      )
      OR (
        OLD.state = 'FAIL'
        AND NEW.state = 'OK'
        AND IFNULL(OLD.error_class, '') LIKE 'inherited:%'
        AND EXISTS (
          SELECT 1 FROM rules_rereview r
          JOIN event e ON e.event_id = r.event_id
            AND e.row_hash = r.content_hash
          WHERE r.project_id = OLD.project_id AND r.stage = OLD.stage
            AND r.input_sha = OLD.input_sha AND r.rules_sha = OLD.rules_sha
            AND e.kind = 'rules_rereview'
            AND e.row_hash = cadkit_event_hash(
                  IFNULL(e.prev_hash, ''), IFNULL(e.rev, ''), e.kind,
                  IFNULL(e.payload, ''), e.created)
            AND json_extract(e.payload, '$.project_id') = r.project_id
            AND json_extract(e.payload, '$.stage') = r.stage
            AND json_extract(e.payload, '$.input_sha') = r.input_sha
            AND json_extract(e.payload, '$.rules_sha') = r.rules_sha
            AND json_extract(e.payload, '$.reason') = r.reason
            AND IFNULL(r.reason, '') <> '')
      )
    );
END;
CREATE TRIGGER job_update_event
AFTER UPDATE ON job
BEGIN
  INSERT INTO event(rev, kind, payload, created, prev_hash, row_hash)
  VALUES (
    NEW.rev,
    'job_transition',
    cadkit_job_payload(OLD.project_id, OLD.stage, OLD.input_sha,
                       OLD.rules_sha, OLD.state, NEW.state,
                       OLD.attempt, NEW.attempt,
                       OLD.error_class, NEW.error_class),
    NEW.updated,
    COALESCE((SELECT row_hash FROM event WHERE row_hash IS NOT NULL
              ORDER BY event_id DESC LIMIT 1), ''),
    cadkit_event_hash(
      COALESCE((SELECT row_hash FROM event WHERE row_hash IS NOT NULL
                ORDER BY event_id DESC LIMIT 1), ''),
      COALESCE(NEW.rev, ''),
      'job_transition',
      cadkit_job_payload(OLD.project_id, OLD.stage, OLD.input_sha,
                         OLD.rules_sha, OLD.state, NEW.state,
                         OLD.attempt, NEW.attempt,
                         OLD.error_class, NEW.error_class),
      NEW.updated));
  INSERT INTO state_transition(
    project_id, stage, input_sha, rules_sha, old_state, new_state,
    old_attempt, new_attempt, old_error, new_error, source, created)
  VALUES (
    OLD.project_id, OLD.stage, OLD.input_sha, OLD.rules_sha,
    OLD.state, NEW.state, OLD.attempt, NEW.attempt,
    OLD.error_class, NEW.error_class, 'job', NEW.updated);
END;
"""

CHAIN_ANCHOR_NAME = "ledger.chain.anchor"
END_SHA_NAME = "ledger.end.sha"
STATUS_ANCHOR_PATH = os.path.join("status", "ledger_anchor.json")
BACKFILL_EVENT_KIND = "event_hash_backfill"
# Named in RUNBOOK. A schema_tampered ack for a legacy first open has to
# include this text so the reason identifies the upgrade, not a blank note.
UPGRADE_ACK = "upgrade 0.5.1"
ALARM_KINDS = ("schema_tampered", "ledger_sha_mismatch",
               "chain_anchor_mismatch", "orphan_authorization",
               "anchor_unverifiable")
_ORPHAN_TABLES = (
    "waiver", "rules_rereview", "review_signoff", "quarantine_mark",
    "ledger_alarm", "orphan_ack",
)


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


class _GuardedConnection:
    """sqlite3.Connection proxy. ``execute`` is read-only on 3.12, so the
    guard has to live on a wrapper that every ledger caller actually uses.
    """

    def __init__(self, conn):
        from .chain import assert_scoped_sql, sql_is_unscoped_mutation
        self._conn = conn
        self._assert = assert_scoped_sql
        self._bad = sql_is_unscoped_mutation
        self._tx_depth = 0
        self._own_tx = False

    def execute(self, sql, parameters=()):
        self._assert(sql)
        return self._conn.execute(sql, parameters)

    def executemany(self, sql, seq):
        self._assert(sql)
        return self._conn.executemany(sql, seq)

    def executescript(self, script):
        for stmt in (script or "").split(";"):
            if self._bad(stmt):
                self._assert(stmt)
        return self._conn.executescript(script)

    def __enter__(self):
        # Default sqlite3 ``with`` starts a deferred transaction on the
        # first statement. Two writers can both read the chain head, then
        # both insert. IMMEDIATE takes the write lock before that read.
        if self._tx_depth == 0:
            if not self._conn.in_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
                self._own_tx = True
            else:
                self._own_tx = False
        self._tx_depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._tx_depth -= 1
        if self._tx_depth == 0 and self._own_tx:
            self._own_tx = False
            if exc_type:
                self._conn.rollback()
            else:
                self._conn.commit()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _install_sql_guard(conn):
    """Refuse unscoped DELETE/UPDATE on every statement this handle runs.

    Typo guard only. ``WHERE 1`` passes. The triggers are what abort a
    raw sqlite3 client. They are not a defense against someone who can
    edit the database file and drop the triggers.
    """
    return _GuardedConnection(conn)


def _event_hash(prev_hash, rev, kind, payload, created):
    h = hashlib.sha256()
    for part in (prev_hash or "", rev or "", kind or "", payload or "",
                 str(created)):
        h.update(str(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _job_payload(project_id, stage, input_sha, rules_sha, old_state, new_state,
                 old_attempt, new_attempt, old_error, new_error):
    def _num(value):
        if value is None or value == "":
            return None
        return int(value)

    return json.dumps({
        "attempt_after": _num(new_attempt),
        "attempt_before": _num(old_attempt),
        "error_class_after": new_error,
        "error_class_before": old_error,
        "input_sha": input_sha,
        "project_id": project_id,
        "rules_sha": rules_sha,
        "stage": stage,
        "transition": "%s -> %s" % (old_state or "", new_state or ""),
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _register_sql_functions(conn):
    conn.create_function("cadkit_event_hash", 5, _event_hash)
    conn.create_function("cadkit_job_payload", 10, _job_payload)


def _insert_event(conn, rev, kind, payload, created=None):
    """Append one event and extend the hash chain. Insert-only.

    The head read and the insert share one IMMEDIATE transaction so two
    writers cannot both chain off the same predecessor.
    """
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False,
                             separators=(",", ":"), sort_keys=True)
    created = _ms() if created is None else int(created)
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        prev = conn.execute(
            "SELECT row_hash FROM event WHERE row_hash IS NOT NULL "
            "ORDER BY event_id DESC LIMIT 1").fetchone()
        prev_hash = prev[0] if prev and prev[0] else ""
        row_hash = _event_hash(prev_hash, rev, kind, payload, created)
        cur = conn.execute(
            "INSERT INTO event(rev, kind, payload, created, prev_hash, row_hash) "
            "VALUES(?,?,?,?,?,?)",
            (rev, kind, payload, created, prev_hash, row_hash))
        event_id = cur.lastrowid
        if own:
            conn.commit()
    except Exception:
        if own:
            conn.rollback()
        raise
    return event_id, row_hash


def _record_transition(conn, project_id, stage, input_sha, rules_sha,
                       old_state, new_state, old_attempt, new_attempt,
                       old_error, new_error, source, created):
    conn.execute(
        "INSERT INTO state_transition(project_id, stage, input_sha, rules_sha, "
        "old_state, new_state, old_attempt, new_attempt, old_error, new_error, "
        "source, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, stage, input_sha, rules_sha, old_state, new_state,
         old_attempt, new_attempt, old_error, new_error, source, created))


EXPECTED_TRIGGERS = (
    "job_no_delete", "event_no_delete", "event_no_update",
    "fingerprint_no_delete", "evidence_seal_no_delete",
    "quarantine_mark_no_update", "quarantine_mark_no_delete",
    "review_signoff_no_update", "review_signoff_no_delete",
    "waiver_no_update", "waiver_no_delete",
    "rules_rereview_no_update", "rules_rereview_no_delete",
    "state_transition_no_update", "state_transition_no_delete",
    "ledger_alarm_no_update", "ledger_alarm_no_delete",
    "orphan_ack_no_update", "orphan_ack_no_delete",
    "job_update_guard", "job_update_event",
)


def _missing_triggers(conn):
    """Triggers that should already exist. Empty on a database with no job table.

    Called before CREATE IF NOT EXISTS so a dropped trigger is visible.
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "job" not in tables:
        return []
    present = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    return [name for name in EXPECTED_TRIGGERS if name not in present]


def verify_event_chain(conn):
    """True when every event row's hash matches its predecessor.

    A NULL row_hash is a break. It is not a request to recompute the chain.
    """
    nulls = conn.execute(
        "SELECT 1 FROM event WHERE row_hash IS NULL OR prev_hash IS NULL "
        "LIMIT 1").fetchone()
    if nulls:
        return False
    rows = conn.execute(
        "SELECT rev, kind, payload, created, prev_hash, row_hash "
        "FROM event ORDER BY event_id").fetchall()
    prev = ""
    for rev, kind, payload, created, prev_hash, row_hash in rows:
        if (prev_hash or "") != prev:
            return False
        expect = _event_hash(prev, rev, kind, payload, created)
        if row_hash != expect:
            return False
        prev = row_hash
    return True


def _backfill_marked(conn):
    """True once the insert-only backfill event is on the chain.

    The marker is an event, so it cannot be deleted while ``event_no_delete``
    is in place. A meta flag could be deleted; this cannot.
    """
    row = conn.execute(
        "SELECT 1 FROM event WHERE kind=? LIMIT 1",
        (BACKFILL_EVENT_KIND,)).fetchone()
    return row is not None


def _backfill_event_hashes(conn, cad_root):
    """Fill hashes once, for a ledger that predates the chain.

    After the ``event_hash_backfill`` event is on the chain, a NULL
    ``row_hash`` stays NULL and ``verify_event_chain`` fails. ``cad_root``
    is unused; the seal is the event, not a file beside the database.
    """
    del cad_root
    cols = {r[1] for r in conn.execute("PRAGMA table_info(event)")}
    if "row_hash" not in cols:
        conn.execute("ALTER TABLE event ADD COLUMN prev_hash TEXT")
        conn.execute("ALTER TABLE event ADD COLUMN row_hash TEXT")
    if _backfill_marked(conn):
        return
    total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    nulls = conn.execute(
        "SELECT COUNT(*) FROM event WHERE row_hash IS NULL").fetchone()[0]
    if total and nulls == total:
        conn.execute("DROP TRIGGER IF EXISTS event_no_update")
        prev = ""
        rows = conn.execute(
            "SELECT event_id, rev, kind, payload, created FROM event "
            "ORDER BY event_id").fetchall()
        for event_id, rev, kind, payload, created in rows:
            row_hash = _event_hash(prev, rev, kind, payload or "", created)
            conn.execute(
                "UPDATE event SET prev_hash=?, row_hash=? WHERE event_id=?",
                (prev, row_hash, event_id))
            prev = row_hash
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS event_no_update
            BEFORE UPDATE ON event
            BEGIN
              SELECT RAISE(ABORT, 'event rows cannot be rewritten');
            END;
        """)
    _insert_event(conn, None, BACKFILL_EVENT_KIND, {"sealed": True})
    if conn.in_transaction:
        conn.commit()


def _authorization_matches(conn, event_id, content_hash, expect, event_kind):
    """True when this row points at the chain event that records the same content.

    ``content_hash`` is that event's ``row_hash``. The hash has to match the
    event row, the event row has to hash to itself, and the payload fields
    have to equal ``expect``. A raw insert that leaves these columns empty,
    or that copies another event's id, does not match.
    """
    if event_id is None or not content_hash:
        return False
    row = conn.execute(
        "SELECT rev, kind, payload, created, prev_hash, row_hash "
        "FROM event WHERE event_id=?",
        (event_id,)).fetchone()
    if not row:
        return False
    rev, kind, payload, created, prev_hash, row_hash = row
    if row_hash != content_hash or kind != event_kind:
        return False
    if _event_hash(prev_hash, rev, kind, payload, created) != row_hash:
        return False
    try:
        doc = json.loads(payload or "")
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    for key, value in expect.items():
        if doc.get(key) != value:
            return False
    return True


def _iter_orphan_authorizations(conn):
    """Authorization rows whose stored event id and hash do not match the chain."""
    rows = conn.execute(
        "SELECT rowid, event_id, content_hash, project_id, stage, input_sha, "
        "rules_sha, kind, reason FROM waiver").fetchall()
    for (rowid, event_id, content_hash, project_id, stage, input_sha,
         rules_sha, kind, reason) in rows:
        if not _authorization_matches(conn, event_id, content_hash, {
            "project_id": project_id, "stage": stage, "input_sha": input_sha,
            "rules_sha": rules_sha, "kind": kind, "reason": reason,
        }, kind):
            yield "waiver", rowid
    rows = conn.execute(
        "SELECT rowid, event_id, content_hash, project_id, stage, input_sha, "
        "rules_sha, reason FROM rules_rereview").fetchall()
    for (rowid, event_id, content_hash, project_id, stage, input_sha,
         rules_sha, reason) in rows:
        if not _authorization_matches(conn, event_id, content_hash, {
            "project_id": project_id, "stage": stage, "input_sha": input_sha,
            "rules_sha": rules_sha, "reason": reason,
        }, "rules_rereview"):
            yield "rules_rereview", rowid
    rows = conn.execute(
        "SELECT rowid, event_id, content_hash, project_id, input_sha, "
        "reviewer, reason FROM review_signoff").fetchall()
    for (rowid, event_id, content_hash, project_id, input_sha, reviewer,
         reason) in rows:
        if not _authorization_matches(conn, event_id, content_hash, {
            "project_id": project_id, "input_sha": input_sha,
            "reviewer": reviewer, "reason": reason,
        }, "signoff"):
            yield "review_signoff", rowid
    rows = conn.execute(
        "SELECT rowid, event_id, content_hash, project_id, input_sha "
        "FROM quarantine_mark").fetchall()
    for rowid, event_id, content_hash, project_id, input_sha in rows:
        if not _authorization_matches(conn, event_id, content_hash, {
            "project_id": project_id, "input_sha": input_sha,
        }, "quarantine"):
            yield "quarantine_mark", rowid
    rows = conn.execute(
        "SELECT alarm_id, kind, action, reason, event_id, content_hash "
        "FROM ledger_alarm").fetchall()
    for alarm_id, kind, action, reason, event_id, content_hash in rows:
        reason = reason or ""
        if action == "ack":
            ok = _authorization_matches(conn, event_id, content_hash, {
                "alarm": kind, "reason": reason,
            }, "alarm_ack")
        else:
            ok = _authorization_matches(conn, event_id, content_hash, {
                "reason": reason,
            }, kind)
        if not ok:
            yield "ledger_alarm", alarm_id
    rows = conn.execute(
        "SELECT ack_id, src_table, src_rowid, reason, event_id, content_hash "
        "FROM orphan_ack").fetchall()
    for ack_id, src_table, src_rowid, reason, event_id, content_hash in rows:
        if not _authorization_matches(conn, event_id, content_hash, {
            "table": src_table, "rowid": int(src_rowid), "reason": reason or "",
        }, "orphan_ack"):
            yield "orphan_ack", ack_id


def _orphan_row_acknowledged(conn, table, rowid):
    """True when a chain-linked orphan_ack names this row and has a reason."""
    rows = conn.execute(
        "SELECT event_id, content_hash, reason FROM orphan_ack "
        "WHERE src_table=? AND src_rowid=?",
        (table, int(rowid))).fetchall()
    for event_id, content_hash, reason in rows:
        reason = reason or ""
        if not str(reason).strip():
            continue
        if _authorization_matches(conn, event_id, content_hash, {
            "table": table, "rowid": int(rowid), "reason": reason,
        }, "orphan_ack"):
            return True
    return False


def audit_orphan_authorizations(conn):
    """Raise ``orphan_authorization`` for unmatched rows that are not acked.

    An acknowledgement is per row and does not make the row authorize
    anything. A raw insert into ``orphan_ack`` does not count: it is
    itself an orphan until a chain event names that ack.
    """
    pending = [(table, rowid) for table, rowid in _iter_orphan_authorizations(conn)
               if not _orphan_row_acknowledged(conn, table, rowid)]
    if pending and not alarm_open(conn, "orphan_authorization"):
        shown = ",".join("%s:%s" % pair for pair in pending[:12])
        raise_alarm(conn, "orphan_authorization",
                    "unmatched authorization rows: %s" % shown)
    return pending


def acknowledge_orphan(conn, table, rowid, reason):
    """Stop re-alarming one orphan row. The row still authorizes nothing.

    ``table`` is the authorization table and ``rowid`` is that row.
    ``reason`` is required. The ack is an ``orphan_ack`` row plus a
    chain event written in the same transaction. A later raw insert of
    another ack does not replace this one and is reported on its own.
    """
    if table not in _ORPHAN_TABLES:
        raise ValueError("unknown authorization table %s" % table)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("orphan acknowledgement requires a reason")
    rowid = int(rowid)
    found = conn.execute(
        "SELECT 1 FROM %s WHERE rowid=?" % table, (rowid,)).fetchone()
    if not found:
        raise ValueError("no %s row %s" % (table, rowid))
    now = _ms()
    payload = {"table": table, "rowid": rowid, "reason": reason}
    with conn:
        event_id, content_hash = _insert_event(
            conn, None, "orphan_ack", payload, now)
        conn.execute(
            "INSERT INTO orphan_ack(src_table, src_rowid, reason, created, "
            "event_id, content_hash) VALUES(?,?,?,?,?,?)",
            (table, rowid, reason, now, event_id, content_hash))
    pending = [(src, src_id) for src, src_id in _iter_orphan_authorizations(conn)
               if not _orphan_row_acknowledged(conn, src, src_id)]
    if not pending and alarm_open(conn, "orphan_authorization"):
        acknowledge_alarm(conn, "orphan_authorization", reason)
    return {"table": table, "rowid": rowid, "reason": reason,
            "event_id": event_id}


def migrate_rules_rereview_from_events(conn):
    """Copy kind=rules_rereview events into the lookup table. Idempotent.

    The copied row stores that event's id and row hash. A payload that
    does not hash to the stored row is not copied.
    """
    rows = conn.execute(
        "SELECT event_id, rev, kind, payload, created, prev_hash, row_hash "
        "FROM event WHERE kind='rules_rereview'"
    ).fetchall()
    for event_id, rev, kind, payload, created, prev_hash, row_hash in rows:
        if _event_hash(prev_hash, rev, kind, payload, created) != row_hash:
            continue
        try:
            doc = json.loads(payload or "")
        except ValueError:
            continue
        if not isinstance(doc, dict):
            continue
        reason = str(doc.get("reason") or "").strip()
        if not (doc.get("project_id") and doc.get("stage")
                and doc.get("input_sha") and doc.get("rules_sha") and reason):
            continue
        if doc.get("reason") != reason:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO rules_rereview(project_id, stage, "
            "input_sha, rules_sha, reason, created, event_id, content_hash) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (doc["project_id"], doc["stage"], doc["input_sha"],
             doc["rules_sha"], reason, created or _ms(), event_id, row_hash))
    conn.commit()


def alarm_open(conn, kind):
    """True when the latest chain-linked row for ``kind`` is not an ack.

    A raw ack insert with no matching event is ignored.
    """
    rows = conn.execute(
        "SELECT action, reason, event_id, content_hash FROM ledger_alarm "
        "WHERE kind=? ORDER BY alarm_id",
        (kind,)).fetchall()
    open_ = False
    for action, reason, event_id, content_hash in rows:
        reason = reason or ""
        if action == "ack":
            if str(reason).strip() and _authorization_matches(
                    conn, event_id, content_hash,
                    {"alarm": kind, "reason": reason}, "alarm_ack"):
                open_ = False
        elif _authorization_matches(
                conn, event_id, content_hash, {"reason": reason}, kind):
            open_ = True
    return open_


def raise_alarm(conn, kind, reason):
    """Append an alarm and the chain event that authorizes it, one transaction."""
    if kind not in ALARM_KINDS:
        raise ValueError("unknown alarm kind %s" % kind)
    reason = str(reason or "").strip()
    now = _ms()
    with conn:
        event_id, content_hash = _insert_event(
            conn, None, kind, {"reason": reason}, now)
        conn.execute(
            "INSERT INTO ledger_alarm(kind, action, reason, created, "
            "event_id, content_hash) VALUES(?, 'raise', ?, ?, ?, ?)",
            (kind, reason, now, event_id, content_hash))
    return {"kind": kind, "action": "raise", "event_id": event_id}


def acknowledge_alarm(conn, kind, reason):
    """Clear one alarm kind with a chain event. ``reason`` is required.

    A ``schema_tampered`` acknowledgement has to name the upgrade
    (``upgrade 0.5.1``). That is the expected first open of a ledger
    created before this trigger set.
    """
    if kind not in ALARM_KINDS:
        raise ValueError("unknown alarm kind %s" % kind)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("acknowledgement requires a reason")
    if kind == "schema_tampered" and UPGRADE_ACK not in reason:
        raise ValueError(
            "schema_tampered acknowledgement must name the upgrade "
            "(%s)" % UPGRADE_ACK)
    now = _ms()
    with conn:
        event_id, content_hash = _insert_event(conn, None, "alarm_ack", {
            "alarm": kind, "reason": reason,
        }, now)
        conn.execute(
            "INSERT INTO ledger_alarm(kind, action, reason, created, "
            "event_id, content_hash) VALUES(?, 'ack', ?, ?, ?, ?)",
            (kind, reason, now, event_id, content_hash))
    return {"kind": kind, "action": "ack", "reason": reason,
            "event_id": event_id}


def ledger_acceptance_blocks(conn):
    """Reasons the ledger itself forbids ACCEPT. Empty means no ledger block.

    Triggers stop slips. They do not stop someone who can edit the file
    and remove the triggers; a missing trigger is recorded as an alarm
    event and stays until an acknowledgement with a reason.
    """
    pending_orphans = audit_orphan_authorizations(conn)
    reasons = []
    if alarm_open(conn, "schema_tampered"):
        reasons.append("schema_tampered")
    if alarm_open(conn, "ledger_sha_mismatch"):
        reasons.append("ledger_changed_outside_runner")
    if alarm_open(conn, "chain_anchor_mismatch"):
        reasons.append("chain_anchor_mismatch")
    if alarm_open(conn, "anchor_unverifiable"):
        reasons.append("anchor_unverifiable")
    if pending_orphans:
        reasons.append("orphan_authorization")
    if not verify_event_chain(conn):
        reasons.append("event_chain_broken")
    return reasons


def event_unwaived_fail(conn, project_id, stage, input_sha, rules_sha):
    """True when a FAIL transition for this key is still uncleared.

    A FAIL recorded by ``n45_inherit`` is cleared by a rules_rereview row.
    Any other FAIL (a design failure) is cleared only by a waiver row.
    The lookup is the indexed ``state_transition`` table.
    """
    rows = conn.execute(
        "SELECT source FROM state_transition WHERE project_id=? AND stage=? "
        "AND input_sha=? AND rules_sha=? AND new_state='FAIL'",
        (project_id, stage, input_sha, rules_sha)).fetchall()
    if not rows:
        return False
    reviewed = _rules_rereviewed(
        conn, project_id, stage, input_sha, rules_sha)
    waived = False
    wrows = conn.execute(
        "SELECT event_id, content_hash, kind, reason FROM waiver "
        "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=?",
        (project_id, stage, input_sha, rules_sha)).fetchall()
    for event_id, content_hash, kind, reason in wrows:
        if _authorization_matches(conn, event_id, content_hash, {
            "project_id": project_id, "stage": stage, "input_sha": input_sha,
            "rules_sha": rules_sha, "kind": kind, "reason": reason,
        }, kind):
            waived = True
            break
    for (source,) in rows:
        if source == "n45_inherit":
            if not reviewed:
                return True
        elif not waived:
            return True
    return False


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET "
        "v=excluded.v",
        (key, value))


def open_ledger(cad_root):
    path = os.path.join(cad_root, "ledger.sqlite3")
    raw = sqlite3.connect(path, timeout=30)
    _register_sql_functions(raw)
    missing = _missing_triggers(raw)
    conn = _install_sql_guard(raw)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    _ensure_event_columns(conn)
    conn.executescript(_SCHEMA)
    if not conn.execute("SELECT v FROM meta WHERE k='schema_version'")\
            .fetchone():
        conn.execute("INSERT INTO meta VALUES('schema_version',?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    _migrate(conn)
    _ensure_auth_link_columns(conn)
    _backfill_event_hashes(conn, cad_root)
    conn.executescript(_JOB_TRIGGERS)
    migrate_rules_rereview_from_events(conn)
    if missing:
        raise_alarm(conn, "schema_tampered",
                    "missing triggers: %s" % ",".join(missing))
    audit_orphan_authorizations(conn)
    return conn


def _ensure_auth_link_columns(conn):
    """Add event_id and content_hash on ledgers created before this link."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("waiver", "rules_rereview", "review_signoff",
                  "quarantine_mark", "ledger_alarm"):
        if table not in tables:
            continue
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        for col, decl in (("event_id", "INTEGER"), ("content_hash", "TEXT")):
            if col not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (
                    table, col, decl))


def _ensure_event_columns(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "event" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(event)")}
    if "prev_hash" not in cols:
        conn.execute("ALTER TABLE event ADD COLUMN prev_hash TEXT")
    if "row_hash" not in cols:
        conn.execute("ALTER TABLE event ADD COLUMN row_hash TEXT")


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
            from .chain import rename_noreplace
            rename_noreplace(staging, final)
        elif _status == "ready" and os.path.lexists(final):
            # Bytes were hashed under the lock. Move the known folder
            # aside, then rename staging in. A failed second rename
            # puts the hashed folder back.
            trash = final + ".superseded." + str(_ms())
            os.rename(final, trash)
            try:
                from .chain import rename_noreplace
                rename_noreplace(staging, final)
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
        _insert_event(conn, rev, "delivered", "", now)
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
        # A rules_sha change is a different unique key, which used to let a
        # later OK hide a FAIL recorded under the previous engine. Inherit
        # that FAIL unless a per-part re-review event names this new rules.
        if (state or "").upper() in ("OK", "PASS", "DONE"):
            prior = conn.execute(
                "SELECT rules_sha FROM job WHERE project_id=? AND stage=? "
                "AND input_sha=? AND rules_sha<>? AND state='FAIL'",
                (project_id, stage, input_sha, rules_sha)).fetchall()
            reviewed = _rules_rereviewed(
                conn, project_id, stage, input_sha, rules_sha)
            if prior and not reviewed:
                inherited = "inherited:" + ",".join(r[0] for r in prior)
                if row is None:
                    conn.execute(
                        "INSERT INTO job(rev,project_id,stage,input_sha,"
                        "rules_sha,state,attempt,error_class,updated) "
                        "VALUES(?,?,?,?,?,'FAIL',1,?,?)",
                        (rev, project_id, stage, input_sha, rules_sha,
                         inherited, now))
                _record_transition(
                    conn, project_id, stage, input_sha, rules_sha,
                    "", "FAIL", 0, 1, None, inherited, "n45_inherit", now)
                _insert_event(conn, rev, "n45_inherit", {
                    "project_id": project_id, "stage": stage,
                    "input_sha": input_sha, "rules_sha": rules_sha,
                    "prior_rules": [r[0] for r in prior],
                    "transition": "-> FAIL",
                }, now)
                return {"applied": False, "state": "FAIL",
                        "reason": "N45: FAIL under previous rules_sha "
                                  "still stands; per-part re-review required"}
            if (prior and reviewed and row is not None
                    and str(old_error or "").startswith("inherited:")):
                # The inherited marker is not a new design FAIL. The
                # re-review event is what allows this rules_sha to pass.
                old_state = ""
        # When the new call is a pass, stickiness depends on the stored
        # failure class, not on the empty error of the passing call.
        kind = old_error if (state or "").upper() in ("OK", "PASS", "DONE") \
            else error_class
        action, decided = job_transition(
            old_state, state, error_class=kind, attempt=old_attempt,
            max_attempts=max_attempts)
        if action == "keep":
            _insert_event(conn, rev, "n45_reject", {
                "project_id": project_id, "stage": stage,
                "input_sha": input_sha, "rules_sha": rules_sha,
                "kept": old_state, "rejected": state, "reason": decided,
            }, now)
            return {"applied": False, "state": old_state, "reason": decided}
        if row is None:
            conn.execute(
                "INSERT INTO job(rev,project_id,stage,input_sha,rules_sha,"
                "state,attempt,error_class,updated) VALUES(?,?,?,?,?,?,1,?,?)",
                (rev, project_id, stage, input_sha, rules_sha, decided,
                 error_class, now))
            _record_transition(
                conn, project_id, stage, input_sha, rules_sha,
                "", decided, 0, 1, None, error_class, "job", now)
            _insert_event(conn, rev, "job_transition", _job_payload(
                project_id, stage, input_sha, rules_sha, "", decided,
                0, 1, None, error_class), now)
        else:
            # Sticky FAIL keeps error_class. The trigger rejects a change,
            # including the inherited-FAIL -> OK re-review, which must
            # leave the inherited: marker in place.
            stored_error = old_error if (
                str(old_error or "").startswith("inherited:")
                and error_class is None) else error_class
            conn.execute(
                "UPDATE job SET state=?, attempt=?, error_class=?, updated=?, "
                "rev=COALESCE(?, rev) WHERE project_id=? AND stage=? AND "
                "input_sha=? AND rules_sha=?",
                (decided, old_attempt + 1, stored_error, now, rev,
                 project_id, stage, input_sha, rules_sha))
        return {"applied": True, "state": decided}


def _rules_rereviewed(conn, project_id, stage, input_sha, rules_sha):
    """Point lookup. The event table is not scanned.

    At 200k parts an event scan on every job write does not scale. The
    primary key is (project, stage, input, rules).
    """
    row = conn.execute(
        "SELECT reason, event_id, content_hash FROM rules_rereview "
        "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=?",
        (project_id, stage, input_sha, rules_sha)).fetchone()
    if not row or not str(row[0] or "").strip():
        return False
    return _authorization_matches(conn, row[1], row[2], {
        "project_id": project_id, "stage": stage, "input_sha": input_sha,
        "rules_sha": rules_sha, "reason": row[0],
    }, "rules_rereview")


def record_rules_rereview(conn, project_id, stage, input_sha, rules_sha,
                          reason):
    """Allow a later OK under a new rules_sha. Does not erase the old FAIL.

    ``reason`` is required. The event is per part, per stage, per input
    hash, per new rules hash.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("rules re-review requires a reason")
    now = _ms()
    payload = {
        "project_id": project_id, "stage": stage,
        "input_sha": input_sha, "rules_sha": rules_sha,
        "reason": reason,
    }
    with conn:
        event_id, content_hash = _insert_event(
            conn, None, "rules_rereview", payload, now)
        conn.execute(
            "INSERT INTO rules_rereview(project_id, stage, input_sha, "
            "rules_sha, reason, created, event_id, content_hash) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (project_id, stage, input_sha, rules_sha, reason, now,
             event_id, content_hash))
    return {"applied": True, "reason": reason}


def latest_build_input_sha(conn, project_id):
    """Build-stage input hash currently in the ledger. None if no such job."""
    row = conn.execute(
        "SELECT input_sha FROM job WHERE project_id=? AND stage='build' "
        "ORDER BY updated DESC, rowid DESC LIMIT 1",
        (project_id,)).fetchone()
    return row[0] if row else None


def mark_quarantine_from_ledger(conn, project_id):
    """Freeze the build input_sha that is already in the ledger.

    The caller does not pass a hash. A second call cannot replace the
    first: ``quarantine_mark`` rejects UPDATE.
    """
    sha = latest_build_input_sha(conn, project_id)
    if not sha:
        raise ValueError(
            "no build job in the ledger for %s; refusing to invent an "
            "input_sha" % project_id)
    existing = conn.execute(
        "SELECT input_sha FROM quarantine_mark WHERE project_id=?",
        (project_id,)).fetchone()
    if existing:
        if existing[0] != sha:
            raise ValueError(
                "quarantine input_sha for %s is already %s and cannot be "
                "rewritten" % (project_id, existing[0]))
        return {"applied": False, "input_sha": existing[0]}
    now = _ms()
    payload = {"project_id": project_id, "input_sha": sha}
    with conn:
        event_id, content_hash = _insert_event(
            conn, None, "quarantine", payload, now)
        conn.execute(
            "INSERT INTO quarantine_mark(project_id, input_sha, created, "
            "event_id, content_hash) VALUES(?,?,?,?,?)",
            (project_id, sha, now, event_id, content_hash))
    return {"applied": True, "input_sha": sha}


def quarantine_mark(conn, project_id):
    row = conn.execute(
        "SELECT input_sha, created, event_id, content_hash "
        "FROM quarantine_mark WHERE project_id=?",
        (project_id,)).fetchone()
    if not row:
        return None
    if not _authorization_matches(conn, row[2], row[3], {
        "project_id": project_id, "input_sha": row[0],
    }, "quarantine"):
        return None
    return {"input_sha": row[0], "created": row[1]}


def record_signoff(conn, project_id, input_sha, reviewer, reason):
    """Ledger sign-off for one new revision. Not a file the operator can edit.

    Insert-only. UPDATE and DELETE are aborted by triggers.
    """
    reviewer = (reviewer or "").strip()
    reason = (reason or "").strip()
    input_sha = (input_sha or "").strip()
    if not (project_id and input_sha and reviewer and reason):
        raise ValueError("sign-off needs project, input_sha, reviewer, reason")
    current = latest_build_input_sha(conn, project_id)
    frozen = quarantine_mark(conn, project_id)
    if not current or input_sha != current:
        raise ValueError(
            "sign-off input_sha must equal the current build hash in the ledger")
    if not frozen or input_sha == frozen["input_sha"]:
        raise ValueError(
            "sign-off must name a new revision, not the hash frozen at quarantine")
    now = _ms()
    payload = {
        "project_id": project_id, "input_sha": input_sha,
        "reviewer": reviewer, "reason": reason,
    }
    with conn:
        event_id, content_hash = _insert_event(
            conn, None, "signoff", payload, now)
        conn.execute(
            "INSERT INTO review_signoff(project_id, input_sha, reviewer, "
            "reason, created, event_id, content_hash) VALUES(?,?,?,?,?,?,?)",
            (project_id, input_sha, reviewer, reason, now,
             event_id, content_hash))
    return {"applied": True, "input_sha": input_sha}


def review_signoff(conn, project_id, input_sha):
    row = conn.execute(
        "SELECT input_sha, reviewer, reason, event_id, content_hash "
        "FROM review_signoff WHERE project_id=? AND input_sha=?",
        (project_id, input_sha)).fetchone()
    if not row:
        return None
    if not _authorization_matches(conn, row[3], row[4], {
        "project_id": project_id, "input_sha": row[0],
        "reviewer": row[1], "reason": row[2],
    }, "signoff"):
        return None
    return {"input_sha": row[0], "reviewer": row[1], "reason": row[2]}


def hold_legacy_publisher_lock(root, batch):
    """Hold ``delivery/<batch>/.publisher.lock`` exclusive until the fd closes.

    The 0.5.0 tarball (``package/python/cadkit/ledger.py``) takes this
    same file with ``fcntl.flock``, not ``lockf`` or ``fcntl.fcntl``
    byte-range locks. ``flock`` and ``lockf`` do not exclude each other
    on Linux, but the shipped 0.5.0 engine uses ``flock``, so holding
    ``flock`` is what blocks it. ``driver.py`` is not in this repo or in
    the 0.5.0 tarball, so its lock call could not be checked. Returns
    the fd, or None if another process already holds the flock.
    """
    import fcntl
    path = os.path.join(root, "delivery", batch, ".publisher.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def backup_ledger(root, dest_dir=None):
    """Online ``backup`` of ledger.sqlite3. Returns (path, sha256) or None.

    Uses the sqlite backup API, not a file copy, so a WAL writer cannot
    leave the snapshot half-written.
    """
    src_path = os.path.join(root, "ledger.sqlite3")
    if not os.path.isfile(src_path):
        return None
    dest_dir = dest_dir or os.path.join(root, "backups")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(
        dest_dir, "ledger-%s.sqlite3" % time.strftime("%Y%m%d_%H%M%S"))
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest, sha256_file(dest)


def _atomic_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def chain_head(conn):
    """Event count and the hash of the last event. The anchor lives outside the db."""
    total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    row = conn.execute(
        "SELECT event_id, row_hash FROM event ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    return {
        "event_count": int(total),
        "head_event_id": row[0] if row else None,
        "head_hash": row[1] if row else "",
    }


def write_chain_anchor(root, conn):
    """Write ``<cadRoot>/ledger.chain.anchor``. Not a row inside the ledger."""
    doc = chain_head(conn)
    _atomic_text(
        os.path.join(root, CHAIN_ANCHOR_NAME),
        json.dumps(doc, sort_keys=True) + "\n")
    return doc


def read_chain_anchor(root):
    path = os.path.join(root, CHAIN_ANCHOR_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        doc = json.loads(f.read())
    if not isinstance(doc, dict):
        return None
    return doc


def _anchor_required(conn):
    """A missing anchor file is a first run only on an empty, unsealed chain."""
    if _backfill_marked(conn):
        return True
    total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    return total > 0


def anchor_holds(conn, anchor):
    """True when the event at the anchored position still has that hash.

    A missing anchor is a mismatch once any event exists or the hash
    backfill event has been written. It is a first run only when both
    are absent.
    """
    if not anchor:
        return not _anchor_required(conn)
    try:
        count = int(anchor.get("event_count") or 0)
    except (TypeError, ValueError):
        return False
    total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    if count <= 0:
        return total == 0
    if total < count:
        return False
    row = conn.execute(
        "SELECT event_id, row_hash FROM event ORDER BY event_id "
        "LIMIT 1 OFFSET ?",
        (count - 1,)).fetchone()
    if not row or row[1] != anchor.get("head_hash"):
        return False
    anchored_id = anchor.get("head_event_id")
    if anchored_id is not None and row[0] != anchored_id:
        return False
    return True


def write_end_sha(root, sha):
    """Remember the last sqlite backup sha in ``<cadRoot>/ledger.end.sha``."""
    if not sha:
        return None
    _atomic_text(os.path.join(root, END_SHA_NAME), str(sha).strip() + "\n")
    return str(sha).strip()


def read_end_sha(root):
    path = os.path.join(root, END_SHA_NAME)
    if not os.path.isfile(path):
        return None
    text = open(path, encoding="utf-8").read().strip()
    return text or None


def write_status_anchor(repo, conn, end_sha=None, timestamp=None):
    """Write ``status/ledger_anchor.json`` in the repo. Does not commit or push."""
    doc = chain_head(conn)
    doc["ledger_end_sha"] = end_sha
    doc["timestamp"] = timestamp or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = os.path.join(repo, STATUS_ANCHOR_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def read_committed_status_anchor(repo, remote="origin"):
    """Fetch ``origin/<branch>`` and read its ``status/ledger_anchor.json``.

    Returns ``(document or None, error or None)``. A local commit is not
    the source. ``document`` is None when the fetch worked and that file
    is not on the remote branch yet. ``error`` is set when fetch or the
    branch cannot be resolved; callers must raise ``anchor_unverifiable``
    and must not fall back to local ``HEAD``.
    """
    import subprocess
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo, stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        return None, "git rev-parse failed: %s" % exc
    if not branch or branch == "HEAD":
        return None, "not on a branch; cannot read %s's anchor" % remote
    try:
        subprocess.check_call(
            ["git", "fetch", remote,
             "+refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch)],
            cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError) as exc:
        return None, "git fetch %s %s failed: %s" % (remote, branch, exc)
    ref = "%s/%s:%s" % (
        remote, branch, STATUS_ANCHOR_PATH.replace(os.sep, "/"))
    try:
        out = subprocess.check_output(
            ["git", "show", ref], cwd=repo, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None, None
    except OSError as exc:
        return None, "git show %s failed: %s" % (ref, exc)
    try:
        doc = json.loads(out.decode("utf-8"))
    except ValueError:
        return None, "origin anchor is not json"
    if not isinstance(doc, dict):
        return None, "origin anchor is not an object"
    return doc, None


def note_anchor_state(conn, root, repo=None, backup_sha=None):
    """Raise alarms when the local or origin anchor fails.

    Missing ``ledger.chain.anchor`` counts once the chain has events or
    the backfill event exists. The off-box copy is
    ``origin/<current-branch>:status/ledger_anchor.json`` after
    ``git fetch``. A failed fetch raises ``anchor_unverifiable`` and
    does not treat a local commit as the anchor.
    """
    problems = []
    if not anchor_holds(conn, read_chain_anchor(root)):
        problems.append(
            "ledger.chain.anchor missing or does not match the event "
            "at that position")
    if repo:
        doc, fetch_error = read_committed_status_anchor(repo)
        if fetch_error:
            problems.append("origin anchor unverifiable: %s" % fetch_error)
            if not alarm_open(conn, "anchor_unverifiable"):
                raise_alarm(conn, "anchor_unverifiable", fetch_error)
        elif doc is not None:
            if not anchor_holds(conn, doc):
                problems.append(
                    "origin status/ledger_anchor.json does not match "
                    "the chain")
            elif (doc.get("ledger_end_sha") and backup_sha
                  and doc.get("ledger_end_sha") != backup_sha):
                problems.append(
                    "origin status/ledger_anchor.json end sha does not "
                    "match the ledger backup")
    chain_problems = [item for item in problems
                      if not item.startswith("origin anchor unverifiable")]
    if chain_problems and not alarm_open(conn, "chain_anchor_mismatch"):
        raise_alarm(conn, "chain_anchor_mismatch", "; ".join(chain_problems))
    return problems


def retire_active_fingerprints(conn, project_id, reason):
    """Withdraw active fingerprint rows for one part.

    A new revision must not leave the previous active row in the
    candidate set, or assemble_candidates reports a permanent conflict
    (the 121/161 rebuild case). Other parts are untouched. The UPDATE
    is scoped.
    """
    from .chain import assert_scoped_sql
    reason = (reason or "").strip() or "new revision"
    sql = ("UPDATE fingerprint SET state='withdrawn' WHERE project_id=? "
           "AND state='active'")
    assert_scoped_sql(sql)
    now = _ms()
    with conn:
        cur = conn.execute(sql, (project_id,))
        n = cur.rowcount
        if n:
                _insert_event(conn, None, "fingerprint_retired", {
                    "project_id": project_id, "rows": n, "reason": reason,
                }, now)
    return n


def seal_evidence(conn, project_id, sha):
    """Record the sha256 of chain_evidence.json. A later hand-edit mismatches."""
    if not project_id or not sha or len(str(sha)) < 32:
        raise ValueError("evidence seal needs a project id and a sha256")
    now = _ms()
    with conn:
        conn.execute(
            "INSERT INTO evidence_seal(project_id, sha, updated) VALUES(?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET sha=excluded.sha, "
            "updated=excluded.updated",
            (project_id, sha, now))
    return sha


def evidence_seal(conn, project_id):
    row = conn.execute(
        "SELECT sha FROM evidence_seal WHERE project_id=?",
        (project_id,)).fetchone()
    return row[0] if row else None


def legacy_publisher_busy(lock_path):
    """True when another process holds the 0.5.0 batch ``.publisher.lock``.

    The new per-part ``.locks/<pid>.lock`` does not exclude that flock, so
    the runner must stop instead of publishing beside the old engine.
    """
    import fcntl
    if not lock_path or not os.path.exists(lock_path):
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


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
    payload = {
        "project_id": project_id, "stage": stage,
        "input_sha": input_sha, "rules_sha": rules_sha,
        "kind": kind, "reason": reason,
    }
    with conn:
        event_id, content_hash = _insert_event(conn, None, kind, payload, now)
        conn.execute(
            "INSERT INTO waiver(project_id, stage, input_sha, rules_sha, "
            "kind, reason, created, event_id, content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (project_id, stage, input_sha, rules_sha, kind, reason, now,
             event_id, content_hash))
        cur = conn.execute(
            "UPDATE job SET state=?, updated=? "
            "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=? "
            "AND state='FAIL'",
            (new_state, now, project_id, stage, input_sha, rules_sha))
        if cur.rowcount != 1:
            raise ValueError(
                "waiver applies only to an existing FAIL row for this "
                "revision")
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
        _insert_event(conn, prev_rev, "reinstated",
                      "post-publish rollback (N2)", _ms())
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
        _insert_event(conn, rev, "published", "", _ms())
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
        _insert_event(conn, rev, "abandoned", reason, _ms())


def withdraw(conn, rev, reason):
    with conn:
        conn.execute("UPDATE fingerprint SET state='withdrawn' WHERE rev=?",
                     (rev,))
        conn.execute("UPDATE delivery SET state='superseded' WHERE rev=? AND "
                     "state='delivered'", (rev,))
        _insert_event(conn, rev, "withdrawn", reason, _ms())
        for b in conn.execute(
                "SELECT DISTINCT batch_id FROM delivery WHERE rev=?",
                (rev,)).fetchall():
            _reelect_classes(conn, b[0])
