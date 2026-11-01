#!/usr/bin/env python3
"""双通道调度器：新件生产与旧件审查并行，包装（package）严格串行。

为什么不能一味拉满：
  * 单件峰值 RSS ~430–530 MB（实测），插件每进程 cap 2048 MB、进程组 6144 MB；
  * N43(P1)/N44(P2) 都在并发发布/跨窗口合并上触发，出厂包未修 ⇒ `package` 必须独占；
  * 探针自带并发上限（probe worker 2）。
所以这里是**资源准入 + 单一 package 槽位**的池：生产跑 N 路 build/validate/probe/export，
审查通道同时跑（它只做只读复检，很轻），要 package 的件排队进那一个槽位。

  python3 tools/fleet_runner.py --root ../cad --batch fleet_a_2026_11 \\
      --intake intake/waves/fleet_a_2026_11_w1.json --stages build,validate,probe,export \\
      --workers 3 --review-batch pilot_2026_10 --review-every 6 --dry-run

断点续跑靠台账幂等键（已 OK 且 input_hash 未变的阶段直接跳过），所以崩了就重跑同一条命令。
"""
import argparse
import hashlib
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plugin_source                                                # noqa: E402
import acceptance_contract as AC   # gates3（A4 修订）：唯一验收契约实现  # noqa: E402

ENTRY = "entry.py"


def find_entry():
    path, label = plugin_source.discover()
    if not path:
        raise SystemExit("找不到插件引擎（%s）—— git pull 或 --plugin-python" % label)
    return os.path.join(path, ENTRY), path


def rules_sha_of(pydir):
    """出厂引擎自己的 rules_sha（宿主 python3 就能算，不用起 FreeCAD）。"""
    probe = ("import sys; sys.path.insert(0, %r); from cadkit.version import rules_sha; "
             "print(rules_sha())") % pydir
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or None


def find_freecad(explicit=None):
    """entry.py 的 shebang 就是 freecadcmd：宿主 python3 里没有 FreeCAD 模块。

    位点顺序：--freecad-bin → FREECAD_BIN → PATH → macOS 默认安装位。
    """
    cand = [explicit, os.environ.get("FREECAD_BIN"),
            shutil.which("freecadcmd"),
            "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"]
    for c in cand:
        if c and os.path.exists(c):
            return c
    raise SystemExit("找不到 freecadcmd（entry.py 必须在它下面跑；用 --freecad-bin 指路）。"
                     "记住：宿主 python3 跑 entry.py 只会得到 \"No module named 'FreeCAD'\"，"
                     "而它仍然退出码 0。")


# ---------------------------------------------------------------------------
# Stage recorder (fix 2026-10-31, A-line; B 机 08:30 报告的两处之外补第三处)
#
# 出厂引擎里 job 表只有 schema 和读写访问器，**没有任何写入方**：
# "崩了重跑、已 OK 阶段自动跳过" 的幂等键全靠这张表，不写它 resume 就是空话，
# merge_check 的"台账五阶段"门禁也永远拿不到 OK 行。pilot 批次当时用
# cad/tools/pilotctl.py 记账；这里按同一套键语义做成舰队版：
#   base = sha(project.yml) + sha(族谱模块) + rules_sha
#   build 输入含 build.py 自身；validate/probe/dedupe/export 含 FCStd；
#   package 含全部证据文件 + STEP + 8 渲染 + build.py（N7 语义）。
# ---------------------------------------------------------------------------

def _h(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:20]


def _sha(path):
    if not os.path.exists(path):
        return "NOFILE"
    hh = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            hh.update(c)
    return hh.hexdigest()


RENDER_VIEWS = ("front", "back", "left", "right", "top", "bottom",
                "iso_up", "iso_down")

# ---------------------------------------------------------------------------
# gates2 补丁（task-9 / CAD-AB-A2, 2026-11-01）：
#   ② input_hash 缺 strict/require_review profile、checker/工具版本、审查回执绑定
#     —— profile 换档/工具升版/回执重写都不会让 package 重跑（弱接受跨续跑存活）；
#   ④ strict 一律拒全部 WARN，与引擎 validate.py:552-554 real_object 恒 WARN
#     （人工终审位）冲突 —— 机器 FAIL 必阻断；WARN 只可由绑定 (pid, rev, 渲染 sha)
#     的人审回执工件放行；无回执生成待审材料（8 视图/检查单/pending 记录）但不
#     最终接受；绝不把 WARN 改判 PASS，绝不伪造回执；
#   ⑤ strict+require_review 是默认路径；--lenient 是唯一显式降级且落审计事件。
# ---------------------------------------------------------------------------
TOOL_VERSION = "fleet_runner-gates3-a4fix-20261101"


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def receipt_path(root, pid):
    '''人审回执工件位置：与待审材料同目录（人工写入；调度器只读不写）。'''
    return os.path.join(root, "projects", pid, "review", "receipts.json")


