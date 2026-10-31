"""Automated acceptance checks mirroring the purchase spec sections 2.3/2.4.

Every machine-checkable hard requirement is one check; any FAIL blocks the
gate. Checks that need human judgement are emitted as WARN with instructions.
"""
from __future__ import annotations

import json
import re
import math

import FreeCAD as App
import Part

from .project import grade_for_steps, GRADE_MIN_STEPS

ORIGIN_TYPES = ("App::Origin", "App::Plane", "App::Line",
                "App::LocalCoordinateSystem", "App::Part", "App::Placement")
CONTAINER_TYPES = ("App::DocumentObjectGroup", "App::MaterialObject",
                   "App::MaterialObjectInternal", "App::FeaturePython")
DUMB_TYPES = ("Mesh::Feature", "Import::TopoShape")
FEATURE_TYPES = ("Sketcher::SketchObject",)
PART_PREFIX = "Part::"

NAME_OK = re.compile(r"^[A-Za-z0-9_]+$")


def is_sketch(o):
    return o.TypeId == "Sketcher::SketchObject"


def is_feature(o):
    t = o.TypeId
    if t in ORIGIN_TYPES or t in CONTAINER_TYPES or t in DUMB_TYPES:
        return False
    if t.startswith("Part::") and t not in ("Part::Feature", "Part::FaceMakerBullseye"):
        return True
    if t == "Part::Feature":
        # only count Part::Feature when it is not the product of a feature chain
        return False
    return False


def is_step_node(o):
    """Node counted toward the buyer's step count: sketches + features.

    Excludes containers, origin, suppressed items, appearance, drawings.
    """
    if getattr(o, "Suppressed", False):
        return False
    return is_sketch(o) or is_feature(o)


def cadkit_marker(doc):
    try:
        meta = json.loads(doc.Comment or "{}")
    except Exception:
        meta = {}
    return meta if meta.get("cadkit") else None


def final_objects(doc):
    """Terminal solid objects of the feature tree."""
    meta = cadkit_marker(doc)
    if meta:
        objs = [doc.getObject(n) for n in meta.get("final", [])]
        objs = [o for o in objs if o is not None]
        if objs:
            return objs
    terminals = []
    for o in doc.Objects:
        if o.TypeId in ORIGIN_TYPES or o.TypeId in CONTAINER_TYPES:
            continue
        if not hasattr(o, "Shape"):
            continue
        if o.InList:  # used by another object -> not terminal
            continue
        terminals.append(o)
    return terminals


def contributing_set(doc):
    fs = final_objects(doc)
    closure = set()
    for f in fs:
        closure.add(f.Name)
        for o in f.OutListRecursive:   # everything this final solid is built from
            closure.add(o.Name)
    return fs, closure


def solids_of(doc):
    out = []
    for f in final_objects(doc):
        try:
            sh = f.Shape
        except Exception:
            continue
        if sh is None or sh.isNull():
            continue
        out.append((f.Name, sh))
    return out


def _check(cid, name, req, status, detail):
    return {"id": cid, "name": name, "requirement": req,
            "status": status, "detail": detail}


