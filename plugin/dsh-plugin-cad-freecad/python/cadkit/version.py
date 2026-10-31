"""Immutable evidence versioning (workflow-audit W17, 2026-10-30).

Every gate report (validate / probe / export / package) is stamped with
rules_sha = SHA-256 over the sorted bytes of the whole cadkit engine plus
entry.py. A report produced by different code is NOT the same evidence:
the packager rejects stale probe/export reports instead of accepting them
forever because a filename happened to match.
"""
import hashlib
import json
import os
import subprocess

_CACHE = {}


def _pkg_dir():
    return os.path.dirname(os.path.abspath(__file__))


def rules_sha():
    if "rules" in _CACHE:
        return _CACHE["rules"]
    h = hashlib.sha256()
    d = _pkg_dir()
    files = sorted(f for f in os.listdir(d) if f.endswith(".py"))
    entry = os.path.join(os.path.dirname(d), "entry.py")
    for name in files:
        p = os.path.join(d, name)
        h.update(name.encode())
        with open(p, "rb") as f:
            h.update(f.read())
    if os.path.exists(entry):
        h.update(b"entry.py")
        with open(entry, "rb") as f:
            h.update(f.read())
    _CACHE["rules"] = h.hexdigest()[:16]
    return _CACHE["rules"]


def stamp(extra=None):
    """Common report header block."""
    import FreeCAD as App
    st = {
        "rules_sha": rules_sha(),
        "freecad": App.Version()[0:4] if hasattr(App, "Version") else "?",
        "ts": int(__import__("time").time()),
    }
    if extra:
        st.update(extra)
    return st


def fcstd_sha(path):
    from .ledger import sha256_file
    return sha256_file(path)


def runtime_sha():
    """Best-effort OCC/Python runtime identity (stable per machine)."""
    import sys
    import FreeCAD as App
    try:
        oc = App.Version()[4] if len(App.Version()) > 4 else "?"
    except Exception:
        oc = "?"
    key = "py%s:oc%s" % (sys.version.split()[0], oc)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def evidence_issues(name, rep, rules_sha, strict):
    """W17 evidence-versioning rule (pure: importable without FreeCAD)."""
    if rep is None:
        return ["%s report missing" % name]
    if rep.get("error"):
        return ["%s report unparseable" % name]
    got = rep.get("rules_sha") or (rep.get("engine") or {}).get("rules_sha")
    if got is None:
        if strict:
            return ["%s report has no rules_sha (legacy) - re-run %s under "
                    "the current engine (W17)" % (name, name)]
        return []
    if got != rules_sha:
        msg = ("%s report was produced by rules %s, current engine is %s "
               "(W17)" % (name, got, rules_sha))
        return [msg + " - re-run required (strict)"] if strict else []
    return []


def adjudicate_review(review, pid, contract, fcstd_sha, render_digests,
                      family_sha, require_review, manifest_sha=None):
    """Review-receipt adjudication (pure, unit-testable - R3 recheck).

    A receipt certifies THIS project's design: identity, binding hash and
    (for L2) the exact render digests the reviewer saw are all verified.
    Returns per-level info dict; caller derives review_status from it."""
    rinfo = {}
    for lvl in ("L1", "L2", "L3"):
        e = (review or {}).get(lvl) or {}
        ok = (isinstance(e, dict) and e.get("reviewer")
              and str(e.get("verdict", "")).lower() in ("pass", "approved"))
        if isinstance(e, dict) and e.get("sha256"):
            ok = ok and e["sha256"] in (fcstd_sha, contract)
        else:
            ok = False
        if (review or {}).get("project_id") != pid:
            ok = False
        # N4 recheck round 4: under require_review these bindings are
        # MANDATORY, not "if present" - stripping native_sha256 /
        # family_sha / review_of_record from a receipt used to pass it.
        if require_review:
            if review.get("native_sha256") != fcstd_sha:
                ok = False
            if not review.get("review_of_record"):
                ok = False
            if manifest_sha and \
                    review.get("render_manifest_sha256") != manifest_sha:
                ok = False   # images signed against a different export run
            if e.get("family_sha") != family_sha:
                ok = False
        else:
            if review.get("native_sha256") and \
                    review["native_sha256"] != fcstd_sha:
                ok = False
            if e.get("family_sha") and e["family_sha"] != family_sha:
                ok = False
        if lvl == "L2":
            rd = e.get("render_sha256") or {}
            if require_review or rd:
                ok = ok and rd == render_digests
        rinfo[lvl] = {"present": bool(e), "valid": bool(ok),
                      "bound": ("contract" if isinstance(e, dict) and
                                e.get("sha256") == contract else
                                "bytes" if isinstance(e, dict) and
                                e.get("sha256") == fcstd_sha else "none"),
                      "reviewer": e.get("reviewer", "")}
    return rinfo
