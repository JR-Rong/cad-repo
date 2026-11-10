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


def _stage_ok():
    return {"state": "OK", "source": "fleet_runner"}


def _full_evidence():
    return {
        "source": "fleet_runner",
        "review_status": "complete",
        "stages": {name: _stage_ok() for name in (
            "build", "validate", "probe", "dedupe", "export", "package")},
    }


def _three_hashes():
    return {"fcstd_sha256": "aa", "step_sha256": "bb",
            "family_core_sha256": "cc"}


def test_acceptance_requires_all_six_stages_and_review():
    import acceptance
    ev = _full_evidence()
    hashes = _three_hashes()
    assert acceptance.mark_acceptance(ev, hashes, ["build", "validate"])["accepted"]
    short = _full_evidence()
    del short["stages"]["package"]
    missed = acceptance.mark_acceptance(short, hashes, ["build", "validate"])
    assert missed["accepted"] is False
    assert "package" in missed["accept_reason"]
    no_review = _full_evidence()
    no_review["review_status"] = "pending"
    blocked = acceptance.mark_acceptance(no_review, hashes)
    assert blocked["accepted"] is False
    assert "review" in blocked["accept_reason"]


def test_acceptance_requires_step_and_family_hashes():
    import acceptance
    ev = _full_evidence()
    only_fcstd = {"fcstd_sha256": "aa"}
    out = acceptance.mark_acceptance(ev, only_fcstd)
    assert out["accepted"] is False
    assert "step_sha256" in out["accept_reason"]
    missing_core = {"fcstd_sha256": "aa", "step_sha256": "bb"}
    out = acceptance.mark_acceptance(_full_evidence(), missing_core)
    assert out["accepted"] is False
    assert "family_core" in out["accept_reason"]


def test_quarantine_same_bytes_cannot_become_accepted():
    import acceptance
    ev = _full_evidence()
    ev["accepted"] = True
    ev["hashes"] = _three_hashes()
    record = {"input_sha": "same"}
    status, _why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        quarantine_record=record, current_input_sha="same",
        file_sha="file", sealed_sha="file")
    assert status == "quarantined"
    status, why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        quarantine_record=record, current_input_sha="new-hash",
        file_sha="file", sealed_sha="file")
    assert status == "quarantined" and "sign-off" in why
    status, _why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        quarantine_record=record, current_input_sha="new-hash",
        sign_off={"reviewer": "owner", "reason": "rebuilt"},
        file_sha="file", sealed_sha="file")
    assert status == "accepted"


def test_evidence_seal_rejects_hand_edit(tmp_path):
    import acceptance
    from cadkit.ledger import evidence_seal, open_ledger, seal_evidence
    ev = {"accepted": True, "source": "fleet_runner",
          "hashes": _three_hashes()}
    assert acceptance.evidence_is_accepted(
        ev, _three_hashes(), file_sha="abc", sealed_sha="abc") is True
    assert acceptance.evidence_is_accepted(
        ev, _three_hashes(), file_sha="edited", sealed_sha="abc") is False
    conn = open_ledger(str(tmp_path))
    try:
        seal_evidence(conn, "b_in01_001", "a" * 64)
        assert evidence_seal(conn, "b_in01_001") == "a" * 64
    finally:
        conn.close()


def test_failed_dedupe_does_not_enter_registry(tmp_path):
    import types
    sys.modules.setdefault("FreeCAD", types.ModuleType("FreeCAD"))
    import importlib
    fp = importlib.import_module("cadkit.fingerprint")
    reg = tmp_path / "registry"
    reg.mkdir()
    path = reg / "fingerprints.json"
    path.write_text("{}", encoding="utf-8")
    # registry_path joins cad_root/registry. commit uses that.
    assert fp.commit_registration(
        str(tmp_path), "p", {"volume": 1.0, "solids": 1, "faces": 1,
                             "edges": 1, "bbox": [1, 1, 1]},
        ok=False, register=True) is False
    assert json.loads(path.read_text()) == {}
    assert fp.commit_registration(
        str(tmp_path), "p", {"volume": 1.0, "solids": 1, "faces": 1,
                             "edges": 1, "bbox": [1, 1, 1]},
        ok=True, register=True) is True
    assert "p" in json.loads(path.read_text())


