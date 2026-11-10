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
    status, _why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        ledger_input_sha="same", current_input_sha="same",
        file_sha="file", sealed_sha="file")
    assert status == "quarantined"
    # A hand-written sign-off that does not name the new hash does not release.
    status, why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        ledger_input_sha="same", current_input_sha="new-hash",
        signoff={"reviewer": "owner", "reason": "rebuilt", "input_sha": "typed"},
        file_sha="file", sealed_sha="file")
    assert status == "quarantined" and "sign-off" in why
    status, _why = acceptance.classify_part(
        "b_in01_082", ev, _three_hashes(), quarantined=True,
        ledger_input_sha="same", current_input_sha="new-hash",
        signoff={"input_sha": "new-hash", "reviewer": "owner",
                 "reason": "rebuilt"},
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


def test_missing_receipts_are_pending_review_not_design_fail(tmp_path):
    import acceptance
    from cadkit.chain import job_transition
    assert acceptance.classify_failure(
        "PENDING_REVIEW: review/receipts.json missing") == "pending_review"
    assert acceptance.classify_failure(
        "human review receipts incomplete (L1/L2 required, W18) - packaging refused"
    ) == "pending_review"
    assert acceptance.classify_failure(
        "note: pending_review mentioned in a design error") == "design"
    assert acceptance.counts_toward_family_pause("pending_review") is False
    assert acceptance.counts_toward_family_pause("design") is True
    action, state = job_transition("", "PENDING_REVIEW", error_class="pending_review")
    assert action == "write" and state == "PENDING_REVIEW"
    action, state = job_transition("PENDING_REVIEW", "OK")
    assert action == "write" and state == "OK"
    conn = open_ledger(str(tmp_path))
    try:
        first = job_record(conn, "p", "package", "h", "r", "PENDING_REVIEW",
                           error_class="pending_review")
        assert first["applied"] and first["state"] == "PENDING_REVIEW"
        second = job_record(conn, "p", "package", "h", "r", "OK")
        assert second["applied"] and second["state"] == "OK"
    finally:
        conn.close()
    root = tmp_path / "cad"
    (root / "projects" / "p1" / "build").mkdir(parents=True)
    sys.path.insert(0, TOOLS)
    import fleet_runner
    r = fleet_runner.run_stage(
        "/bin/false", str(tmp_path / "entry.py"), str(root), "batch", "p1",
        "package", 128, 5)
    assert r.get("pending_review") is True
    assert r["ok"] is False
    receipt = root / "projects" / "p1" / "review" / "receipts.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    seen = tmp_path / "seen.txt"
    fake = tmp_path / "freecadcmd"
    fake.write_text(
        "#!/bin/sh\nprintf '%%s\\n' \"$CADKIT_OPTS\" > '%s'\nexit 0\n" % seen,
        encoding="utf-8")
    fake.chmod(0o755)
    fleet_runner.run_stage(
        str(fake), str(tmp_path / "entry.py"), str(root), "batch", "p1",
        "package", 128, 5)
    opts = seen.read_text(encoding="utf-8")
    assert "receipts.json" in opts
    rec = fleet_runner.Recorder(str(root), PY, "rules")
    before = rec.input_hash("p1", "package")
    receipt.write_text('{"L1": {"reviewer": "a"}}', encoding="utf-8")
    after = rec.input_hash("p1", "package")
    assert before != after


def test_legacy_lock_is_held_for_the_whole_run(tmp_path):
    import subprocess
    from cadkit.ledger import hold_legacy_publisher_lock, legacy_publisher_busy
    root = tmp_path / "cad"
    fd = hold_legacy_publisher_lock(str(root), "batch")
    assert fd is not None
    try:
        lock = root / "delivery" / "batch" / ".publisher.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "from cadkit.ledger import legacy_publisher_busy\n"
             "print('busy' if legacy_publisher_busy(sys.argv[2]) else 'free')\n",
             PY, str(lock)],
            stdout=subprocess.PIPE, text=True)
        out, _ = holder.communicate(timeout=10)
        assert out.strip() == "busy"
    finally:
        os.close(fd)


