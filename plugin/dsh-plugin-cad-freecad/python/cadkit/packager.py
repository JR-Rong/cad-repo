"""Delivery packaging + the six-item hard checklist (purchase spec 2.3).

Nothing is delivered unless every checklist item PASSES:
 1 native file (full feature history, no compressed/frozen/dumb geometry)
 2 references complete (all referenced parts present, open without missing)
 3 STEP exported from the native file (AP214/AP242)
 4 8 render images per spec (white bg, >=2000 px long side)
 5 project info fields complete
 6 delivery format (folder per project id, safe filenames)
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import struct
import time

import FreeCAD as App

from .project import check_safe_name
from .version import evidence_issues as _evidence_issues
from .validate import validate_document

RENDER_NAMES = ["front", "back", "left", "right", "top", "bottom",
                "iso_down", "iso_up"]
INFO_FIELDS = ["project_name", "software", "author", "source_url", "license",
               "unit", "parts_count", "features_count", "description",
               "series", "grade", "steps"]
SAFE_PATH = __import__("re").compile(r"^[A-Za-z0-9_\-/.]+$")


def _png_dims(path):
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    return struct.unpack(">II", head[16:24])


def _png_white_corners(path):
    """Our PNGs use filter 0 rows; check first pixel of top/bottom rows."""
    import zlib
    data = open(path, "rb").read()
    idat = b""
    pos = 8
    w = h = 0
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", payload[:8])
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        pos += 12 + ln
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    stride = 1 + w * 3
    if len(raw) < stride * h:
        return None
    def px(off):
        return raw[off + 1:off + 4]
    corners = [px(0), px((w - 1) * 3), px(stride * (h - 1)),
               px(stride * (h - 1) + (w - 1) * 3)]
    return all(c == b"\xff\xff\xff" for c in corners)


def _png_content(path):
    """Audit F8 + W9: fully decode and VERIFY the PNG (chunk CRCs, bit
    depth, color type, decompression size cap, alpha fully opaque, real
    pixel content) - not just size/corner probes."""
    import zlib, hashlib
    _NONWHITE_TBL = bytes(1 if x < 250 else 0 for x in range(256))

    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)

    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return {"error": "bad signature"}
    idat = b""
    pos = 8
    w = h = bitdepth = ctype = interlace = 0
    seen_ihdr = seen_iend = False
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        if not seen_ihdr and tag != b"IHDR":
            return {"error": "IHDR must be the first chunk"}
        payload = data[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])[0]
        if (zlib.crc32(tag + payload) & 0xFFFFFFFF) != crc:
            return {"error": "chunk CRC mismatch (%s)" % tag}
        if tag == b"IHDR":
            if seen_ihdr:
                return {"error": "duplicate IHDR"}
            seen_ihdr = True
            w, h, bitdepth, ctype, compr, filt, interlace = \
                struct.unpack(">IIBBBBB", payload[:13])
            if compr != 0 or filt != 0:
                return {"error": "IHDR compression/filter must be 0 "
                                 "(deflate adaptive)"}
        elif tag.isupper() and tag not in (b"IHDR", b"PLTE", b"IDAT",
                                           b"IEND"):
            # unknown CRITICAL chunk: the spec says readers must fail on
            # unrecognized critical chunks (N9)
            return {"error": "unknown critical chunk %s" % tag.decode(
                "latin1")}
        elif tag == b"IDAT":
            idat += payload
            if len(idat) > 2_000_000_000:
                return {"error": "IDAT absurdly large"}
        elif tag == b"IEND":
            seen_iend = True
            break
        pos += 12 + ln
    if not seen_ihdr:
        return {"error": "no IHDR"}
    if not seen_iend:
        return {"error": "no IEND (truncated PNG)"}
    if not (0 < w <= 8192 and 0 < h <= 8192):
        return {"error": "dimensions out of bounds %dx%d" % (w, h)}
    if interlace:
        return {"error": "interlaced PNG not accepted"}
    if bitdepth != 8 or ctype not in (2, 6):
        return {"error": "bitdepth %d colortype %d (need 8-bit RGB/RGBA)"
                % (bitdepth, ctype)}
    nch = 3 if ctype == 2 else 4
    stride = 1 + w * nch
    max_out = stride * h + 1          # EXACT decoded size + slack byte
    z = zlib.decompressobj(15)
    raw = b""
    try:
        while True:
            part = z.decompress(idat, 4 * 1024 * 1024)
            raw += part
            if not part and not z.unconsumed_tail:
                break
            idat = z.unconsumed_tail or idat
            if z.eof:
                break
            if len(raw) > max_out:
                return {"error": "decoded stream exceeds image size (zip "
                                 "bomb guard, W9/R8)"}
        raw += z.flush()
    except Exception:
        return {"error": "IDAT decompress failed"}
    if not z.eof:
        return {"error": "IDAT stream truncated"}
    if len(raw) != stride * h:
        return {"error": "decoded size %d != exact %d (W9/R8)"
                % (len(raw), stride * h)}
    prev = bytearray(w * nch)
    nonwhite = 0
    gray_dev = 0
    hh = hashlib.sha256()
    corners = []
    border_ok = True
    sampled = 1
    for y in range(h):
        f = raw[y * stride]
        line = bytearray(raw[y * stride + 1:(y + 1) * stride])
        if f:
            for i in range(len(line)):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                x = line[i]
                if f == 1:
                    line[i] = (x + a) & 255
                elif f == 2:
                    line[i] = (x + b) & 255
                elif f == 3:
                    line[i] = (x + ((a + b) >> 1)) & 255
                elif f == 4:
                    line[i] = (x + paeth(a, b, c)) & 255
                else:
                    return {"error": "bad filter type %d" % f}
        if nch == 4:
            alphas = line[3::4]
            if len(alphas) != w or bytes(alphas).count(255) != w:
                return {"error": "non-opaque alpha present (full scan, R8)"}
        # N9 recheck: EVERY border pixel must be white (the background is
        # the entire frame edge, not four sampled corners), and the colour
        # metric is EXACT per row via channel-extent comparison in C-speed
        # builtins - sampling let a coloured decoy slip between probes.
        first = bytes(line[0:3])
        last = bytes(line[len(line) - nch:len(line) - nch + 3])
        if min(first) < 250 or min(last) < 250:
            border_ok = False
        if y in (0, h - 1):
            rowpx = [bytes(line[x:x + 3])
                     for x in range(0, w * nch, nch)]
            if any(min(q) < 250 for q in rowpx):
                border_ok = False
            corners.append(first)
            corners.append(last)
        rr = bytes(line[0:w * nch:nch])
        gg = bytes(line[1:w * nch:nch])
        bbb = bytes(line[2:w * nch:nch])
        # N9 recheck (round 4): per-CHANNEL extrema cancel - red+green+blue
        # striped pixels share identical min/max per channel and scored 0.
        # Colour is a PER-PIXEL property: dev = max(r,g,b)-min(r,g,b).
        # Byte equality keeps pure-grey rows at two C-speed compares.
        if rr != gg or gg != bbb:
            d = 0
            for a, b_, c_ in zip(rr, gg, bbb):
                hi = a if a > b_ else b_
                if c_ > hi:
                    hi = c_
                lo = a if a < b_ else b_
                if c_ < lo:
                    lo = c_
                if hi - lo > d:
                    d = hi - lo
            if d > gray_dev:
                gray_dev = d
        nonwhite += (rr.translate(_NONWHITE_TBL).count(1)
                     + gg.translate(_NONWHITE_TBL).count(1)
                     + bbb.translate(_NONWHITE_TBL).count(1))
        prev = line
        hh.update(bytes(line))
    white_corners = all(all(v >= 250 for v in c) for c in corners)
    return {"size": [w, h],
            "nonwhite_ratio": nonwhite / float(3 * max(1, w * h)),
            "gray_max_dev": gray_dev,
            "white_corners": white_corners,
            "border_ok": border_ok,
            "digest": hh.hexdigest()[:16]}
    return {"size": [w, h],
            "nonwhite_ratio": nonwhite / float(3 * sampled),
            "gray_max_dev": gray_dev,
            "white_corners": white_corners,
            "border_ok": border_ok,
            "digest": hh.hexdigest()[:16]}


def _check_native(path):
    """Item 1: full history, no suppressed / dumb / mesh."""
    doc = App.openDocument(path)
    try:
        bad = [o.Name for o in doc.Objects
               if getattr(o, "Suppressed", False)]
        dumb = [o.Name for o in doc.Objects
                if o.TypeId in ("Mesh::Feature", "Import::TopoShape")]
        errs = [o.Name for o in doc.Objects
                if any(s in o.State for s in ("Invalid", "Error"))]
        from .validate import contributing_set, is_step_node
        fs, closure = contributing_set(doc)
        nodes = [o for o in doc.Objects if o.Name in closure and is_step_node(o)]
        return doc, {"history_nodes": len(nodes), "suppressed": bad,
                     "dumb": dumb, "errors": errs, "ok": bool(nodes)
                     and not bad and not dumb and not errs and bool(fs)}
    except Exception as e:
        App.closeDocument(doc.Name)
        return None, {"error": str(e)[:200], "ok": False}


def _check_references(asm_path):
    doc = App.openDocument(asm_path)
    try:
        missing, links = [], 0
        for o in doc.Objects:
            if o.TypeId == "App::Link":
                links += 1
                if o.LinkedObject is None:
                    missing.append(o.Name)
                else:
                    lo = o.LinkedObject
                    src = lo.Document.FileName if lo and lo.Document else ""
                    if not src or not os.path.exists(src):
                        missing.append(o.Name + " (file)")
        return {"links": links, "missing": missing, "ok": not missing}
    finally:
        App.closeDocument(doc.Name)


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"error": "unparseable"}


def _safe_open_w(path, mode="w"):
    """Open for writing WITHOUT following a pre-existing symlink (N2)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    return os.fdopen(fd, mode, encoding="utf-8" if "t" in mode or
                     mode == "w" else None)