def validate_document(doc, unit="mm", target_grade=None, strict_bop=True,
                    source_url=None, features=None):
    """features: expected design features from PROJECT metadata
    (recheck W2/R5): where the design declares a seal groove or bolt
    pattern, a gate that finds NO geometry must FAIL (hidden/renamed
    feature), never report "not applicable"."""
    features = features or {}
    checks = []
    stats = {}

    # ---- 1. rebuild without errors --------------------------------------
    doc.recompute(None, True, True)
    bad = []
    for o in doc.Objects:
        st = list(o.State)
        if any(s in st for s in ("Invalid", "Error")) or "Touched" in st:
            bad.append("%s%s" % (o.Name, st))
    checks.append(_check(
        "rebuild_no_error", "重建无错", "2.4",
        "PASS" if not bad else "FAIL",
        "forced recompute, 0 errors" if not bad else "objects in error: " + "; ".join(bad[:10])))

    fs, closure = contributing_set(doc)
    steps_objs = [o for o in doc.Objects if o.Name in closure and is_step_node(o)]
    n_steps = len(steps_objs)
    n_sketch = sum(1 for o in steps_objs if is_sketch(o))
    stats["steps"] = n_steps
    stats["sketches"] = n_sketch
    stats["features"] = n_steps - n_sketch
    stats["grade"] = grade_for_steps(n_steps)

    # ---- 2. step floor ---------------------------------------------------
    floor_ok = n_steps >= 20
    grade_detail = "contributing nodes=%d (sketches=%d, features=%d) -> grade %s" % (
        n_steps, n_sketch, n_steps - n_sketch, stats["grade"])
    if target_grade:
        need = GRADE_MIN_STEPS[target_grade]
        floor_ok = floor_ok and n_steps >= need
        grade_detail += "; target grade %s needs >=%d" % (target_grade, need)
    checks.append(_check(
        "step_floor", "步骤下限", "2.4",
        "PASS" if floor_ok else "FAIL", grade_detail))

    # ---- 3. sketch constraints -------------------------------------------
    bad_sk, unfully = [], []
    for o in steps_objs:
        if not is_sketch(o):
            continue
        if o.GeometryCount == 0:
            bad_sk.append(o.Name + " (empty)")
        elif len(o.Constraints) < 1:
            bad_sk.append(o.Name + " (no constraints)")
        elif not getattr(o, "FullyConstrained", False):
            unfully.append(o.Name)
    status = "PASS" if not bad_sk else "FAIL"
    detail = "%d sketches constrained" % sum(1 for o in steps_objs if is_sketch(o))
    if bad_sk:
        detail = "violations: " + ", ".join(bad_sk[:10])
    elif unfully:
        status = "WARN"
        detail += "; not fully constrained (recommend): " + ", ".join(unfully[:10])
    checks.append(_check("sketch_constraints", "草图有约束", "2.4", status, detail))

    # ---- 4. solid validity -------------------------------------------------
    problems = []
    bop_issues = []
    vols = []
    _finals = list(final_objects(doc))
    # W3/R (recheck): a declared final that no longer exists must FAIL,
    # not be silently dropped by final_objects().
    _miss = [n for n in (cadkit_marker(doc) or {}).get("final", [])
             if doc.getObject(n) is None]
    if _miss:
        problems.append("declared final object(s) missing from document: %s"
                        % ", ".join(_miss))
    _found = solids_of(doc)
    if len(_found) < len(_finals):
        # W3 (workflow audit): a missing/unreadable final Shape must fail,
        # never be silently skipped by solids_of().
        problems.append("%d of %d final object(s) have no usable Shape"
                        % (len(_finals) - len(_found), len(_finals)))
    for name, sh in _found:
        if not sh.Solids:
            problems.append("%s: no solid" % name)
            continue
        for i, s in enumerate(sh.Solids):
            if not s.isValid():
                problems.append("%s.solid%d: invalid" % (name, i))
            if s.Volume <= 1e-9:
                # W3: signed — inside-out solids have abs()>0, Volume<0
                problems.append("%s.solid%d: non-positive signed volume %0.3f"
                                % (name, i, s.Volume))
            for shell in s.Shells:
                if not shell.isClosed():
                    problems.append("%s.solid%d: open shell" % (name, i))
        try:
            sh.check(False)
        except Exception as e:
            problems.append("%s: check(): %s" % (name, str(e)[:120]))
        # Rule R-F1 (audit F1; RE-CALIBRATED 2026-10-30 by L3 audit P1-P4):
        # strict BOP SelfIntersect/InvalidCurveOnSurface flags previously
        # treated as OCC over-strictness were independently reproduced at
        # real fusion seams (rib pads) — they are now a HARD gate
        # (opts strict_bop=0 downgrades to informational for triage only).
        try:
            sh.check(True)
        except Exception as e:
            bop_issues.append("%s: %s" % (name, str(e)[:90]))
        if sh.Volume <= 0:
            problems.append("%s: model signed volume <= 0 (%0.3f) — inverted"
                            % (name, sh.Volume))
        vols.append(abs(sh.Volume))
    total_vol = sum(vols)
    stats["volume"] = total_vol
    stats["solids"] = sum(len(sh.Solids) for _, sh in solids_of(doc))
    stats["bop_strict_issues"] = len(bop_issues)
    stats["bop_strict_clean"] = not bop_issues
    geo_fail = bool(problems) or total_vol <= 0 or (strict_bop and bool(bop_issues))
    fail_list = list(problems) + (bop_issues if strict_bop else [])
    if total_vol <= 0:
        fail_list.append("zero total volume")
    geo_detail = ("solids closed, valid, volume>0" if not geo_fail
                  else "; ".join(fail_list)[:400])
    if bop_issues and not strict_bop:
        geo_detail += "; BOP-strict flags %d (informational ONLY because strict_bop=0: %s)" % (
            len(bop_issues), "; ".join(bop_issues[:3])[:180])
    checks.append(_check(
        "geometry_valid", "几何有效", "2.4",
        "FAIL" if geo_fail else "PASS", geo_detail))

    # ---- 5. units & scale --------------------------------------------------
    bb = None
    for _, sh in solids_of(doc):
        b = sh.BoundBox
        bb = b if bb is None else bb.united(b) if hasattr(bb, "united") else _union(bb, b)
    if bb is None:
        checks.append(_check("unit_scale", "单位与尺度", "2.4", "FAIL", "no shape"))
        size_mm = 0.0
    else:
        # W22 (workflow audit): fail CLOSED on unknown/foreign units. The
        # silent {m:1000, in:25.4} conversion table let a document with a
        # missing unit scale pass a 1000x-wrong size - a wrong-unit model
        # must never reach delivery. Pipeline is mm-declared.
        _u = str(unit).strip().lower()
        if _u not in ("mm", "millimeter", "millimetres"):
            checks.append(_check(
                "unit_scale", "单位与尺度", "2.4", "FAIL",
                "declared unit '%s' refused: pipeline is mm-declared, "
                "non-mm documents need a normalized export (audit W22)"
                % unit))
            size_mm = 0.0
        else:
            size_mm = max(bb.XLength, bb.YLength, bb.ZLength)
            ok = 1.0 <= size_mm <= 20000.0
            stats["bbox_mm"] = [bb.XLength, bb.YLength, bb.ZLength]
            checks.append(_check(
                "unit_scale", "单位与尺度", "2.4",
                "PASS" if ok else "FAIL",
                "unit=mm overall max size %.1f mm (limit 1 mm .. 20 m)"
                % size_mm))

    # ---- 6. tidy -----------------------------------------------------------
    issues = []
    for o in doc.Objects:
        if o.TypeId in ORIGIN_TYPES:
            continue
        if getattr(o, "Suppressed", False):
            issues.append("suppressed: " + o.Name)
        if is_sketch(o) and o.GeometryCount == 0 and o.Name in closure:
            issues.append("empty sketch: " + o.Name)
        if o.Name not in closure and _should_be_in_closure(o):
            issues.append("orphan (unused reference): " + o.Name)
        if o.Name != o.Name.strip() or "\ufffd" in (o.Label or "") or \
                any(ord(ch) < 32 for ch in o.Name):
            issues.append("garbled name: " + o.Name)
    checks.append(_check(
        "tidy", "整洁", "2.4",
        "PASS" if not issues else "FAIL",
        "no empty sketches / suppressions / orphans" if not issues
        else "; ".join(issues[:10])))
    # W29 (workflow audit): full visibility on objects that contribute
    # nothing to the delivered solids (informational; helpers legitimate).
    stats["objects_outside_closure"] = len([
        o for o in doc.Objects
        if o.Name not in closure and o.TypeId not in ORIGIN_TYPES
        and o.TypeId not in CONTAINER_TYPES])

    # ---- 7. not reverse-engineered -----------------------------------------
    dumb = [o.Name for o in doc.Objects if o.TypeId in DUMB_TYPES]
    meshish = []
    for o in doc.Objects:
        if o.TypeId == "Part::Feature" and o.Name not in ORIGIN_TYPES:
            if o not in [f for f in fs] and not o.InList:
                continue
        if o.TypeId in ("App::FeaturePython",) and o.Name.startswith("Part__Feature") and not o.InList:
            meshish.append(o.Name)
    ok = not dumb and not meshish
    checks.append(_check(
        "not_reverse_engineered", "非逆向生成", "2.4",
        "PASS" if ok else "FAIL",
        "pure parametric history, no mesh/imported geometry" if ok
        else "mesh/imported objects: " + ", ".join((dumb + meshish)[:10])))

    # ---- 8. parametric history present ------------------------------------
    has_history = n_sketch >= 1 and n_steps > n_sketch
    checks.append(_check(
        "parametric_history", "参数化特征历史", "2.1",
        "PASS" if has_history else "FAIL",
        "sketch+feature chain present" if has_history else "need sketches AND derived features"))

    # ---- parametric design rules (L3 audit 2026-10-30 P5-P10) -------------
    # Derived from the params spreadsheet when the driving aliases exist.
    import math as _m
    _ps = doc.getObject("params")

    def pget(k):
        if _ps is None:
            return None
        try:
            return float(_ps.get(k))
        except Exception:
            return None

    def _seal_land():
        # W2 (workflow audit): real-geometry check. The groove band comes
        # from the actual revolved groove cutter, and intruders are the
        # ACTUAL cutting-tool footprints sectioned at mid-groove height —
        # not constants re-inferred from the parameter sheet (that scheme
        # could drift from the family and silently rubber-stamp).
        grooves, cutters = [], []
        for o in doc.Objects:
            if o.TypeId != "Part::Cut":
                continue
            tool = getattr(o, "Tool", None)
            if tool is None or not hasattr(tool, "Shape"):
                continue
            if tool.TypeId == "Part::Revolution" and \
                    any(k in tool.Name.lower()
                        for k in ("oring", "groove", "seal")):
                rs = [(v.Point.x ** 2 + v.Point.y ** 2) ** 0.5
                      for v in tool.Shape.Vertexes]
                rs = [r for r in rs if r > 1e-6]
                if not rs or (max(rs) - min(rs)) < 1.0:
                    return "FAIL", ("groove cutter %s has no usable profile "
                                    "radii - seal check cannot run "
                                    "(fail closed)" % tool.Name)
                bb = tool.Shape.BoundBox
                grooves.append((tool.Name, min(rs), max(rs), bb.ZMin, bb.ZMax))
            elif tool.TypeId != "Part::Revolution":
                cutters.append(tool)
        if not grooves:
            if features.get("seal_groove"):
                return "FAIL", ("project declares a seal groove but no "
                                "groove cutter geometry found - "
                                "feature hidden or removed "
                                "(fail closed, W2/R5)")
            return "PASS", "no o-ring groove geometry present - not applicable"
        hits = []
        for gname, r0, r1, gz0, gz1 in grooves:
            zmid = (gz0 + gz1) / 2.0
            for t in cutters:
                try:
                    bb = t.Shape.BoundBox
                except Exception:
                    return "FAIL", "cutter %s has no shape - fail closed" % t.Name
                if not (bb.ZMin - 1e-6 <= zmid <= bb.ZMax + 1e-6):
                    continue
                try:
                    wires = t.Shape.slice(App.Vector(0, 0, 1), zmid)
                except Exception as e:
                    return "FAIL", ("slice of cutter %s failed (%s) - seal "
                                    "check cannot run (fail closed)"
                                    % (t.Name, str(e)[:60]))
                for w in wires:
                    ds = []
                    try:
                        for eg in w.Edges:
                            ds += [(q.x ** 2 + q.y ** 2) ** 0.5
                                   for q in eg.discretize(24)]
                    except Exception:
                        ds = [(v.Point.x ** 2 + v.Point.y ** 2) ** 0.5
                              for v in w.Vertexes]
                    if not ds:
                        continue
                    dmin, dmax = min(ds), max(ds)
                    if dmax > r0 and dmin < r1:
                        hits.append("%s footprint r%.2f..%.2f intrudes groove "
                                    "band r%.2f..%.2f"
                                    % (t.Name, dmin, dmax, r0, r1))
        if hits:
            return "FAIL", ("seal land cut through by real cutter geometry "
                            "(audit P5/P6): "
                            + "; ".join(sorted(set(hits))[:5]))
        g = grooves[0]
        return ("PASS", "no cutter footprint intrudes groove band r%.2f..%.2f "
                "(real-geometry check: %d cutters vs %d groove(s))"
                % (g[1], g[2], len(cutters), len(grooves)))

    def _bolt_access():
        # W2 (workflow audit): real-geometry check, scoped to bolts. Bolt
        # positions come from the real circles of sk_bolt<d> sketches; every
        # bolt circle MUST exist as a through-bore face in the final solid;
        # head-seat + insertion envelopes are booleans against the real
        # body. Other bores (earth taps, dowels, drains) have their own
        # insertion semantics and are not held to fastener-head clearance.
        # The 1.5xR + 2 head allowance is the shared contract constant with
        # the family rib assertion (rules_suite pins both sides).
        import Part as _P
        import re as _re2
        finals = [sh for _, sh in solids_of(doc)]
        if not finals:
            return "PASS", "no final solid - covered by geometry_valid"
        bolts = []
        for o in doc.Objects:
            if o.TypeId == "Sketcher::SketchObject" and \
                    _re2.match(r"sk_bolt\d+$", o.Name):
                for g in o.Geometry:
                    if isinstance(g, _P.Circle) and g.Radius <= 20:
                        c3 = o.Placement.multVec(g.Center)
                        bolts.append((c3.x, c3.y, g.Radius))
        _decl = int(features.get("bolts") or 0)
        if _decl and len(bolts) < _decl:
            # N6 recheck: "at least one bolt hole found" is not the declared
            # fastening pattern - every declared bolt must exist.
            return "FAIL", ("declares %d bolts but only %d bolt-circle(s) "
                            "found in sk_bolt* sketches (N6 fail closed)"
                            % (_decl, len(bolts)))
        if not bolts:
            if int(features.get("bolts") or 0) > 0:
                return "FAIL", ("project declares %d flange bolts but "
                                "no sk_bolt* pattern sketches exist - "
                                "hidden or renamed (fail closed, "
                                "W2/R5)" % int(features["bolts"]))
            return "PASS", "no bolt sketches present - not applicable"
        holes = []
        for bx, by, br in bolts:
            hit = None
            for sh in finals:
                for f in sh.Faces:
                    surf = getattr(f, "Surface", None)
                    if surf is None or surf.__class__.__name__ != "Cylinder":
                        continue
                    if abs(abs(surf.Axis.z) - 1.0) > 1e-6:
                        continue
                    if abs(surf.Radius - br) > 0.3:
                        continue
                    if (surf.Center.x - bx) ** 2 + \
                            (surf.Center.y - by) ** 2 > 0.5:
                        continue
                    fbb = f.BoundBox
                    # counterbores and seams split a bore wall across
                    # several cylinder faces: keep ALL spans; through-
                    # coverage must be CONTINUOUS (R5 recheck: two blind
                    # bores separated by a web are not a through-bore).
                    if hit is None:
                        hit = [bx, by, br, [(fbb.ZMin, fbb.ZMax)]]
                    else:
                        hit[3].append((fbb.ZMin, fbb.ZMax))
            run_lo = run_hi = z_top = None
            if hit is not None:
                spans = sorted(hit[3])
                run = 0.0
                cl = spans[0][0]
                ch = spans[0][1]
                for lo, hi in spans[1:]:
                    # N6 recheck round 4: a 0.2 mm diaphragm must NOT be
                    # merged away into one clean run - merge only on real
                    # overlap, not on a 0.4 mm tolerance.
                    if lo <= ch + 0.05:           # touching/overlapping
                        ch = max(ch, hi)
                    else:
                        if ch - cl > run:
                            run, run_lo, run_hi = ch - cl, cl, ch
                        cl, ch = lo, hi
                if spans[-1][1] - cl > run:
                    run, run_lo, run_hi = spans[-1][1] - cl, cl, spans[-1][1]
                z_top = spans[-1][1]
                if run < 6.0:
                    hit = None
            if hit is None:
                return "FAIL", ("bolt circle at (%.1f,%.1f) r%.2f has no "
                                "matching through-bore wall in the final "
                                "solid - pattern not cut through "
                                "(fail closed, audit W2)" % (bx, by, br))
            # N6 recheck: a continuous WALL is not a THROUGH bore - the
            # material-aware test is that below the bore's lowest point
            # there is AIR, not membrane or blind-floor, along the axis.
            try:
                r_probe = max(br - 0.3, 0.15)
                # N6 recheck round 4: sample the ENTIRE nominal bore
                # channel (entry down to 3 mm below the lowest wall), not
                # only below the wall - a 0.2 mm diaphragm INSIDE the
                # merged channel is 15.7 mm3 of fastener-blocking
                # material and must fail exactly like a blind bore.
                chan = _P.makeCylinder(
                    r_probe, (z_top + 0.5) - (run_lo - 3.0),
                    App.Vector(bx, by, run_lo - 3.0))
                mat_below = sum(sh.common(chan).Volume for sh in finals)
            except Exception as e:
                return "FAIL", ("bore openness boolean failed (%s) - "
                                "fail closed (N6)" % str(e)[:50])
            if mat_below > 0.5:
                return "FAIL", ("bolt bore at (%.1f,%.1f) terminates on "
                                "%.0f mm3 of material INSIDE the "
                                "nominal bore channel (below entry z=%.1f) - "
                                "blind/membraned/diaphragmed, not a through "
                                "fastening hole (N6 fail closed)"
                                % (bx, by, mat_below, run_lo))
            holes.append((hit[0], hit[1], hit[2], z_top))
        issues = []
        from .contract import head_envelope_radius, head_envelope_height
        for i, (x, y, rh, zt) in enumerate(holes):
            hr = head_envelope_radius(rh)
            hh = head_envelope_height(rh)
            try:
                c1 = _P.makeCylinder(hr, hh, App.Vector(x, y, zt))
                c2 = _P.makeCylinder(hr, hh + 15.0, App.Vector(x, y, zt))
                v_seat = sum(sh.common(c1).Volume for sh in finals)
                v_all = sum(sh.common(c2).Volume for sh in finals)
            except Exception as e:
                return "FAIL", ("bolt-access boolean failed on bolt #%d (%s) "
                                "- fail closed" % (i, str(e)[:60]))
            if v_seat > 2.0:
                issues.append("bolt #%d at (%.1f,%.1f): head-seat envelope "
                              "blocked by %.0f mm3 above the flange face"
                              % (i, x, y, v_seat))
            elif v_all - v_seat > 2.0:
                issues.append("bolt #%d at (%.1f,%.1f): insertion path "
                              "blocked by %.0f mm3 above the head space"
                              % (i, x, y, v_all - v_seat))
        if issues:
            return "FAIL", ("bolt insertion blocked (audit P7): "
                            + "; ".join(issues)[:340])
        return ("PASS", "%d bolt bores are real through-bores with clear "
                "head-seat and insertion envelopes (real-geometry check)"
                % len(holes))

    st, dt = _seal_land()
    checks.append(_check("seal_land_clear", "密封圈带无贯通孔", "2.4", st, dt))
    st, dt = _bolt_access()
    checks.append(_check("bolt_access", "螺栓装配路径", "2.4", st, dt))

    # ---- human-judgement items (WARN, listed on final checklist) -----------
    checks.append(_check(
        "real_object", "真实可辨识 (人工终审)", "2.4", "WARN",
        "P0 design review + L2 vision + P7 human checklist confirm this is a real, identifiable object"))
    # Internal-sourcing policy (buyer decision 2026-10-29): parts are
    # self-made for internal manufacturing — "internal"/"self-originated"
    # PASSES; anything else still needs the human URL/license check.
    _src = (source_url or "").strip().lower()
    _src_ok = _src.startswith(("internal", "self-originated"))
    checks.append(_check(
        "source_license", "来源与许可证", "2.2",
        "PASS" if _src_ok else "WARN",
        "internal sourcing declared (buyer waives external provenance check)"
        if _src_ok else "non-internal source_url: human URL/license verification required"))

    ok_all = all(c["status"] != "FAIL" for c in checks)
    return {"ok": ok_all, "checks": checks, "stats": stats,
            "final": [o.Name for o in fs]}


