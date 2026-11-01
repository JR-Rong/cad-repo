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
import hashlib
import json
import re
import os
import struct
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
HANDOFF = os.path.dirname(HERE)

# ---------------------------------------------------------------------------
# gates2 补丁（task-9 / CAD-AB-A2, 2026-11-01）：
#   ① 证据裁决取**全 evidence 表** kinds + job OK 仅按 pid —— 甲件 PASS 可掩乙件
#     缺证据、旧 rev 的 OK 可冒充新 rev 证据。改为按 (project_id, 交付 rev,
#     input_sha 依赖哈希, native/step sha, rules_sha) 绑定当前件当前 rev；
#   ③ 单给 --require-review 不隐含 strict：acceptance_overall=FAIL +
#     review_status=complete 可跳过 FAIL。统一实际接受 profile：FAIL 永不因
#     review 放行（review 只把 pending 变 complete）；
#   ⑤ strict+require_review 为默认；--lenient 唯一显式降级且落审计事件。
# ---------------------------------------------------------------------------
TOOL_VERSION = "merge_check-gates3-a4fix-20261101"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def audit_event(log_path, event):
    '''审计 JSONL（验收 profile；--lenient 必须留痕）。目录不可写时降级为打印。'''
    if not log_path:
        return None
    ev = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": TOOL_VERSION}
    ev.update(event)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + chr(10))
        return log_path
    except OSError:
        print("AUDIT " + json.dumps(ev, ensure_ascii=False))
        return None

# reuse the engine's own predicates when the plugin is importable
def find_plugin_python(explicit=None):
    """Where is cadkit/? A machine has it next to the workspace, B machine has it
    in the pnpm profile dir — and a wrong guess must say so, not silently
    disable the 2.5 gate."""
    import plugin_source
    path, _label = plugin_source.discover(explicit)
    return path


sys.path.insert(0, HERE)
import acceptance_contract as AC      # gates3（A4 修订）：唯一验收契约实现   # noqa: E402


def load_adjudicate(plugin_py=None):
    """收货侧也必须用**引擎同一个** adjudicate_review 语义（A4 P1-2）：
    回执裁决只有一份实现，收货侧不写缩水第二套。拿不到就 fail closed。"""
    import importlib
    if plugin_py and plugin_py not in sys.path:
        sys.path.insert(0, plugin_py)
    try:
        ver = importlib.import_module("cadkit.version")
        return getattr(ver, "adjudicate_review", None)
    except Exception:                                   # noqa: BLE001
        return None


def load_predicates(plugin_py=None):
    """Return (scaled_same, related, mode, path). Never silently None-compare."""
    path = find_plugin_python(plugin_py)
    if path is None:
        return None, None, "plugin python not found (见 tools/plugin_source.py)", None
    try:
        import engine_predicates
        fp, mode = engine_predicates.load(path)
        return fp.scaled_same, fp.related, mode, path
    except Exception as exc:                          # noqa: BLE001
        return None, None, "unavailable: %s" % exc, path