def _sheet_cells(fcstd):
    """Independent driving-cell inventory from the native document (R4).
    N5: delegates to the SAME probe.driving_cells implementation so the
    evidence gate compares like with like (drift between the two lists
    was itself a finding)."""
    try:
        from .probe import driving_cells
        d = App.openDocument(fcstd)
        try:
            sh = d.getObject("params")
            if sh is None:
                return []
            return [a for a, _v in driving_cells(sh)]
        finally:
            App.closeDocument(d.Name)
    except Exception:
        return []

def package_project(proj, cad_root, batch, render_long_side=2400, opts=None):
    """Stage -> self-verify -> COMMIT (ledger transaction) -> publish
    (atomic rename) -> derive views (W12/W13/W21).

    A crash or refusal at ANY point leaves the previous committed delivery
    byte-identical and the ledger unchanged; nothing half-written is ever
    visible under delivery/<batch>/<pid>/."""
    opts = opts or {}
    strict = str(opts.get("strict",
                          os.environ.get("CADKIT_STRICT", ""))) == "1"
    require_review = str(opts.get("require_review",
                                 os.environ.get("CADKIT_REQUIRE_REVIEW", ""))
                         ) == "1"
    batch = check_safe_name(batch)
    pid = proj.id
    build = proj.build_dir
    fcstd = proj.fcstd_path
    step = proj.step_path
    renders = proj.renders_dir

    from . import version as ver
    from .ledger import (open_ledger, sha256_file, stage_dir, fsync_file,
                         fsync_dir, publish, commit_acceptance,
                         is_committed, get_committed_rev,
                         mark_published, abandon_pending,
                         withdraw as ledger_withdraw,
                         is_stale_attempt, reinstate_previous)
    rules_sha = ver.rules_sha()
    fcstd_sha = sha256_file(fcstd)
    warnings = []

    # ---- 0. intake gate (W20): deliveries are driven by a hashed batch
    #         manifest; the CSV can be reconciled against it (nothing extra,
    #         nothing missing, no duplicates).
    bman = _load_json(os.path.join(cad_root, "batches", batch + ".json"))
    if not bman or not bman.get("parts"):
        return {"ok": False, "stage": "intake",
                "error": "batch intake manifest cad/batches/%s.json missing "
                         "or empty - create it from the part list first (W20)"
                         % batch}
    if pid not in list(bman["parts"]):
        return {"ok": False, "stage": "intake",
                "error": "%s is not in batch %s intake manifest (W20)"
                         % (pid, batch)}

    # ---- 1. evidence gates: current-generation reports or nothing ---------
    gate = _load_json(os.path.join(build, "report.json"))
    probe = _load_json(os.path.join(build, "probe.json"))
    manifest0 = _load_json(os.path.join(build, "export_manifest.json"))
    errs = []
    if gate is None:
        errs.append("acceptance report (run validate first)")
    if probe is None:
        errs.append("editability probe (run probe first)")
    if manifest0 is None:
        if strict:
            errs.append("export manifest (re-export under the current engine)")
        else:
            warnings.append("export manifest missing (legacy export)")
    if errs:
        return {"ok": False, "stage": "inputs", "missing": errs}
    for nm, rp in (("validate", gate), ("probe", probe),
                   ("export", manifest0)):
        if rp is None:
            continue
        iss = _evidence_issues(nm, rp, rules_sha, strict)
        errs += iss
        if not iss and not strict:
            got = rp.get("rules_sha") or (rp.get("engine") or {}).get("rules_sha")
            if got and got != rules_sha:
                warnings.append(nm + " report predates current rules "
                                "(mid-run); strict pass regenerates all")
    if not gate.get("ok"):
        errs.append("acceptance report FAIL: %s"
                    % [c["id"] for c in gate.get("checks", [])
                       if c["status"] == "FAIL"])
    # probe depth (W5): recompute verdict from per-cell details; never
    # trust the summary fields alone.
    det = probe.get("details") or []
    n_bad = sum(1 for d in det if d.get("status") != "PASS")
    if probe.get("source_moved_during_probe") is True:
        errs.append("probe: source FCStd changed while workers ran - "
                    "evidence void (re-probe required)")
    if probe.get("crashes") and "CRASH" not in json.dumps(det):
        errs.append("probe aggregate contradicts details (crashes>0 but "
                    "no crashed cell reported) - forged or stale")
    if not probe.get("ok") or n_bad or not det:
        errs.append("editability probe not clean (%d/%d cells not PASS)"
                    % (n_bad, len(det)))
    if strict and det:
        # N5 recheck round 4: a bare {"cell":X,"status":"PASS"} proves
        # nothing - each PASS row must carry the perturbed VALUE the
        # worker actually applied to the sheet.
        for d in det:
            if not isinstance(d.get("value"), (int, float)):
                errs.append("probe cell %s reports PASS without a numeric "
                            "perturbation value (evidence forgery, N5)"
                            % d.get("cell"))
                break
    if probe.get("cells_expected") and len(det) < probe["cells_expected"]:
        errs.append("probe covers %d/%d sheet cells - partial (W5)"
                    % (len(det), probe["cells_expected"]))
    if strict:
        if probe.get("probe_version", 0) < 5:
            errs.append("probe report is pre-W5 generation (no snapshot "
                        "protocol) - re-probe")
        if probe.get("legacy_mode"):
            errs.append("legacy sketch-constraints probe not accepted in "
                        "strict mode (params sheet required)")
    if probe.get("fcstd_sha256") != fcstd_sha:
        errs.append("probe report does not match this FCStd (stale/foreign)")
    if strict:
        # N5: probe results must provably come from an untampered snapshot
        # of THIS native, verified after the last worker exited.
        if probe.get("snapshot_final_ok") is not True:
            errs.append("probe snapshot not verified end-to-end "
                        "(snapshot_final_ok, N5)")
        if probe.get("snapshot_sha256") != fcstd_sha:
            errs.append("probe snapshot bytes differ from delivered "
                        "native (N5)")
    # R4/W5 recheck: never trust report-internal bookkeeping alone - the
    # driving-cell inventory comes from the NATIVE document itself.
    _cells_real = _sheet_cells(fcstd)
    if _cells_real:
        if probe.get("cells_expected") not in (None, len(_cells_real)):
            errs.append("probe cells_expected=%s but native drives %d "
                        "sheet cells (independent inventory, R4)"
                        % (probe.get("cells_expected"), len(_cells_real)))
        _det_names = [d.get("cell") for d in det]
        if len(set(_det_names)) != len(_det_names):
            errs.append("probe details contain duplicate cells (forgery, R4)")
        if not set(_det_names) <= set(_cells_real):
            errs.append("probe covers cells that do not exist in the native "
                        "sheet (forgery, R4)")
        if strict and set(_det_names) != set(_cells_real):
            errs.append("probe coverage %d unique of %d real driving cells "
                        "- incomplete (R4)" % (len(set(_det_names)),
                                               len(_cells_real)))
    if "failed" in probe and probe["failed"] != n_bad:
        errs.append("probe aggregate self-contradiction: failed=%s but %d "
                    "non-PASS details (R4)" % (probe["failed"], n_bad))
    if "probed" in probe and probe["probed"] != len(det):
        errs.append("probe aggregate self-contradiction: probed=%s but %d "
                    "detail rows (R4)" % (probe["probed"], len(det)))
    _gate_ids = {c.get("id") for c in gate.get("checks", [])}
    if len(gate.get("checks", [])) < 8 or \
            not {"rebuild_no_error", "geometry_valid"} <= _gate_ids:
        errs.append("validate report lacks the standard check set "
                    "(forged/empty report, R4)")
    if manifest0:
        if manifest0.get("fcstd_sha256") != fcstd_sha:
            errs.append("export manifest binds a DIFFERENT FCStd (mixed "
                        "generation) - re-export (W8)")
        ms = (manifest0.get("step") or {}).get("sha256")
        if ms and ms != sha256_file(step):
            errs.append("delivered STEP differs from export manifest")
        if strict:
            want_renders = {"view_%s.png" % n for n in RENDER_NAMES}
            got_renders = set((manifest0.get("renders") or {}).keys())
            if got_renders != want_renders:
                errs.append("export manifest renders %s != required 8 views "
                            "(W8/R4)" % sorted(got_renders)[:3])
            if not isinstance(manifest0.get("px_mm_at_long_side"), (int, float)):
                errs.append("export manifest missing px_mm_at_long_side "
                            "framing scale (W24/R4)")
            for n in RENDER_NAMES:
                d = (manifest0.get("renders") or {}).get("view_%s.png" % n)
                if d and d != sha256_file(os.path.join(
                        renders, "view_%s.png" % n)):
                    errs.append("view_%s.png on disk differs from export "
                                "manifest (W8)" % n)
    if errs:
        return {"ok": False, "stage": "gate", "error": "; ".join(errs)[:600],
                "warnings": warnings}

    # ---- review receipts (W18) ---------------------------------------------
    review = opts.get("review")
    if isinstance(review, str) and review.startswith("@"):
        review = _load_json(os.path.abspath(review[1:])) or {}
    elif isinstance(review, str):
        try:
            review = json.loads(review)
        except Exception:
            review = {}
    review = review or {}
    # Receipt binding identity: a review reviews the DESIGN (parameter
    # contract + topology), which is stable across rebuilds; FCStd bytes are
    # not. A receipt may bind to either the contract sha (preferred) or the
    # exact fcstd bytes sha; anything else is unbound and invalid.
    import hashlib as _hl
    contract = _hl.sha256(json.dumps(
        {"params": proj.field("variant", {}).get("params", {}),
         "topo": proj.field("variant", {}).get("topo", {}),
         "family": proj.field("family", "")},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # R3 recheck: a receipt certifies THIS project's design - identity is
    # verified, not assumed. L2 must bind the exact render digests it saw.
    _mod = proj.field("family_module") or (
        str(proj.field("family", "")) + ".py")
    if _mod and not str(_mod).endswith(".py"):
        _mod = str(_mod) + ".py"
    _family_file = os.path.join(cad_root, "family",
                                os.path.basename(str(_mod))) if _mod else ""
    _family_sha = (sha256_file(_family_file)
                   if os.path.exists(_family_file) else None)
    _render_digests = {}
    for n in RENDER_NAMES:
        rp = os.path.join(renders, "view_%s.png" % n)
        if os.path.exists(rp):
            _render_digests["view_%s.png" % n] = sha256_file(rp)
    review_receipt_sha = None
    from .version import adjudicate_review
    _man_sha = (sha256_file(os.path.join(build, "export_manifest.json"))
                if manifest0 else None)
    rinfo = adjudicate_review(review, pid, contract, fcstd_sha,
                              _render_digests, _family_sha, require_review,
                              manifest_sha=_man_sha)
    # N12: review_receipt_sha is set ONLY from the archived
    # review/receipts.json bytes after staging, never from an in-memory
    # re-serialisation of the input (the two differ).
    review_status = ("complete"
                     if rinfo["L1"]["valid"] and rinfo["L2"]["valid"]
                     else "pending")
    if require_review and review_status != "complete":
        return {"ok": False, "stage": "review",
                "error": "human review receipts incomplete (L1/L2 required, "
                         "W18) - packaging refused", "reviews": rinfo}

    # ---- source waiver artifact (W19) ---------------------------------------
    src = (proj.field("source_url") or "").strip().lower()
    waiver_shas = []
    if src.startswith("http") and not (proj.field("license") or "").strip():
        return {"ok": False, "stage": "waiver",
                "error": "external source_url but no license declared "
                         "(spec 2.2: 外部来源必须给出许可)"}
    if not src.startswith("http"):
        for wid in (proj.field("waivers", []) or []):
            check_safe_name(wid)
            wp = os.path.join(cad_root, "waivers", wid + ".json")
            w = _load_json(wp)
            if not w or w.get("revoked"):
                continue
            scope = w.get("scope") or {}
            _wb = scope.get("buyer")
            if _wb not in (None, "*", str(bman.get("buyer", "internal"))):
                continue     # waiver belongs to a DIFFERENT buyer (W19)
            if not (w.get("waives") and w.get("granted_by")):
                continue     # malformed grant is not authority
            if (scope.get("all") or batch in scope.get("batches", [])
                    or pid in scope.get("projects", [])):
                waiver_shas.append(sha256_file(wp))
        if not waiver_shas:
            return {"ok": False, "stage": "waiver",
                    "error": "non-http source_url and no covering waiver "
                             "artifact under cad/waivers/ (W19): grant, "
                             "scope and reference it in project.yml waivers"}

    conn = open_ledger(cad_root)

    # committed revision handling (W12): repackaging the SAME geometry is a
    # no-op; a different geometry is an EXPLICIT supersession (event +
    # withdrawal of the old fingerprint), never a silent rmtree.
    if is_committed(conn, batch, pid, fcstd_sha):
        d0 = os.path.join(cad_root, "delivery", batch, pid)
        # R1/W17 recheck: "already delivered" is a CLAIM to verify, not a
        # short-circuit - the folder, its checklist and (in strict mode) the
        # engine generation of the committed evidence all have to agree.
        _rv = get_committed_rev(conn, batch, pid) or ""
        okd = os.path.isdir(d0)
        cl0 = _load_json(os.path.join(d0, "DELIVERY_CHECKLIST.json")) or {}
        okd = okd and cl0.get("acceptance_overall") == "PASS"
        if okd:
            from .chain import verify_delivery_hashes
            _hash_ok, _hash_why = verify_delivery_hashes(
                cl0, d0, sha256_file)
            okd = _hash_ok and sha256_file(
                os.path.join(d0, pid + ".FCStd")) == fcstd_sha
            if not _hash_ok:
                warnings.append("checklist byte identity failed: " + _hash_why)
        # W17 recheck: the DELIVERED claim is the ledger's artifact digest
        # table - verify every committed byte, not just the native, before
        # honouring "already delivered" (post-delivery render tampering is
        # otherwise invisible to a same-geometry re-package).
        if okd:
            _arts = conn.execute(
                "SELECT path, sha FROM artifact WHERE rev=?",
                (_rv,)).fetchall()
            for _rel, _sha in _arts:
                _fp = os.path.join(d0, _rel)
                if not (os.path.isfile(_fp) and sha256_file(_fp) == _sha):
                    okd = False
                    warnings.append("delivered artifact %s no longer matches "
                                    "ledger digest - re-verifying" % _rel)
                    break
        if okd and strict:
            row = conn.execute("SELECT rules_sha FROM revision WHERE rev=?",
                               (_rv,)).fetchone()
            okd = bool(row) and row[0] == rules_sha
            if okd and require_review:
                okd = cl0.get("review_status") == "complete"
        if okd:
            return {"ok": True, "already_delivered": True, "delivery": d0,
                    "review_status": cl0.get("review_status"),
                    "note": "identical revision already committed and folder "
                            "verified; committed deliveries are never "
                            "rewritten silently (W12)"}
        warnings.append("committed row for this geometry failed verification "
                        "(folder/checklist/rules) - republishing")
    _prev_rev = (get_committed_rev(conn, batch, pid)
                 if is_committed(conn, batch, pid) else None)
    superseded_rev = _prev_rev if _prev_rev != fcstd_sha else None
    from .ledger import same_project_revisions
    for (_rv, _ob, _sha, _st) in same_project_revisions(conn, pid,
                                                        exclude_batch=batch):
        if _sha == fcstd_sha:
            return {"ok": False, "stage": "dedupe",
                    "error": "identical geometry for this project id is "
                             "already delivered in batch %s - re-delivering "
                             "the same model under the same id across "
                             "batches is refused (W11)" % _ob}

    # ---- 2. stage (nothing under delivery/ is touched until commit+rename)
    rev = fcstd_sha
    staging = stage_dir(cad_root, batch, pid, rev)
    copied = []
    _SAFE_SEG = __import__("re").compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9]+)?$")

    def _safe_rel_ok(rel):
        if not rel or rel.startswith(("/", "\\")) or \
                ".." in rel.replace("\\", "/").split("/"):
            return False
        segs = rel.replace("\\", "/").split("/")
        return all(_SAFE_SEG.match(s) for s in segs if s)

    _STAGING_REAL = os.path.realpath(staging)

    def copy_into(src, relname):
        if not _safe_rel_ok(relname):
            raise ValueError("delivery path not allowed by naming rule: %r"
                             % relname)
        dst = os.path.join(staging, relname)
        dst_dir = os.path.dirname(dst)
        os.makedirs(dst_dir, exist_ok=True)
        # R2/W1 recheck: never follow symlinks, never write outside staging,
        # never silently overwrite an already-copied file with DIFFERENT
        # bytes (bundle_extra must not clobber the probed native).
        if os.path.islink(src):
            raise ValueError("symlink source refused: %s" % src)
        src_real = os.path.realpath(src)
        if os.path.exists(dst) or os.path.islink(dst):
            if os.path.islink(dst) or os.path.isdir(dst) or \
                    sha256_file(dst) != sha256_file(src):
                raise ValueError("delivery collision: %r already staged "
                                 "with different content (R2)" % relname)
            return dst
        shutil.copy2(src_real, dst)
        rd = os.path.realpath(dst)
        if not rd.startswith(_STAGING_REAL + os.sep):
            raise ValueError("staged path escaped delivery root: %r" % relname)
        if os.path.islink(dst):
            raise ValueError("symlink appeared in staging: %s" % dst)
        with open(dst, "rb") as f:
            os.fsync(f.fileno())
        copied.append(relname)
        return dst

    committed = False
    try:
        native_names = [pid + ".FCStd"]
        copy_into(fcstd, pid + ".FCStd")
        doc = App.openDocument(fcstd)
        try:
            queue = [(doc, fcstd)]
            seen_docs = {os.path.realpath(fcstd)}
            while queue:
              dcur, fcur = queue.pop(0)
              for o in dcur.Objects:
                if o.TypeId in ("App::Link", "App::LinkSub",
                                "App::LinkExtension") or "Link" in o.TypeId:
                    lo = getattr(o, "LinkedObject", None)
                    s2 = (lo.Document.FileName if lo is not None and
                          getattr(lo, "Document", None) else
                          getattr(o, "LinkedFile", ""))
                    if not s2:
                        continue
                    s2 = os.path.realpath(s2)
                    if s2 in seen_docs:
                        continue
                    seen_docs.add(s2)
                    if lo is not None and getattr(lo, "Document", None) is not None:
                        queue.append((lo.Document, s2))
                    _src_dir = os.path.realpath(os.path.dirname(fcstd))
                    if not (s2 == _src_dir or
                            s2.startswith(_src_dir + os.sep)):
                        # W10 (recheck): partial copy with rewritten links
                        # is NOT implemented; shipping an assembly whose
                        # dependencies live outside the project folder is
                        # how broken deliveries are born. Fail closed.
                        raise ValueError(
                            "linked file outside project folder - refusing "
                            "to deliver an assembly with unresolved external "
                            "dependencies: %s (W10)" % s2)
                    if not os.path.exists(s2):
                        raise ValueError("link target missing: %s (W10)" % s2)
                    copy_into(s2, os.path.relpath(s2, _src_dir))
                    native_names.append(os.path.relpath(s2, _src_dir))
            # W10: link fail-closed - an assembly whose links resolve to
            # nothing usable must not ship.
            if proj.field("type") == "assembly" and not native_names[1:]:
                raise ValueError("assembly declares no resolvable linked "
                                 "part files (W10 fail-closed)")
        finally:
            App.closeDocument(doc.Name)
        copy_into(step, pid + ".step")
        pngs = {n: os.path.join(renders, "view_%s.png" % n)
                for n in RENDER_NAMES}
        for n, p in pngs.items():
            if not os.path.exists(p):
                raise FileNotFoundError("render view_%s.png missing" % n)
            if manifest0:
                want = (manifest0.get("renders") or {}).get("view_%s.png" % n)
                if want and want != sha256_file(p):
                    raise ValueError("view_%s.png differs from export "
                                     "manifest (mixed generation, W8)" % n)
            copy_into(p, "renders/view_%s.png" % n)
        for extra in ("design.md", "requirements.md", "build.py",
                      "project.yml"):
            p = os.path.join(proj.root, extra)
            if os.path.exists(p):
                copy_into(p, extra)
        _root_real = os.path.realpath(proj.root)
        # The family generator module is pipeline-owned source that §2.3
        # REQUIRES in the delivery (reproducibility); it is allowed by
        # EXACT identity, not by loosening the containment rule.
        _fam_py = os.path.realpath(os.path.join(
            cad_root, "family", str(proj.field("family", "")) + ".py"))
        for rel in (proj.field("bundle_extra", []) or []):
            p = os.path.normpath(os.path.join(proj.root, rel))
            rp = os.path.realpath(p)
            if not (rp == _fam_py or rp == _root_real or
                    rp.startswith(_root_real + os.sep)):
                raise ValueError("bundle_extra escapes project root (only "
                                 "the declared family module may travel "
                                 "with it): %s" % rel)
            base = os.path.basename(p)
            if base.lower().endswith((".fcstd", ".step", ".stp", ".iges",
                                      ".igs", ".brep", ".stl")) and \
                    os.path.realpath(p) not in (os.path.realpath(fcstd),
                                                os.path.realpath(step)):
                # an extra that IS a CAD model would bypass the native /
                # STEP gates (N3): refuse; only declared family module or
                # docs/manifests may travel.
                raise ValueError("bundle_extra may not smuggle additional "
                                 "CAD files: %s (N3)" % rel)
            if os.path.exists(p):
                copy_into(p, base)
            else:
                raise FileNotFoundError("bundle_extra missing: " + p)
        if manifest0:
            copy_into(os.path.join(build, "export_manifest.json"),
                      "export_manifest.json")
        if review:
            rdir = os.path.join(staging, "review")
            os.makedirs(rdir, exist_ok=True)
            rj = os.path.join(rdir, "receipts.json")
            with _safe_open_w(rj) as f:
                json.dump(dict(review, project_id=pid, contract_sha256=
                               contract), f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            copied.append("review/receipts.json")
            # N12 recheck: the evidence row binds the DELIVERED receipt
            # file bytes, so "verify the binding" is a single sha256sum.
            review_receipt_sha = sha256_file(rj)
        # R2 recheck: EVERY byte the ledger is about to bind must match the
        # staged copy - the probed/exported native, the STEP, the renders.
        if sha256_file(os.path.join(staging, pid + ".FCStd")) != fcstd_sha:
            raise ValueError("staged FCStd differs from probed source (R2)")
        if manifest0:
            if sha256_file(os.path.join(staging, pid + ".step")) != \
                    (manifest0.get("step") or {}).get("sha256"):
                raise ValueError("staged STEP differs from export manifest "
                                 "(R2/W8)")
            for n in RENDER_NAMES:
                want = (manifest0.get("renders") or {}).get("view_%s.png" % n)
                if want and sha256_file(os.path.join(
                        staging, "renders", "view_%s.png" % n)) != want:
                    raise ValueError("staged view_%s.png differs from "
                                     "export manifest (R2/W8)" % n)
        # W17: the exact engine source that produced this delivery
        import hashlib
        hsrc = hashlib.sha256()
        cdir = os.path.dirname(os.path.abspath(__file__))
        for fname in sorted(os.listdir(cdir)):
            if fname.endswith(".py"):
                with open(os.path.join(cdir, fname), "rb") as f:
                    hsrc.update(f.read())
        # W26: REPRO bundle - hashes of every input + environment identity
        repro = dict(ver.stamp())
        repro.pop("ts", None)          # deterministic artifact (N1 r5)
        repro.update({
            "project_id": pid, "batch": batch, "fcstd_sha256": fcstd_sha,
            "family": proj.field("family") or "",
            "family_sha256": _family_sha,
            "cadkit_files_sha256": hsrc.hexdigest(),
            "files": {rel: sha256_file(os.path.join(staging, rel))
                      for rel in sorted(copied)},
            "reproduce": "CADKIT_ACTION=build CADKIT_ROOT=<cad root> "
                         "CADKIT_PROJECT=%s freecadcmd entry.py" % pid,
        })
        with _safe_open_w(os.path.join(staging, "repro_manifest.json")) as f:
            json.dump(repro, f, ensure_ascii=False, indent=1)
        copied.append("repro_manifest.json")

        # ---- 3. checklist self-verification against the STAGED copies ----
        chk = {}
        native_path = os.path.join(staging, pid + ".FCStd")
        doc2, native = _check_native(native_path)
        chk["1_native_full_history"] = native
        if doc2 is not None:
            App.closeDocument(doc2.Name)
        refs = _check_references(native_path) \
            if proj.field("type") == "assembly" \
            else {"links": 0, "missing": [], "ok": True}
        chk["2_references_complete"] = refs

        from .exporter import step_schema, consistency_check
        step_p = os.path.join(staging, pid + ".step")
        schema = step_schema(step_p)
        cons = consistency_check(native_path, step_p)
        chk["3_step_from_native"] = {
            "schema": schema, "consistency": cons,
            "manifest_bound": bool(manifest0),
            "ok": schema in ("AP214", "AP242") and cons["ok"]
            and (not manifest0
                 or manifest0.get("step", {}).get("sha256")
                 == sha256_file(step_p))}

        renders_ok = True
        rinfo_png = {}
        digests = {}
        for n in RENDER_NAMES:
            p = os.path.join(staging, "renders", "view_%s.png" % n)
            c = _png_content(p)
            if not c or c.get("error"):
                rinfo_png[n] = {"error": (c or {}).get("error",
                                                       "decode failed")}
                renders_ok = False
                continue
            bad = []
            if max(c["size"]) < 2000:
                bad.append("long_side<2000")
            if not c["white_corners"]:
                bad.append("corners_not_white")
            if not c.get("border_ok", True):
                bad.append("border_not_white")
            if c["nonwhite_ratio"] < 0.002:
                bad.append("blank_image")
            if c["nonwhite_ratio"] > 0.90:
                bad.append("no_white_background")
            if c["gray_max_dev"] > 24:
                bad.append("not_grey_shading")
            dup = digests.get(c["digest"])
            if dup:
                bad.append("identical_to_%s" % dup)
            digests[c["digest"]] = n
            rinfo_png[n] = {"w": c["size"][0], "h": c["size"][1],
                            "content_ratio": round(c["nonwhite_ratio"], 4),
                            "grey_dev": c["gray_max_dev"], "issues": bad}
            if bad:
                renders_ok = False
        _pxmm = (manifest0 or {}).get("px_mm_at_long_side")
        _framing_ok = isinstance(_pxmm, (int, float)) and _pxmm > 0
        if not _framing_ok and strict:
            renders_ok = False
        chk["4_render_8_views"] = {"images": rinfo_png,
                                   "framing": ("manifest px/mm %s" % _pxmm
                                               if _pxmm is not None
                                               else "legacy (no manifest)"),
                                   "framing_bound": _framing_ok,
                                   "ok": renders_ok}

        vdoc = App.openDocument(native_path)
        try:
            from .validate import features_for
            rep = validate_document(vdoc, unit=proj.field("unit", "mm"),
                                    target_grade=proj.field("target_grade"),
                                    source_url=proj.field("source_url"),
                                    features=features_for(proj))
        finally:
            App.closeDocument(vdoc.Name)
        fresh_fails = [c["id"] for c in rep.get("checks", [])
                       if c["status"] == "FAIL"]
        chk["0_fresh_validation"] = {"ok": bool(rep.get("ok")),
                                     "failed_checks": fresh_fails,
                                     "steps": rep.get("stats", {}).get("steps", 0),
                                     "rules_sha": rules_sha}
        steps = rep["stats"].get("steps", 0)
        try:
            _fv = ".".join(str(x) for x in App.Version()[:3])
        except Exception:
            _fv = str(App.Version()[0])
        # W23: MEASURED metadata from the delivered geometry, never the
        # declared target or file counts.
        info = {
            "project_name": proj.field("project_name") or "",
            "software": "FreeCAD %s" % _fv,
            "author": proj.field("author") or "",
            "source_url": proj.field("source_url") or "",
            "license": proj.field("license") or "",
            "unit": proj.field("unit") or "",
            "parts_count": rep["stats"].get("solids", len(native_names)),
            "features_count": rep["stats"].get("features", steps),
            "description": proj.field("description") or "",
            "series": proj.field("series") or "",
            "grade": rep["stats"].get("grade") or "",
            "steps": steps,
        }
        REQUIRED_INFO = ["project_name", "software", "author", "source_url",
                         "license", "unit", "parts_count", "features_count",
                         "description", "series"]
        empty = [k for k in REQUIRED_INFO
                 if str(info.get(k, "")).strip() == ""]
        chk["5_info_fields"] = {"values": info, "missing": empty,
                                "ok": not empty}
        path_issue = [rel for rel in copied if not _safe_rel_ok(rel)]
        chk["6_delivery_format"] = {
            "files": len(copied), "bad_paths": path_issue,
            "folder_named_project_id": True,
            "ok": not path_issue}
        file_hashes = {rel: sha256_file(os.path.join(staging, rel))
                       for rel in sorted(set(copied))}
        chk["7_evidence_provenance"] = {
            "rules_sha": rules_sha,
            "family_sha256": _family_sha,
            "validate_report": bool(gate.get("engine") or gate.get("rules_sha")),
            "probe_bound": True, "export_manifest": bool(manifest0),
            "waivers": len(waiver_shas), "warnings": warnings,
            "file_count": len(file_hashes),
            "ok": bool(file_hashes) and bool(rules_sha)}
        review_ok = (review_status == "complete") if require_review else True
        chk["8_review_receipts"] = {"status": review_status,
                                    "receipts": rinfo,
                                    "required": bool(require_review),
                                    "review_satisfied": review_status == "complete",
                                    "ok": review_ok}
        chk["9_byte_identity"] = {"files": file_hashes,
                                  "ok": bool(file_hashes)}
        all_ok = all(c["ok"] for c in chk.values())
        # N1 round 5: artifact files must be PURE functions of content -
        # a wall-clock stamp here made an identical re-package of a
        # delivered rev impossible (the immutability guard fires on the
        # timestamp drift). Content binding keeps the field honest.
        generated = "content-bound:" + rev[:16]
        delivery_manifest = {"project_id": pid, "batch": batch,
                             "generated": generated,
                             "acceptance_overall": "PASS" if all_ok else "FAIL",
                             "review_status": review_status,
                             "superseded_rev": superseded_rev,
                             "family_sha256": _family_sha,
                             "files": file_hashes,
                             "checklist": chk}
        with _safe_open_w(os.path.join(staging,
                              "DELIVERY_CHECKLIST.json")) as f:
            json.dump(delivery_manifest, f, ensure_ascii=False, indent=2)
        copied.append("DELIVERY_CHECKLIST.json")
        if not all_ok:
            raise ValueError("checklist failed: %s"
                             % [k for k, v in chk.items() if not v.get("ok")])

        # ---- 4. commit + publish (the only destructive section) ----------
        artifacts = [(rel, sha256_file(os.path.join(staging, rel)),
                      os.path.getsize(os.path.join(staging, rel)))
                     for rel in sorted(set(copied))]
        evidence = [("validate", c["id"], c["status"], None)
                    for c in gate.get("checks", [])]
        evidence.append(("probe", "editability",
                         "PASS" if probe.get("ok") else "FAIL", None))
        if manifest0:
            evidence.append(("export", "step_renders_manifest", "PASS", None))
        for lvl in ("L1", "L2", "L3"):
            if rinfo[lvl]["present"]:
                evidence.append(("review", lvl,
                                 "PASS" if rinfo[lvl]["valid"] else "FAIL",
                                 review_receipt_sha or fcstd_sha))
        for ws in waiver_shas:
            evidence.append(("waiver", ws[:16], "PASS", ws))
        from .fingerprint import compute as fp_compute
        fpk = fp_compute(native_path)   # the DELIVERED bytes, not the mutable source
        if not fpk or not fpk.get("volume"):
            raise ValueError("fingerprint compute failed - refusing to "
                             "commit a blind dedupe entry")
        fingerprint = {
            "version": 2,
            "structure_sha": "%s|%s|%s" % (fpk and fpk.get("steps"),
                                           fpk and fpk.get("solids"),
                                           fpk and fpk.get("faces")),
            "shape_bucket": "%.3f" % (fpk and __import__("math").log10(
                max(fpk.get("volume", 1.0), 1e-9))) if fpk else "?",
            "cells": fpk and fpk.get("cells"), "steps": fpk and fpk.get("steps"),
            "solids": fpk and fpk.get("solids"),
            "volume": fpk and fpk.get("volume"),
            "bbox": json.dumps(fpk and fpk.get("bbox")),
            "faces": fpk and fpk.get("faces"),
            "edges": fpk and fpk.get("edges"),
            "compactness": fpk and fpk.get("compactness"),
            # N15: gen-8 shipped blobs WITHOUT ratios/volume_scale - the
            # whitelist fix never reached this dict, silently downgrading
            # related() classification. Commit the full invariant set.
            "ratios": fpk and fpk.get("ratios"),
            "volume_scale": fpk and fpk.get("volume_scale"),
        }
        fsync_dir(staging)
        committed = False
        commit_acceptance(
            conn, batch=batch, buyer_id=str(bman.get("buyer", "internal")),
            project_id=pid, rev=rev, fcstd_sha=fcstd_sha,
            source_sha=sha256_file(os.path.join(proj.root, "project.yml")),
            meta_sha=sha256_file(os.path.join(staging, "project.yml"))
            if os.path.exists(os.path.join(staging, "project.yml")) else None,
            rules_sha=rules_sha, runtime_sha=ver.runtime_sha(),
            artifacts=artifacts, evidence=evidence,
            fingerprint=fingerprint, family=proj.field("family"),
            series=proj.field("series"),
            intake=bman["parts"],
            intake_sha=sha256_file(
                os.path.join(cad_root, "batches", batch + ".json"))[:16],
            waiver_sha=",".join(w[:12] for w in waiver_shas) or None)
        with conn:
            conn.execute(
                "INSERT INTO event(rev,kind,payload,created) VALUES(?,?,?,?)",
                (rev, "info", json.dumps(dict(info, generated=generated,
                                              review_status=review_status)),
                 int(time.time() * 1000)))
        # N1 recheck: retiring the previous version is part of
        # mark_published (atomic with the publish), never before it.
        committed = True
        # N2 recheck: the ledger now COMMITS the accepted bytes; before the
        # publish swaps them into delivery/, every file is re-hashed one
        # final time against the rows just written. Anything rewritten
        # between self-verify and publish is caught HERE, under the
        # durability of the committed transaction.
        want = {p: (s, z) for (p, s, z) in artifacts}
        seen = set()
        for dirpath, _dn, fnames in os.walk(staging):
            for fn in fnames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, staging)
                seen.add(rel)
                wsha = want.get(rel)
                if wsha is None:
                    raise RuntimeError("untracked file appeared in staging "
                                       "before publish: %s (N2)" % rel)
                if os.path.islink(full) or \
                        sha256_file(full) != wsha[0]:
                    raise RuntimeError("staged bytes changed after verify: "
                                       "%s (N2 fail closed)" % rel)
        if seen != set(want):
            raise RuntimeError("staging inventory does not match committed "
                               "artifact rows (N2)")
        final = os.path.join(cad_root, "delivery", batch, pid)
        # N1 round 5: refuse a STALE attempt BEFORE touching the delivered
        # folder on disk - publishing first and refusing to mark after
        # leaves disk ahead of the ledger (state_contract repro).
        if is_stale_attempt(conn, batch, pid, rev):
            shutil.rmtree(staging, ignore_errors=True)
            abandon_pending(conn, batch, rev,
                            "stale attempt - newer delivery exists (N1)")
            return {"ok": False, "stage": "prepublish",
                    "error": "stale attempt: a newer revision of this "
                             "model is already delivered - refusing to "
                             "stomp its folder (N1 round 5)"}
        # N2 round 5: remember whether this rev was ALREADY delivered
        # before this attempt (same-native repair flow) - a rollback must
        # not withdraw the pre-existing delivered row whose bytes the
        # restored folder actually represents.
        pre_delivered = conn.execute(
            "SELECT 1 FROM delivery WHERE rev=? AND state='delivered' "
            "LIMIT 1", (rev,)).fetchone() is not None
        trash = publish(staging, final, conn=conn)
        if not mark_published(conn, batch, pid, rev,
                              disk_final=final, expect_sha=fcstd_sha):
            # N20 round 6: the folder on disk is the arbiter - restore our
            # pre-publish trash ONLY if the path does not currently hold
            # a different revision that the ledger has delivered. The old
            # blind restore deleted the newer winner's folder and left it
            # delivered against the loser's bytes.
            # N38 round 9: EVERY byte of this cleanup - re-reading the
            # folder, deciding kept_newer, restoring trash or deleting
            # our own - runs UNDER the publisher flock. Releasing it at
            # the failed mark and cleaning up unprotected let a fresh
            # publisher B slip in between: A then deleted or replaced
            # B's freshly delivered folder while B's row stayed
            # delivered+published.
            from .ledger import (_pub_lock as _pl8, _pub_unlock as _pu8,
                                 inspect_native)
            from .chain import cleanup_decision
            _fd8 = _pl8(final)
            kept_newer = False
            note2 = ""
            try:
                _nat = os.path.join(final, pid + ".FCStd")
                _status, on_disk = inspect_native(_nat)
                query_error = None
                winner_sha = None
                if _status == "ready" and on_disk != fcstd_sha:
                    try:
                        conn.commit()
                        winner = conn.execute(
                            "SELECT r.fcstd_sha FROM delivery d JOIN model m "
                            "ON m.model_id=d.model_id JOIN revision r "
                            "ON r.rev=d.rev WHERE d.batch_id=? AND "
                            "m.project_id=? AND d.state='delivered' AND "
                            "d.rev<>? ORDER BY d.rowid DESC LIMIT 1",
                            (batch, pid, rev)).fetchone()
                        winner_sha = winner[0] if winner else None
                    except Exception as exc:
                        query_error = str(exc)
                # Re-read immediately before any destructive call. A
                # second I/O error is unknown even if the first read worked.
                _status2, _sha2 = inspect_native(_nat)
                if _status2 == "io-error":
                    query_error = query_error or "re-check io-error"
                    _status = "io-error"
                decision = cleanup_decision(
                    native_status=_status, native_sha=on_disk,
                    our_sha=fcstd_sha, winner_sha=winner_sha,
                    query_error=query_error,
                    final_exists=os.path.lexists(final))
                if decision == "unknown":
                    return {"ok": False, "stage": "publish",
                            "error": "ownership unreadable; delivery folder "
                                     "left intact (N43)"}
                kept_newer = decision == "keep"
                if not kept_newer:
                    if trash:
                        shutil.rmtree(final, ignore_errors=True)
                        try:
                            os.rename(trash, final)
                            fsync_dir(os.path.dirname(final))
                        except OSError:
                        # N21 round 6 / N32 round 7 fail closed: the
                        # previous bytes could not be restored - EVERY
                        # delivered claim of this model (a different
                        # revision's row included) may stand down, since
                        # the folder behind them is gone.
                            try:
                                _withdraw_model_deliveries(
                                    conn, batch, pid,
                                    "CAS lost; restore failed (N32)")
                            except Exception:
                                pass
                            note2 = ("; RECOVERY FAILED - previous "
                                     "bytes are still at %s and every "
                                     "delivered claim for them was "
                                     "withdrawn (N21/N32)" % trash)
                    else:
                        # rejected FIRST publication: our own bytes must
                        # not linger unledgered on the delivery path.
                        shutil.rmtree(final, ignore_errors=True)
            finally:
                _pu8(_fd8)
            return {"ok": False, "stage": "publish",
                    "error": "another actor superseded this pending "
                             "delivery mid-publish (CAS failed) - " +
                             (("newer delivery's folder left intact "
                               "(N20)") if kept_newer else
                              ("previous delivery restored (N1)"
                               if not note2 else "recovery failed (N21)"))
                             + note2}
        # N2 recheck round 4: the rename window itself is closed only by
        # re-hashing AT THE FINAL PATH after publication against the
        # committed artifact rows - and on any mismatch rolling the
        # previous delivery back and WITHDRAWING this rev, so the ledger
        # can never claim bytes that are not on disk.
        try:
            pseen = set()
            for dirpath, _dn, fnames in os.walk(final):
                for fn in fnames:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, final)
                    pseen.add(rel)
                    wsha = want.get(rel)
                    if wsha is None:
                        raise RuntimeError("untracked file in published "
                                           "folder: %s (N2)" % rel)
                    if os.path.islink(full) or \
                            sha256_file(full) != wsha[0]:
                        raise RuntimeError("published bytes changed during "
                                           "the swap: %s (N2)" % rel)
            if pseen != set(want):
                raise RuntimeError("published inventory does not match "
                                   "committed rows (N2)")
        except RuntimeError as e2:
            # N31 round 7: decide UNDER THE PUBLISHER LOCK who owns the
            # folder right now. If a DIFFERENT delivered revision's
            # bytes sit on the path, this attempt was superseded after
            # its own successful mark - restoring our trash would stomp
            # that delivery, and withdrawing anything would lie about
            # it. Leave disk and ledger untouched; just report.
            from .ledger import _pub_lock as _pl, _pub_unlock as _pu
            _fd7 = _pl(final)
            try:
                from .chain import OwnershipUnknown
                try:
                    owner = _delivered_owns_disk(conn, batch, pid, final)
                except OwnershipUnknown as own_err:
                    return {"ok": False, "stage": "publish",
                            "error": str(own_err)[:180] +
                            " - disk and ledger left intact (N43)"}
                if owner is not None and owner != rev:
                    return {"ok": False, "stage": "publish",
                            "error": str(e2)[:180] +
                            " - a newer delivery owns the folder; disk "
                            "and its ledger row left intact (N31)"}
                return _rollback_drift(trash, final, conn, batch,
                                       pid, rev, pre_delivered, e2)
            finally:
                # N34 round 8: EVERY exit of this critical section -
                # including the ownership short-circuit - releases the
                # publisher flock; a leak would deadlock later
                # publishers of the batch.
                _pu(_fd7)
        if trash:
            shutil.rmtree(trash, ignore_errors=True)
        rebuild_info_csv(cad_root, batch)
        return {"ok": True, "delivery": final,
                "review_status": review_status, "rev": rev[:12],
                "checklist": chk}
    except Exception as e:
        if committed:
            try:
                abandon_pending(conn, batch, rev, str(e)[:120])
            except Exception:
                pass
        elif os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "stage": "stage", "error": str(e)[:500]}
    finally:
        conn.close()


