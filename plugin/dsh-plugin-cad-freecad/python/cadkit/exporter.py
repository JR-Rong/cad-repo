"""STEP export (AP214) and native-vs-STEP consistency self-check.

Spec 2.3: STEP must be exported directly from the delivered native file.
Spec 2.4: rebuilding from native and comparing to STEP must match within
volume 1e-4 and overall size 1e-3 — we run the same comparison before delivery.
"""
from __future__ import annotations

import os
import time

import FreeCAD as App
import Part


def _final_objs(doc):
    from .validate import final_objects
    return final_objects(doc)


def export_step(fcstd_path, step_path, objs_names=None):
    doc = App.openDocument(fcstd_path)
    try:
        objs = _final_objs(doc)
        if objs_names:
            objs = [o for o in doc.Objects if o.Name in objs_names]
        doc.recompute(None, True, True)   # ensure fresh geometry, do NOT save
        Import = _import_module()
        if Import is not None:
            Import.export(objs, step_path)
        else:
            Part.export(objs, step_path)
    finally:
        App.closeDocument(doc.Name)
    return step_path


def _import_module():
    try:
        import Import  # provides the STEP exporter with scheme settings
        return Import
    except Exception:
        return None


def step_schema(step_path):
    """W25 + N11 recheck: STEP-lexical parse of the physical file.

    A scanner walks the text tracking /*…*/ comments and '…' string
    literals (with '' escapes) and produces a SAME-LENGTH code stream in
    which comments and string CONTENTS are blanked - so nothing inside a
    comment or a string (fake ENDSEC, embedded FILE_SCHEMA prose) can be
    mistaken for real header syntax, while string bodies survive intact
    for extraction at their true offsets. The first real FILE_SCHEMA
    statement at a statement position wins; its first string must match
    the exact schema NAME plus the registration version tuple."""
    import re as _re
    with open(step_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(400000)
    code = []      # comments AND string contents blanked - structure scan
    noc = []       # comments blanked ONLY - string bytes verbatim (N11:
                   # extraction must come from THIS stream so a quoted
                   # decoy inside an argument comment can never win)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j2 = text.find("*/", i + 2)
            j2 = n if j2 < 0 else j2 + 2
            code.append(" " * (j2 - i))
            noc.append(" " * (j2 - i))
            i = j2
            continue
        if ch == "'":
            k = i + 1
            buf = ["'"]
            while k < n:
                if text[k] == "'":
                    if k + 1 < n and text[k + 1] == "'":
                        buf.append("  ")
                        k += 2
                        continue
                    buf.append("'")
                    k += 1
                    break
                buf.append(" ")
                k += 1
            code.append("".join(buf))
            noc.append(text[i:k])
            i = k
            continue
        code.append(ch)
        noc.append(ch)
        i += 1
    code = "".join(code)
    noc = "".join(noc)
    hdr = code.find("HEADER")
    if hdr < 0:
        return "unknown"
    endsec = code.find("ENDSEC", hdr)
    region = code[hdr:endsec if endsec > hdr else hdr + 8000]
    m = _re.search(r"^[ \t]*FILE_SCHEMA\s*\(", region, _re.M | _re.I)
    if not m:
        return "unknown"
    ps = hdr + m.end() - 1
    depth = 0
    pe = -1
    for k in range(ps, len(code)):
        if code[k] == "(":
            depth += 1
        elif code[k] == ")":
            depth -= 1
            if depth == 0:
                pe = k
                break
    if pe < 0:
        return "unknown"
    stmt = noc[ps:pe + 1]           # comment-free, strings verbatim (N11)
    q1 = stmt.find("'")
    if q1 < 0:
        return "unknown"
    k = q1 + 1
    ent = []
    while k < len(stmt):
        if stmt[k] == "'":
            if k + 1 < len(stmt) and stmt[k + 1] == "'":
                ent.append("'")
                k += 2
                continue
            break
        ent.append(stmt[k])
        k += 1
    ent = "".join(ent).strip().upper()
    name, _, rest = ent.partition("{")
    name = name.strip()
    toks = rest.replace("}", " ").split()
    ver = toks[3] if len(toks) >= 4 else "?"
    # N11 recheck round 4: the version TOKEN at index 3 alone is not the
    # registration - a schema is certified only by the EXACT standard
    # registration tuple (name + all 8 registry numbers). Anything else
    # (203, malformed, foreign) is refused downstream as unknown.
    if name == "AUTOMOTIVE_DESIGN_CONTROL_MFG" and \
            toks == ["1", "0", "10309", "242", "1", "1", "1", "1"]:
        return "AP242"
    if name == "AUTOMOTIVE_DESIGN" and \
            toks == ["1", "0", "10303", "214", "1", "1", "1", "1"]:
        return "AP214"
    # N11: entity NAME alone never certifies the schema - bare or
    # foreign declarations (AP203 included) are refused.
    return "unknown:%s/%s" % (name[:28], ver)


def force_rebuild_and_check(doc):
    """Force a full rebuild, then run the STRONG (BOP) geometry check on the
    final solids — same gate the buyer runs (audit F1/F10)."""
    doc.recompute(None, True, True)
    from .validate import final_objects
    errs = []
    for o in final_objects(doc):
        try:
            o.Shape.check(True)
        except Exception as e:
            errs.append("%s: %s" % (o.Name, str(e)[:160]))
    return errs


def consistency_check(fcstd_path, step_path,
                      vol_tol=1e-4, size_tol=1e-3):
    """Compare native final solids against re-imported STEP geometry.
    The native doc is force-rebuilt first so cached stale geometry can
    never green-light a STEP that was exported from it (audit F10)."""
    doc = App.openDocument(fcstd_path)
    try:
        doc.recompute(None, True, True)
        nv = 0.0
        nb = None
        for o in _final_objs(doc):
            sh = o.Shape
            nv += abs(sh.Volume)
            b = sh.BoundBox
            if nb is None:
                nb = App.BoundBox(b) if hasattr(App, "BoundBox") else b
            else:
                nb.add(b)
        tmp = Part.Shape()
        tmp.read(step_path)
        sv = abs(tmp.Volume)
        sb = tmp.BoundBox
        vd = abs(nv - sv) / max(nv, 1e-12)
        axes = [("X", nb.XLength), ("Y", nb.YLength), ("Z", nb.ZLength)]
        sd = 0.0
        for (ax, nlen), slen in zip(axes, [sb.XLength, sb.YLength, sb.ZLength]):
            sd = max(sd, abs(nlen - slen) / max(nlen, 1e-12))
        ok = vd <= vol_tol and sd <= size_tol
        return {"ok": ok, "volume_rel_diff": vd, "size_rel_diff": sd,
                "volume_tol": vol_tol, "size_tol": size_tol,
                "schema": step_schema(step_path)}
    finally:
        App.closeDocument(doc.Name)