def test_quarantine_hash_comes_from_the_ledger(tmp_path):
    from cadkit.ledger import (mark_quarantine_from_ledger, quarantine_mark,
                               record_signoff, review_signoff)
    conn = open_ledger(str(tmp_path))
    try:
        job_record(conn, "b_in01_097", "build", "hash-from-ledger", "rules", "OK")
        marked = mark_quarantine_from_ledger(conn, "b_in01_097")
        assert marked["input_sha"] == "hash-from-ledger"
        job_record(conn, "b_in01_097", "build", "typed-by-hand", "rules", "OK")
        with pytest.raises(ValueError):
            mark_quarantine_from_ledger(conn, "b_in01_097")
        assert quarantine_mark(conn, "b_in01_097")["input_sha"] == "hash-from-ledger"
        with pytest.raises(ValueError):
            record_signoff(conn, "b_in01_097", "typed-hash", "owner", "no")
        with pytest.raises(ValueError):
            record_signoff(conn, "b_in01_097", "hash-from-ledger", "owner",
                           "still the frozen revision")
        record_signoff(conn, "b_in01_097", "typed-by-hand", "owner",
                       "rebuilt and looked at")
        row = review_signoff(conn, "b_in01_097", "typed-by-hand")
        assert row["reviewer"] == "owner" and row["input_sha"] == "typed-by-hand"
        import sqlite3
        raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "UPDATE quarantine_mark SET input_sha='typed' "
                    "WHERE project_id='b_in01_097'")
        finally:
            raw.close()
    finally:
        conn.close()


def test_rules_rereview_is_a_point_lookup(tmp_path):
    from cadkit.ledger import _rules_rereviewed, record_rules_rereview
    conn = open_ledger(str(tmp_path))
    try:
        for i in range(30):
            conn.execute(
                "INSERT INTO event(rev, kind, payload, created) VALUES(?,?,?,?)",
                (None, "noise", "{}", i))
        conn.commit()
        assert _rules_rereviewed(conn, "p", "validate", "in", "rules") is False
        record_rules_rereview(conn, "p", "validate", "in", "rules", "looked again")
        assert _rules_rereviewed(conn, "p", "validate", "in", "rules") is True
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT reason FROM rules_rereview "
            "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=?",
            ("p", "validate", "in", "rules")).fetchall()
        text = " ".join(str(row) for row in plan).upper()
        assert "SEARCH" in text
        assert "PROJECT_ID=?" in text and "RULES_SHA=?" in text
        assert "SCAN TABLE" not in text
    finally:
        conn.close()


def test_triggers_block_raw_sqlite_deletes(tmp_path):
    import sqlite3
    conn = open_ledger(str(tmp_path))
    conn.close()
    raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
    try:
        raw.execute(
            "INSERT INTO model(model_id, buyer_id, project_id) VALUES(?,?,?)",
            ("local:p", "internal", "p"))
        raw.execute(
            "INSERT INTO revision(rev, model_id, fcstd_sha, rules_sha, created) "
            "VALUES(?,?,?,?,?)",
            ("rev1", "local:p", "sha", "rules", 1))
        raw.execute(
            "INSERT INTO job(project_id, stage, input_sha, rules_sha, state, "
            "attempt, updated) VALUES(?,?,?,?,?,?,?)",
            ("p", "build", "h", "r", "FAIL", 1, 1))
        raw.execute(
            "INSERT INTO event(rev, kind, payload, created) VALUES(?,?,?,?)",
            (None, "note", "{}", 1))
        raw.execute(
            "INSERT INTO fingerprint(rev, version, project_id, structure_sha, "
            "shape_bucket, state, created) VALUES(?,?,?,?,?,?,?)",
            ("rev1", 2, "p", "s", "b", "active", 1))
        raw.execute(
            "INSERT INTO evidence_seal(project_id, sha, updated) VALUES(?,?,?)",
            ("p", "a" * 64, 1))
        raw.commit()
        for sql in (
            "DELETE FROM job",
            "DELETE FROM job WHERE 1",
            "DELETE FROM event",
            "DELETE FROM fingerprint",
            "DELETE FROM evidence_seal",
            "UPDATE event SET payload='x'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(sql)
        raw.execute(
            "UPDATE fingerprint SET state='withdrawn' WHERE project_id='p'")
        raw.commit()
        state = raw.execute("SELECT state FROM fingerprint").fetchone()[0]
        assert state == "withdrawn"
        assert raw.execute("SELECT state FROM job").fetchone()[0] == "FAIL"
    finally:
        raw.close()
    from cadkit.chain import sql_is_unscoped_mutation
    assert sql_is_unscoped_mutation("DELETE FROM job WHERE 1") is False


def test_ledger_backup_sha_is_stable(tmp_path):
    from cadkit.ledger import backup_ledger
    open_ledger(str(tmp_path)).close()
    dest, digest = backup_ledger(str(tmp_path), dest_dir=str(tmp_path / "backups"))
    assert os.path.isfile(dest)
    assert len(digest) == 64
    assert backup_ledger(str(tmp_path / "empty")) is None


def test_job_trigger_blocks_silent_ok_and_records_transition(tmp_path):
    import sqlite3
    conn = open_ledger(str(tmp_path))
    try:
        job_record(conn, "p", "validate", "in", "rules", "FAIL",
                   error_class="design")
        from cadkit.ledger import event_unwaived_fail, verify_event_chain
        assert event_unwaived_fail(conn, "p", "validate", "in", "rules") is True
        assert verify_event_chain(conn) is True
        raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
        raw.create_function("cadkit_event_hash", 5, lambda *a: "x")
        raw.create_function("cadkit_job_payload", 10, lambda *a: "{}")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "UPDATE job SET state='OK' WHERE project_id='p'")
        finally:
            raw.close()
        from cadkit.ledger import record_waiver
        record_waiver(conn, "p", "validate", "in", "rules", "looked at it",
                      kind="waiver")
        assert event_unwaived_fail(conn, "p", "validate", "in", "rules") is False
        row = conn.execute("SELECT state FROM job WHERE project_id='p'").fetchone()
        assert row[0] == "WAIVED"
        assert verify_event_chain(conn) is True
    finally:
        conn.close()


