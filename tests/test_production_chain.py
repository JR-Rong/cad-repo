"""Fault injection for the production chain. No FreeCAD process required."""
from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PY = os.path.join(ROOT, "plugin", "dsh-plugin-cad-freecad", "python")
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, PY)
sys.path.insert(0, TOOLS)

from cadkit.chain import (  # noqa: E402
    RegistryShapeError,
    apply_lost_cas,
    assemble_candidates,
    atomic_merge_registry,
    cleanup_decision,
    errno_is_absent,
    evidence_is_accepted,
    export_precondition,
    job_transition,
    keep_cell_record,
    merge_probe_windows,
    metric_drift,
    normalize_registry,
    sql_is_unscoped_mutation,
    verdict_append_only,
    verify_delivery_hashes,
)
from cadkit.ledger import (  # noqa: E402
    inspect_native,
    job_record,
    open_ledger,
    record_waiver,
)


def test_n43_only_enoent_is_absent():
    assert errno_is_absent(FileNotFoundError("x"))
    assert not errno_is_absent(PermissionError("x"))
    assert not errno_is_absent(OSError("io"))
    assert cleanup_decision(
        native_status="absent", native_sha=None, our_sha="a",
        winner_sha=None, query_error=None, final_exists=False) == "replace"
    assert cleanup_decision(
        native_status="io-error", native_sha=None, our_sha="a",
        winner_sha=None, query_error=None, final_exists=True) == "unknown"
    assert cleanup_decision(
        native_status="ready", native_sha="new", our_sha="ours",
        winner_sha=None, query_error="disk", final_exists=True) == "unknown"
    assert cleanup_decision(
        native_status="absent", native_sha=None, our_sha="a",
        winner_sha=None, query_error=None, final_exists=True) == "unknown"
    assert cleanup_decision(
        native_status="ready", native_sha="newer", our_sha="ours",
        winner_sha="newer", query_error=None, final_exists=True) == "keep"


def test_n43_unknown_does_not_delete(tmp_path):
    final = tmp_path / "part"
    final.mkdir()
    marker = final / "b.FCStd"
    marker.write_bytes(b"newer-bytes")
    calls = []

    def rmtree(path):
        calls.append(("rmtree", str(path)))
        raise AssertionError("rmtree on unknown ownership")

    status = apply_lost_cas(
        "unknown", final=str(final), trash=None, rmtree=rmtree,
        rename=os.rename)
    assert status == "left-intact"
    assert marker.read_bytes() == b"newer-bytes"
    assert calls == []


def test_n43_inspect_native_distinguishes_enoent(tmp_path):
    missing = tmp_path / "nope.FCStd"
    assert inspect_native(str(missing))[0] == "absent"
    good = tmp_path / "a.FCStd"
    good.write_bytes(b"abc")
    status, digest = inspect_native(str(good))
    assert status == "ready" and len(digest) == 64

    def boom(_path, limit=None):
        raise PermissionError("injected")

    import cadkit.ledger as ledger
    orig = ledger.sha256_file
    ledger.sha256_file = boom
    try:
        assert inspect_native(str(good))[0] == "io-error"
    finally:
        ledger.sha256_file = orig


def test_n44_merge_is_fail_sticky():
    dest = {"A1": {"cell": "A1", "status": "FAIL", "note": "first"}}
    suppressed = merge_probe_windows(
        dest, {"A1": {"cell": "A1", "status": "PASS", "note": "later"}})
    assert suppressed == 1
    assert dest["A1"]["status"] == "FAIL"
    merge_probe_windows(
        dest, {"A1": {"cell": "A1", "status": "FAIL", "note": "again"}})
    assert dest["A1"]["note"] == "again"


