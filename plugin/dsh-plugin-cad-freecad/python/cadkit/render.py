"""Headless software renderer for the delivery images (spec 2.3).

Produces the 8 required views: front/back/left/right/top/bottom plus 45 deg
looking-down and 45 deg looking-up axonometric views. White background,
monochrome grey shading, no grid/axes/watermark, consistent framing, long
side >= 2000 px. Implemented as a pure-numpy z-buffer so it runs inside
freecadcmd without any GUI (FreeCAD GUI hangs headless on macOS).

Memory governance (see cadkit/mem.py): this module never allocates beyond
what the per-process cap allows.

  * the mesh is tessellated to a *triangle budget* (coarser deflection is
    auto-selected, logged as a degrade step) — previously the deflection was
    fixed and one complex part could park a million-plus triangles in Python
    containers, ~1.45 KB per triangle;
  * the triangle table and the vertex weld are built with vectorized numpy
    (~0.2 KB per triangle, no per-triangle Python objects, no 3 M entry dict);
  * the weld + id table are computed once per document instead of per view;
  * rasterization runs in horizontal bands so per-facet scratch is bounded
    (~40 MB) instead of scaling with the whole 4800 px supersample buffer;
  * buffers are released between views and the watchdog can drop
    supersampling mid-run if RSS approaches the cap.
"""
from __future__ import annotations

import gc
import math
import os
import struct
import zlib

import numpy as np

import FreeCAD as App
import MeshPart

from . import mem
from .validate import final_objects

VIEWS = [
    # name, view direction (into scene), up reference
    ("front",     (0, 1, 0),  (0, 0, 1)),
    ("back",      (0, -1, 0), (0, 0, 1)),
    ("left",      (1, 0, 0),  (0, 0, 1)),
    ("right",     (-1, 0, 0), (0, 0, 1)),
    ("top",       (0, 0, -1), (0, -1, 0)),
    ("bottom",    (0, 0, 1),  (0, 1, 0)),
    # 45 deg looking down / up, azimuth between front and right
    ("iso_down",  (-0.5, 0.5, -0.7071067811865476), (0, 0, 1)),
    ("iso_up",    (-0.5, 0.5, 0.7071067811865476),  (0, 0, -1)),
]

MIN_LONG_SIDE = 2000          # purchase spec: long side >= 2000 px
# Wall-clock ceiling matters as much as RSS here: the z-buffer loop is
# Python-driven, so an unbounded mesh would be slow long before it is huge.
TRI_CEILING = 600_000
# Scratch bound for one raster band, in bytes.
BAND_SCRATCH = 48 << 20


def collect_solids(doc):
    """Global-coordinate shapes of the delivered final solids."""
    out = []
    for o in final_objects(doc):
        try:
            sh = o.Shape
        except Exception:
            continue
        if sh is None or sh.isNull():
            continue
        g = o.getGlobalPlacement()
        sh = sh.copy()
        sh.Placement = g
        out.append((o.Name, sh))
    if not out:  # assembly whose terminals are links
        for o in doc.Objects:
            if o.TypeId == "App::Link" and o.LinkedObject is not None:
                lo = o.LinkedObject
                try:
                    sh = lo.Shape
                except Exception:
                    continue
                if sh is None or sh.isNull():
                    continue
                sh = sh.copy()
                sh.Placement = o.getGlobalPlacement().multiply(lo.getGlobalPlacement())
                out.append((o.Name, sh))
    return out


def _add(a, b):
    a.add(b)
    return a


def _diag(shapes):
    total_bb = None
    for _, sh in shapes:
        bb = sh.BoundBox
        total_bb = bb if total_bb is None else _add(total_bb, bb)
    if total_bb is None:
        return 1.0
    return math.sqrt(total_bb.XLength ** 2 + total_bb.YLength ** 2 +
                     total_bb.ZLength ** 2)


def _mesh_one(sh, lin, angular):
    """Tessellate one shape -> (points (V,3) float64, faces (F,3) int32)."""
    try:
        m = MeshPart.meshFromShape(Shape=sh, LinearDeflection=lin,
                                   AngularDeflection=angular, Relative=False)
    except Exception:
        m = MeshPart.meshFromShape(Shape=sh, LinearDeflection=lin * 4,
                                   AngularDeflection=0.6, Relative=False)
    pts, faces = m.Topology
    if not pts:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int32)
    P = np.fromiter((c for p in pts for c in (p.x, p.y, p.z)), float,
                    count=len(pts) * 3).reshape(-1, 3)
    F = np.fromiter((c for f in faces for c in f), np.int32,
                    count=len(faces) * 3).reshape(-1, 3)
    del m, pts, faces
    return P, F