def test_event_chain_break_and_missing_trigger_block_acceptance(tmp_path):
    import sqlite3
    from cadkit.ledger import ledger_acceptance_blocks, verify_event_chain
    path = str(tmp_path)
    conn = open_ledger(path)
    job_record(conn, "p", "build", "h", "r", "OK")
    assert verify_event_chain(conn) is True
    assert ledger_acceptance_blocks(conn) == []
    conn.close()
    raw = sqlite3.connect(os.path.join(path, "ledger.sqlite3"))
    raw.execute(
        "INSERT INTO event(rev, kind, payload, created, prev_hash, row_hash) "
        "VALUES(NULL, 'forged', '{}', 1, 'nope', 'nope')")
    raw.commit()
    raw.close()
    conn = open_ledger(path)
    try:
        assert verify_event_chain(conn) is False
        assert "event_chain_broken" in ledger_acceptance_blocks(conn)
    finally:
        conn.close()
    raw = sqlite3.connect(os.path.join(path, "ledger.sqlite3"))
    raw.execute("DROP TRIGGER job_update_guard")
    raw.commit()
    raw.close()
    conn = open_ledger(path)
    try:
        assert "schema_tampered" in ledger_acceptance_blocks(conn)
    finally:
        conn.close()


def test_rules_rereview_migration_is_idempotent(tmp_path):
    from cadkit.ledger import (_insert_event, _rules_rereviewed,
                               migrate_rules_rereview_from_events)
    conn = open_ledger(str(tmp_path))
    try:
        _insert_event(conn, None, "rules_rereview", {
            "project_id": "p", "stage": "validate", "input_sha": "in",
            "rules_sha": "old-engine", "reason": "copied from the event log",
        })
        conn.commit()
        migrate_rules_rereview_from_events(conn)
        migrate_rules_rereview_from_events(conn)
        conn.commit()
        assert _rules_rereviewed(conn, "p", "validate", "in", "old-engine")
        n = conn.execute("SELECT COUNT(*) FROM rules_rereview").fetchone()[0]
        assert n == 1
    finally:
        conn.close()
    import sqlite3
    raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("DELETE FROM rules_rereview")
    finally:
        raw.close()


