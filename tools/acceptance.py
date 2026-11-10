"""Wave acceptance that does not trust a ledger job row.

A part is accepted only when fleet_runner wrote chain_evidence.json and a
fresh read of the FCStd, STEP, family core and fingerprint still matches
that file. Historical ``job.state='OK'`` rows — including the 172 backfilled
b_in01 parts — are unverified until this file exists.
"""
from __future__ import annotations

import hashlib
import json
import os

EVIDENCE_NAME = "chain_evidence.json"
REQUIRED_STAGES = ("build", "validate", "probe", "dedupe", "export", "package")
TRANSIENT_PREFIXES = ("timeout", "nojson", "rc", "spawn", "oserror")


def evidence_is_accepted(ev, disk_hashes):
    """Re-export of the engine predicate (host Python, no FreeCAD)."""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    vendored = os.path.join(os.path.dirname(here),
                            "plugin", "dsh-plugin-cad-freecad", "python")
    if vendored not in sys.path:
        sys.path.insert(0, vendored)
    from cadkit.chain import evidence_is_accepted as _impl
    return _impl(ev, disk_hashes)


def evidence_path(root, pid):
    return os.path.join(root, "projects", pid, "build", EVIDENCE_NAME)


def load_evidence(root, pid):
    path = evidence_path(root, pid)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc if isinstance(doc, dict) else None


