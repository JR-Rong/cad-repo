"""Geometric fingerprints for de-duplication (spec 2.5).

Scaled/mirror variants of the same model share dimensionless invariants:
compactness, bbox ratios, solid count, feature structure. Used to warn
before delivering a duplicate of something already in the library.
"""
from __future__ import annotations

import json
import math
import os

import FreeCAD as App


def compute(fp_fcstd):
    doc = App.openDocument(fp_fcstd)
    try:
        from .validate import final_objects, contributing_set
        objs = final_objects(doc)
        v = a = 0.0
        nsolids = nfaces = nedges = 0
        bb = None
        for o in objs:
            sh = o.Shape
            v += abs(sh.Volume)
            a += sh.Area
            nsolids += len(sh.Solids)
            nfaces += len(sh.Faces)
            nedges += len(sh.Edges)
            b = sh.BoundBox
            bb = b if bb is None else bb.united(b) if hasattr(bb, "united") else _add(bb, b)
        _, closure = contributing_set(doc)
        steps = sum(1 for oo in doc.Objects if oo.Name in closure and (
            oo.TypeId == "Sketcher::SketchObject" or oo.TypeId.startswith("Part::")))
        # structural cell count of the driving sheet (audit P12: buyer 2.5
        # counts dimension-only variants of one feature structure as ONE model)
        cells = 0
        ps = doc.getObject("params")
        if ps is not None:
            r = 1
            while True:
                try:
                    name = ps.getContents("A%d" % r)
                except Exception:
                    break
                if not name or not isinstance(name, str) or not name.strip():
                    break
                cells += 1
                r += 1
        if v <= 0 or bb is None:
            raise RuntimeError("empty geometry, no fingerprint")
        c = bb.XLength, bb.YLength, bb.ZLength
        m = max(c)
        return {
            "volume_scale": (v ** (1.0 / 3.0)) / m,          # scale invariant shape idiosyncrasy
            "compactness": a ** 1.5 / v,                     # A^(3/2)/V
            "ratios": [round(x / m, 6) for x in sorted(c, reverse=True)],
            "bbox": [round(x, 4) for x in sorted(c, reverse=True)],
            "solids": nsolids, "faces": nfaces, "edges": nedges,
            "steps": steps, "cells": cells,
            "volume": v,
        }
    finally:
        App.closeDocument(doc.Name)


def _add(a, b):
    a.add(b)
    return a


def registry_path(cad_root):
    d = os.path.join(cad_root, "registry")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "fingerprints.json")


def load_registry(cad_root):
    p = registry_path(cad_root)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def scaled_same(f1, f2):
    """Spec 2.5 strict semantics: identical topology (faces/edges/solids) AND
    all three bbox ratios equal AND volume ratio == k^3 → the SAME model,
    merely scaled (mirrors share these invariants too). Non-uniform design
    changes (different bearing, bolt count…) are NOT duplicates."""
    if f1["solids"] != f2["solids"] or f1["faces"] != f2["faces"] \
            or f1["edges"] != f2["edges"]:
        return False
    # A true scaled copy preserves every DIMENSIONLESS invariant. Face/edge
    # counts alone let different sizes of a parametric family collide
    # (132 vs 80b shared 132 faces by coincidence): compactness and step
    # count must match too, or it is a different design, not a copy.
    if "compactness" in f1 and "compactness" in f2:
        if abs(f1["compactness"] - f2["compactness"]) \
                / max(f1["compactness"], f2["compactness"]) > 0.015:
            return False
    if "steps" in f1 and "steps" in f2 and f1["steps"] != f2["steps"]:
        return False
    k = [a / b for a, b in zip(f1["bbox"], f2["bbox"])]
    k0 = sum(k) / 3.0
    if max(abs(x / k0 - 1.0) for x in k) > 0.005:
        return False                                  # not uniform scale
    vol_ratio = f1["volume"] / f2["volume"]
    return abs(vol_ratio - k0 ** 3) / (k0 ** 3) < 0.02


def related(f1, f2):
    """Informational: same-series look-alike (never blocks packaging)."""
    if "ratios" not in f1 or "ratios" not in f2:
        return False
    if f1["solids"] != f2["solids"]:
        return False
    if abs(f1["compactness"] - f2["compactness"]) / max(f1["compactness"], f2["compactness"]) > 0.03:
        return False
    if max(abs(x - y) for x, y in zip(f1["ratios"], f2["ratios"])) > 0.02:
        return False
    if abs(f1["steps"] - f2["steps"]) > max(2, 0.12 * f2["steps"]):
        return False
    return True