def _tessellate(shapes, tri_budget=None):
    """Return (tris (T,3,3) float64, info).

    `tri_budget` caps the facet count: the deflection is widened (area
    scaling => linear ~ sqrt) until the estimate fits, and the widening is
    reported as an auto-degrade so a human can re-shoot at higher fidelity
    with a bigger cap.
    """
    diag = _diag(shapes)
    lin0 = max(diag * 0.0009, 5e-5)
    info = {"budget": int(tri_budget) if tri_budget else None}
    lin, scale = lin0, 1.0
    tris = None
    for attempt in range(4):
        chunks = []
        est = 0
        over = False
        for name, sh in shapes:
            P, F = _mesh_one(sh, lin * scale, 0.28 if scale == 1 else 0.6)
            if len(F):
                F = F[F.min(axis=1) >= 0]          # drop degenerate facets
            if len(P) and len(F):
                chunks.append(P[F])
                est += len(F)
            del P, F
            if tri_budget and est > tri_budget * 1.35:
                over = True
                break                              # re-mesh coarser, cheaply
        if not over:
            if not est:
                raise RuntimeError("no geometry to render")
            tris = np.ascontiguousarray(chunks[0].reshape(-1, 3, 3)) if len(chunks) == 1 \
                else np.ascontiguousarray(np.concatenate(chunks)).reshape(-1, 3, 3)
            chunks = None
            gc.collect()
            break
        chunks = None
        gc.collect()
        info["budget_hit"] = True
        if attempt < 3:
            scale *= math.sqrt(est / float(tri_budget)) * 1.15
            mem.degrade("mesh density reduced to fit the memory budget: "
                        ">%d triangles > budget %d (deflection x%.2f)"
                        % (est, tri_budget, scale))
        else:
            # W16 (workflow audit): removed the uncapped fallback pass —
            # "capped as far as the shape allows" meant an unbounded mesh
            # could still be materialized under a hard memory cap.
            # Infeasible render is now a FAIL, not an escape hatch.
            raise RuntimeError(
                "cannot fit mesh under triangle budget %d after 4 coarsening "
                "passes (last estimate %d) — raise memCapMb/max_triangles "
                "or simplify the model" % (tri_budget, est))
    info["deflection_scale"] = round(scale, 3)
    info["budgeted_tris"] = tri_budget
    return tris, info


def _tessellate_uncapped(shapes, lin):
    """Final coarse pass: mesh everything at `lin`, no budget check."""
    chunks = []
    for name, sh in shapes:
        P, F = _mesh_one(sh, lin, 0.6)
        if len(F):
            F = F[F.min(axis=1) >= 0]
        if len(P) and len(F):
            chunks.append(P[F])
        del P, F
    if not chunks:
        raise RuntimeError("no geometry to render")
    return np.ascontiguousarray(np.concatenate(chunks)).reshape(-1, 3, 3), None


def _weld(tris):
    """Vertex id table identical to the old dict-of-rounded-tuples weld,
    built with one sort instead of 3 T Python objects."""
    key = np.round(tris, 5).reshape(-1, 3)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    nv = int(inv.max()) + 1
    vid = np.ascontiguousarray(inv.reshape(tris.shape[0], 3), np.int32)
    del key, inv
    return vid, nv


def _vertex_normals(n3, vid, nv):
    """Crease-filtered smooth normals: a vertex shares lighting only with
    adjacent facets within 60 deg of its first facet, so cylinders shade
    smoothly while machined edges stay crisp.

    Equivalent to the original per-vertex Python loop, but vectorized. All
    slot-wise arrays stay in the ORIGINAL (facet-major) order; the sorted
    order is used only to find each vertex's first adjacent facet.
    """
    T = n3.shape[0]
    vidf = vid.reshape(-1)                            # slot -> vertex id
    order = np.argsort(vidf, kind="stable")           # ties keep facet order
    vs = vidf[order]
    first_pos = np.flatnonzero(np.concatenate(([True], vs[1:] != vs[:-1])))
    first = np.zeros(nv, np.int64)
    first[vs[first_pos]] = order[first_pos] // 3      # lowest adjacent facet
    dom = n3[first]
    with np.errstate(invalid="ignore", divide="ignore"):
        dom = dom / np.linalg.norm(dom, axis=1, keepdims=True)
    n3f = n3[np.repeat(np.arange(T), 3)]              # (3T,3) slot facet normal
    keep = np.einsum("ij,ij->i", n3f, dom[vidf]) > 0.5
    kv, kf = vidf[keep], n3f[keep]
    nacc = np.empty((nv, 3), np.float64)
    for c in range(3):
        nacc[:, c] = np.bincount(kv, weights=kf[:, c], minlength=nv)
    empty = ~np.any(nacc, axis=1)
    if empty.any():
        nacc[empty] = dom[empty]
    del n3f
    return nacc