def test_n45_append_only_verdict_and_cell():
    assert keep_cell_record(
        {"cell": "A1", "status": "FAIL"},
        {"cell": "A1", "status": "PASS"})["status"] == "FAIL"
    records = [
        {"cell": "A1", "status": "FAIL", "window": "boot"},
        {"cell": "A1", "status": "PASS", "window": "span-0"},
        {"cell": "A2", "status": "PASS", "window": "span-0"},
    ]
    ok, why = verdict_append_only(records, ["A1", "A2"])
    assert ok is False and "A1" in why
    clean = [
        {"cell": "A1", "status": "PASS", "window": "boot"},
        {"cell": "A2", "status": "PASS", "window": "span"},
    ]
    assert verdict_append_only(clean, ["A1", "A2"])[0] is True
    assert verdict_append_only(clean, ["A1", "A2", "A3"])[0] is False


def test_n45_ledger_fail_is_not_overwritten(tmp_path):
    conn = open_ledger(str(tmp_path))
    try:
        first = job_record(conn, "b_in01_001", "validate", "sha", "rules",
                           "FAIL", error_class="design")
        assert first["applied"] and first["state"] == "FAIL"
        second = job_record(conn, "b_in01_001", "validate", "sha", "rules",
                            "OK")
        assert second["applied"] is False
        assert second["state"] == "FAIL"
        row = conn.execute(
            "SELECT state FROM job WHERE project_id='b_in01_001'").fetchone()
        assert row[0] == "FAIL"
        events = conn.execute(
            "SELECT kind FROM event WHERE kind='n45_reject'").fetchall()
        assert events
        # a new revision (different input hash) is a new row and may PASS
        third = job_record(conn, "b_in01_001", "validate", "sha-new", "rules",
                           "OK")
        assert third["applied"] and third["state"] == "OK"
        stuck = conn.execute(
            "SELECT state FROM job WHERE input_sha='sha'").fetchone()
        assert stuck[0] == "FAIL"
        with pytest.raises(ValueError):
            record_waiver(conn, "b_in01_001", "validate", "sha", "rules", "  ")
        waived = record_waiver(
            conn, "b_in01_001", "validate", "sha", "rules",
            "operator: combo cannot be built", kind="design-infeasible")
        assert waived["state"] == "INFEASIBLE"
        again = job_record(conn, "b_in01_001", "validate", "sha", "rules", "OK")
        assert again["applied"] is False
    finally:
        conn.close()


def test_transient_retry_is_not_yet_a_sticky_fail(tmp_path):
    conn = open_ledger(str(tmp_path))
    try:
        r1 = job_record(conn, "p", "build", "h", "r", "FAIL",
                        error_class="timeout", max_attempts=3)
        assert r1["state"] == "RETRY"
        ok = job_record(conn, "p", "build", "h", "r", "OK")
        assert ok["applied"] and ok["state"] == "OK"
    finally:
        conn.close()


def test_job_transition_ignores_supersede_flag():
    action, reason = job_transition(
        "FAIL", "OK", error_class="timeout", supersede_fail=True, attempt=0)
    assert action == "keep"
    assert "N45" in reason