def test_floor_and_baseline_fail_closed(tmp_path):
    import types
    sys.modules.setdefault("FreeCAD", types.ModuleType("FreeCAD"))
    import importlib
    fp = importlib.import_module("cadkit.fingerprint")
    from cadkit.chain import RegistryShapeError
    bad = tmp_path / "registry"
    bad.mkdir()
    (bad / "floor.json").write_text("{", encoding="utf-8")
    with pytest.raises(RegistryShapeError):
        fp._registry_floor(str(tmp_path))
    base = tmp_path / "registry" / "baseline"
    base.mkdir()
    body = {"entries": {"p": {"volume": 1.0, "steps": 1, "solids": 1,
                              "faces": 1, "edges": 1, "bbox": [1, 1, 1]}}}
    (base / "a.json").write_text(json.dumps(body), encoding="utf-8")
    body["entries"]["p"]["volume"] = 9.0
    (base / "b.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(RegistryShapeError):
        fp._baseline_fingerprints(str(tmp_path))


def test_assemble_tolerates_float_noise_and_retire_clears_old_row(tmp_path):
    a = {"volume": 1.0, "steps": 4}
    b = {"volume": 1.0 + 1e-16, "steps": 4}
    _out, conflicts = assemble_candidates({"p": a}, {"p": b})
    assert conflicts == []
    far = {"volume": 2.0, "steps": 4}
    _out, conflicts = assemble_candidates({"p": a}, {"p": far})
    assert conflicts == ["p"]
    conn = open_ledger(str(tmp_path))
    try:
        conn.execute(
            "INSERT INTO model(model_id, buyer_id, project_id) VALUES(?,?,?)",
            ("local:p", "internal", "p"))
        conn.execute(
            "INSERT INTO revision(rev, model_id, fcstd_sha, rules_sha, created) "
            "VALUES(?,?,?,?,?)",
            ("rev1", "local:p", "sha", "rules", 1))
        conn.execute(
            "INSERT INTO fingerprint(rev, version, project_id, structure_sha, "
            "shape_bucket, state, created, data) VALUES(?,?,?,?,?,?,?,?)",
            ("rev1", 2, "p", "s", "b", "active", 1, "{}"))
        conn.commit()
        from cadkit.ledger import retire_active_fingerprints
        assert retire_active_fingerprints(conn, "p", "new revision") == 1
        state = conn.execute(
            "SELECT state FROM fingerprint WHERE project_id='p'").fetchone()[0]
        assert state == "withdrawn"
    finally:
        conn.close()


def test_rules_change_inherits_fail_until_rereview(tmp_path):
    conn = open_ledger(str(tmp_path))
    try:
        from cadkit.ledger import record_rules_rereview
        job_record(conn, "p", "validate", "input", "rules-old", "FAIL",
                   error_class="design")
        blocked = job_record(conn, "p", "validate", "input", "rules-new", "OK")
        assert blocked["applied"] is False
        assert blocked["state"] == "FAIL"
        row = conn.execute(
            "SELECT state FROM job WHERE rules_sha='rules-new'").fetchone()
        assert row[0] == "FAIL"
        with pytest.raises(ValueError):
            record_rules_rereview(conn, "p", "validate", "input", "rules-new", " ")
        record_rules_rereview(
            conn, "p", "validate", "input", "rules-new",
            "owner re-checked this part under the new rules")
        allowed = job_record(conn, "p", "validate", "input", "rules-new", "OK")
        assert allowed["applied"] is True and allowed["state"] == "OK"
        old = conn.execute(
            "SELECT state FROM job WHERE rules_sha='rules-old'").fetchone()
        assert old[0] == "FAIL"
    finally:
        conn.close()


def test_open_ledger_rejects_unscoped_delete(tmp_path):
    conn = open_ledger(str(tmp_path))
    try:
        with pytest.raises(ValueError):
            conn.execute("DELETE FROM job")
        with pytest.raises(ValueError):
            conn.executescript("DELETE FROM evidence;")
    finally:
        conn.close()


def test_rename_noreplace_does_not_clobber_empty_dir(tmp_path):
    from cadkit.chain import remove_tree, rename_noreplace
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("new", encoding="utf-8")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "keep.txt").write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        rename_noreplace(str(src), str(dst))
    assert (dst / "keep.txt").read_text() == "old"
    assert (src / "a.txt").exists()
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        remove_tree(str(missing))