def audit_event(log_path, event):
    '''追加一条审计 JSONL（验收 profile、review_pending 等）。--lenient 必须留痕。'''
    if not log_path:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    ev = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": TOOL_VERSION}
    ev.update(event)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + chr(10))
    return log_path


class Recorder:
    def __init__(self, root, engine_py, rules, profile=None):
        if engine_py not in sys.path:
            sys.path.insert(0, engine_py)
        from cadkit.ledger import open_ledger, job_state, job_record
        self.root, self.rules = root, rules
        self._open, self._state, self._rec = open_ledger, job_state, job_record
        # gates2 ②：验收 profile、checker/工具版本、人审回执字节都是 package 的输入。
        # 旧版 input_hash 只绑 yml/family/rules_sha+产物 sha —— lenient→strict 换档、
        # 工具升版、回执重写都不让 package 重跑，弱接受跨续跑存活。
        prof = {"strict": False, "require_review": False,
                "tool_version": TOOL_VERSION,
                "tool_sha": _sha(os.path.abspath(__file__))}
        prof.update(profile or {})
        self.profile = prof
        # ---------------- 依赖值接口（A4 整合项；eng_prof 合并时生效）--------
        # prof_sha 历史上只吃 strict/require_review 两旗标 + 工具版本 + runner
        # 文件 hash。这里改成对**整个 profile 规范化后全量 hash**：
        #   * 合并 eng_prof 时把 delivery profile 正文（尺寸档/渲染口径/验收清单
        #     版本…）与 checker 语义版本一并放进 profile= 参数；
        #   * 于是 profile 内容或 checker 语义一变 ⇒ prof_sha 变 ⇒ package 的
        #     input_hash 变 ⇒ 台账 OK 不再命中 ⇒ 下游自动重跑（旧弱接受不会跨
        #     续跑存活）；接口点实跑证据见 out2/out_gates3_integration.txt I1。
        # 两旗标本身仍单独落审计事件（--lenient 留痕语义不变）。
        self.prof_sha = _h("profile", AC.canon(prof))

    def review_receipt_sha(self, pid):
        return _sha(receipt_path(self.root, pid))

    def dep_identity(self, pid):
        """A4 修订 P1-3 的**生产侧写点**：当前实际依赖值 = native/step/渲染/
        rules_sha/checker 版本/profile/回执字节。
        台账导出必须随 job 行落 dep_sha = self.dep_sha(pid, stage, rev)；
        收货侧 merge_check 用包内字节 + versions.json + 回执工件重算同一函数，
        同 rev 换 Native/STEP、错 checker、换 profile、重写回执都会显式不符。"""
        pr = os.path.join(self.root, "projects", pid)
        bd = os.path.join(pr, "build")
        renders = {}
        for v in RENDER_VIEWS:
            f = os.path.join(bd, "renders", "view_%s.png" % v)
            if os.path.exists(f):
                renders["view_%s.png" % v] = _sha(f)
        return AC.dep_identity(
            _sha(os.path.join(bd, pid + ".FCStd")),
            _sha(os.path.join(bd, pid + ".step")),
            self.rules, TOOL_VERSION, self.prof_sha,
            AC.manifest_digest(renders) if renders else None,
            self.review_receipt_sha(pid))

    def dep_sha(self, pid, stage, rev=None):
        idn = self.dep_identity(pid)
        return AC.dep_input_sha(stage, pid, rev or ("rev:" + idn["native"]), idn)

    def input_hash(self, pid, stage):
        pr = os.path.join(self.root, "projects", pid)
        try:
            fam = (json.load(open(os.path.join(pr, "project.yml"),
                                  encoding="utf-8")) or {}).get("family")
        except Exception:
            fam = None
        yml = _sha(os.path.join(pr, "project.yml"))
        famsha = _sha(os.path.join(self.root, "family", fam + ".py")) \
            if fam else "NOFAM"
        base = _h(yml, famsha, self.rules)
        bsrc = _sha(os.path.join(pr, "build.py"))
        bd = os.path.join(pr, "build")
        fc = _sha(os.path.join(bd, pid + ".FCStd"))
        if stage == "build":
            return _h(base, bsrc)
        if stage in ("validate", "probe", "dedupe", "export"):
            return _h(base, fc)
        if stage == "package":
            extra = "".join(_sha(os.path.join(bd, fn)) for fn in
                            ("report.json", "probe.json", "export_manifest.json"))
            for v in RENDER_VIEWS:
                extra += _sha(os.path.join(bd, "renders", "view_%s.png" % v))
            extra += _sha(os.path.join(bd, pid + ".step")) + bsrc
            # gates2 ②：package 是验收阶段 —— profile/工具版本/人审回执字节都参与
            # input_hash；任一变化 ⇒ 台账 OK 不再命中 ⇒ 必须重跑（旧证据行保留）。
            return _h(base, fc, extra, self.prof_sha,
                      _h("receipt", self.review_receipt_sha(pid)))
        raise SystemExit("unknown stage " + stage)

    def state(self, pid, stage):
        conn = self._open(self.root)
        try:
            return self._state(conn, pid, stage,
                               self.input_hash(pid, stage), self.rules)
        finally:
            conn.close()

    def record(self, pid, stage, ok, error_class=None):
        conn = self._open(self.root)
        try:
            self._rec(conn, pid, stage, self.input_hash(pid, stage),
                      self.rules, "OK" if ok else "FAIL", error_class)
        finally:
            conn.close()