def check_and_register(cad_root, project_id, fcstd, register=False):
    f = compute(fcstd)
    # N10 recheck: candidates come from the LEDGER - the authoritative set
    # of DELIVERED, non-withdrawn revisions with their recorded invariants.
    # The flat registry survives only as a cache for legacy/no-ledger
    # worlds; deleting the JSON can no longer blind dedupe.
    reg = {}
    ledger_mode = False
    try:
        import json as _j
        import sqlite3 as _sq
        lp = os.path.join(cad_root, "ledger.sqlite3")
        if os.path.exists(lp):
            conn = _sq.connect(lp, timeout=30)
            try:
                rows = conn.execute(
                    "SELECT m.project_id, f.data FROM fingerprint f "
                    "JOIN delivery d ON d.rev=f.rev AND d.state='delivered' "
                    "JOIN model m ON m.model_id=d.model_id "
                    "WHERE f.state='active' AND f.data IS NOT NULL "
                    "GROUP BY m.project_id").fetchall()
                for pid, data in rows:
                    try:
                        reg[pid] = _j.loads(data)
                    except Exception:
                        pass
                if rows:
                    ledger_mode = True
            finally:
                conn.close()
    except Exception:
        pass
    if not ledger_mode:
        reg = load_registry(cad_root)
    related_ = []
    scaled = []
    exact = []
    series = []
    volume_coincidence = []
    hit_details = {}
    for pid, prev in reg.items():
        if pid == project_id:
            continue
        if abs(prev["volume"] - f["volume"]) / max(prev["volume"], 1e-9) < 1e-6:
            # R9 recheck: equal volume ALONE is a coincidence, not a copy.
            # A true re-delivery preserves every recorded invariant.
            inv = (prev.get("solids") == f["solids"]
                   and prev.get("faces") == f["faces"]
                   and prev.get("edges") == f["edges"]
                   and prev.get("steps") == f["steps"]
                   and "compactness" in prev
                   and abs(prev["compactness"] - f["compactness"])
                   / max(prev["compactness"], f["compactness"], 1e-9) < 0.005)
            if inv:
                exact.append(pid)
            else:
                if pid not in volume_coincidence:
                    volume_coincidence.append(pid)
        else:
            try:                      # legacy entries lack bbox/faces/edges
                if scaled_same(f, prev):
                    scaled.append(pid)
                    continue
            except KeyError:
                pass
            # buyer 2.5 (audit P12): identical feature structure (same driving
            # cells, same contributing steps, same solids) = dimension-only
            # variant of one model — counts as ONE deliverable.
            try:
                if (f.get("cells") and prev.get("cells") and f["cells"] == prev["cells"]
                        and f["steps"] == prev["steps"] and f["solids"] == prev["solids"]):
                    series.append(pid)
                    continue
            except KeyError:
                pass
            if related(f, prev):
                related_.append(pid)
    ledger_unbacked = []
    if ledger_mode:
        delivered = {r for r in reg}
        have_model = delivered
    try:
        from .ledger import open_ledger
        conn = open_ledger(cad_root)
        try:
            delivered = {r[0] for r in conn.execute(
                "SELECT DISTINCT m.project_id FROM delivery d "
                "JOIN model m ON m.model_id=d.model_id "
                "WHERE d.state='delivered'")}
            have_model = {r[0] for r in conn.execute(
                "SELECT project_id FROM model")}
        finally:
            conn.close()
        blocking = exact + scaled
        for pid in blocking:
            if pid not in delivered and pid in have_model:
                ledger_unbacked.append(pid)   # withdrawn / never accepted
        exact = [p for p in exact if p not in ledger_unbacked]
        scaled = [p for p in scaled if p not in ledger_unbacked]
    except Exception:
        delivered = None
    for pid in exact + scaled:
        hit_details[pid] = reg.get(pid)
    if register:
        reg[project_id] = f
        with open(registry_path(cad_root), "w", encoding="utf-8") as fp:
            json.dump(reg, fp, ensure_ascii=False, indent=1)
    return {"fingerprint": {k: v for k, v in f.items() if k != "volume"},
            "exact": exact, "scaled": scaled, "series": series,
            "related": related_,
            "volume_coincidence": volume_coincidence,
            "ledger_unbacked": ledger_unbacked,
            "hit_details": hit_details,
            # backward-compatible keys
            "similar_series": exact + scaled + series, "possible_exact": exact,
            "registry_size": len(reg)}