def _should_be_in_closure(o):
    t = o.TypeId
    if t in ORIGIN_TYPES or t in CONTAINER_TYPES:
        return False
    if t.startswith("Part::") or t == "Sketcher::SketchObject":
        return True
    return False


def _union(a, b):
    import FreeCAD
    bb = a
    bb.add(b)
    return bb


def features_for(proj):
    """Expected-feature map from project metadata, shared by validate_project
    and the packager's fresh validation (single source)."""
    topo = (proj.field("variant", {}) or {}).get("topo", {}) or {}
    return {"seal_groove": bool(topo.get(
        "seal_groove", proj.field("family", "") == "iec_endcap")),
        "bolts": int(topo.get("bolts") or 0)}


def validate_project(proj, strict_bop=None):
    import os
    if strict_bop is None:
        strict_bop = bool(proj.field("strict_bop", True))
    doc = App.openDocument(proj.fcstd_path)
    try:
        rep = validate_document(doc, unit=proj.field("unit", "mm"),
                                target_grade=proj.field("target_grade"),
                                strict_bop=strict_bop,
                                source_url=proj.field("source_url"),
                                features=features_for(proj))
        rep["project"] = proj.id
        from . import version as _ver
        rep["engine"] = _ver.stamp()
        return rep
    finally:
        App.closeDocument(doc.Name)