def pending(rec, intake, stages, limit=None):
    """Only queue what the ledger has not already accepted **for these very
    inputs**（旧版按 (pid,stage) 查任意 input_sha 的 OK，改了模型也能续跑过去，
    这是错的；现在是 pilotctl 的严格语义）。"""
    doc = json.load(open(intake, encoding="utf-8"))
    pids = doc.get("parts", [])
    out = []
    for pid in pids:
        todo = [s for s in stages if rec.state(pid, s) != "OK"]
        if todo:
            out.append((pid, todo))
    return out[:limit] if limit else out


def verdict(payload, stage, expect_rules=None, strict=False):
    """只有引擎自己说 ok 才算过；checks[] 里任何 FAIL 都算不过。

    修（B 机 08:30 报告①，代码复核成立）：checks 里的 WARN 是引擎设计好的
    "人工终审提示位"（validate.py:552 real_object 恒为 WARN），引擎门禁与
    packager 都按 WARN 放行；旧白名单没有它，fleet 模式下 validate 会全灭。
    WARN 计入日志但**不**算不过。

    修（去重阶段）：act_dedupe 的返回没有 ok 键，只有 exact/scaled 命中列表；
    工单 §7 的口径是 exact/scaled_same 阻断、series/related 不阻断 ——
    与 act_package 的 pre-gate（possible_exact + scaled∩series）同语义。
    """
    if not isinstance(payload, dict) or "ok" not in payload:
        if stage == "dedupe" and isinstance(payload, dict) and (
                "exact" in payload or "similar_series" in payload):
            hits = list(payload.get("possible_exact") or [])
            sc = set(payload.get("scaled") or [])
            hits += [m for m in (payload.get("similar_series") or []) if m in sc]
            if hits:
                return False, ("dedupe 命中 exact/scaled_same: %s —— 工单 §7：停下喊人"
                               % ",".join(hits[:5]))
            return True, ""
        return False, "无 CADKIT JSON 输出（阶段未真正执行）"
    got = ((payload.get("engine") or {}).get("rules_sha"))
    if strict and expect_rules and not got:
        return False, ("strict: 引擎报告没带 rules_sha，版本握手无从校验"
                       "（旧代码 got=None 直接放行）")
    if expect_rules and got and got != expect_rules:
        return False, ("版本握手不一致：本阶段引擎 rules_sha=%s，签发值=%s "
                       "—— 整波停下，别用错引擎生产（见 shared/versions.json）"
                       % (got, expect_rules))
    _allow = ("PASS", "SKIP", "N/A") if strict else \
             ("PASS", "SKIP", "N/A", "", "WARN")
    bad = [c for c in (payload.get("checks") or [])
           if isinstance(c, dict) and str(c.get("status", "")).upper() not in _allow]
    if bad:
        return False, "checks FAIL: " + "; ".join(
            "%s(%s)" % (c.get("id"), str(c.get("detail", ""))[:60]) for c in bad[:3])
    for key in ("report", "probe", "gate"):
        sub = payload.get(key)
        if isinstance(sub, dict) and sub.get("passed") is False:
            return False, "%s.passed=false %s" % (key, json.dumps(
                sub.get("failures") or sub.get("failed") or "", ensure_ascii=False))[:180]
    if payload.get("passed") is False:
        return False, "%s.passed=false" % stage
    if not payload.get("ok"):
        for k in ("error", "reason", "failures", "failed", "blocking"):
            v = payload.get(k)
            if v:
                return False, ("%s=%s" % (k, json.dumps(v, ensure_ascii=False)
                                          if isinstance(v, (list, dict)) else v))[:220]
        return False, "ok=false（引擎未给出原因）"
    return True, ""


def _warn_ids(payload):
    return [c.get("id") for c in ((payload or {}).get("checks") or [])
            if isinstance(c, dict)
            and str(c.get("status", "")).upper() == "WARN"]


