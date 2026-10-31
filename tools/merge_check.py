#!/usr/bin/env python3
"""Cross-machine receipt gate: A checks what B returned, with A's OWN rules.

Why this exists: each machine keeps its own ledger, and spec 2.5 (duplicate
judgement) is only decidable over the UNION of both machines' geometry. The
plugin's dedupe can only see one cadRoot, so the merge step is where the real
gate has to live. Comparison uses the shipped `cadkit.fingerprint` predicates
(scaled_same / related) — not a paraphrase of them.

    python3 handoff/tools/merge_check.py \
        --mine cad --theirs handoff/outbox/fleet_b_2026_11 \
        [--intake handoff/intake/fleet_b_2026_11.json] \
        [--baseline handoff/shared/fingerprints.json]

Exit 0 = accept, 1 = blocking problems (printed as reasons).
"""
import argparse
import json
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
HANDOFF = os.path.dirname(HERE)

# reuse the engine's own predicates when the plugin is importable
def find_plugin_python(explicit=None):
    """Where is cadkit/? A machine has it next to the workspace, B machine has it
    in the pnpm profile dir — and a wrong guess must say so, not silently
    disable the 2.5 gate."""
    import glob
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("CADKIT_PLUGIN_PY"):
        cands.append(os.environ["CADKIT_PLUGIN_PY"])
    cands.append(os.path.join(HANDOFF, os.pardir, "dsh-plugin-cad-freecad", "python"))
    for pat in ("@local/dsh-plugin-cad-freecad/python",
                "**/@local/dsh-plugin-cad-freecad/python"):
        cands += sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".dsh", "profiles",
                                               "*", "node_modules", pat), recursive=True))
    for c in cands:
        if c and os.path.exists(os.path.join(c, "cadkit", "fingerprint.py")):
            return os.path.abspath(c)
    return None


sys.path.insert(0, HERE)


def load_predicates(plugin_py=None):
    """Return (scaled_same, related, mode, path). Never silently None-compare."""
    path = find_plugin_python(plugin_py)
    if path is None:
        return None, None, "plugin python not found", None
    try:
        import engine_predicates
        fp, mode = engine_predicates.load(path)
        return fp.scaled_same, fp.related, mode, path
    except Exception as exc:                          # noqa: BLE001
        return None, None, "unavailable: %s" % exc, path


scaled_same = related = None
PREDICATE_MODE = "not loaded yet"
HAVE_ENGINE = False


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def step_flavour(path):
    """AP214 / AP242 claim straight out of the STEP header."""
    with open(path, "rb") as f:
        blob = f.read(4096).decode("utf-8", "replace")
    if not blob.startswith("ISO-10303-21;"):
        return "not-a-step-file"
    up = blob.upper()
    for tag in ("AP242", "AP214", "AP203", "AP203E2", "AP242"):
        if "AUTOMOTIVE_DESIGN" in up or tag in up.replace(" ", ""):
            if tag in up.replace(" ", ""):
                return tag
    return "unknown-profile"


def load_baseline(path):
    """Baseline may be a directory of per-machine files (shared/fingerprints/<X>.json)
    or one json. Same-id entries that disagree are a hard stop, not a merge."""
    import glob
    out, src = {}, {}
    files = sorted(glob.glob(os.path.join(path, "*.json"))) if os.path.isdir(path) else [path]
    for fp in files:
        doc = json.load(open(fp, encoding="utf-8"))
        tag = os.path.splitext(os.path.basename(fp))[0]
        for pid, v in (doc.get("entries", doc)).items():
            if not isinstance(v, dict) or "volume" not in v:
                continue
            if pid in out and out[pid].get("structure_sha") != v.get("structure_sha"):
                raise SystemExit("底表冲突: %s 在 %s 与 %s 里几何不一致，先人工裁定"
                                 % (pid, src[pid], tag))
            out[pid] = v
            src[pid] = tag
    return out