def _rollback_drift(trash, final, conn, batch, project_id, rev,
                    pre_delivered, e2):
    """Drift-rollback body, executed under the publisher lock (N31).

    A rejected attempt must never leave unledgered bytes on the
    delivery path (N2 round 5), must keep a same-rev repair's delivered
    row when the previous folder goes back intact, and - N21/N32 rounds
    6/7 - when the previous bytes CANNOT be restored, every delivered
    claim of this model withdraws, because the folder behind them is
    gone regardless of which revision the row names."""
    from .ledger import fsync_dir, reinstate_previous
    restored = False
    if trash:
        shutil.rmtree(final, ignore_errors=True)
        try:
            os.rename(trash, final)
            fsync_dir(os.path.dirname(final))
            restored = True
        except OSError:
            pass
    else:
        # N2 round 5: a REJECTED first publication has no previous
        # folder to restore - remove the bad bytes so disk does not
        # hold an unledgered delivery.
        shutil.rmtree(final, ignore_errors=True)
    note = ""
    try:
        from .ledger import withdraw
        if pre_delivered and restored:
            # Same-rev repair: the delivered row for THIS rev
            # never legitimately left - withdraw() would demote
            # it along with the attempt. Record the rollback and
            # keep the row; the restored folder is that delivery.
            with conn:
                conn.execute(
                    "INSERT INTO event(rev,kind,payload,created) "
                    "VALUES(?,?,?,?)",
                    (rev, "rollback_same_rev", str(e2)[:120],
                     int(time.time() * 1000)))
        elif pre_delivered and not restored:
            # N21 round 6 / N32 round 7 fail closed: nothing may claim
            # delivered bytes that are not on disk - demote EVERY
            # delivered row of this model, not only this attempt's rev.
            _withdraw_model_deliveries(conn, batch, project_id,
                                       "restore failed; " + str(e2)[:90])
            note = ("; RECOVERY FAILED - previous delivery bytes are "
                    "still at %s and every delivered claim for this "
                    "model was withdrawn (N21/N32)" % trash)
        else:
            withdraw(conn, rev, str(e2)[:120])
            # N2 round 5: the trashed folder we just put back IS
            # the previous delivery - the ledger must flip it
            # back to delivered, or the restored bytes have no
            # ledger row.
            if restored:
                reinstate_previous(conn, batch, project_id,
                                   exclude_rev=rev)
            else:
                _withdraw_model_deliveries(
                    conn, batch, project_id,
                    "drift + restore failed (N32)")
                note = ("; RECOVERY FAILED - previous bytes are "
                        "still at %s and every delivered claim for "
                        "this model was withdrawn (N21/N32)" % trash)
    except Exception:
        pass
    return {"ok": False, "stage": "publish",
            "error": str(e2)[:300] + note}