scaled_same = related = None
PREDICATE_MODE = "not loaded yet"
HAVE_ENGINE = False
ADJUDICATE = None          # cadkit.version.adjudicate_review（收货侧同一裁决实现）


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
    ap.add_argument("--strict", action="store_true",
                    help="gates2 ⑤：strict 收货是**默认**。此旗标仅为兼容既有命令行保留")
    ap.add_argument("--require-review", action="store_true",
                    help="gates2 ⑤：require_review 是**默认**。此旗标仅为兼容保留")
    ap.add_argument("--lenient", action="store_true",
                    help="gates2 ⑤：唯一显式降级——回旧弱语义（证据缺失只 note、"
                         "不查回执），并落审计事件")
    ap.add_argument("--audit-log", default=None,
                    help="审计事件 JSONL（默认 <theirs>/merge_audit.jsonl）")
    ap.add_argument("--require-dep-sha", action="store_true",
                    help="A4 修订 P1-3：即便回传未声明 dep_contract，也强制按现算依赖"
                         "指纹（dep_sha）绑定 job/evidence 行")
    ap.add_argument("--plugin-python", default=None,
                    help="含 cadkit/ 的 python 目录（默认自动找 profile 的 node_modules）")
    a = ap.parse_args()
    # gates2 ⑤：默认 strict+require_review；--lenient 显式降级且与显式强化冲突。
    if a.lenient and (a.strict or a.require_review):
        raise SystemExit("--lenient 与 --strict/--require-review 冲突："
                         "降级只能单独显式声明")
    a.strict = not a.lenient
    a.require_review = not a.lenient
    # gates2 ③：实际接受 profile 统一——单 --require-review 同样隐含 strict；
    # acceptance_overall=FAIL **永不**因 review_status=complete 放行。
    a.strict_eff = a.strict or a.require_review
    global scaled_same, related, PREDICATE_MODE, HAVE_ENGINE
    scaled_same, related, PREDICATE_MODE, PLUGIN_PY = load_predicates(a.plugin_python)
    HAVE_ENGINE = scaled_same is not None
    global ADJUDICATE
    ADJUDICATE = load_adjudicate(PLUGIN_PY)

    blocking, notes, stats = [], [], {}
    theirs = a.theirs
    batch = os.path.basename(theirs.rstrip("/"))
    audit_log = a.audit_log or os.path.join(theirs, "merge_audit.jsonl")
    # gates2 ⑤：验收 profile 落审计事件（lenient 每次收货都留痕）。
    audit_event(audit_log, {"event": "acceptance_profile", "batch": batch,
                            "lenient": bool(a.lenient), "strict": a.strict,
                            "require_review": a.require_review,
                            "theirs": theirs})

    # ---- 1. required artefacts -------------------------------------------
    need = ["ledger.jsonl", "fingerprints.delta.json", "versions.json", "REPORT.md",
            os.path.join("delivery", batch, "info.csv")]
    for rel in need:
        if not os.path.exists(os.path.join(theirs, rel)):
            blocking.append("缺少回传文件: " + rel)
    if blocking:
        # Nothing else is readable without ledger/delta/versions — stop here,
        # but only here: a partial report is worse than a full defect list.
        return emit({"ok": False, "batch": batch, "blocking": blocking, "notes": notes}, a.json)

    # lessons must have been READ before generating, not asserted in chat:
    # tools/recall.py stamps a sha of LESSONS.md and the return echoes it.
    rep_path = os.path.join(theirs, "REPORT.md")
    lessons_p = os.path.join(HANDOFF, "LESSONS.md")
    if os.path.exists(rep_path):
        rep = open(rep_path, encoding="utf-8").read()
        m = re.search(r"LESSONS_ACK:\s*([0-9a-f]{6,40})", rep)
        if os.path.exists(lessons_p):
            import hashlib
            want = hashlib.sha256(open(lessons_p, encoding="utf-8").read()
                                  .encode("utf-8")).hexdigest()[:8]
            if not m:
                blocking.append("REPORT.md 缺 LESSONS_ACK —— 生成前必须跑 tools/recall.py（WORKFLOW §7）")
            elif m.group(1)[:8] != want:
                blocking.append("LESSONS_ACK 过期: 回传 %s，当前教训 %s（教训已更新，重读后重跑）"
                                % (m.group(1)[:8], want))
            else:
                notes.append("LESSONS_ACK %s 与当前教训一致" % want)
        else:
            notes.append("LESSONS.md 不在仓里，跳过 ack 校验（异常状态）")

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
    intake_path = a.intake
    want, waves = [], []
    if intake_path:
        want = list(json.load(open(intake_path)).get("parts", []))
    else:
        # 500-family plan: the batch file carries families, the *wave* files carry
        # the ordered part ids. Union the waves that exist on disk.
        batch_doc = json.load(open(os.path.join(HANDOFF, "intake", batch + ".json")))
        want = list(batch_doc.get("parts", []))
        wdir = os.path.join(HANDOFF, "intake", "waves")
        if not want and os.path.isdir(wdir):
            import glob as _g
            for f in sorted(_g.glob(os.path.join(wdir, batch + "_w*.json"))):
                waves.append(os.path.basename(f))
                want += list(json.load(open(f)).get("parts", []))
        if not want:
            blocking.append("没有可核对的 intake（批次文件无 parts，且 intake/waves/%s_w*.json 不存在）"
                            "—— 没有合同就不收货" % batch)
    stats["intake_waves"] = waves
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

    # ---- 4. evidence chain: 绑定当前件当前 rev 的证据裁决 -------------------
    # gates2 ①：旧实现 (a) kinds 取**全 evidence 表** —— 甲件 PASS 可掩乙件缺证据；
    # (b) job OK 只按 pid —— 旧 rev 的 OK 可冒充新 rev。现在五阶段 OK、证据 PASS、
    # native/step sha 全部绑定交付 rev + input_sha 依赖哈希 + rules_sha。
    batch_dir = os.path.join(theirs, "delivery", batch)
    rev_rows = {r.get("rev"): r for r in rows_of(rows, "revision")}
    art_rows = {}
    for ar in rows_of(rows, "artifact"):
        art_rows.setdefault(ar.get("rev"), {})[ar.get("path")] = ar.get("sha")
    rev_of, dep_of, renders_of = {}, {}, {}   # 每件当前 rev/现算依赖值/包内渲染 digest
    delivered_rev = {}
    for d in deliveries:
        dp = by_model.get(d.get("model_id")) or d.get("project_id")
        if dp:
            delivered_rev.setdefault(dp, set()).add(d.get("rev"))
    their_rules = str(their_v.get("rules_sha") or "")
    for pid in want:
        if pid in missing:
            continue
        revs = sorted(r for r in (delivered_rev.get(pid) or set()) if r)
        R = revs[0] if len(revs) == 1 else None
        if a.strict and not R:
            blocking.append("%s: 交付行无法唯一确定当前 rev（得到 %s）—— 证据无法绑定当前件当前 rev"
                            % (pid, revs or "无 delivery.rev"))
        # ---- 4a. 当前**实际**依赖值（A4 修订 P1-3）----------------------------
        # 原缺陷：只验 job.project_id+rev+input_sha 非空，不看依赖值本身；
        # revision/artifact 行也不强制存在。现在按实际 schema 连
        # model→revision→artifact→evidence/job，并用包内字节 + 本次 rules_sha/
        # checker/profile **现算**依赖指纹（AC.dep_identity/dep_input_sha 与生产侧
        # fleet_runner.Recorder.dep_sha 同一函数），job 行必须与现算值相符。
        pdir0 = os.path.join(batch_dir, pid)
        arts = art_rows.get(R) or {}
        native_now = (sha256_file(os.path.join(pdir0, pid + ".FCStd"))
                      if os.path.exists(os.path.join(pdir0, pid + ".FCStd")) else None)
        step_now = (sha256_file(os.path.join(pdir0, pid + ".step"))
                    if os.path.exists(os.path.join(pdir0, pid + ".step")) else None)
        rdir0 = os.path.join(pdir0, "renders")
        renders_now = {}
        if os.path.isdir(rdir0):
            for name in sorted(os.listdir(rdir0)):
                if name.endswith(".png"):
                    renders_now[name] = sha256_file(os.path.join(rdir0, name))
        rc_path0 = os.path.join(pdir0, "review", "receipts.json")
        receipt_now = (sha256_file(rc_path0)
                       if os.path.exists(rc_path0) else None)
        man_path0 = os.path.join(pdir0, "export_manifest.json")
        manifest_now = (sha256_file(man_path0) if os.path.exists(man_path0)
                        else (AC.manifest_digest(renders_now) if renders_now else None))
        checker_now = str(their_v.get("engine_plugin_version")
                          or their_v.get("checker") or "")
        profile_now = str(their_v.get("profile_sha")
                          or their_v.get("delivery_profile_sha") or "")
        dep_now = AC.dep_identity(native_now, step_now, their_rules, checker_now,
                                  profile_now,
                                  AC.manifest_digest(renders_now) if renders_now else None,
                                  receipt_now)
        dep_declared = bool(their_v.get("dep_contract")) or a.require_dep_sha
        rev_of[pid] = R
        dep_of[pid] = dict(dep_now, manifest=manifest_now or "")
        renders_of[pid] = renders_now
        if a.strict:
            ok_stages, stale, dep_gap = set(), 0, []
            for j in jobs:
                if j.get("project_id") != pid or j.get("state") != "OK":
                    continue
                if j.get("rev") != R or not j.get("input_sha"):
                    stale += 1          # 旧 rev / 缺依赖哈希的 OK 行不再计数
                    continue
                if j.get("rules_sha") and their_rules and \
                        str(j["rules_sha"]) != their_rules:
                    dep_gap.append("%s(job.rules_sha=%s ≠ 本次 %s)"
                                   % (j.get("stage"), str(j["rules_sha"])[:10], their_rules[:10]))
                    continue
                if dep_declared:
                    # 变量名绝不撞 want（intake 清单）：本文件早前就踩过 for ... , want
                    # 把 want 毁掉的坑，这里用 want_dep。
                    want_dep = AC.dep_input_sha(j.get("stage"), pid, R, dep_now)
                    if not j.get("dep_sha"):
                        dep_gap.append("%s(缺 dep_sha)" % j.get("stage"))
                        continue
                    if str(j["dep_sha"]) != want_dep:
                        dep_gap.append("%s(dep_sha=%s ≠ 现算 %s)"
                                       % (j.get("stage"), str(j["dep_sha"])[:10],
                                          want_dep[:10]))
                        continue
                ok_stages.add(j["stage"])
            if stale:
                notes.append("%s: 忽略 %d 行旧 rev/缺 input_sha 的 OK（不能冒充当前 rev %s 证据）"
                             % (pid, stale, R))
            for g in dep_gap:
                blocking.append("%s: 当前 rev(%s) 的**依赖值**与本次实际不符（旧依赖/错 "
                                "checker/换 profile/换 Native 或 STEP/重写回执）—— %s"
                                % (pid, R, g))
            if dep_declared:
                kinds = {e.get("kind") for e in evidence
                         if e.get("status") == "PASS" and e.get("rev") == R
                         and str(e.get("rules_sha") or "") == their_rules
                         and (not e.get("dep_sha")
                              or str(e["dep_sha"]) == AC.dep_input_sha(e.get("kind"), pid, R, dep_now))}
            else:
                kinds = {e.get("kind") for e in evidence
                         if e.get("status") == "PASS" and e.get("rev") == R
                         and (not their_rules
                              or str(e.get("rules_sha") or "") == their_rules)}
        else:                            # lenient（显式降级）保留旧语义，仅记录
            ok_stages = {j["stage"] for j in jobs
                         if j.get("project_id") == pid and j.get("state") == "OK"}
            kinds = {e.get("kind") for e in evidence if e.get("status") == "PASS"}
        need_stages = {"build", "validate", "probe", "export", "package"}
        gap = need_stages - ok_stages
        if gap:
            blocking.append("%s 台账缺少当前 rev(%s) 绑定的 OK 阶段（含 input_sha 依赖哈希）: %s"
                            % (pid, R or "-", ",".join(sorted(gap))))
        if a.strict and not dep_declared:
            notes.append("%s: 回传未声明 dep_contract（旧导出器）——依赖值只验到 "
                         "rev+input_sha+artifact 字节；新包必须声明 dep_contract "
                         "或加 --require-dep-sha" % pid)
        for k in ("validate", "probe", "export"):
            if k not in kinds:
                msg = ("%s: 证据表没有**绑定当前 rev(%s)** 的 %s PASS 行"
                       "（甲件证据不算乙件；全表 %d 行不足为凭）" % (pid, R, k, len(evidence)))
                if a.strict:
                    blocking.append(msg)
                else:
                    notes.append(msg)
                break
        # native/step sha 与交付 rev 的 artifact/revision 绑定（证据与实物同一件）
        if a.strict and R:
            for fn in (pid + ".FCStd", pid + ".step"):
                want_sha = arts.get(fn)
                fpath = os.path.join(pdir0, fn)
                if not want_sha:
                    blocking.append("%s: 台账 artifact 无 %s 绑定行（rev %s 的 native/step sha 无从核验）"
                                    % (pid, fn, R))
                elif os.path.exists(fpath):
                    got = sha256_file(fpath)
                    if got != want_sha:
                        blocking.append("%s %s 回传字节(%s…)与台账 rev %s 绑定(%s…)不符 —— 证据与实物不是同件"
                                        % (pid, fn, got[:12], R, want_sha[:12]))
            rv = rev_rows.get(R)
            if not rv:
                blocking.append("%s: rev %s 无 revision 行（model/依赖值/产物无从串联，"
                                "拒绝把 job+evidence 当作当前件当前 rev 的证据）" % (pid, R))
            else:
                mid_deliver = None
                for d in deliveries:
                    dp = by_model.get(d.get("model_id")) or d.get("project_id")
                    if dp == pid and d.get("rev") == R and d.get("state") == "delivered":
                        mid_deliver = d.get("model_id")
                if mid_deliver is not None and str(rv.get("model_id")) != str(mid_deliver):
                    blocking.append("%s: revision.model_id=%s 不是交付 model_id=%s"
                                    "（不得借其他 model 的 revision 行）" % (pid, rv.get("model_id"), mid_deliver))
                # 注意：models 以 project_id 为键（文件上面就是这么建的），
                # 这里按本件取 model 行再核 model_id，不得 models.get(model_id)。
                mrow = models.get(pid) or {}
                if not mrow:
                    blocking.append("%s: 台账缺 project_id=%s 的 model 行（model_id=%s "
                                    "无从串联 revision）" % (pid, pid, mid_deliver))
                elif mid_deliver is not None and \
                        str(mrow.get("model_id")) != str(mid_deliver):
                    blocking.append("%s: model 行 model_id=%s 与交付 model_id=%s 不符"
                                    "（跨件混绑）" % (pid, mrow.get("model_id"), mid_deliver))
                if rv.get("fcstd_sha") and arts.get(pid + ".FCStd") and \
                        str(rv["fcstd_sha"]) != arts[pid + ".FCStd"]:
                    blocking.append("%s: revision.fcstd_sha 与 artifact 行不符（台账自相矛盾）" % pid)
                elif dep_declared and not rv.get("fcstd_sha"):
                    blocking.append("%s: rev %s 的 revision.fcstd_sha 缺失（依赖值链断裂）"
                                    % (pid, R))
                if dep_declared and rv.get("rules_sha") and their_rules and \
                        str(rv["rules_sha"]) != their_rules:
                    blocking.append("%s: revision.rules_sha=%s ≠ 本次 %s（错 checker/旧依赖）"
                                    % (pid, str(rv["rules_sha"])[:10], their_rules[:10]))

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

    # ---- 5b. 实际接受 profile：交付自证 + 绑定回执工件（gates2 ③④⑤）----
    # gates2 ③：统一 profile —— acceptance_overall=FAIL **永不**因
    # review_status=complete 放行（review 只把 pending 变 complete）；
    # gates2 ④：review_status=complete 必须有随包的绑定人审回执工件
    # （review/receipts.json：project_id + L2 渲染 digest 与实际交付字节逐张相符）。
    if a.strict_eff:
        for pid in want:
            if pid in missing:
                continue
            clp = os.path.join(batch_dir, pid, "DELIVERY_CHECKLIST.json")
            if not os.path.exists(clp):
                blocking.append("%s 缺 DELIVERY_CHECKLIST.json（strict 收货必须核验交付自证）" % pid)
                continue
            try:
                cl = json.load(open(clp, encoding="utf-8"))
            except Exception as exc:
                blocking.append("%s DELIVERY_CHECKLIST.json 读不动: %s" % (pid, exc))
                continue
            ao = cl.get("acceptance_overall")
            if a.strict_eff and ao != "PASS":
                blocking.append("%s acceptance_overall=%s —— FAIL 永不因 "
                                "review_status=complete 放行（review 只把 pending 变 complete）"
                                % (pid, ao))
            if a.require_review and cl.get("review_status") != "complete":
                blocking.append("%s review_status=%s（require_review：人审未闭环不收）"
                                % (pid, cl.get("review_status")))
            if a.require_review:
                # A4 修订 P1-2：收货侧回执 = 与签发侧**同一份完整裁决契约**
                # （acceptance_contract.adjudicate_receipt 内部再调引擎
                # cadkit.version.adjudicate_review）。绑定 project + 交付 rev +
                # native_sha256 + family + contract_version + review_of_record +
                # manifest sha；L1 必须存在且 PASS；L2 必须覆盖全部 8 张渲染且
                # 逐张与包内字节相符。不再自写"只看 project_id + 非空 L2"的缩水版。
                rcp = os.path.join(batch_dir, pid, "review", "receipts.json")
                if not os.path.exists(rcp):
                    blocking.append("%s review_status=complete 但交付包无绑定人审回执 "
                                    "review/receipts.json（验收无工件）" % pid)
                    continue
                try:
                    rc = json.load(open(rcp, encoding="utf-8"))
                except Exception as exc:
                    blocking.append("%s review/receipts.json 读不动: %s" % (pid, exc))
                    continue
                if ADJUDICATE is None:
                    blocking.append("%s 拿不到引擎 adjudicate_review，无法按完整契约裁决"
                                    "回执（fail closed）: %s" % (pid, PREDICATE_MODE))
                    continue
                res = AC.adjudicate_receipt(
                    rc, ADJUDICATE, pid, dep_of.get(pid, {}).get("native"),
                    renders_of.get(pid, {}), family_sha=None,
                    contract=rc.get("contract_sha256"),
                    manifest_sha=dep_of.get(pid, {}).get("manifest"),
                    rev=rev_of.get(pid), contract_version=AC.CONTRACT_VERSION,
                    require=("rev", "contract_version"))
                for why in res["reasons"]:
                    blocking.append("%s 人审回执完整契约不符: %s" % (pid, why))
                if res["ok"]:
                    notes.append("%s 人审回执按完整契约核验通过（rev=%s，8 图逐张相符）"
                                 % (pid, rev_of.get(pid)))

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
        pool = [(k, v) for k, v in baseline.items() if isinstance(v, dict)]
        pool += [(k, v) for k, v in delta.items() if isinstance(v, dict) and isinstance(v, dict)]
        exact_hits, scaled_hits, related_hits = [], [], []
        # Planned volume is 200,000 parts, so the naive O(n²) sweep is not an
        # option. `exact` needs equal volume and `scaled_same` requires identical
        # solid/face/edge counts, therefore (solids, faces, edges) is a *sound*
        # blocking key: nothing that could be judged a duplicate is skipped.
        # (`related` stays advisory and is only reported inside buckets — noted
        # in stats so the wording of a report never overstates it.)
        buckets = {}
        for k, f in pool:
            try:
                key = (f["solids"], f["faces"], f["edges"])
            except KeyError as e:
                blocking.append("指纹 %s 缺字段 %s" % (k, e))
                continue
            buckets.setdefault(key, []).append((k, f))
        pairs = 0
        for group in buckets.values():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pairs += 1
                    (ka, fa), (kb, fb) = group[i], group[j]
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
        stats.update(cross_pairs=pairs, cross_pool=len(pool), cross_buckets=len(buckets),
                     exact=len(exact_hits), scaled_same=len(scaled_hits),
                     related=len(related_hits))
        if related_hits:
            notes.append("同系列相似形（不阻断，仅记录）: " + "; ".join(related_hits[:6]))
    for pid in want:
        if pid not in delta:
            blocking.append("fingerprints.delta.json 没有 %s 的指纹" % pid)

    ok = not blocking
    verdict = {"ok": ok, "batch": batch, "stats": stats,
               "lenient": bool(a.lenient),      # A4 修订：正式接受计数/发布导出须排除
               "blocking": blocking, "notes": notes,
               "acceptance_profile": {"tool": TOOL_VERSION, "strict": a.strict,
                                      "require_review": a.require_review,
                                      "strict_eff": a.strict_eff,
                                      "lenient": bool(a.lenient),
                                      "audit_log": audit_log},
               "counting_guess": {"intake": len(want),
                                  "expected_unique": len(want) - stats.get("scaled_same", 0)
                                  - stats.get("exact", 0)}}
    if not a.json:
        print("== merge_check %s: %s%s" % (
            batch, "ACCEPT" if ok else "REJECT",
            " [lenient=true：弱口径收货，不得计入正式接受计数/发布导出]"
            if a.lenient else ""))
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
