#!/usr/bin/env freecadcmd
"""cadkit CLI run under freecadcmd.

usage: freecadcmd entry.py --action <action> [--root <cadRoot>]
        [--project <id>] [--out <result.json>] [--opt k=v ...]

Actions:
  new      create project skeleton        opts: name= desc= unit= grade= type= author= series=
  build    run project build.py -> FCStd
  validate acceptance gate -> build/report.json
  probe    editability probe  -> build/probe.json
  export   STEP + 8 renders (opts: long_side=2400)
  package  delivery folder + checklist   opts: batch=required
  dedupe   fingerprint check             opts: register=1
  status   project state summary
  board    rebuild <cadRoot>/mem/board.json for the sidebar panel
  memcheck report the memory budget and the next export working point
Every action writes {"ok": bool, ...} JSON to --out and prints CADKIT>> {json}.
"""
import importlib.util
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cadkit.project import Project  # noqa: E402


def parse_args(argv):
    opts, out, root, project = {}, None, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--action":
            action = argv[i + 1]; i += 2; continue
        if a == "--root":
            root = argv[i + 1]; i += 2; continue
        if a == "--project":
            project = argv[i + 1]; i += 2; continue
        if a == "--out":
            out = argv[i + 1]; i += 2; continue
        if a == "--opt" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            opts[k] = v; i += 2; continue
        i += 1
    return action, root, project, out, opts


def load_project(root, pid):
    return Project(os.path.join(os.path.abspath(root), "projects", pid))


def act_new(root, opts):
    meta = {
        "project_id": opts.get("id"),
        "project_name": opts.get("name", opts.get("id", "")),
        "description": opts.get("desc", ""),
        "unit": opts.get("unit", "mm"),
        "type": opts.get("type", "part"),
        "target_grade": opts.get("grade", "A"),
        "author": opts.get("author", ""),
        "source_url": opts.get("source_url", "self-originated"),
        "license": opts.get("license", "Author-granted commercial redistribution"),
        "series": opts.get("series", ""),
    }
    if not meta["project_id"]:
        raise ValueError("new requires opts id=...")
    proj = Project.create(root, meta)
    if not os.path.exists(os.path.join(proj.root, "build.py")):
        with open(os.path.join(proj.root, "build.py"), "w", encoding="utf-8") as f:
            f.write(BUILD_STUB % {"id": meta["project_id"]})
    for fname, tmpl in (("design.md", DESIGN_MD), ("requirements.md", REQUIREMENTS_MD)):
        p = os.path.join(proj.root, fname)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(tmpl)
    return {"ok": True, "project": proj.id, "dir": proj.root,
            "next": "edit build.py (features + named dimensions), then design.md, then action=build"}


