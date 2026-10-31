"""Feature-history document context: the framework every build.py uses.

Modeling rule (purchase spec 2.1 / 2.4 "non-reverse-engineered"): geometry may
ONLY be created through sketches with constraints and Part feature objects.
This module deliberately exposes no mesh/image import.
"""
from __future__ import annotations

import json
import math
import os

import FreeCAD as App
import Part
import Sketcher

from .project import check_safe_name


class BuildError(Exception):
    pass


def _v(x=0.0, y=0.0, z=0.0):
    return App.Vector(float(x), float(y), float(z))


# --------------------------------------------------------------------------
# Sketch builder
# --------------------------------------------------------------------------
import re as _re

_TOK = _re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)(?![\w(])")


def _ex(expr):
    """Turn an alias or raw arithmetic expression of aliases into a full
    reference to the params sheet, e.g. ``RING_R-RING_W/2`` ->
    ``params.RING_R-params.RING_W/2``. Numbers and function calls are left
    untouched; an expression already namespaced with a dot passes through."""
    expr = str(expr).strip()
    if expr.startswith("params."):
        return expr
    if _re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\(", expr):   # a function call, leave raw
        return expr
    return _TOK.sub(lambda m: "params." + m.group(1), expr)


class SketchBuilder:
    """Wraps a Sketcher::SketchObject with auto-constrained primitives.

    Every primitive records at least one driving dimensional constraint named
    ``<sketch>_<kind><n>`` so the edit-probe can find editable parameters.
    """

    def __init__(self, ctx, name, plane="XY", offset=0.0, invert=False,
                 attach=None, offset_expr=None):
        self.ctx = ctx
        self.name = check_safe_name(name)
        sk = ctx.doc.addObject("Sketcher::SketchObject", self.name)
        sk.Label = self.name
        self.sk = sk
        self._cseq = 0
        if attach is not None:
            obj, facename = attach
            sk.AttachmentSupport = [(obj, facename)]
            sk.MapMode = "FlatFace"
            off = float(offset) * (-1.0 if invert else 1.0)
            sk.AttachmentOffset = App.Placement(
                _v(0, 0, off),
                App.Rotation(_v(0, 1, 0), 180) if invert else App.Rotation())
        else:
            planes = {
                "XY": ((0, 0, 1), (1, 0, 0)),
                "XZ": ((0, -1, 0), (1, 0, 0)),
                "YZ": ((1, 0, 0), (0, 1, 0)),
            }
            if plane not in planes:
                raise BuildError("unknown plane " + str(plane))
            nz, nx = planes[plane]
            nz, nx = _v(*nz), _v(*nx)
            n = -1.0 if invert else 1.0
            ny = nz.cross(nx) * n
            base = nz * (float(offset) * n)
            mat = App.Matrix(nx.x, ny.x, nz.x * n, base.x,
                             nx.y, ny.y, nz.y * n, base.y,
                             nx.z, ny.z, nz.z * n, base.z,
                             0, 0, 0, 1)
            sk.Placement = App.Placement(mat)
            if offset_expr:
                # placement.Base = nz * (offset*n); bind the dominant axis comp.
                ax = "xyz"[max(range(3), key=lambda i: abs(nz[i]))]
                coeff = nz[{"x": 0, "y": 1, "z": 2}[ax]] * n
                sk.setExpression(".Placement.Base." + ax,
                                 ("" if coeff > 0 else "-") + _ex(offset_expr))

    # -- low level --------------------------------------------------------
    def _cname(self, kind):
        self._cseq += 1
        return "%s_%s%d" % (self.name, kind, self._cseq)

    def _addc(self, c, kind="c"):
        idx = self.sk.addConstraint(c)
        if idx >= 0 and kind != "geom":
            try:
                self.sk.renameConstraint(idx, self._cname(kind))
            except Exception:
                pass
        return idx

    def bind(self, idx, expr):
        """Drive constraint ``idx`` from a params-sheet alias (or raw expression)."""
        if not expr:
            return
        try:
            cname = self.sk.Constraints[idx].Name
            self.sk.setExpression("Constraints." + cname, _ex(expr))
        except Exception as e:
            raise BuildError("bind constraint %d of %s to %r failed: %s"
                             % (idx, self.name, expr, e))

    def dim_radius(self, gi, r, expr=None):
        idx = self._addc(Sketcher.Constraint("Radius", gi, float(r)), "r")
        self.bind(idx, expr)

    def dim_dist(self, g1, p1, g2, p2, d, axis="XY", expr=None):
        kind = {"X": "x", "Y": "y", "XY": "d"}[axis]
        idx = self._addc(Sketcher.Constraint("Distance" + axis, g1, p1, g2, p2, float(d)), kind)
        self.bind(idx, expr)

    def dim_pos(self, gi, pi, x, y, xexpr=None, yexpr=None):
        if abs(x) < 1e-9 and abs(y) < 1e-9 and not xexpr and not yexpr:
            self._addc(Sketcher.Constraint("Coincident", gi, pi, -1, 1), "o")
        else:
            self.dim_dist(-1, 1, gi, pi, x, "X", expr=xexpr)
            self.dim_dist(-1, 1, gi, pi, y, "Y", expr=yexpr)

    def anchor_first(self, gi, pi):
        """Anchor the very first point of the sketch (origin relative)."""
        self.dim_pos(gi, pi, 0, 0)

    # -- primitives -------------------------------------------------------
    def circle(self, cx, cy, r, x_expr=None, y_expr=None, r_expr=None):
        gi = self.sk.addGeometry(
            Part.Circle(_v(cx, cy, 0), _v(0, 0, 1), float(r)), False)
        self.dim_pos(gi, 3, cx, cy, xexpr=x_expr, yexpr=y_expr)
        self.dim_radius(gi, r, expr=r_expr)
        return gi

    def line(self, p1, p2, dims=True, prev=None):
        p1, p2 = _v(p1[0], p1[1]), _v(p2[0], p2[1])
        gi = self.sk.addGeometry(Part.LineSegment(p1, p2), False)
        if prev is not None:
            self._addc(Sketcher.Constraint("Coincident", prev, 2, gi, 1), "geom")
        if dims:
            dx, dy = p2.x - p1.x, p2.y - p1.y
            if abs(dy) < 1e-9:
                self._addc(Sketcher.Constraint("Horizontal", gi), "geom")
                self.dim_dist(gi, 1, gi, 2, dx, "X")
            elif abs(dx) < 1e-9:
                self._addc(Sketcher.Constraint("Vertical", gi), "geom")
                self.dim_dist(gi, 1, gi, 2, dy, "Y")
            else:
                self.dim_dist(gi, 1, gi, 2, dx, "X")
                self.dim_dist(gi, 1, gi, 2, dy, "Y")
        return gi

    def closed_line_loop(self, pts, anchor=True, exprs=None):
        """Fully constrained closed polyline: anchor + DX/DY on all but the
        closing segment (its two endpoints are already determined, dimensioning
        it too would conflict). ``exprs`` optionally binds constraint cells:
        keys ``x0,y0`` (anchor point) and ``dx<i>,dy<i>`` (per segment)."""
        exprs = exprs or {}
        n = len(pts)
        idxs = []
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            gi = self.sk.addGeometry(
                Part.LineSegment(_v(x1, y1, 0), _v(x2, y2, 0)), False)
            if idxs:
                self._addc(Sketcher.Constraint("Coincident", idxs[-1], 2, gi, 1), "geom")
            if i < n - 1:
                self.dim_dist(gi, 1, gi, 2, x2 - x1, "X", expr=exprs.get("dx%d" % i))
                self.dim_dist(gi, 1, gi, 2, y2 - y1, "Y", expr=exprs.get("dy%d" % i))
            idxs.append(gi)
        self._addc(Sketcher.Constraint("Coincident", idxs[-1], 2, idxs[0], 1), "geom")
        if anchor:
            self.dim_pos(idxs[0], 1, pts[0][0], pts[0][1],
                         xexpr=exprs.get("x0"), yexpr=exprs.get("y0"))
        return idxs

    def rect(self, cx, cy, w, h, w_expr=None, h_expr=None, x_expr=None, y_expr=None):
        """Fully constrained centered rectangle: coincident loop + H/V + w + h."""
        x, y, w, h = float(cx), float(cy), float(w), float(h)
        pts = [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
               (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)]
        idxs = []
        for i in range(4):
            p1, p2 = _v(*pts[i], 0), _v(*pts[(i + 1) % 4], 0)
            gi = self.sk.addGeometry(Part.LineSegment(p1, p2), False)
            if idxs:
                self._addc(Sketcher.Constraint("Coincident", idxs[-1], 2, gi, 1), "geom")
            idxs.append(gi)
        self._addc(Sketcher.Constraint("Coincident", idxs[3], 2, idxs[0], 1), "geom")
        self._addc(Sketcher.Constraint("Horizontal", idxs[0]), "geom")
        self._addc(Sketcher.Constraint("Horizontal", idxs[2]), "geom")
        self._addc(Sketcher.Constraint("Vertical", idxs[1]), "geom")
        self._addc(Sketcher.Constraint("Vertical", idxs[3]), "geom")
        self.dim_pos(idxs[0], 1, pts[0][0], pts[0][1], xexpr=x_expr, yexpr=y_expr)
        self.dim_dist(idxs[0], 1, idxs[0], 2, w, "X", expr=w_expr)
        self.dim_dist(idxs[1], 1, idxs[1], 2, h, "Y", expr=h_expr)
        return idxs

    def regular_polygon(self, cx, cy, r, n, rot=0.0):
        pts = []
        for i in range(n):
            a = rot + 2 * math.pi * i / n
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        idxs = self.closed_line_loop(pts)
        return idxs

    def polygon(self, points):
        return self.closed_line_loop(points)

    def slot(self, x1, y1, x2, y2, w):
        raise NotImplementedError

    # -- assembly of the sketch ------------------------------------------
    def finish(self):
        rc = self.sk.solve()
        if rc not in (0,):
            raise BuildError("sketch %s unsolved (rc=%s)" % (self.name, rc))
        return self.sk


# --------------------------------------------------------------------------
# Part builder
# --------------------------------------------------------------------------
ORIGIN_TYPES = ("App::Origin", "App::Plane", "App::Line", "App::LocalCoordinateSystem")
CONTAINER_TYPES = ("App::DocumentObjectGroup", "App::Part")


class PartBuilder:
    """Owns a FreeCAD document and the feature-history rules of a project."""

    def __init__(self, name, unit="mm", type_="part", note=""):
        self.doc_name = check_safe_name(name)
        self.doc = App.newDocument(self.doc_name)
        self.unit = unit
        self.type = type_
        self.note = note
        self.errors = []
        self._order = 0

    # -- bookkeeping ------------------------------------------------------
    def _recompute(self, label):
        if os.environ.get("CADKIT_TRACE"):
            import sys
            sys.stderr.write("CADKIT-TRACE> %s\n" % label)
            sys.stderr.flush()
        self.doc.recompute()
        bad = [o.Name for o in self.doc.Objects
               if any(s in o.State for s in ("Invalid", "Error", "Touched"))]
        if bad:
            raise BuildError("recompute failed after %s: %s" % (label, ", ".join(bad)))

    # -- parameter table ----------------------------------------------------
    def params(self, alias_map, sheet="params"):
        """Create the driving parameter sheet (one source of truth).

        All sketches/features bind their driving dimensions to
        ``params.<ALIAS>`` so editing one cell updates the whole model
        consistently (spec 2.4 editability; also the family-variant mechanism).
        """
        sh = self.doc.addObject("Spreadsheet::Sheet", check_safe_name(sheet))
        sh.Label = sheet
        row = 1
        for alias, val in alias_map.items():
            cell = "A%d" % row
            sh.set(cell, "%.10g" % float(val))
            try:
                sh.setAlias(cell, alias)
            except Exception as e:
                raise BuildError("bad parameter alias %r: %s" % (alias, e))
            row += 1
        self._recompute(sheet)
        return sh

    def pv(self, alias):
        """Current value of a parameter cell (keep edge selectors in sync)."""
        return float(self.doc.getObject("params").get(alias))

    # -- sketches ---------------------------------------------------------
    def sketch(self, name, plane="XY", offset=0.0, invert=False, attach=None,
               offset_expr=None):
        return SketchBuilder(self, name, plane=plane, offset=offset,
                             invert=invert, attach=attach, offset_expr=offset_expr)

    # -- solid features ----------------------------------------------------
    @staticmethod
    def _sdir(sketch, sign):
        """Unit extrude direction = sketch plane normal (times sign)."""
        return sketch.sk.Placement.Rotation.multVec(_v(0, 0, sign))

    def pad(self, name, sketch, length, taper=0.0, midplane=False, reversed_=False,
            length_rev=0.0, length_expr=None):
        e = self.doc.addObject("Part::Extrusion", check_safe_name(name))
        e.Label = e.Name
        e.Base = sketch.sk
        e.Dir = self._sdir(sketch, -1 if reversed_ else 1)
        e.LengthFwd = float(length)
        e.LengthRev = float(length_rev)
        e.Solid = True
        e.Symmetric = bool(midplane)
        e.TaperAngle = float(taper)
        if length_expr:
            e.setExpression("LengthFwd", _ex(length_expr))
        self._recompute(e.Name)
        return e

    def pad_fuse(self, name, base, sketch, length, reversed_=False, taper=0.0,
                 length_expr=None):
        """Extrude a profile and fuse it onto ``base`` (additive boss)."""
        e = self.doc.addObject("Part::Extrusion", check_safe_name(name) + "_ext")
        e.Label = e.Name
        e.Base = sketch.sk
        e.Dir = self._sdir(sketch, -1 if reversed_ else 1)
        e.LengthFwd = float(length)
        e.Solid = True
        e.TaperAngle = float(taper)
        if length_expr:
            e.setExpression("LengthFwd", _ex(length_expr))
        f = self.doc.addObject("Part::MultiFuse", check_safe_name(name))
        f.Label = f.Name
        f.Shapes = [base, e]
        self._recompute(f.Name)
        return f

    def pocket(self, name, base, sketch, depth, through=False, reversed_=False,
               depth_expr=None, taper=0.0):
        bb = base.Shape.BoundBox
        ln = math.sqrt(bb.XLength**2 + bb.YLength**2 + bb.ZLength**2) if through else depth
        e = self.doc.addObject("Part::Extrusion", check_safe_name(name) + "_cutter")
        e.Label = e.Name
        e.Base = sketch.sk
        e.Dir = self._sdir(sketch, 1 if reversed_ else -1)
        e.LengthFwd = float(ln)
        e.Solid = True
        if taper:
            e.TaperAngle = float(taper)
        if depth_expr and not through:
            e.setExpression("LengthFwd", _ex(depth_expr))
        c = self.doc.addObject("Part::Cut", check_safe_name(name))
        c.Label = c.Name
        c.Base = base
        c.Tool = e
        self._recompute(c.Name)
        return c

    def boss(self, name, base, sketch, depth, reversed_=False):
        e = self.doc.addObject("Part::Extrusion", check_safe_name(name) + "_boss")
        e.Label = e.Name
        e.Base = sketch.sk
        e.Dir = _v(0, 0, 1 if reversed_ else -1)
        e.LengthFwd = float(depth)
        e.Solid = True
        f = self.doc.addObject("Part::MultiFuse", check_safe_name(name))
        f.Label = f.Name
        f.Shapes = [base, e]
        self._recompute(f.Name)
        return f

    def fuse(self, name, shapes):
        f = self.doc.addObject("Part::MultiFuse", check_safe_name(name))
        f.Label = f.Name
        f.Shapes = list(shapes)
        self._recompute(f.Name)
        return f

    def cut(self, name, base, tool):
        c = self.doc.addObject("Part::Cut", check_safe_name(name))
        c.Label = c.Name
        c.Base, c.Tool = base, tool
        self._recompute(c.Name)
        return c

    def revolve(self, name, sketch, angle=360.0, axis="normal", reference=None):
        r = self.doc.addObject("Part::Revolution", check_safe_name(name))
        r.Label = r.Name
        r.Source = sketch.sk            # FreeCAD 1.1: input link is Source
        r.Angle = float(angle)
        r.Solid = True
        # FreeCAD 1.1: Revolution.Axis is a plain direction through the profile
        # origin; map the requested local axis to a global vector via the sketch
        # placement so XY/XZ/YZ sketches revolve about the intended axis.
        rot = sketch.sk.Placement.Rotation
        local = {"normal": (0, 0, 1), "vx": (1, 0, 0), "vy": (0, 1, 0)}[axis]
        g = rot.multVec(_v(*local))
        if reference is not None:
            r.AxisLink = reference
        r.Axis = (g.x, g.y, g.z)
        self._recompute(r.Name)
        return r

    def polar_pattern(self, name, base, count, sketch, axis="normal", angle=360.0):
        p = self.doc.addObject("Part::PolarPattern", check_safe_name(name))
        p.Label = p.Name
        p.Base = (base, [])
        p.ReferenceAxis = (sketch.sk, ["N_Axis"])
        p.Axis = (0, 0, 1)
        p.Angle = float(angle)
        p.Occurrences = int(count)
        self._recompute(p.Name)
        return p

    def linear_array(self, name, base, sketch, inter_x, inter_y, n_x, n_y, inter_z=0, n_z=1):
        a = self.doc.addObject("Part::Array", check_safe_name(name))
        a.Label = a.Name
        a.Base = (base, [])
        a.NumberX, a.NumberY, a.NumberZ = int(n_x), int(n_y), int(n_z)
        a.IntervalX = (inter_x, 0, 0)
        a.IntervalY = (0, inter_y, 0)
        a.IntervalZ = (0, 0, inter_z)
        pos = sketch.sk
        a.OriginalPos = pos.Placement.Base
        self._recompute(a.Name)
        return a

    def mirror_sym(self, name, base, plane_sketch):
        m = self.doc.addObject("Part::Mirroring", check_safe_name(name))
        m.Label = m.Name
        m.Source = base
        m.Normal = (plane_sketch.sk, (0, 0, 1))
        m.Base = plane_sketch.sk.Placement.Base
        self._recompute(m.Name)
        return m

    def loft(self, name, sketches, solid=True, ruled=False, closed=False):
        l = self.doc.addObject("Part::Loft", check_safe_name(name))
        l.Label = l.Name
        l.Sections = [s.sk for s in sketches]
        l.Solid = solid
        l.Ruled = ruled
        l.Closed = closed
        self._recompute(l.Name)
        return l

    def shell(self, name, base, remove_faces, thickness):
        t = self.doc.addObject("Part::Thickness", check_safe_name(name))
        t.Label = t.Name
        t.Source = base
        t.Faces = [(remove_faces[0], list(remove_faces[1]))]
        t.Offset = -abs(float(thickness))
        t.Intersection = False
        t.Refine = True
        self._recompute(t.Name)
        return t

    def fillet_edges(self, name, base, edge_indices, radius):
        f = self.doc.addObject("Part::Fillet", check_safe_name(name))
        f.Label = f.Name
        self._set_dressup(f, base, edge_indices, radius)
        self._recompute(f.Name)
        return f

    def chamfer_edges(self, name, base, edge_indices, size):
        c = self.doc.addObject("Part::Chamfer", check_safe_name(name))
        c.Label = c.Name
        self._set_dressup(c, base, edge_indices, size)
        self._recompute(c.Name)
        return c

    @staticmethod
    def _set_dressup(obj, base, edge_indices, val):
        """FreeCAD 1.1: Base is the object link, Edges holds (idx, v1, v2)."""
        obj.Base = base
        obj.Edges = [(int(i), float(val), float(val)) for i in edge_indices]

    def dressup_by_selector(self, kind, name, base, radius_or_size, predicate):
        """fillet/chamfer with edge indices computed from a geometric predicate.

        predicate(edge_1based_index, edge) -> bool
        """
        idxs = [i for i, e in enumerate(base.Shape.Edges, start=1) if predicate(i, e)]
        if not idxs:
            raise BuildError("%s %s: no edges selected" % (kind, name))
        if kind == "fillet":
            return self.fillet_edges(name, base, idxs, radius_or_size)
        return self.chamfer_edges(name, base, idxs, radius_or_size)

    # -- face finders ------------------------------------------------------
    def face(self, obj, predicate):
        """Return 'FaceN' (1-based) of first planar/cylindrical face matching."""
        for i, f in enumerate(obj.Shape.Faces, start=1):
            if predicate(f):
                return "Face%d" % i
        raise BuildError("no matching face on %s" % obj.Name)

    def face_index(self, obj, predicate):
        for i, f in enumerate(obj.Shape.Faces, start=1):
            if predicate(f):
                return i
        raise BuildError("no matching face on %s" % obj.Name)

    @staticmethod
    def planar_normal(normal, z=None, tol=1e-6):
        n = _v(*normal)

        def p(face):
            if not isinstance(face.Surface, Part.Plane):
                return False
            u, v = face.Surface.ParameterRanges
            fn = face.Surface.Axis
            if abs(abs(fn.dot(n)) - 1.0) > tol:
                return False
            if z is not None:
                c = face.CenterOfMass
                zc = z(c, face) if callable(z) else abs(c.z - z) < 1e-6
                if not zc:
                    return False
            return True
        return p

    @staticmethod
    def cylinder_at_radius(r, tol=0.05, axisz=True):
        def p(face):
            if not isinstance(face.Surface, Part.Cylinder):
                return False
            if axisz and abs(abs(face.Surface.Axis.z) - 1.0) > 1e-6:
                return False
            return abs(face.Surface.Radius - r) < tol
        return p

    def sketch_on(self, name, obj, predicate, offset=0.0, invert=False):
        facename = self.face(obj, predicate)
        return self.sketch(name, attach=(obj, facename), offset=offset, invert=invert)

    # -- finalize -----------------------------------------------------------
    def finalize(self, final_objs, save_path):
        if final_objs is None:
            objs_list = []
        elif isinstance(final_objs, (list, tuple)):
            objs_list = list(final_objs)
        else:
            objs_list = [final_objs]
        names = [o.Name for o in objs_list]
        meta = {"cadkit": 1, "final": names, "unit": self.unit,
                "type": self.type, "note": self.note}
        self.doc.Comment = json.dumps(meta, ensure_ascii=False)
        self.doc.recompute(None, True, True)
        self.doc.saveAs(save_path)
        return self.report()

    def report(self):
        bad = [o.Name for o in self.doc.Objects
               if any(s in o.State for s in ("Invalid", "Error", "Touched"))]
        return {"doc": self.doc.Name, "objects": len(self.doc.Objects),
                "recompute_errors": bad}

    def dispose(self):
        App.closeDocument(self.doc.Name)


def open_project_doc(fcstd_path, name):
    doc = App.openDocument(fcstd_path)
    return doc