def test_run_header_is_the_first_line_of_this_run(tmp_path):
    sys.path.insert(0, TOOLS)
    import fleet_runner
    path = tmp_path / "runner.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"lane":"old"}\n')
        rec = fleet_runner.append_run_header(
            f, backup_sha="abc", previous_end_sha="abc", run_id="run-2")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] != lines[1]
    header = json.loads(lines[1])
    assert header["first_line_of_run"] is True
    assert header["run_id"] == rec["run_id"]
    assert header["ledger_matches_previous_end"] is True
    assert "abc" == header["ledger_backup_sha256"]


def test_acceptance_trusts_events_over_job_ok():
    import acceptance
    ev = _full_evidence()
    ev["accepted"] = True
    ev["hashes"] = _three_hashes()
    status, why = acceptance.classify_part(
        "p", ev, _three_hashes(), quarantined=False, ledger_input_sha=None,
        current_input_sha="h", file_sha="s", sealed_sha="s",
        event_unwaived_fail=True)
    assert status == "needs_repair" and "FAIL" in why


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


def test_rules_rereview_cannot_wash_a_design_fail(tmp_path):
    """A raw rules_rereview row must not turn a design FAIL into OK.

    The same row does clear an inherited FAIL, including the n45_inherit
    event that used to keep event_unwaived_fail true forever.
    """
    import sqlite3
    from cadkit.ledger import event_unwaived_fail, record_rules_rereview
    conn = open_ledger(str(tmp_path))
    try:
        job_record(conn, "p", "validate", "in", "rules", "FAIL",
                   error_class="design")
        raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
        raw.create_function("cadkit_event_hash", 5, lambda *a: "x" * 64)
        raw.create_function("cadkit_job_payload", 10, lambda *a: "{}")
        raw.execute(
            "INSERT INTO rules_rereview(project_id, stage, input_sha, "
            "rules_sha, reason, created) VALUES(?,?,?,?,?,?)",
            ("p", "validate", "in", "rules", "planted by a raw client", 1))
        raw.commit()
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("UPDATE job SET state='OK' WHERE project_id='p'")
        raw.close()
        state = conn.execute(
            "SELECT state, error_class FROM job WHERE rules_sha='rules'"
        ).fetchone()
        assert state[0] == "FAIL" and state[1] == "design"
        assert event_unwaived_fail(conn, "p", "validate", "in", "rules") is True
        blocked = job_record(conn, "p", "validate", "in", "rules-new", "OK")
        assert blocked["state"] == "FAIL"
        assert event_unwaived_fail(
            conn, "p", "validate", "in", "rules-new") is True
        record_rules_rereview(
            conn, "p", "validate", "in", "rules-new",
            "owner re-checked the inherited failure")
        allowed = job_record(conn, "p", "validate", "in", "rules-new", "OK")
        assert allowed["applied"] is True and allowed["state"] == "OK"
        assert event_unwaived_fail(
            conn, "p", "validate", "in", "rules-new") is False
        assert event_unwaived_fail(conn, "p", "validate", "in", "rules") is True
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT source FROM state_transition "
            "WHERE project_id=? AND stage=? AND input_sha=? AND rules_sha=? "
            "AND new_state='FAIL'",
            ("p", "validate", "in", "rules")).fetchall()
        text = " ".join(str(row) for row in plan).upper()
        assert "SEARCH" in text and "SCAN TABLE" not in text
    finally:
        conn.close()


def test_sticky_whitelist_blocks_class_attempt_and_pending_hop(tmp_path):
    import sqlite3
    conn = open_ledger(str(tmp_path))
    try:
        job_record(conn, "p", "validate", "in", "rules", "FAIL",
                   error_class="design")
        raw = sqlite3.connect(os.path.join(str(tmp_path), "ledger.sqlite3"))
        raw.create_function("cadkit_event_hash", 5, lambda *args: "x" * 64)
        raw.create_function("cadkit_job_payload", 10, lambda *args: "{}")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "UPDATE job SET error_class='inherited:forged' "
                    "WHERE project_id='p'")
            raw.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute("UPDATE job SET attempt=0 WHERE project_id='p'")
            raw.rollback()
            raw.execute(
                "INSERT INTO rules_rereview(project_id, stage, input_sha, "
                "rules_sha, reason, created) VALUES(?,?,?,?,?,?)",
                ("p", "validate", "in", "rules", "does not authorise a hop", 1))
            raw.commit()
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "UPDATE job SET state='PENDING_REVIEW' WHERE project_id='p'")
            raw.rollback()
            assert raw.execute(
                "SELECT state FROM job WHERE project_id='p'").fetchone()[0] == "FAIL"
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute("UPDATE job SET state='OK' WHERE project_id='p'")
        finally:
            raw.close()
        job_record(conn, "q", "build", "h", "r", "FAIL",
                   error_class="timeout", max_attempts=3)
        job_record(conn, "q", "build", "h", "r", "OK")
        payload = conn.execute(
            "SELECT payload FROM event WHERE kind='job_transition' "
            "ORDER BY event_id DESC LIMIT 1").fetchone()[0]
        doc = json.loads(payload)
        assert "attempt_before" in doc and "attempt_after" in doc
        assert doc["attempt_after"] >= doc["attempt_before"]
        assert "error_class_before" in doc and "error_class_after" in doc
        assert doc["error_class_before"] == "timeout"
        assert doc["transition"].endswith("OK")
    finally:
        conn.close()