def warn_only_blockers(payload, allow=None):
    '''gates3（A4 修订 P1-1）：strict 口径下**可由人审闭环**的 WARN id 列表。

    返回 None 的三种情形（都不得走回执）：
      * 混有机器 FAIL / 空 / 未知状态 —— 机器硬 FAIL；
      * 任何**不在合同白名单** AC.HUMAN_CLOSABLE_WARN_IDS 的 WARN —— 未知 WARN
        一律不可人审放行（白名单只含引擎人工终审位 real_object，扩容须改合同）；
      * 没有未允许状态（本来就该过）。'''
    allow = tuple(allow) if allow else AC.HUMAN_CLOSABLE_WARN_IDS
    bad = [c for c in ((payload or {}).get("checks") or []) if isinstance(c, dict)
           and str(c.get("status", "")).upper() not in AC.ALLOW_STATUSES]
    if not bad:
        return None
    if any(str(c.get("status", "")).upper() != "WARN" for c in bad):
        return None
    ids = [str(c.get("id")) for c in bad]
    if any(i not in allow for i in ids):
        return None
    return ids


def review_receipt_state(root, pid, engine_py, rules=None, warn_ids=None):
    '''绑定人审回执裁决（gates2 ④）：回执必须绑定 (pid, rev=native 字节 sha,
    contract, family, L2 渲染 digest 集)。复用引擎 cadkit.version.adjudicate_review
    的 require_review 强制绑定语义 —— 不另起炉灶、不放宽、不伪造。'''
    rp = receipt_path(root, pid)
    receipt = _load_json(rp)
    if not isinstance(receipt, dict) or not receipt:
        return {"valid": False, "path": rp,
                "reason": "review/receipts.json 缺失或为空（无绑定人审回执工件）"}
    if engine_py and engine_py not in sys.path:
        sys.path.insert(0, engine_py)
    try:
        from cadkit.version import adjudicate_review
    except Exception as exc:
        return {"valid": False, "path": rp,
                "reason": "无法导入 adjudicate_review: %s" % exc}
    pr = os.path.join(root, "projects", pid)
    bd = os.path.join(pr, "build")
    doc = _load_json(os.path.join(pr, "project.yml")) or {}
    fcstd_sha = _sha(os.path.join(bd, pid + ".FCStd"))
    fam = doc.get("family")
    famsha = _sha(os.path.join(root, "family", fam + ".py")) if fam else None
    contract = hashlib.sha256(json.dumps(
        {"params": (doc.get("variant") or {}).get("params", {}),
         "topo": (doc.get("variant") or {}).get("topo", {}),
         "family": fam or ""},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    renders = {}
    for v in RENDER_VIEWS:
        p = os.path.join(bd, "renders", "view_%s.png" % v)
        if os.path.exists(p):
            renders["view_%s.png" % v] = _sha(p)
    man = os.path.join(bd, "export_manifest.json")
    man_sha = _sha(man) if os.path.exists(man) else None
    # A4 修订 P1-2：签发侧也走 acceptance_contract.adjudicate_receipt 同一入口
    # （与收货侧共用一份完整裁决契约，不做两套）。
    res = AC.adjudicate_receipt(receipt, adjudicate_review, pid, fcstd_sha, renders,
                                family_sha=famsha, contract=contract,
                                manifest_sha=man_sha, warn_ids=warn_ids)
    return {"valid": res["ok"], "rinfo": res["rinfo"], "path": rp,
            "receipt_sha": _sha(rp), "reason": "; ".join(res["reasons"])}


def write_pending_review_material(root, pid, warn_ids, reason=""):
    '''WARN 阻断后的待审材料：8 视图清点 + 检查单 + pending 记录 + 填写说明。
    只生成**材料**，不生成回执——回执只能由人写（不得伪造）。'''
    rdir = os.path.join(root, "projects", pid, "review")
    os.makedirs(rdir, exist_ok=True)
    bd = os.path.join(root, "projects", pid, "build")
    views = []
    for v in RENDER_VIEWS:
        p = os.path.join(bd, "renders", "view_%s.png" % v)
        views.append({"view": v, "path": p, "exists": os.path.exists(p),
                      "sha256": _sha(p) if os.path.exists(p) else None})
    ids = sorted(set([str(w) for w in (warn_ids or [])]))
    doc = {"kind": "pending_review", "project_id": pid, "status": "pending",
           "warn_checks": ids, "reason": str(reason)[:400],
           "render_checklist": views,
           "howto": "人审通过后把 L1/L2 回执写入 review/receipts.json（project_id、"
                    "native_sha256、family_sha、review_of_record、"
                    "render_manifest_sha256、L2.render_sha256 必须绑定当前件当前 "
                    "rev 与实际渲染字节），再重跑续作；调度器不生成也不修改回执。",
           "tool": TOOL_VERSION, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    path = os.path.join(rdir, "pending_checklist.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return path


def strict_review_adjudication(root, pid, stage, payload, engine_py,
                               audit_log=None, expect_rules=None):
    '''gates2 ④ 统一入口（仅 strict 下调用）。返回 (ok, err, extra)：
      None  ⇒ 不归本裁决管（保持原 verdict 结论，机器 FAIL 阻断不变）；
      True  ⇒ 纯 WARN 且存在绑定 (pid,rev,渲染sha) 的人审回执 ⇒ 放行并留证；
      False ⇒ 纯 WARN 无绑定回执：生成待审材料（8 视图/检查单/pending 记录）
              但**不**最终接受。'''
    wids = warn_only_blockers(payload)
    if wids is None:
        if isinstance(payload, dict) and payload.get("stage") == "review":
            # 引擎 require_review 拒 package：同样生成待审材料，但不最终接受
            mp = write_pending_review_material(
                root, pid, sorted((payload.get("reviews") or {}).keys()),
                payload.get("error") or "engine require_review refused")
            audit_event(audit_log, {"event": "review_pending", "pid": pid,
                                    "stage": stage, "material": mp})
            return False, "require_review: 人审回执不全（待审材料 %s）" % mp, \
                {"review_pending": True, "material": mp}
        return None, "", {}
    # A4 修订 P1-1：先决条件（握手/协议/ok=false/passed=false/机器 FAIL/未知 WARN）
    # 先判一遍——即便 checks 全白名单 WARN，先决条件不过也**不得**进入回执翻转。
    hard, hard_reason = AC.hard_prerequisite(payload, stage, expect_rules, strict=True)
    if hard:
        audit_event(audit_log, {"event": "hard_reject", "pid": pid, "stage": stage,
                                 "reason": hard_reason, "receipt_can_flip": False})
        return None, "硬拒（人审回执不可翻转）：" + hard_reason, {"hard_reject": True}
    state = review_receipt_state(root, pid, engine_py, warn_ids=wids)
    if state.get("valid"):
        return True, "", {"reviewed_warns": wids,
                          "review_receipt_sha": state.get("receipt_sha")}
    mp = write_pending_review_material(root, pid, wids, state.get("reason"))
    audit_event(audit_log, {"event": "review_pending", "pid": pid, "stage": stage,
                            "warns": wids, "material": mp,
                            "reason": state.get("reason")})
    return False, ("strict: WARN %s 无绑定 (pid,rev,渲染sha) 人审回执 —— 已生成待审"
                   "材料 %s（不最终接受）" % (",".join(wids), mp)), \
        {"review_pending": True, "material": mp}


def run_stage(freecad, entry, root, batch, pid, stage, mem_mb, timeout,
              expect_rules=None, strict=False, require_review=False,
              engine_py=None, audit_log=None):
    # 参数只能走环境：freecadcmd 会先把 --xxx 当成自己的选项吃掉
    cmd = [freecad, entry]
    env = dict(os.environ)
    opts = {}
    if stage == "package":
        opts["batch"] = batch
        # gates2 ④：把绑定人审回执工件透传给引擎（@文件 ⇒ packager 自己裁决绑定）
        rp = receipt_path(root, pid)
        if os.path.exists(rp):
            opts["review"] = "@" + os.path.abspath(rp)
    if stage == "export":
        opts["long_side"] = 2400
    env.update({"CADKIT_ACTION": stage, "CADKIT_ROOT": root, "CADKIT_PROJECT": pid,
                "CADKIT_MEM_MB": str(mem_mb), "CADKIT_OPTS": json.dumps(opts)})
    # T-strict 补丁A：strict/require_review 必须真的传到引擎（packager L329-333 认这两个 env）。
    # 旧版舰队跑 package 永远是引擎默认非 strict——strict 名不副实。
    if strict:
        env["CADKIT_STRICT"] = "1"
    if require_review:
        env["CADKIT_REQUIRE_REVIEW"] = "1"
        env["CADKIT_STRICT"] = "1"   # 人审强制隐含 strict
    # 修（B 机 08:30 报告②，代码复核成立）：FreeCAD 的进度条与 CADKIT JSON
    # 共享同一 stdout 行流，40KB 块边界可以把 JSON 拦腰截断 —— 而 entry.py
    # 对**所有** action 都支持 --out（结果文件由引擎自己落盘，不过 stdout）。
    # 旧版只给 validate/probe/export/package 设了 CADKIT_OUT，build 裸奔；
    # 现在全阶段都落文件、优先读文件，stdout 只剩兜底。旧文件先删，
    # 防上一轮的陈旧结果冒充本轮执行。
    out = os.path.join(root, "projects", pid, "build", "runner_%s.json" % stage)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        os.remove(out)
    except OSError:
        pass
    env["CADKIT_OUT"] = out
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"pid": pid, "stage": stage, "ok": False, "error": "timeout %ds" % timeout,
                "seconds": round(time.time() - t0, 1)}
    payload = None
    try:
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        payload = None
    if payload is None:  # 兜底：stdout 的 CADKIT>> 行（可能被 spinner 污染）
        for line in reversed((p.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("CADKIT>>"):
                try:
                    payload = json.loads(line[len("CADKIT>>"):].strip())
                except ValueError:
                    payload = None
                break
    ok, err = verdict(payload, stage, expect_rules, strict=strict)
    adj = {}
    hard = False
    if not ok and strict:
        # A4 修订 P1-1：**硬拒优先**。先决条件（握手/协议/ok=false/
        # report|probe|gate.passed=false/机器 FAIL/非白名单 WARN）不成立时，
        # 根本不去问有没有回执——任何回执都不可翻转硬拒（原缺陷是 L460-461 把
        # 原拒绝直接覆盖成 ok=True）。
        hard, hard_reason = AC.hard_prerequisite(payload, stage, expect_rules,
                                                 strict=True)
        if hard:
            audit_event(audit_log, {"event": "hard_reject", "pid": pid,
                                    "stage": stage, "reason": hard_reason,
                                    "receipt_can_flip": False})
            adj = {"hard_reject": True}
            err = "%s｜硬拒先决条件：%s（人审回执不可翻转）" % (err, hard_reason)
        else:
            adj_ok, adj_err, adj = strict_review_adjudication(
                root, pid, stage, payload, engine_py, audit_log,
                expect_rules=expect_rules)
            if adj_ok is True:
                ok, err = True, ""
            elif adj_ok is False:
                err = adj_err or err
    if not ok and not err:
        err = ((p.stderr or p.stdout or "").strip().splitlines() or ["?"])[-1][:200]
    if payload is None and p.returncode != 0:
        err = "rc=%d %s" % (p.returncode, err)
    mem = (payload or {}).get("mem") or {}
    out = {"pid": pid, "stage": stage, "ok": ok, "rc": p.returncode,
           "seconds": round(time.time() - t0, 1),
           "peak_mb": mem.get("peak_mb"), "degraded": bool(mem.get("degraded")),
           "warns": _warn_ids(payload),
           # A4 修订（整合项）：输出结构带验收 profile 标记。lenient=true 的记录
           # 只是历史导入/研究口径，**不得**计入正式接受计数、不得进发布导出链。
           "lenient": not strict, "strict": bool(strict),
           "require_review": bool(require_review),
           "hard_reject": bool(adj.get("hard_reject")),
           "error": None if ok else err}
    if adj:
        out["review_pending"] = bool(adj.get("review_pending"))
        if adj.get("reviewed_warns"):
            out["reviewed_warns"] = adj["reviewed_warns"]
            out["review_receipt_sha"] = adj.get("review_receipt_sha")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--intake", required=True, help="emit_members.py 生成的本波 intake")
    ap.add_argument("--stages", default="build,validate,probe,export")
    ap.add_argument("--package", action="store_true", help="本波结束后串行包装")
    ap.add_argument("--workers", type=int, default=3, help="生产并发（按内存预算取）")
    ap.add_argument("--mem-budget-mb", type=int, default=6144)
    ap.add_argument("--per-part-mb", type=int, default=600,
                    help="准入用的单件内存预留（实测峰值 430–530MB），不是引擎 cap")
    ap.add_argument("--stage-mem-mb", type=int, default=2048,
                    help="传给引擎的每进程 cap（CADKIT_MEM_MB，默认与出厂一致 2048）")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--review-batch", default=None, help="同时复检的旧批次")
    ap.add_argument("--review-root", default=None, help="旧批次所在 cadRoot（默认同生产 root）")
    ap.add_argument("--review-every", type=int, default=6, help="每完成 N 件插一次旧件复检")
    ap.add_argument("--review-interval-secs", type=int, default=900,
                    help="心跳复检：即使一件都没完成，也要隔这段时间审一次旧件")
    ap.add_argument("--review-sample", type=int, default=20)
    ap.add_argument("--machine", default="A")
    ap.add_argument("--log", default=None)
    ap.add_argument("--freecad-bin", default=None,
                    help="freecadcmd 路径（默认 FREECAD_BIN → PATH → /Applications/FreeCAD.app/…）")
    ap.add_argument("--expect-rules", default=None,
                    help="签发的 rules_sha（默认读 shared/versions.json），每个阶段自查一次")
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 件（试点/分批用；续跑语义不变）")
    ap.add_argument("--strict", action="store_true",
                    help="gates2 ⑤：strict 验收是**默认**。此旗标仅为兼容既有命令行保留"
                         "（与默认一致）；降级只用 --lenient")
    ap.add_argument("--require-review", action="store_true",
                    help="gates2 ⑤：require_review 是**默认**。此旗标仅为兼容保留"
                         "（与默认一致）；降级只用 --lenient")
    ap.add_argument("--lenient", action="store_true",
                    help="gates2 ⑤：唯一显式降级开关——关 strict/require_review 回旧"
                         "弱语义，并落审计事件（--audit-log）；与 --strict/"
                         "--require-review 同时给出直接报错")
    ap.add_argument("--audit-log", default=None,
                    help="审计事件 JSONL（默认 <root>/audit/fleet_acceptance.jsonl）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    # gates2 ⑤：默认路径 = strict+require_review（舰队打包/验收不再弱接受）。
    # --lenient 必须**显式声明**并落审计事件；与显式 --strict/--require-review 冲突。
    if a.lenient and (a.strict or a.require_review):
        raise SystemExit("--lenient 与 --strict/--require-review 冲突："
                         "降级只能单独显式声明")
    a.strict = not a.lenient
    a.require_review = not a.lenient
    if a.require_review:
        a.strict = True   # 人审强制隐含 strict

    if a.expect_rules is None:
        vj = os.path.join(REPO, "shared", "versions.json")
        if os.path.exists(vj):
            a.expect_rules = (json.load(open(vj, encoding="utf-8")) or {}).get("rules_sha")
    stages = [x.strip() for x in a.stages.split(",") if x.strip()]
    if a.package:
        need_chain = {"build", "validate", "probe", "export"}
        if not need_chain <= set(stages):
            raise SystemExit("--package 要求 --stages 覆盖 %s（当前 %s）：跳过阶段直接包装，"
                             "等于让调度器替人绕过工单 §3 的顺序约束"
                             % ("/".join(sorted(need_chain)), ",".join(stages)))
    entry, engine = find_entry()
    freecad = find_freecad(a.freecad_bin)
    if a.expect_rules:
        got = rules_sha_of(engine)
        if got and got != a.expect_rules:
            print("BLOCK 引擎版本握手不一致：%s 算得 rules_sha=%s，签发值=%s"
                  % (engine, got, a.expect_rules))
            print("      整波停下：用错引擎生产 = 这一波全部返工。"
                  "要么换回签发的那个包（releases/*.tgz），要么请 A 机重签 shared/versions.json。")
            return 2
        print("握手自检：%s ✓（与签发值一致）" % (got or "引擎未报告 rules_sha"))
    root = os.path.abspath(os.path.expanduser(a.root))
    allowed = max(1, min(a.workers, a.mem_budget_mb // a.per_part_mb))
    audit_log = a.audit_log or os.path.join(root, "audit", "fleet_acceptance.jsonl")
    profile = {"strict": a.strict, "require_review": a.require_review}
    # gates2 ⑤：验收 profile 是**审计事件**（lenient 每次运行都留痕）。
    audit_event(audit_log, {"event": "acceptance_profile", "batch": a.batch,
                            "intake": a.intake, "stages": stages,
                            "machine": a.machine, "lenient": bool(a.lenient),
                            "strict": a.strict, "require_review": a.require_review})
    rec = Recorder(root, engine, a.expect_rules or rules_sha_of(engine), profile)
    todo = pending(rec, a.intake, stages, a.limit)
    print("验收 profile：%s%s（gates2 默认 strict+require_review；降级只认显式 "
          "--lenient，审计事件写 %s）"
          % ("LENIENT（弱接受，已留痕）" if a.lenient else "strict",
             "" if a.lenient else "+require_review", audit_log))
    print("引擎 %s（%s）\n本波待跑 %d 件（阶段 %s）；生产并发 %d（内存预算 %dMB / 单件 %dMB），package %s"
          % (engine, os.path.basename(freecad), len(todo), ",".join(stages), allowed,
             a.mem_budget_mb, a.per_part_mb,
             "串行单槽" if a.package else "本次不做"))
    if a.dry_run:
        for pid, t in todo[:5]:
            print("  %s: %s" % (pid, ",".join(t)))
        print("dry-run，未执行")
        return 0

    q = queue.Queue()
    for pid, t in todo:
        q.put((pid, t))
    pkg_q = queue.Queue()
    log = open(a.log or os.path.join(REPO, "status", "runner_%s_%s.jsonl"
                                     % (a.batch, time.strftime("%Y%m%d_%H%M"))), "a",
               encoding="utf-8")
    lock = threading.Lock()
    stop = threading.Event()
    stats = {"ok": 0, "fail": 0, "reviews": 0, "packaged": 0, "done_parts": 0,
             "lenient": bool(a.lenient)}      # A4 修订：输出结构里的降级标记

    def emit(rec):
        with lock:
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()

    def worker(n):
        while not stop.is_set():
            try:
                pid, t = q.get_nowait()
            except queue.Empty:
                return
            for stage in t:
                r = run_stage(freecad, entry, root, a.batch, pid, stage,
                              a.stage_mem_mb, a.timeout, a.expect_rules,
                              strict=a.strict, require_review=a.require_review,
                              engine_py=engine, audit_log=audit_log)
                r["lane"] = "produce"
                r["worker"] = n
                emit(r)
                with lock:
                    rec.record(pid, stage, r["ok"],
                               (r.get("error") or "")[:40] or None)
                    stats["ok" if r["ok"] else "fail"] += 1
                    print("%-7s [%d] %-18s %-8s %s%s" % ("ok" if r["ok"] else "FAIL", n, pid,
                          stage, "%5.1fs" % r["seconds"], "  " + str(r["error"])[:90] if not r["ok"]
                          else ("  degraded" if r["degraded"] else "")), flush=True)
                if not r["ok"]:
                    break                        # 任一 FAIL 不再往下走（工单 §3）
            else:
                with lock:
                    stats["done_parts"] += 1
                    if a.package:
                        pkg_q.put(pid)          # 单一消费者 ⇒ 包装天然串行
                    if a.review_batch and stats["done_parts"] % a.review_every == 0:
                        review_q.put_nowait(stats["done_parts"])   # 与 package 无关
            q.task_done()

    review_q = queue.Queue()
    review_thread = None
    if a.review_batch:
        def do_reviews():
            last = time.time()
            beat = max(1, a.review_interval_secs)
            while not stop.is_set():
                n = None
                try:
                    n = review_q.get(timeout=1)
                except queue.Empty:
                    if time.time() - last < beat:
                        continue
                    n = "heartbeat"          # 一件都没完成也要按时间复检旧件
                last = time.time()
                p = subprocess.run([sys.executable, os.path.join(HERE, "review_scan.py"),
                                    "--root", os.path.expanduser(a.review_root or a.root),
                                    "--batch", a.review_batch, "--machine", a.machine,
                                    "--sample", str(a.review_sample), "--to-lessons"],
                                   capture_output=True, text=True)
                with lock:
                    stats["reviews"] += 1
                rec = {"lane": "review", "after_parts": n, "rc": p.returncode,
                     "root": os.path.expanduser(a.review_root or a.root),
                       "tail": (p.stdout or "").strip().splitlines()[-1:], "at": time.strftime("%H:%M:%S")}
                emit(rec)
                print("  旧件复检(%s): %s" % (a.review_batch, " ".join(rec["tail"])), flush=True)
        review_thread = threading.Thread(target=do_reviews, daemon=True)
        review_thread.start()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(allowed)]
    for t in threads:
        t.start()
    pkg_thread = None
    if a.package:
        def do_package():
            while not stop.is_set():
                try:
                    pid = pkg_q.get(timeout=2)
                except queue.Empty:
                    if all(not t.is_alive() for t in threads) and pkg_q.empty():
                        return
                    continue
                try:
                    r = run_stage(freecad, entry, root, a.batch, pid, "package",
                                  a.stage_mem_mb, a.timeout, a.expect_rules,
                                  strict=a.strict, require_review=a.require_review,
                                  engine_py=engine, audit_log=audit_log)
                finally:
                    pkg_q.task_done()      # 没有这句，pkg_q.join() 永远返回不了（实测挂死）
                r["lane"] = "package"
                emit(r)
                with lock:
                    rec.record(pid, "package", r["ok"],
                               (r.get("error") or "")[:40] or None)
                    stats["packaged" if r["ok"] else "fail"] += 1
                    print("PACKAGE %-18s %s%s" % (pid, "ok" if r["ok"] else "FAIL",
                          "  " + str(r["error"])[:90] if not r["ok"] else ""), flush=True)
        pkg_thread = threading.Thread(target=do_package, daemon=True)
        pkg_thread.start()

    for t in threads:
        t.join()
    # 生产线程结束后必须把包装队列排空再停：半途 stop 会静默丢掉交付，
    # 在 20 万件规模上就是"台账说做了 2 万件、盘上只有 1.9 万件"。
    if pkg_thread:
        pkg_q.join()
    stop.set()
    for t in (review_thread, pkg_thread):
        if t:
            t.join(timeout=120)
    # A4 修订（整合项）：lenient 运行在**输出结构**里显式标记；正式接受计数与
    # 发布导出链必须按 acceptance_profile.lenient / 记录里的 lenient 字段排除。
    print("完成：件 %d（含 %d 次旧件复检），阶段 ok=%d fail=%d，package=%d；日志 %s"
          % (stats["done_parts"], stats["reviews"], stats["ok"], stats["fail"],
             stats["packaged"], os.path.relpath(log.name, REPO)))
    print("acceptance_profile: lenient=%s strict=%s require_review=%s "
          "（lenient 记录不得计入正式接受/发布导出）%s"
          % (bool(a.lenient), a.strict, a.require_review, audit_log))
    return 1 if stats["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