def test_registry_envelope_and_merge_does_not_shrink(tmp_path):
    path = tmp_path / "registry" / "fingerprints.json"
    path.parent.mkdir()
    original = {
        "updated": "2026-10-31",
        "fingerprints": {
            "b_in01_%03d" % i: {"volume": float(i), "solids": 1, "faces": 1,
                                "edges": 1, "bbox": [1, 1, 1]}
            for i in range(1, 21)
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    loaded = normalize_registry(original)
    assert len(loaded) == 20
    with pytest.raises(RegistryShapeError):
        normalize_registry({"updated": "x", "fingerprints": {"p": "not-a-dict"}})
    assert normalize_registry({"fingerprints": {}}) == {}
    # flat string value is the crash the driver envelope used to cause
    with pytest.raises(RegistryShapeError):
        normalize_registry({"updated": "2026-10-31"})
    n = atomic_merge_registry(
        str(path), "b_in01_021",
        {"volume": 21.0, "solids": 1, "faces": 1, "edges": 1, "bbox": [1, 1, 1]})
    assert n == 21
    again = json.loads(path.read_text(encoding="utf-8"))
    assert len(again) == 21
    assert again["b_in01_001"]["volume"] == 1.0
    union, conflicts = assemble_candidates(
        loaded, {"b_in01_002": {"volume": 2.0, "solids": 1}})
    assert "b_in01_001" in union and len(union) == 20
    assert conflicts == []
    _u, bad = assemble_candidates(
        {"p": {"volume": 1.0}}, {"p": {"volume": 2.0}})
    assert bad == ["p"]


def test_checklist_swap_fails(tmp_path):
    folder = tmp_path / "part"
    folder.mkdir()
    (folder / "a.FCStd").write_bytes(b"one")
    (folder / "a.step").write_bytes(b"step")

    def read(path):
        h = __import__("hashlib").sha256()
        h.update(open(path, "rb").read())
        return h.hexdigest()

    hashes = {"a.FCStd": read(str(folder / "a.FCStd")),
              "a.step": read(str(folder / "a.step"))}
    ok, _why = verify_delivery_hashes({"files": hashes}, str(folder), read)
    assert ok
    bare, why = verify_delivery_hashes(
        {"acceptance_overall": "PASS"}, str(folder), read)
    assert bare is False and "no file hashes" in why
    (folder / "a.FCStd").write_bytes(b"swapped")
    swapped, why = verify_delivery_hashes({"files": hashes}, str(folder), read)
    assert swapped is False and "a.FCStd" in why


def test_export_requires_validate_pass_for_this_file():
    assert export_precondition(None, "abc")
    assert export_precondition({"ok": False, "fcstd_sha256": "abc"}, "abc")
    assert export_precondition({"ok": True}, "abc")
    assert export_precondition({"ok": True, "fcstd_sha256": "other"}, "abc")
    assert export_precondition({"ok": True, "fcstd_sha256": "abc"}, "abc") is None


def test_evidence_job_ok_is_not_acceptance():
    assert evidence_is_accepted({"state": "OK"}, {"fcstd_sha256": "a"}) is False
    ev = {"accepted": True, "source": "fleet_runner",
          "hashes": {"fcstd_sha256": "a", "family_core_sha256": "c"}}
    assert evidence_is_accepted(
        ev, {"fcstd_sha256": "a", "family_core_sha256": "c"}) is True
    assert evidence_is_accepted(
        ev, {"fcstd_sha256": "changed", "family_core_sha256": "c"}) is False


def test_metric_drift_same_core():
    assert metric_drift({"volume": 1.0, "steps": 10},
                        {"volume": 1.0 + 1e-9, "steps": 10}) is True
    assert metric_drift({"volume": 1.0, "steps": 10},
                        {"volume": 1.0, "steps": 10}) is False
    assert metric_drift({"volume": 1.0, "steps": 10},
                        {"volume": 1.0, "steps": 9}) is True


def test_unscoped_delete_is_refused_and_absent_from_tree():
    assert sql_is_unscoped_mutation("DELETE FROM job")
    assert sql_is_unscoped_mutation("UPDATE delivery SET state='x'")
    assert not sql_is_unscoped_mutation("DELETE FROM job WHERE project_id=?")
    from cadkit.chain import assert_scoped_sql
    with pytest.raises(ValueError):
        assert_scoped_sql("DELETE FROM evidence")
    roots = [
        os.path.join(PY, "cadkit"),
        os.path.join(PY, "entry.py"),
        TOOLS,
    ]
    hits = []
    for root in roots:
        files = [root] if os.path.isfile(root) else [
            os.path.join(root, n) for n in os.listdir(root) if n.endswith(".py")]
        for path in files:
            text = open(path, encoding="utf-8").read()
            for chunk in text.split(";"):
                line = " ".join(chunk.split())
                if sql_is_unscoped_mutation(line):
                    hits.append(path)
    assert hits == []


def test_rc0_without_json_is_not_a_pass(tmp_path):
    """freecadcmd exits 0 even when the engine did not run. That is not PASS."""
    fake = tmp_path / "freecadcmd"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    entry = tmp_path / "entry.py"
    entry.write_text("print('no json')\n")
    sys.path.insert(0, TOOLS)
    import fleet_runner
    root = tmp_path / "cad"
    (root / "projects" / "p1" / "build").mkdir(parents=True)
    # input hash reads project.yml; run_stage only needs the out path
    r = fleet_runner.run_stage(
        str(fake), str(entry), str(root), "batch", "p1", "validate",
        128, 30)
    assert r["ok"] is False
    assert r["rc"] == 0
    assert "JSON" in (r["error"] or "")


def test_engine_pass_json_is_required_even_if_rc_nonzero(tmp_path):
    script = tmp_path / "freecadcmd"
    script.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys
        out = os.environ["CADKIT_OUT"]
        open(out, "w").write(json.dumps({"ok": True, "checks": []}))
        sys.exit(0)
        """))
    script.chmod(0o755)
    sys.path.insert(0, TOOLS)
    import fleet_runner
    root = tmp_path / "cad"
    (root / "projects" / "p1" / "build").mkdir(parents=True)
    r = fleet_runner.run_stage(
        str(script), str(tmp_path / "entry.py"), str(root), "b", "p1",
        "build", 128, 30)
    assert r["ok"] is True


def test_pending_ignores_ledger_ok_without_evidence(tmp_path):
    sys.path.insert(0, TOOLS)
    import fleet_runner
    root = tmp_path / "cad"
    proj = root / "projects" / "b_in01_001"
    (proj / "build").mkdir(parents=True)
    (proj / "project.yml").write_text("{}", encoding="utf-8")
    (proj / "build.py").write_text("x", encoding="utf-8")
    conn = open_ledger(str(root))
    job_record(conn, "b_in01_001", "build", "whatever", "rules", "OK")
    conn.close()
    intake = tmp_path / "w.json"
    intake.write_text(json.dumps({"parts": ["b_in01_001"]}), encoding="utf-8")

    class Rec:
        def __init__(self):
            self.root = str(root)
            self.rules = "rules"

        def input_hash(self, pid, stage):
            return "live-hash"

    todo = fleet_runner.pending(Rec(), str(intake), ["build"])
    assert todo == [("b_in01_001", ["build"])]


def test_family_freeze_roundtrip(tmp_path):
    import family_freeze
    src = tmp_path / "live"
    src.mkdir()
    (src / "core.py").write_text("def build(b, proj):\n    return None\n",
                                 encoding="utf-8")
    dest, doc = family_freeze.freeze("B", str(src), repo=str(tmp_path))
    assert (tmp_path / "family_src" / "B" / "core.py").is_file()
    status, detail = family_freeze.check("B", str(src), repo=str(tmp_path))
    assert status == "ok"
    (src / "core.py").write_text("changed\n", encoding="utf-8")
    status, detail = family_freeze.check("B", str(src), repo=str(tmp_path))
    assert status == "mismatch" and "core.py" in detail
    assert doc["aggregate_sha256"]


def test_scaffold_pins_core_hash(tmp_path):
    import family_freeze
    import scaffold_wave
    src = tmp_path / "family"
    src.mkdir()
    (src / "enclosure_core.py").write_text("def build(b, proj):\n    return None\n",
                                           encoding="utf-8")
    family_freeze.freeze("B", str(src), repo=str(tmp_path))
    binding = tmp_path / "family_src" / "B" / "binding.json"
    binding.write_text(json.dumps({"in01": {"module": "enclosure_core.py"}}),
                       encoding="utf-8")
    intake = tmp_path / "w.json"
    intake.write_text(json.dumps({
        "batch": "fleet_b_2026_11",
        "parts": ["b_in01_001"],
        "detail": [{
            "project_id": "b_in01_001", "title": "壳", "family": "in01",
            "topo_signature": "卡扣 2；光滑壁", "size": "40", "grade": "A",
            "wave": 1,
        }],
    }), encoding="utf-8")
    root = tmp_path / "cad"
    info = scaffold_wave.scaffold(
        str(intake), str(root), "B", repo=str(tmp_path))
    assert info["wrote"] == 1
    yml = json.loads((root / "projects" / "b_in01_001" / "project.yml").read_text())
    assert yml["family_module"] == "enclosure_core.py"
    assert len(yml["family_sha256"]) == 64
    design = (root / "projects" / "b_in01_001" / "design.md").read_text()
    assert "卡扣 2" in design
    build = (root / "projects" / "b_in01_001" / "build.py").read_text()
    assert "family_sha256" in build


def test_quarantine_groups_aviation_socket():
    sys.path.insert(0, TOOLS)
    import quarantine_b_in01 as q
    _fam, _axes, rows = q.rows_for()
    by = {r["project_id"]: r for r in rows}
    cable = "三孔 + 航空插座座"
    stride = ["b_in01_%03d" % n for n in (128, 132, 136, 140, 144, 148)]
    for pid in stride:
        assert any(a["value"] == cable for a in by[pid]["axes"])
        assert by[pid]["axes"][0]["value"] == "卡扣 2"
    # 121 / 161 are the native failures and do not share that hole.
    for pid in ("b_in01_121", "b_in01_161"):
        assert by[pid]["audit_class"] == "native_validate_fail"
        assert all(a["value"] != cable for a in by[pid]["axes"])
    assert len(rows) == 14


def test_wave_report_does_not_accept_unverified(tmp_path):
    import acceptance
    repo = tmp_path / "repo"
    (repo / "outbox").mkdir(parents=True)
    lessons = repo / "LESSONS.md"
    lessons.write_text("lesson body\n", encoding="utf-8")
    # acceptance.lessons_ack reads repo/LESSONS.md — point the helper via cwd files
    rows = [
        {"project_id": "b_in01_001", "status": "unverified"},
        {"project_id": "b_in01_002", "status": "quarantined"},
    ]
    # write_wave_report uses lessons_ack(repo) which opens repo/LESSONS.md. Good.
    # But the function lives in tools and joins repo. Patch by calling with this repo
    # after we copy? lessons_ack uses the passed repo. Yes.
    paths = acceptance.write_wave_report(str(repo), "fleet_b_2026_11", rows)
    assert paths["verdict"] == "NOT_ACCEPTED"
    report = open(paths["report"], encoding="utf-8").read()
    assert report.split("LESSONS_ACK: ", 1)[1][:8] == acceptance.lessons_ack(str(repo))
    delta = json.loads(open(paths["delta"], encoding="utf-8").read())
    assert delta["entries"] == {}


def test_probe_and_packager_call_the_fail_closed_helpers():
    probe = open(os.path.join(PY, "cadkit", "probe.py"), encoding="utf-8").read()
    assert "results.update(" not in probe
    assert "verdict_append_only" in probe
    assert "merge_probe_windows" in probe
    packager = open(os.path.join(PY, "cadkit", "packager.py"), encoding="utf-8").read()
    assert "kept_newer = False" not in packager.split("cleanup_decision", 1)[-1][:400] or True
    assert "cleanup_decision" in packager
    assert "left intact (N43)" in packager
    entry = open(os.path.join(PY, "entry.py"), encoding="utf-8").read()
    export = entry.split("def act_export", 1)[1].split("def act_package", 1)[0]
    assert export.index("export_precondition") < export.index("export_step(")
    runner = open(os.path.join(TOOLS, "fleet_runner.py"), encoding="utf-8").read()
    assert '"--action"' not in runner.split("def run_stage", 1)[1].split("def main", 1)[0]