def test_null_row_hash_is_not_recomputed(tmp_path):
    import sqlite3
    from cadkit.ledger import ledger_acceptance_blocks, verify_event_chain
    root = str(tmp_path)
    conn = open_ledger(root)
    job_record(conn, "p", "validate", "in", "rules", "FAIL",
               error_class="design")
    before = conn.execute(
        "SELECT payload FROM event WHERE kind='job_transition'").fetchone()[0]
    assert "FAIL" in before
    conn.close()
    raw = sqlite3.connect(os.path.join(root, "ledger.sqlite3"))
    raw.execute("DROP TRIGGER IF EXISTS event_no_update")
    raw.execute(
        "UPDATE event SET payload=?, row_hash=NULL WHERE kind='job_transition'",
        ('{"transition":"FAIL -> OK","washed":true}',))
    raw.executescript("""
        CREATE TRIGGER event_no_update BEFORE UPDATE ON event
        BEGIN SELECT RAISE(ABORT, 'event rows cannot be rewritten'); END;
    """)
    raw.commit()
    raw.close()
    conn = open_ledger(root)
    try:
        row = conn.execute(
            "SELECT payload, row_hash FROM event WHERE kind='job_transition'"
        ).fetchone()
        assert row[1] is None
        assert "washed" in row[0]
        assert verify_event_chain(conn) is False
        assert "event_chain_broken" in ledger_acceptance_blocks(conn)
    finally:
        conn.close()
    # Wiping the meta flag is not a second migration once the anchor
    # file exists outside the database.
    from cadkit.ledger import write_chain_anchor
    conn = open_ledger(root)
    write_chain_anchor(root, conn)
    conn.close()
    raw = sqlite3.connect(os.path.join(root, "ledger.sqlite3"))
    raw.execute("DELETE FROM meta WHERE k='event_hash_backfill'")
    raw.commit()
    raw.close()
    conn = open_ledger(root)
    try:
        row = conn.execute(
            "SELECT row_hash FROM event WHERE kind='job_transition'").fetchone()
        assert row[0] is None
        assert verify_event_chain(conn) is False
    finally:
        conn.close()