def rows_of(rows, table):
    return [r for r in rows if r.get("_table") == table]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mine", "--root", dest="mine", default="cad",
                    help="你自己的 cadRoot（用于本机台账交叉检查）")
    ap.add_argument("--theirs", required=True, help="outbox/<batch> 回传目录")
    ap.add_argument("--intake", default=None)
    ap.add_argument("--baseline", default=os.path.join(HANDOFF, "shared", "fingerprints"),
                    help="去重底表：目录（每台机器一个 <X>.json）或单个 json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plugin-python", default=None,
                    help="含 cadkit/ 的 python 目录（默认自动找 profile 的 node_modules）")
    a = ap.parse_args()
    global scaled_same, related, PREDICATE_MODE, HAVE_ENGINE
    scaled_same, related, PREDICATE_MODE, PLUGIN_PY = load_predicates(a.plugin_python)
    HAVE_ENGINE = scaled_same is not None

    blocking, notes, stats = [], [], {}
    theirs = a.theirs
    batch = os.path.basename(theirs.rstrip("/"))

    # ---- 1. required artefacts -------------------------------------------
    need = ["ledger.jsonl", "fingerprints.delta.json", "versions.json", "REPORT.md",
            os.path.join("delivery", batch, "info.csv")]
    for rel in need:
        if not os.path.exists(os.path.join(theirs, rel)):
            blocking.append("缺少回传文件: " + rel)
    if blocking:
        return emit({"ok": False, "batch": batch, "blocking": blocking, "notes": notes}, a.json)

    rows = load_jsonl(os.path.join(theirs, "ledger.jsonl"))
    models = {r.get("project_id"): r for r in rows_of(rows, "model")}
    deliveries = rows_of(rows, "delivery")
    jobs = rows_of(rows, "job")
    evidence = rows_of(rows, "evidence")
    fps = rows_of(rows, "fingerprint")

    # ---- 2. rule / toolchain agreement ------------------------------------
    mine_v = json.load(open(os.path.join(HANDOFF, "shared", "versions.json")))
    their_v = json.load(open(os.path.join(theirs, "versions.json")))
    for key in ("rules_sha", "engine_plugin_version", "tarball_sha256"):
        if key in their_v and key in mine_v and str(their_v[key]) != str(mine_v[key]):
            blocking.append("%s 不一致: B=%s A=%s（两边不在同一验收口径下）"
                            % (key, their_v.get(key), mine_v.get(key)))
    if str(their_v.get("freecad_version_local", ""))[:8] != str(mine_v.get("freecad_version_local", ""))[:8]:
        notes.append("FreeCAD 版本不同: B=%s A=%s（渲染像素与几何可能有细微差异，抽查一致性）"
                     % (their_v.get("freecad_version_local"), mine_v.get("freecad_version_local")))

    # ---- 3. intake vs delivery -------------------------------------------
    intake_path = a.intake or os.path.join(HANDOFF, "intake", batch + ".json")
    intake = json.load(open(intake_path))
    want = list(intake["parts"])
    by_model = {m.get("model_id"): m.get("project_id") for m in models.values()}
    delivered = {}
    for d in deliveries:
        if d.get("state") != "delivered":
            continue
        pid = by_model.get(d.get("model_id")) or d.get("project_id")
        if not pid:
            blocking.append("台账 delivery 行找不到对应 model（model_id=%s）" % d.get("model_id"))
            continue
        delivered[pid] = delivered.get(pid, 0) + 1
    missing = [p for p in want if p not in delivered]
    extra = [p for p in delivered if p not in want]
    dupes = [p for p, n in delivered.items() if n > 1]
    for p in missing:
        blocking.append("intake 有但未交付: " + p)
    for p in extra:
        blocking.append("交付了 intake 之外的件: " + p + "（清单是合同）")
    for p in dupes:
        blocking.append("同一件重复交付 %d 次: %s" % (delivered[p], p))
    stats["intake"] = len(want)
    stats["delivered"] = len(delivered)

    # ---- 4. evidence chain: 5 stages OK + gate/probe/export receipts -------
    for pid in want:
        if pid in missing:
            continue
        ok_stages = {j["stage"] for j in jobs
                     if j.get("project_id") == pid and j.get("state") == "OK"}
        need_stages = {"build", "validate", "probe", "export", "package"}
        gap = need_stages - ok_stages
        if gap:
            blocking.append("%s 台账缺少 OK 阶段: %s" % (pid, ",".join(sorted(gap))))
        kinds = {e.get("kind") for e in evidence if e.get("status") == "PASS"}
        for k in ("validate", "probe", "export"):
            if k not in kinds:
                notes.append("%s: 证据表没有 %s PASS 行（只有 %d 行证据）" % (pid, k, len(evidence)))
                break

    # ---- 5. physical delivery folder per part ------------------------------
    batch_dir = os.path.join(theirs, "delivery", batch)
    csv_rows = []
    with open(os.path.join(batch_dir, "info.csv"), encoding="utf-8-sig") as f:
        header = f.readline().strip().split(",")
        for line in f:
            csv_rows.append(dict(zip(header, [c for c in line.rstrip("\n").split(",")
                                              if c is not None][:len(header)])))
    stats["info_csv_rows"] = len(csv_rows)
    for pid in want:
        if pid in missing:
            continue
        pdir = os.path.join(batch_dir, pid)
        if not os.path.isdir(pdir):
            blocking.append("交付目录缺 %s/" % pid)
            continue
        fcstd = os.path.join(pdir, pid + ".FCStd")
        step = os.path.join(pdir, pid + ".step")
        if not os.path.exists(fcstd):
            blocking.append("%s 缺原生 FCStd（2.3 原生文件）" % pid)
        if not os.path.exists(step):
            blocking.append("%s 缺 STEP（2.3）" % pid)
        else:
            flavour = step_flavour(step)
            if flavour not in ("AP242", "AP214"):
                blocking.append("%s STEP 不是 AP214/AP242: %s" % (pid, flavour))
        renders = os.path.join(pdir, "renders")
        pngs = sorted(os.listdir(renders)) if os.path.isdir(renders) else []
        if len(pngs) != 8:
            blocking.append("%s 渲染图 %d 张，交付要求 8 张（2.3）" % (pid, len(pngs)))
        small = []
        for name in pngs:
            size = png_size(os.path.join(renders, name))
            if size and max(size) < 2000:
                small.append("%s=%dx%d" % (name, size[0], size[1]))
        if small:
            blocking.append("%s 渲染长边不足 2000px: %s" % (pid, ", ".join(small)))
        if not os.path.exists(os.path.join(pdir, "design.md")):
            notes.append("%s 没有 design.md（加分项：设计说明）" % pid)

    # ---- 6. cross-machine dedupe: THE reason this tool exists -------------
    baseline = load_baseline(a.baseline)
    delta_path = os.path.join(theirs, "fingerprints.delta.json")
    delta_raw = json.load(open(delta_path))
    delta = delta_raw.get("entries", delta_raw) if isinstance(delta_raw, dict) else {}
    stats["predicates"] = PREDICATE_MODE
    if not HAVE_ENGINE:
        blocking.append("拿不到 cadkit.fingerprint，跨机去重未执行（fail closed）: %s (%s)。"
                        "用 --plugin-python <含 cadkit 的 python 目录> 或设 CADKIT_PLUGIN_PY 指定"
                        % (PLUGIN_PY, PREDICATE_MODE))
    else:
        pool = [("A/" + k, v) for k, v in baseline.items() if isinstance(v, dict)]
        pool += [("B/" + k, v) for k, v in delta.items() if isinstance(v, dict)]
        exact_hits, scaled_hits, related_hits = [], [], []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                (ka, fa), (kb, fb) = pool[i], pool[j]
                try:
                    if abs(fa["volume"] - fb["volume"]) / max(fa["volume"], fb["volume"]) < 1e-6:
                        exact_hits.append("%s == %s (volume)" % (ka, kb))
                    elif scaled_same(fa, fb):
                        scaled_hits.append("%s ~ %s (scaled_same)" % (ka, kb))
                    elif related(fa, fb):
                        related_hits.append("%s ~ %s (related)" % (ka, kb))
                except KeyError as e:
                    blocking.append("指纹缺字段 %s: %s / %s" % (e, ka, kb))
        for h in exact_hits + scaled_hits:
            blocking.append("跨机重复（2.5 会判不计数）: " + h)
        stats.update(cross_pairs=len(pool) * (len(pool) - 1) // 2,
                     exact=len(exact_hits), scaled_same=len(scaled_hits),
                     related=len(related_hits))
        if related_hits:
            notes.append("同系列相似形（不阻断，仅记录）: " + "; ".join(related_hits[:6]))
    for pid in want:
        if pid not in delta:
            blocking.append("fingerprints.delta.json 没有 %s 的指纹" % pid)

    ok = not blocking
    verdict = {"ok": ok, "batch": batch, "stats": stats,
               "blocking": blocking, "notes": notes,
               "counting_guess": {"intake": len(want),
                                  "expected_unique": len(want) - stats.get("scaled_same", 0)
                                  - stats.get("exact", 0)}}
    if not a.json:
        print("== merge_check %s: %s" % (batch, "ACCEPT" if ok else "REJECT"))
        for k, v in stats.items():
            print("   %-16s %s" % (k, v))
        for b in blocking:
            print("   BLOCK  " + b)
        for n in notes:
            print("   note   " + n)
    else:
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def emit(verdict, as_json):
    if as_json:
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
    else:
        print("== merge_check %s: REJECT" % verdict["batch"])
        for b in verdict["blocking"]:
            print("   BLOCK  " + b)
    return 1


if __name__ == "__main__":
    sys.exit(main())