def _nrm(v):
    n = np.linalg.norm(v)
    return v / (n if n > 1e-12 else 1.0)


def _lit(n):
    with np.errstate(invalid="ignore", divide="ignore"):
        nn = n / np.linalg.norm(n, axis=-1, keepdims=True)
    nn = np.nan_to_num(nn)
    l1 = _nrm(np.array([0.35, 0.55, -0.75]))          # key light
    l2 = _nrm(np.array([-0.6, -0.35, -0.5]))          # fill light
    lam = 0.40 + 0.44 * np.clip(nn @ l1, 0, 1) + 0.16 * np.clip(nn @ l2, 0, 1)
    return np.clip(lam * 175 + 42, 36, 232)


def _frame(tris, dvec, upref, size, fill=0.82, scale=None):
    """Project the mesh for one view: screen coords, depth, facet normals,
    per-vertex Gouraud shades, per-facet bounding box.
    W24: when `scale` (px/mm) is given it is used as-is — all eight views of
    one export share a single model-space scale (common framing)."""
    d = _nrm(np.array(dvec, float))
    up = np.array(upref, float)
    u = up - up.dot(d) * d
    u = _nrm(u)
    r = _nrm(np.cross(d, u))

    allp = tris.reshape(-1, 3)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    ctr = (lo + hi) / 2.0
    Q = tris - ctr
    X = Q @ r
    Y = Q @ u
    D = Q @ d
    if scale is None:
        span = max(X.max() - X.min(), Y.max() - Y.min(), 1e-12)
        scale = fill * size / span
    ox, oy = 0.5 * (X.max() + X.min()), 0.5 * (Y.max() + Y.min())
    sx = (X - ox) * scale + size * 0.5
    sy = size * 0.5 - (Y - oy) * scale          # y down

    n3 = np.cross(Q[:, 1] - Q[:, 0], Q[:, 2] - Q[:, 0])
    with np.errstate(invalid="ignore", divide="ignore"):
        n3 /= np.linalg.norm(n3, axis=1, keepdims=True)
    n3 = np.nan_to_num(n3)
    n3[n3[:, 2] > 0] *= -1                      # faces toward viewer

    shade = _lit(n3)                            # per-triangle fallback
    return dict(sx=sx, sy=sy, D=D, n3=n3, shade=shade)