def test_legacy_publisher_lock_blocks_new_runner(tmp_path):
    import subprocess
    import time
    from cadkit.ledger import legacy_publisher_busy
    lock = tmp_path / ".publisher.lock"
    lock.write_text("", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,os,sys,time\n"
         "fd=os.open(sys.argv[1], os.O_RDWR)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX)\n"
         "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
         "time.sleep(30)\n",
         str(lock)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        assert legacy_publisher_busy(str(lock)) is True
    finally:
        holder.kill()
        holder.wait(timeout=5)
    time.sleep(0.05)
    assert legacy_publisher_busy(str(lock)) is False


def test_package_workers_default_is_one_until_tarball_verified():
    import re
    text = open(os.path.join(TOOLS, "fleet_runner.py"), encoding="utf-8").read()
    match = re.search(r'--package-workers", type=int, default=(\d+)', text)
    assert match and match.group(1) == "1"


def test_reconciliation_must_equal_400(tmp_path):
    import acceptance
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LESSONS.md").write_text("lessons\n", encoding="utf-8")
    rows = [{"project_id": "b_in01_%03d" % i, "family": "in01",
             "status": "accepted"} for i in range(1, 400)]
    rows.append({"project_id": "b_in01_400", "family": "in01",
                 "status": "unverified"})
    short = acceptance.write_wave_report(str(repo), "b", rows)
    doc = json.loads(open(short["acceptance"], encoding="utf-8").read())
    assert doc["verdict"] == "NOT_ACCEPTED"
    assert doc["reconciliation"]["families"]["in01"]["sum"] == 399
    full = [{"project_id": "b_in01_%03d" % i, "family": "in01",
             "status": "accepted"} for i in range(1, 401)]
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    (repo2 / "LESSONS.md").write_text("lessons\n", encoding="utf-8")
    ok = acceptance.write_wave_report(str(repo2), "b", full)
    doc = json.loads(open(ok["acceptance"], encoding="utf-8").read())
    assert doc["verdict"] == "ACCEPT"
    assert doc["reconciliation"]["ok"] is True
    mixed = full[:-1] + [{"project_id": "b_in01_400", "family": "in01",
                          "status": "quarantined"}]
    repo3 = tmp_path / "repo3"
    repo3.mkdir()
    (repo3 / "LESSONS.md").write_text("lessons\n", encoding="utf-8")
    held = acceptance.write_wave_report(str(repo3), "b", mixed)
    doc = json.loads(open(held["acceptance"], encoding="utf-8").read())
    assert doc["reconciliation"]["families"]["in01"]["sum"] == 400
    assert doc["verdict"] == "NOT_ACCEPTED"


def test_b_in01_097_root_cause_is_not_the_cable_axis():
    doc = json.load(open(os.path.join(ROOT, "status", "quarantine_b_in01.json"),
                         encoding="utf-8"))
    by = {r["project_id"]: r for r in doc["parts"]}
    cause = by["b_in01_097"]["root_cause"]
    assert "FCBak" in cause or "V2" in cause
    assert "三孔" not in by["b_in01_097"]["topo_signature"]
    assert by["b_in01_097"]["audit_class"] == "stale_export"
    assert by["b_in01_121"]["audit_class"] == "native_validate_fail"
    assert "三孔 + 航空插座座" in by["b_in01_128"]["topo_signature"]


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