def _delivered_owns_disk(conn, batch, project_id, final):
    """N31 round 7: whose bytes, if anybody's, currently sit on the
    delivery path? Returns the rev of a DELIVERED revision whose
    committed native digest matches the folder's FCStd, else None."""
    from .ledger import inspect_native
    nat = os.path.join(final, project_id + ".FCStd")
    status, dsha = inspect_native(nat)
    if status == "io-error":
        from .chain import OwnershipUnknown
        raise OwnershipUnknown(
            "cannot hash %s; refusing to decide ownership (N43)" % nat)
    if status != "ready":
        return None
    try:
        conn.commit()  # end our read snapshot; see the freshest ledger
    except Exception as exc:
        from .chain import OwnershipUnknown
        raise OwnershipUnknown(
            "ledger refresh failed during ownership read (N43): %s" % exc)
    row = conn.execute(
        "SELECT d.rev FROM delivery d JOIN model m "
        "ON m.model_id=d.model_id JOIN revision r ON r.rev=d.rev "
        "WHERE d.batch_id=? AND m.project_id=? AND d.state='delivered' "
        "AND r.fcstd_sha=? ORDER BY d.rowid DESC LIMIT 1",
        (batch, project_id, dsha)).fetchone()
    return row[0] if row else None


def _withdraw_model_deliveries(conn, batch, project_id, reason):
    """N32 round 7: when the folder's bytes can neither be kept nor
    restored, EVERY delivered claim of this model - not just the
    attempt's own revision - has lost its on-disk basis and must be
    withdrawn together."""
    from .ledger import withdraw
    revs = [r_ for (r_,) in conn.execute(
        "SELECT DISTINCT d.rev FROM delivery d JOIN model m "
        "ON m.model_id=d.model_id WHERE d.batch_id=? AND m.project_id=?"
        " AND d.state='delivered'", (batch, project_id)).fetchall()]
    for r_ in revs:
        withdraw(conn, r_, reason[:110])
    return revs