def act_build(root, pid):
    proj = load_project(root, pid)
    script = os.path.join(proj.root, "build.py")
    spec = importlib.util.spec_from_file_location("cadbuild", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from cadkit.docctx import PartBuilder
    b = PartBuilder(proj.id, unit=proj.field("unit", "mm"),
                    type_=proj.field("type", "part"))
    try:
        ret = mod.build(b, proj)
        res = b.finalize(ret, proj.fcstd_path)
        return {"ok": not res["recompute_errors"], "project": pid,
                "objects": res["objects"], "recompute_errors": res["recompute_errors"],
                "fcstd": proj.fcstd_path}
    finally:
        b.dispose()


def _final_of(mod, b):
    return None  # deprecated: build() returns the final object directly


def act_validate(root, pid, opts=None):
    from cadkit.validate import validate_project
    proj = load_project(root, pid)
    _sb = (opts or {}).get("strict_bop")
    rep = validate_project(proj, strict_bop=None if _sb is None else bool(_sb))
    with open(os.path.join(proj.build_dir, "report.json"), "w",
              encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return rep


def act_probe(root, pid, opts, wd=None):
    from cadkit.probe import probe_project
    proj = load_project(root, pid)
    rep = probe_project(proj,
                        limit=int(opts.get("limit", 40)),
                        delta=float(opts.get("delta", 0.01)),
                        workers=(None if opts.get("workers") in (None, "")
                                 else int(opts["workers"])))
    with open(os.path.join(proj.build_dir, "probe.json"), "w",
              encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return rep


def act_export(root, pid, opts, wd=None):
    from cadkit.exporter import export_step, consistency_check, step_schema
    from cadkit.render import render_document
    from cadkit import version as ver
    from cadkit.ledger import sha256_file
    proj = load_project(root, pid)
    long_side = int(opts.get("long_side", "2400"))
    max_tri = opts.get("max_triangles")
    step = export_step(proj.fcstd_path, proj.step_path)
    cons = consistency_check(proj.fcstd_path, step)
    ren = render_document(proj.fcstd_path, proj.renders_dir, long_side=long_side,
                          max_triangles=(None if max_tri in (None, "")
                                         else int(max_tri)),
                          wd=wd)
    # W8 (workflow audit): same-generation export manifest binds STEP + the
    # eight renders + the source FCStd hash + engine/rules versions with
    # SHA-256 digests. The packager re-verifies every digest on the delivered
    # copies, so relocated/stale/mixed-generation artifacts cannot pass.
    manifest = dict(ver.stamp())
    manifest["fcstd_sha256"] = sha256_file(proj.fcstd_path)
    manifest["step"] = {"name": os.path.basename(step),
                        "sha256": sha256_file(step),
                        "schema": step_schema(step)}
    manifest["renders"] = {os.path.basename(p): sha256_file(p)
                           for p in ren.get("files", [])}
    manifest["px_mm_at_long_side"] = ren.get("px_mm_at_long_side")
    manifest["views"] = ren.get("views")
    manifest["long_side"] = ren.get("size")
    manifest["triangles"] = ren.get("triangles")
    mpath = os.path.join(os.path.dirname(proj.fcstd_path),
                         "export_manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return {"ok": cons["ok"], "step": step, "consistency": cons,
            "renders": ren, "manifest": mpath}


def act_package(root, pid, opts):
    from cadkit.packager import package_project
    from cadkit.fingerprint import check_and_register
    proj = load_project(root, pid)
    batch = opts.get("batch")
    if not batch:
        raise ValueError("package requires opts batch=...")
    # audit F12: dedupe is a BLOCKING pre-gate — a near-duplicate of an
    # already-registered model (scale/mirror included) never reaches delivery.
    pre = check_and_register(os.path.abspath(root), pid, proj.fcstd_path,
                             register=False)
    # Blocking classes are TRUE COPIES only: byte-identical volume (exact)
    # or uniform-scaled with identical dimensionless invariants (scaled).
    # The cells+steps "series" class is NOT a copy - a standardised series
    # (IEC 80 / 132 / ...) shares feature structure by design and remains a
    # distinct deliverable, declared via its series id (spec 2.5 申报义务).
    # Blocking it would reject the whole family (audit W11 refinement).
    dup_hits = ((pre.get("possible_exact") or [])
                + [m for m in (pre.get("similar_series") or [])
                   if m in (pre.get("scaled") or [])])
    if dup_hits:
        return {"ok": False, "stage": "dedupe", "error":
                "near-duplicate of an already-registered model (spec 2.5: "
                "scaled/mirrored copies count as the same model) — packaging "
                "refused", "matches": dup_hits, "detail": pre}
    # W11 (workflow audit): buyer 2.5 counts dimension-only variants once,
    # the representative being the highest-step member of the equivalence
    # class. If a delivered sibling in the SAME class has more steps, this
    # member must not ship (the series rule blocks equal structure; this
    # blocks shipping the lesser twin alongside its representative).
    own_steps = (pre.get("fingerprint") or {}).get("steps") or 0
    _hd = pre.get("hit_details") or {}
    higher = [h for h in dup_hits
              if int((( _hd.get(h) or {}).get("steps")) or 0)
              > int(own_steps)]
    if higher:
        return {"ok": False, "stage": "dedupe_representative",
                "error": "equivalence-class representative with more steps "
                         "already delivered (spec 2.5 counts one model per "
                         "class; the richer member ships)",
                "matches": higher}
    res = package_project(proj, os.path.abspath(root), batch, opts=opts)
    if res["ok"]:
        fp = check_and_register(os.path.abspath(root), pid, proj.fcstd_path,
                                register=True)
        res["fingerprint"] = fp
    return res


def act_dedupe(root, pid, opts):
    from cadkit.fingerprint import check_and_register
    proj = load_project(root, pid)
    return check_and_register(os.path.abspath(root), pid, proj.fcstd_path,
                              register=opts.get("register") == "1")


def act_status(root, pid):
    proj = load_project(root, pid)
    def ex(p):
        return os.path.exists(p)
    files = {"fcstd": ex(proj.fcstd_path), "step": ex(proj.step_path),
             "report": ex(os.path.join(proj.build_dir, "report.json")),
             "probe": ex(os.path.join(proj.build_dir, "probe.json"))}
    rep = None
    if files["report"]:
        with open(os.path.join(proj.build_dir, "report.json"), encoding="utf-8") as f:
            rep = json.load(f)
    return {"ok": True, "project": pid, "files": files,
            "meta": proj.meta, "last_report": rep}


BUILD_STUB = '''"""Parametric build script for {id}.

Rules (enforced by cadkit): geometry ONLY via constrained sketches + Part
feature objects; every driving size is a named dimensional constraint.
"""
from cadkit.docctx import SketchBuilder


def build(b, proj):
    # -- parameters: real design sizes in mm --------------------------------
    D = 60.0          # base diameter
    H = 12.0          # base height
    sk = b.sketch("sk_base", plane="XY")
    sk.circle(0, 0, D / 2)
    body = b.pad("pad_base", sk, H)

    sk2 = b.sketch("sk_bore", plane="XY", offset=H)
    sk2.circle(0, 0, D / 2 - 4)
    final = b.pocket("bore_main", body, sk2, depth=0, through=True)
    return final
'''

DESIGN_MD = """# Design notes (设计说明 — bonus + non-reverse-engineering evidence)

## Object & function
(real object, purpose, operating context)

## Key dimensions & sources
(table: dimension, value, rationale — catalogue/bearing standard, functional)

## Feature plan (step budget)
(list sketches -> features; must justify target grade count)

## Interfaces
(mating parts, fasteners, shafts, seals)
"""

REQUIREMENTS_MD = """# User requirements (完整用户需求文档 — bonus)

- Use case:
- Functional requirements:
- Key dimensions:
- Interfaces:
- Constraints (mass, temperature, material, standards):
"""


# ---------------------------------------------------------------------------
# GUI board snapshot (<cadRoot>/mem/board.json)
#
# The browser half of this plugin (the sidebar panel) cannot import cadkit: it
# can only read files through the Host's workspace-file remote. One compact
# snapshot per CAD root keeps that read to a single request and gives humans
# and agents the same overview. Best-effort by construction — a telemetry
# failure must never fail a CAD action, so everything here swallows errors.
# ---------------------------------------------------------------------------

BOARD_SCHEMA = 1
HISTORY_MAX = 200
BOARD_HISTORY_MAX = 40


def mem_dir(root):
    return os.path.join(os.path.abspath(root), "mem")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _png_size(path):
    """(w, h) straight from a PNG IHDR: cheap, and needs no image library."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return (int.from_bytes(head[16:20], "big"),
                    int.from_bytes(head[20:24], "big"))
    except Exception:
        pass
    return None


def _write_atomic(path, text):
    """Whole-file replace: the browser parses board.json in one shot, so a
    reader must never see a half-written file (parallel CAD tasks share one
    cadRoot). os.replace is atomic within a filesystem."""
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _stamp(path):
    try:
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(os.path.getmtime(path)))
    except Exception:
        return None


def record_action(root, action, pid, res):
    """Persist one governance record for this action, then refresh the board."""
    if not root:
        return
    try:
        d = mem_dir(root)
        os.makedirs(d, exist_ok=True)
        m = (res or {}).get("mem") or {}
        r = (res or {}).get("renders") or {}
        rec = {
            "ts": _stamp(os.devnull) or "",
            "action": action,
            "project": pid,
            "ok": bool((res or {}).get("ok")),
            "error": str((res or {}).get("error") or "")[:300],
            "cap_mb": m.get("cap_mb"),
            "peak_mb": m.get("peak_mb"),
            "avail_mb": m.get("avail_mb"),
            "degraded": m.get("degraded") or [],
        }
        if r:
            rec["render"] = {k: r.get(k) for k in
                             ("size", "supersample", "triangles",
                              "deflection_scale", "budget_hit")}
        _write_atomic(os.path.join(d, "latest.json"),
                      json.dumps(rec, ensure_ascii=False, indent=1))
        hp = os.path.join(d, "history.jsonl")
        lines = []
        if os.path.exists(hp):
            with open(hp, encoding="utf-8") as f:
                lines = f.read().splitlines()[-(HISTORY_MAX - 1):]
        lines.append(json.dumps(rec, ensure_ascii=False, default=str))
        _write_atomic(hp, "\n".join(lines) + "\n")
    except Exception:
        pass
    build_board(root)


def build_board(root, write=True):
    """Assemble the one-file board; writes <cadRoot>/mem/board.json."""
    board = {"schema": BOARD_SCHEMA, "generated_at": _stamp(os.devnull)}
    try:
        from cadkit import mem as _mem
        gov = {"cap_mb": _mem.cap_mb(), "floor_mb": _mem.floor_mb(),
               "probe_worker_ceiling": _mem.probe_max_workers()}
        try:
            from cadkit.render import plan
            gov["next_export"] = plan()
        except Exception:
            pass
        board["governor"] = gov
    except Exception:
        board["governor"] = {}

    d = mem_dir(root)
    board["last"] = _read_json(os.path.join(d, "latest.json"))
    try:
        with open(os.path.join(d, "history.jsonl"), encoding="utf-8") as f:
            hist = [json.loads(l) for l in
                    f.read().splitlines()[-BOARD_HISTORY_MAX:] if l.strip()]
    except Exception:
        hist = []
    board["history"] = list(reversed(hist))

    projects = []
    proot = os.path.join(os.path.abspath(root), "projects")
    try:
        pids = sorted(os.listdir(proot))
    except Exception:
        pids = []
    for pid in pids:
        pdir = os.path.join(proot, pid)
        if not os.path.isdir(pdir) or not os.path.exists(
                os.path.join(pdir, "project.yml")):
            continue
        meta = _read_json(os.path.join(pdir, "project.yml")) or {}
        bdir = os.path.join(pdir, "build")
        fcstd = os.path.join(bdir, pid + ".FCStd")
        item = {
            "id": pid,
            "name": meta.get("project_name") or pid,
            "grade": meta.get("target_grade"),
            "unit": meta.get("unit"),
            "series": meta.get("series"),
            "files": {
                "fcstd": os.path.exists(fcstd),
                "step": os.path.exists(os.path.join(bdir, pid + ".step")),
                "report": os.path.exists(os.path.join(bdir, "report.json")),
                "probe": os.path.exists(os.path.join(bdir, "probe.json")),
            },
            "updated_at": _stamp(fcstd),
        }
        rep = _read_json(os.path.join(bdir, "report.json")) or {}
        checks = rep.get("checks") or []
        item["report"] = {
            "ok": rep.get("ok"),
            "steps": (rep.get("stats") or {}).get("steps"),
            "failed": [c.get("id") for c in checks if c.get("status") == "FAIL"],
            "warns": [c.get("id") for c in checks if c.get("status") == "WARN"],
        }
        prb = _read_json(os.path.join(bdir, "probe.json")) or {}
        if prb:
            item["probe"] = {k: prb.get(k) for k in
                             ("ok", "probed", "failed", "workers",
                              "per_worker_mb", "probe_version")}
        views = []
        rdir = os.path.join(bdir, "renders")
        try:
            for name in sorted(os.listdir(rdir)):
                if name.lower().endswith(".png"):
                    wh = _png_size(os.path.join(rdir, name)) or (None, None)
                    views.append({"file": name, "w": wh[0], "h": wh[1]})
        except Exception:
            pass
        item["renders"] = {"count": len(views), "views": views,
                           "updated_at": _stamp(rdir) if views else None}
        projects.append(item)
    board["projects"] = projects

    batches = []
    droot = os.path.join(os.path.abspath(root), "delivery")
    try:
        for name in sorted(os.listdir(droot)):
            bdir = os.path.join(droot, name)
            if os.path.isdir(bdir):
                parts = sorted(e for e in os.listdir(bdir)
                               if not e.startswith(".")
                               and os.path.isdir(os.path.join(bdir, e)))
                batches.append({"name": name,
                                "projects": parts,
                                "csv": os.path.exists(
                                    os.path.join(bdir, "info.csv")),
                                "staging": os.path.isdir(
                                    os.path.join(bdir, ".staging")),
                                "updated_at": _stamp(bdir)})
    except Exception:
        pass
    board["delivery"] = {
        "batches": batches,
        "registered": bool(_read_json(os.path.join(os.path.abspath(root),
                                                   "registry",
                                                   "fingerprints.json")))}
    if write:
        try:
            os.makedirs(d, exist_ok=True)
            _write_atomic(os.path.join(d, "board.json"),
                          json.dumps(board, ensure_ascii=False, indent=1))
        except Exception:
            pass
    return board


def act_board(root):
    return {"ok": True, "action": "board",
            "path": os.path.join(mem_dir(root), "board.json"),
            "board": build_board(root)}


def main():
    argv = sys.argv[1:]
    if not any(a == "--action" for a in argv) and os.environ.get("CADKIT_ACTION"):
        argv = ["--action", os.environ["CADKIT_ACTION"]]
        argv += ["--root", os.environ.get("CADKIT_ROOT", "")]
        if os.environ.get("CADKIT_PROJECT"):
            argv += ["--project", os.environ["CADKIT_PROJECT"]]
        if os.environ.get("CADKIT_OUT"):
            argv += ["--out", os.environ["CADKIT_OUT"]]
        for k, v in json.loads(os.environ.get("CADKIT_OPTS") or "{}").items():
            argv += ["--opt", "%s=%s" % (k, v)]
    action, root, pid, out, opts = parse_args(argv)
    from cadkit import mem
    if opts.get("mem_cap_mb") and "CADKIT_MEM_MB" not in os.environ:
        os.environ["CADKIT_MEM_MB"] = str(int(float(opts["mem_cap_mb"])))
    if opts.get("mem_floor_mb") and "CADKIT_MEM_FLOOR_MB" not in os.environ:
        os.environ["CADKIT_MEM_FLOOR_MB"] = str(int(float(opts["mem_floor_mb"])))
    # W16 (workflow audit): the mem_off escape hatch is gone. The memory
    # governor is a delivery gate, not an advisory: per-call opts could
    # previously disable the only protection against fork-bombing the host,
    # and opts are not part of any evidence hash. The hard ceiling can still
    # be *raised* (mem_cap_mb) but never to zero.
    if opts.get("mem_off"):
        print("CADKIT>>" + json.dumps({
            "ok": False, "action": action, "error":
            "mem_off is not supported: the memory governor cannot be turned "
            "off (audit W16). Use mem_cap_mb to raise the ceiling.",
        }), flush=True)
        sys.exit(64)
    # Recursion tripwire: a probe *worker* must never orchestrate another
    # probe. If that ever comes back, it forks freecadcmd processes until the
    # host is out of memory, so fail loudly instead.
    if action == "probe" and os.environ.get("CADKIT_ORCHESTRATOR") == "1":
        print("CADKIT>>" + json.dumps({
            "ok": False, "action": action,
            "error": "refusing nested probe (would fork unbounded workers)",
            "mem": mem.status()}))
        return
    # Heavy actions get a memory governor: budget-aware sizing plus a
    # watchdog that degrades, then aborts, before the host starts paging.
    # N14 recheck round 4: probe_worker (the forked FreeCAD probe child)
    # and dedupe (opens geometry) are just as memory-heavy as the parent
    # actions and must carry the same governor.
    governor = action in ("probe", "probe_worker", "dedupe", "export",
                          "package", "validate", "build")
    wd = mem.Watchdog(enable=governor and mem.abort_enabled()).start() \
        if governor else None
    try:
        if action == "new":
            res = act_new(root, opts)
        elif action == "build":
            res = act_build(root, pid)
        elif action == "validate":
            res = act_validate(root, pid, opts)
        elif action == "probe":
            res = act_probe(root, pid, opts, wd=wd)
        elif action == "probe_worker":
            from cadkit.probe import probe_worker
            proj = load_project(root, pid)
            _snap = os.environ.get("CADKIT_PROBE_FCSTD")
            if _snap:
                if not os.path.exists(_snap):
                    raise ValueError("CADKIT_PROBE_FCSTD snapshot missing: "
                                     + _snap)
                if os.environ.get("CADKIT_PROBE_SNAP_SHA"):
                    import hashlib as _hl
                    _h = _hl.sha256()
                    with open(_snap, "rb") as _f:
                        for _c in iter(lambda: _f.read(1 << 20), b""):
                            _h.update(_c)
                    if _h.hexdigest() != os.environ["CADKIT_PROBE_SNAP_SHA"]:
                        raise ValueError("probe snapshot hash mismatch "
                                         "(tampered mid-run, R4)")
            probe_worker(_snap or proj.fcstd_path,
                         start=int(opts.get("start", 0)),
                         limit=int(opts.get("limit", 40)),
                         delta=float(opts.get("delta", 0.01)))
            return
        elif action == "export":
            res = act_export(root, pid, opts, wd=wd)
        elif action == "package":
            res = act_package(root, pid, opts)
        elif action == "dedupe":
            res = act_dedupe(root, pid, opts)
        elif action == "status":
            res = act_status(root, pid)
        elif action == "board":
            res = act_board(root)
        elif action == "memcheck":
            from cadkit.render import plan
            res = {"ok": True, "action": "memcheck", "mem": mem.status(),
                   "plan": plan(int(opts.get("long_side", "2400")))}
        else:
            res = {"ok": False, "error": "unknown action " + str(action)}
    except Exception as e:
        res = {"ok": False, "error": str(e), "trace": traceback.format_exc(limit=8)}
    if wd is not None:
        wd.stop = True
    if isinstance(res, dict):
        res.setdefault("mem", mem.status())
    # Governance telemetry for the sidebar panel and later audits; the probe
    # worker child returns earlier on purpose so children never race the board.
    if action != "probe_worker":
        record_action(root, action, pid, res)
    text = json.dumps(res, ensure_ascii=False, default=str)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    print("CADKIT>>" + text)


main()