def _raster_band(fr, vid, vshade, size, y0b, y1b, zbuf, img):
    """Rasterize rows [y0b, y1b) of one view in far->near painter order.
    Scratch per facet is bounded by BAND_SCRATCH (row band x column split)."""
    sx, sy, D = fr["sx"], fr["sy"], fr["D"]
    T = sx.shape[0]
    bx0 = np.maximum(np.floor(sx.min(axis=1)), 0).astype(np.int64)
    bx1 = np.minimum(np.ceil(sx.max(axis=1)), size - 1).astype(np.int64)
    by0 = np.maximum(np.floor(sy.min(axis=1)), 0).astype(np.int64)
    by1 = np.minimum(np.ceil(sy.max(axis=1)), size - 1).astype(np.int64)
    band = (by0 <= y1b - 1) & (by1 >= y0b) & (bx0 <= bx1)
    if not band.any():
        return
    order = np.argsort(D.mean(axis=1), kind="stable")   # far -> near
    band_h = max(1, y1b - y0b)
    maxw = max(8, min(size, int(BAND_SCRATCH / max(1, band_h * 4 * 6))))
    for ti in order[band[order]]:
        x0, x1 = int(bx0[ti]), int(bx1[ti])
        y0 = max(int(by0[ti]), y0b)
        y1 = min(int(by1[ti]), y1b - 1)
        if y0 > y1:
            continue
        xs = sx[ti]
        ys = sy[ti]
        ds = D[ti].astype(np.float32)
        (ax, ay, ad), (bx, by, bd), (cx, cy, cd) = (
            (xs[0], ys[0], ds[0]), (xs[1], ys[1], ds[1]), (xs[2], ys[2], ds[2]))
        area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(area) < 1e-9:
            continue
        cols = x1 - x0 + 1
        steps = 1 if cols <= maxw else int(math.ceil(cols / float(maxw)))
        cw = int(math.ceil(cols / float(steps)))
        for seg in range(steps):
            cx0 = x0 + seg * cw
            cx1 = min(x1, cx0 + cw - 1)
            if cx0 > cx1:
                break
            gw = np.arange(cx0, cx1 + 1, dtype=np.float32) + 0.5
            gh = np.arange(y0, y1 + 1, dtype=np.float32) + 0.5
            gx, gy = gw[None, :] - cx, gh[:, None] - cy
            wA = ((by - cy) * gx + (cx - bx) * gy) / area
            wB = ((cy - ay) * gx + (ax - cx) * gy) / area
            wC = 1.0 - wA - wB
            inside = (wA >= 0) & (wB >= 0) & (wC >= 0)
            if not inside.any():
                continue
            zpl = wA * ad + wB * bd + wC * cd
            zreg = zbuf[y0:y1 + 1, cx0:cx1 + 1]
            hit = inside & (zpl < zreg)
            if not hit.any():
                continue
            v0, v1, v2 = vshade[vid[ti, 0]], vshade[vid[ti, 1]], vshade[vid[ti, 2]]
            if max(abs(v0 - v1), abs(v1 - v2), abs(v0 - v2)) > 36:
                g = np.uint8(float(fr["shade"][ti]))    # crease: flat shading
            else:
                g = np.clip(wA * v0 + wB * v1 + wC * v2, 36, 232).astype(np.uint8)
            zbuf[y0:y1 + 1, cx0:cx1 + 1] = np.where(hit, zpl, zreg)
            tile = img[y0:y1 + 1, cx0:cx1 + 1]
            img[y0:y1 + 1, cx0:cx1 + 1] = np.where(
                hit[..., None], g[..., None] if g.ndim == 2 else g, tile)


def _render_view(tris, dvec, upref, size, vid=None, nv=None, band_rows=None,
                 wd=None, scale=None):
    """Render one view of an already-welded mesh."""
    fr = _frame(tris, dvec, upref, size, scale=scale)
    if vid is None:
        vid, nv = _weld(tris)
    nacc = _vertex_normals(fr["n3"], vid, nv)
    vshade = _lit(nacc).astype(np.float32)
    del nacc
    zbuf = np.full((size, size), np.inf, np.float32)
    img = np.full((size, size, 3), 255, np.uint8)
    if band_rows is None:
        band_rows = max(64, int(BAND_SCRATCH / max(1, size * 4 * 6)))
    for y0 in range(0, size, band_rows):
        _raster_band(fr, vid, vshade, size, y0, min(size, y0 + band_rows),
                     zbuf, img)
        if wd is not None:
            wd.sample()
    del zbuf, fr, vshade
    gc.collect()
    return img