def rebuild_info_csv(cad_root, batch):
    """W21: batch CSV is a DERIVED VIEW rebuilt from the ledger after every
    commit - it can never diverge from what is actually committed."""
    from .ledger import open_ledger
    conn = open_ledger(cad_root)
    try:
        rows = conn.execute(
            "SELECT m.project_id, r.rev, e.payload, d.counts, d.counts_as "
            "FROM delivery d "
            "JOIN model m ON m.model_id=d.model_id "
            "JOIN revision r ON r.rev=d.rev "
            "LEFT JOIN event e ON e.rev=d.rev AND e.kind='info' "
            "AND e.rowid=(SELECT MAX(e2.rowid) FROM event e2 WHERE "
            "e2.rev=d.rev AND e2.kind='info') "
            "WHERE d.batch_id=? AND d.state='delivered' "
            "ORDER BY m.project_id", (batch,)).fetchall()
        out = os.path.join(cad_root, "delivery", batch, "info.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        fields = (["project_id"] + INFO_FIELDS
                  + ["review_status", "rev_sha256", "counts_as_unique",
                     "counted_for"])
        expect = {}
        tmp = out + ".tmp%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for p, rev, payload, counts, counts_as in rows:
                info = json.loads(payload) if payload else {}
                row = {k: info.get(k, "") for k in
                       ["project_id"] + INFO_FIELDS + ["review_status"]}
                row.update({"project_id": p, "rev_sha256": rev[:16],
                            "counts_as_unique": "1" if counts else "0",
                            "counted_for": counts_as or p})
                expect[p] = row
                w.writerow(row)
        os.replace(tmp, out)
        # W23 recheck: verify EVERY value (string-normalised - the ledger
        # stores ints; the CSV round-trip yields strings)
        with open(out, "r", encoding="utf-8-sig", newline="") as f:
            back = {r["project_id"]: r for r in csv.DictReader(f)}

        def _s(v):
            return "" if v is None else str(v)
        bad = None
        if set(back) != set(expect):
            bad = "row set differs: %s" % (
                sorted(set(back) ^ set(expect))[:3])
        for _p in sorted(expect):
            if bad:
                break
            for _k in fields:
                if _s(expect[_p].get(_k)) != (back[_p].get(_k) or ""):
                    bad = "%s.%s want %r got %r" % (
                        _p, _k, str(expect[_p].get(_k))[:40],
                        str(back[_p].get(_k))[:40])
                    break
        if bad:
            raise IOError("info.csv re-read verification failed (W23): %s"
                          % bad)
        return out
    finally:
        conn.close()


def _csv_drop(csv_path, project_id):
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        fields = rdr.fieldnames or (["project_id"] + INFO_FIELDS)
        rows = [r for r in rdr if r.get("project_id") != project_id]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def write_info(csv_path, project_id, info):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    rows = {}
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                rows[r.get("project_id", "")] = r
    rows[project_id] = dict(info, project_id=project_id)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["project_id"] + INFO_FIELDS)
        w.writeheader()
        for pid in sorted(rows):
            w.writerow({k: rows[pid].get(k, "") for k in ["project_id"] + INFO_FIELDS})