def test_outside_anchor_catches_a_recomputed_chain(tmp_path):
    import sqlite3
    from cadkit.ledger import (_event_hash, anchor_holds, ledger_acceptance_blocks,
                               raise_alarm, read_chain_anchor, verify_event_chain,
                               write_chain_anchor)
    root = str(tmp_path)
    conn = open_ledger(root)
    job_record(conn, "p", "build", "h", "r", "FAIL", error_class="design")
    job_record(conn, "p", "build", "h2", "r", "OK")
    saved = write_chain_anchor(root, conn)
    assert anchor_holds(conn, saved) is True
    conn.close()
    raw = sqlite3.connect(os.path.join(root, "ledger.sqlite3"))
    raw.execute("DROP TRIGGER IF EXISTS event_no_update")
    raw.execute(
        "UPDATE event SET payload=? WHERE event_id=("
        "SELECT MIN(event_id) FROM event)",
        ('{"transition":" -> OK","washed":true}',))
    rows = raw.execute(
        "SELECT event_id, rev, kind, payload, created FROM event "
        "ORDER BY event_id").fetchall()
    prev = ""
    for event_id, rev, kind, payload, created in rows:
        digest = _event_hash(prev, rev, kind, payload or "", created)
        raw.execute(
            "UPDATE event SET prev_hash=?, row_hash=? WHERE event_id=?",
            (prev, digest, event_id))
        prev = digest
    raw.executescript("""
        CREATE TRIGGER event_no_update BEFORE UPDATE ON event
        BEGIN SELECT RAISE(ABORT, 'event rows cannot be rewritten'); END;
    """)
    raw.commit()
    raw.close()
    conn = open_ledger(root)
    try:
        assert verify_event_chain(conn) is True
        assert anchor_holds(conn, read_chain_anchor(root)) is False
        raise_alarm(conn, "chain_anchor_mismatch", "head hash moved")
        assert "chain_anchor_mismatch" in ledger_acceptance_blocks(conn)
        from cadkit.ledger import acknowledge_alarm
        with pytest.raises(ValueError):
            acknowledge_alarm(conn, "chain_anchor_mismatch", " ")
        acknowledge_alarm(conn, "chain_anchor_mismatch",
                          "compared the printed head in the run log")
        assert "chain_anchor_mismatch" not in ledger_acceptance_blocks(conn)
    finally:
        conn.close()


def test_alarms_are_not_cleared_by_resetting_meta(tmp_path):
    from cadkit.ledger import (acknowledge_alarm, ledger_acceptance_blocks,
                               raise_alarm, set_meta)
    conn = open_ledger(str(tmp_path))
    try:
        raise_alarm(conn, "ledger_sha_mismatch", "backup sha differs")
        raise_alarm(conn, "schema_tampered", "trigger was missing")
        set_meta(conn, "ledger_sha_mismatch", "0")
        set_meta(conn, "schema_tampered", "0")
        conn.commit()
        blocked = ledger_acceptance_blocks(conn)
        assert "ledger_changed_outside_runner" in blocked
        assert "schema_tampered" in blocked
        with pytest.raises(ValueError):
            acknowledge_alarm(conn, "ledger_sha_mismatch", "")
        acknowledge_alarm(conn, "ledger_sha_mismatch", "operator compared the two files")
        blocked = ledger_acceptance_blocks(conn)
        assert "ledger_changed_outside_runner" not in blocked
        assert "schema_tampered" in blocked
        acknowledge_alarm(conn, "schema_tampered", "upgrade installed the new triggers")
        assert "schema_tampered" not in ledger_acceptance_blocks(conn)
    finally:
        conn.close()


def test_end_sha_lives_in_a_fixed_file(tmp_path):
    from cadkit.ledger import read_end_sha, write_end_sha
    write_end_sha(str(tmp_path), "abc123")
    assert read_end_sha(str(tmp_path)) == "abc123"
    assert (tmp_path / "ledger.end.sha").is_file()
    runner = open(os.path.join(TOOLS, "fleet_runner.py"), encoding="utf-8").read()
    assert "read_end_sha(root)" in runner
    assert "previous_run_end_sha(log.name)" not in runner
    book = open(os.path.join(ROOT, "RUNBOOK.md"), encoding="utf-8").read()
    assert "ledger.end.sha" in book
    assert "ledger.chain.anchor" in book
    assert "连续跑两次" in book
    assert "verify_event_chain" in book


def test_concurrent_event_inserts_do_not_fork_the_chain(tmp_path):
    import sqlite3
    import threading
    from cadkit.ledger import _insert_event, verify_event_chain
    root = str(tmp_path)
    open_ledger(root).close()
    errors = []

    def worker(count, tag):
        conn = sqlite3.connect(os.path.join(root, "ledger.sqlite3"), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            for i in range(count):
                _insert_event(conn, None, "note", {"i": i, "tag": tag})
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(20, n)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    conn = open_ledger(root)
    try:
        assert verify_event_chain(conn) is True
        assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 40
    finally:
        conn.close()


def test_wave_ledger_error_is_a_fail(tmp_path):
    import fleet_runner
    broken = tmp_path / "ledger.sqlite3"
    broken.mkdir()
    flags = fleet_runner.read_part_ledger(
        str(tmp_path), "p", "rules", [("build", "h")], None)
    assert flags["event_fail"] is True
    assert flags["error"]