def downsample2(img, out_size):
    h, w, _ = img.shape
    small = img[: h // 2 * 2, : w // 2 * 2]
    avg = small.reshape(small.shape[0] // 2, 2, small.shape[1] // 2, 2, 3)
    out = avg.mean(axis=(1, 3)).astype(np.uint8)
    del avg, small
    if out.shape[0] != out_size:
        ys = (np.linspace(0, out.shape[0] - 1, out_size)).round().astype(int)
        xs = (np.linspace(0, out.shape[1] - 1, out_size)).round().astype(int)
        out = out[np.ix_(ys, xs)]
    return out


def write_png(path, rgb):
    """Encode an RGB8 PNG row-by-row: only the compressed payload is ever
    buffered, never the full uncompressed scanline copy."""
    h, w, _ = rgb.shape
    co = zlib.compressobj(6)
    body = []
    for y in range(h):
        b = co.compress(b"\x00" + rgb[y].tobytes())
        if b:
            body.append(b)
    tail = co.flush()
    if tail:
        body.append(tail)
    data = b"".join(body)

    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", data))
        f.write(chunk(b"IEND", b""))


def plan(long_side=2400, cap=None, max_triangles=None):
    """Decide the working point from the memory cap (pure arithmetic)."""
    cap = cap or mem.cap_mb()
    allow = max(120.0, cap * 0.55)
    fb_limit = int(((allow * 1048576.0 / mem.RENDER_BYTES_PER_PX) / 4.0) ** 0.5)
    ss_ok = (long_side * 2) ** 2 * mem.RENDER_BYTES_PER_PX <= allow * 1048576.0
    tri = mem.tri_budget(allow_mb=max(64.0, cap * 0.30), cap=cap)
    tri = min(tri, int(max_triangles) if max_triangles else TRI_CEILING)
    return {
        "cap_mb": int(cap),
        "long_side": int(min(long_side, max(MIN_LONG_SIDE, fb_limit))),
        "supersample": 2 if ss_ok else 1,
        "max_triangles": int(tri),
    }


def render_document(fcstd_path, out_dir, long_side=2400, views=None,
                    max_triangles=None, wd=None):
    cap = mem.cap_mb()
    doc = App.openDocument(fcstd_path)
    shapes = None
    tris = None
    try:
        shapes = collect_solids(doc)
        if not shapes:
            raise RuntimeError("no final solids to render")
        p = plan(long_side, cap=cap, max_triangles=max_triangles)
        if p["long_side"] < long_side:
            mem.degrade("render long side %d -> %d px to fit the %.0fMB cap"
                        % (long_side, p["long_side"], cap))
        if p["supersample"] < 2:
            mem.degrade("2x supersampling disabled for the %.0fMB cap "
                        "(1x still >= 2000 px long side)" % cap)
        tri_allow = max(64.0, cap * 0.9 - mem.rss_mb() -
                        (p["long_side"] * p["supersample"]) ** 2
                        * mem.RENDER_BYTES_PER_PX / 1048576.0)
        p["max_triangles"] = int(min(p["max_triangles"],
                                     mem.tri_budget(allow_mb=tri_allow, cap=cap)))
        tris, info = _tessellate(shapes, p["max_triangles"])
        info.update(p)
        os.makedirs(out_dir, exist_ok=True)
        vid, nv = _weld(tris)
        info["vertices"] = int(nv)
        out = []
        size = p["long_side"] * p["supersample"]
        # W24 (workflow audit): compute ONE shared px/mm scale from the mesh
        # bounding-box corners across all views, so the eight views share a
        # common model scale (buyer 2.3 framing); serialized to the export
        # manifest and re-checked by the packager.
        _views = list(views or VIEWS)
        allp = tris.reshape(-1, 3)
        lo, hi = allp.min(axis=0), allp.max(axis=0)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0])
                            for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        maxspan = 1e-12
        for _n, dv, ur in _views:
            _d = _nrm(np.array(dv, float))
            _u = _nrm(np.array(ur, float) - np.array(ur, float).dot(_d) * _d)
            _r = _nrm(np.cross(_d, _u))
            cx = corners @ _r
            cy = corners @ _u
            maxspan = max(maxspan, cx.max() - cx.min(), cy.max() - cy.min())
        px_mm = 0.82 * size / maxspan
        for name, dvec, upref in _views:
            if wd is not None and wd.level >= 1 and size > p["long_side"]:
                size = p["long_side"]
                mem.degrade("supersampling dropped mid-run at %.0fMB RSS"
                            % wd.last)
                info["supersample"] = 1
            img = _render_view(tris, dvec, upref, size, vid=vid, nv=nv,
                               wd=wd, scale=0.82 * size / maxspan)
            if size > p["long_side"]:
                small = downsample2(img, p["long_side"])
                del img
                img = small
            path = os.path.join(out_dir, "view_%s.png" % name)
            write_png(path, img)
            del img
            gc.collect()
            out.append(path)
            if wd is not None:
                wd.sample()
                if wd.level >= 1:
                    size = p["long_side"]
        info["mem"] = mem.status({"tri_bytes_live": int(tris.nbytes)})
        return {"triangles": int(len(tris)), "files": out,
                "size": p["long_side"], "supersample": info["supersample"],
                "max_triangles": p["max_triangles"],
                "px_mm_at_long_side": 0.82 * p["long_side"] / float(maxspan),
                "views": [n for n, _d, _u in _views],
                "deflection_scale": info.get("deflection_scale", 1.0),
                "budget_hit": bool(info.get("budget_hit")),
                "mem": info["mem"]}
    finally:
        shapes = None
        tris = None
        vid = None
        gc.collect()
        try:
            App.closeDocument(doc.Name)
        except Exception:
            pass