def save_evidence(root, pid, doc):
    path = evidence_path(root, pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def disk_hashes(root, pid, family_file=None):
    build = os.path.join(root, "projects", pid, "build")
    return {
        "fcstd_sha256": sha256_file(os.path.join(build, pid + ".FCStd")),
        "step_sha256": sha256_file(os.path.join(build, pid + ".step")),
        "family_core_sha256": sha256_file(family_file) if family_file else None,
    }


def fresh_evidence(pid, family, family_core_sha, rules_sha):
    return {
        "project_id": pid,
        "family": family,
        "source": "fleet_runner",
        "family_core_sha256": family_core_sha,
        "rules_sha": rules_sha,
        "stages": {},
        "accepted": False,
        "accept_reason": "not finished",
        "hashes": {},
        "ledger_job_ok_is_not_acceptance": True,
    }


def classify_failure(error):
    text = (error or "").strip()
    low = text.lower()
    if low.startswith("timeout"):
        return "timeout"
    if "无 cadkit json" in low or "no cadkit" in low:
        return "nojson"
    if low.startswith("rc=") or low.startswith("rc "):
        return "rc"
    if "oserror" in low or "permission" in low:
        return "oserror"
    return "design"


def is_transient(error_class):
    kind = (error_class or "").split(":")[0].lower()
    return kind in TRANSIENT_PREFIXES


def stage_skippable(ev, stage, input_sha, hashes):
    """Skip a stage only when this runner accepted it for these exact bytes."""
    if not isinstance(ev, dict) or ev.get("source") != "fleet_runner":
        return False
    st = (ev.get("stages") or {}).get(stage) or {}
    if st.get("state") != "OK" or st.get("input_sha") != input_sha:
        return False
    for key in ("fcstd_sha256", "step_sha256", "family_core_sha256"):
        if key in st and st[key] != (hashes or {}).get(key):
            return False
    return True


def attempts_of(ev, stage):
    st = ((ev or {}).get("stages") or {}).get(stage) or {}
    try:
        return int(st.get("attempt") or 0)
    except (TypeError, ValueError):
        return 0


def note_stage(ev, stage, *, ok, input_sha, error_class, hashes, fingerprint=None,
               max_attempts=3, drift=False):
    """Record one stage attempt. A design FAIL sticks for this input hash.

    Returns the evidence dict. ``accepted`` is recomputed by
    ``mark_acceptance`` afterwards; this function never sets it true.
    """
    stages = ev.setdefault("stages", {})
    prev = stages.get(stage) or {}
    attempt = attempts_of(ev, stage) + 1
    state = "OK" if ok else "FAIL"
    if (prev.get("state") in ("FAIL", "WAIVED", "INFEASIBLE")
            and prev.get("input_sha") == input_sha and state == "OK"):
        # Same revision: a later PASS does not erase the FAIL. A new
        # input hash is a new revision and is recorded as its own row.
        state = "FAIL"
        error_class = "n45_sticky"
        ev["n45_conflict"] = stage
    elif (not ok and is_transient(error_class)
          and attempt < max_attempts and prev.get("state") != "FAIL"):
        state = "RETRY"
    row = {
        "state": state,
        "input_sha": input_sha,
        "attempt": attempt,
        "error_class": error_class,
        "source": "fleet_runner",
    }
    for key in ("fcstd_sha256", "step_sha256", "family_core_sha256"):
        if hashes and hashes.get(key):
            row[key] = hashes[key]
    if fingerprint is not None:
        row["fingerprint"] = fingerprint
        if prev.get("fingerprint") and prev.get("family_core_sha256") == row.get(
                "family_core_sha256") and drift:
            row["recompute_drift"] = True
            ev["recompute_drift"] = True
    stages[stage] = row
    ev["accepted"] = False
    return ev


def mark_acceptance(ev, hashes, stages):
    """Set accepted only when every requested stage is OK and hashes match.

    Drift, an N45 conflict, or a missing hash clears acceptance even if
    every stage row says OK.
    """
    if ev.get("n45_conflict") or ev.get("recompute_drift"):
        ev["accepted"] = False
        ev["accept_reason"] = "n45 or recompute drift"
        ev["hashes"] = {}
        return ev
    for stage in stages:
        st = (ev.get("stages") or {}).get(stage) or {}
        if st.get("state") != "OK" or st.get("source") != "fleet_runner":
            ev["accepted"] = False
            ev["accept_reason"] = "stage %s not re-verified OK" % stage
            ev["hashes"] = {}
            return ev
    bound = {}
    for key, val in (hashes or {}).items():
        if val:
            bound[key] = val
    if "fcstd_sha256" not in bound:
        ev["accepted"] = False
        ev["accept_reason"] = "FCStd hash missing"
        ev["hashes"] = {}
        return ev
    ev["hashes"] = bound
    ev["accepted"] = True
    ev["accept_reason"] = "hashes re-read"
    ev["source"] = "fleet_runner"
    return ev


def family_key(pid):
    """b_in01_082 -> in01. Returns '' when the id is not machine_family_index."""
    parts = (pid or "").split("_")
    if len(parts) < 3:
        return ""
    return parts[1]


def lessons_ack(repo):
    path = os.path.join(repo, "LESSONS.md")
    text = open(path, encoding="utf-8").read()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def write_wave_report(repo, batch, part_rows, *, intake=None, root=None,
                      extra=None):
    """Write outbox/<batch>/{REPORT.md,ACCEPTANCE.json,fingerprints.delta.json}.

    ``part_rows`` is a list of dicts with project_id, status, detail.
    Status ``accepted`` is the only value counted as accepted. Ledger job
    OK is reported as unverified when that is the status the caller passed.
    The delta file contains fingerprints only for accepted parts, so a
    later merge_check cannot treat unverified geometry as new uniqueness
    evidence.
    """
    out = os.path.join(repo, "outbox", batch)
    os.makedirs(out, exist_ok=True)
    accepted = [r for r in part_rows if r.get("status") == "accepted"]
    ack = lessons_ack(repo)
    summary = {
        "batch": batch,
        "verdict": "ACCEPT" if part_rows and len(accepted) == len(part_rows) else "NOT_ACCEPTED",
        "accepted": len(accepted),
        "total": len(part_rows),
        "lessons_ack": ack,
        "ledger_job_ok_is_not_acceptance": True,
        "parts": part_rows,
        "intake": intake,
        "cad_root": root,
    }
    if extra:
        summary.update(extra)
    acc_path = os.path.join(out, "ACCEPTANCE.json")
    with open(acc_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    entries = {}
    for row in accepted:
        fp = row.get("fingerprint")
        if isinstance(fp, dict) and "volume" in fp:
            entries[row["project_id"]] = fp
    delta_path = os.path.join(out, "fingerprints.delta.json")
    with open(delta_path, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=1)
    lines = [
        "# %s wave acceptance" % batch,
        "",
        "LESSONS_ACK: %s" % ack,
        "",
        "VERDICT: %s" % summary["verdict"],
        "accepted: %d / %d" % (len(accepted), len(part_rows)),
        "",
        "ledger job=OK is not acceptance. Parts without a fleet_runner",
        "chain_evidence.json whose hashes re-read clean are unverified,",
        "including any historical backfill.",
        "",
    ]
    counts = {}
    for row in part_rows:
        counts[row.get("status") or "?"] = counts.get(row.get("status") or "?", 0) + 1
    for key in sorted(counts):
        lines.append("- %s: %d" % (key, counts[key]))
    lines.append("")
    report_path = os.path.join(out, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"report": report_path, "acceptance": acc_path, "delta": delta_path,
            "verdict": summary["verdict"], "lessons_ack": ack}
