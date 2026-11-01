#!/usr/bin/env python3
"""gates3（A4 修订）验收契约的**唯一实现**。

签发侧 fleet_runner.py 与收货侧 merge_check.py 都 import 本模块：A4 P1-2 的
原缺陷正是收货侧自写了一套"只看 project_id + 非空 L2.render_sha256"的缩水
判定。契约只有一份，任何一侧不得另写。

导出：
  RENDER_VIEWS / RENDER_NAMES          交付 8 视图权威名单
  HUMAN_CLOSABLE_WARN_IDS              可由人审回执闭环的 WARN 合同白名单
  CONTRACT_VERSION                     验收契约版本（回执须声明并相符）
  hard_prerequisite()                  硬拒先决条件（任何回执不可翻转）
  adjudicate_receipt()                 回执完整契约裁决（签发/收货同一入口）
  dep_identity() / dep_input_sha()     依赖指纹（收货侧可重算，A4 P1-3）
"""
import hashlib
import json

TOOL_VERSION = "acceptance_contract-gates3-a4fix-20261101"

RENDER_VIEWS = ("front", "back", "left", "right", "top", "bottom",
                "iso_up", "iso_down")
RENDER_NAMES = tuple("view_%s.png" % v for v in RENDER_VIEWS)

#: 验收契约版本；回执声明的 contract_version 必须与本次一致。
CONTRACT_VERSION = "cadkit-review-20261101-gates3"

#: 可由人审回执闭环的 WARN **合同白名单**（A4 P1-1）。
#: 引擎 validate.py:552 的 real_object 是设计好的人工终审位（恒 WARN）。
#: 白名单之外的任何 WARN（未知的、将来新增的）一律不得人审放行 —— fail closed。
HUMAN_CLOSABLE_WARN_IDS = ("real_object",)

#: checks 里允许直接通过的状态。
ALLOW_STATUSES = ("PASS", "SKIP", "N/A")

#: 依赖指纹契约版本（改公式即改版本 ⇒ 历史包显式失效，不静默过）。
DEP_CONTRACT = "dep1"


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def sha_str(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def manifest_digest(renders):
    """包内无 export_manifest.json 时的 manifest 锚点：渲染 digest 的规范化 sha。"""
    return sha_str(canon({"renders": renders or {}}))


# ---------------------------------------------------------------------------
# A4 P1-1：硬拒先决条件（握手 / 协议 / 机器判定 / 非白名单 WARN）
# ---------------------------------------------------------------------------

def hard_prerequisite(payload, stage, expect_rules=None, strict=True):
    """返回 (hard, reason)。

    hard=True 表示**先决条件**不成立：无 CADKIT JSON（协议违规）、版本握手缺/
    不一致、ok=false、report/probe/gate.passed=false、passed=false、checks 里
    任何非 WARN 的未允许状态（机器 FAIL/空/未知）、以及不在合同白名单里的
    WARN。这些一律**不可**由人审回执翻转；只有"纯白名单 WARN"才允许进入回执
    裁决闭环。
    """
    if not isinstance(payload, dict) or "ok" not in payload:
        if stage == "dedupe" and isinstance(payload, dict) and (
                "exact" in payload or "similar_series" in payload):
            hits = list(payload.get("possible_exact") or [])
            sc = set(payload.get("scaled") or [])
            hits += [m for m in (payload.get("similar_series") or []) if m in sc]
            if hits:
                return True, "dedupe 命中 exact/scaled_same: %s" % ",".join(hits[:5])
            return False, ""
        return True, "协议违规：无 CADKIT JSON 输出（回执无从裁决）"
    got = ((payload.get("engine") or {}).get("rules_sha"))
    if strict and expect_rules and not got:
        return True, "版本握手缺 rules_sha（引擎版本不可校验）"
    if expect_rules and got and got != expect_rules:
        return True, "版本握手不一致：本阶段=%s 签发值=%s" % (got, expect_rules)
    if not payload.get("ok"):
        for k in ("error", "reason", "failures", "failed", "blocking"):
            v = payload.get(k)
            if v:
                return True, "ok=false（引擎未放行：%s=%s）" % (
                    k, json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (list, dict)) else str(v))[:160]
        return True, "ok=false（引擎未给出原因）"
    for key in ("report", "probe", "gate"):
        sub = payload.get(key)
        if isinstance(sub, dict) and sub.get("passed") is False:
            return True, "%s.passed=false（机器判定不通过）" % key
    if payload.get("passed") is False:
        return True, "%s.passed=false（机器判定不通过）" % stage
    for c in (payload.get("checks") or []):
        if not isinstance(c, dict):
            return True, "协议违规：checks 项不是对象"
        st = str(c.get("status", "")).upper()
        if st in ALLOW_STATUSES:
            continue
        if st == "WARN":
            if not strict:
                continue
            if str(c.get("id")) not in HUMAN_CLOSABLE_WARN_IDS:
                return True, ("WARN %s 不在人审合同白名单（%s）——不得人审放行"
                              % (c.get("id"), ",".join(HUMAN_CLOSABLE_WARN_IDS)))
            continue
        return True, "checks %s=%s（机器 FAIL/空/未知状态，非人审可闭环项）" % (
            c.get("id"), st or "<空>")
    return False, ""


# ---------------------------------------------------------------------------
# A4 P1-2：回执完整契约（签发侧 + 收货侧同一入口，复用引擎 adjudicate_review）
# ---------------------------------------------------------------------------

def adjudicate_receipt(receipt, adjudicate, pid, native_sha, renders,
                       family_sha=None, contract=None, manifest_sha=None,
                       rev=None, contract_version=None, require=(),
                       warn_ids=None, require_review=True):
    """回执完整契约裁决（唯一实现）。

    必填绑定（任一不符即 invalid，绝不"缺就跳过"）：project_id、
    native_sha256(==当前交付 rev 的 native 字节)、family、review_of_record、
    render_manifest_sha256、L1/L2 裁决语义（由引擎 cadkit.version.
    adjudicate_review 同一函数给出）、L2 覆盖**全部 8 张**渲染且逐张与包内字节
    相符、WARN 合同白名单。
    require 里的项（收货侧传 ("rev","contract_version")）强制回执必须声明。

    family_sha=None 表示收货侧拿不到族谱模块（生产侧一定给）：此时改为
    "回执内部一致 + 非空"的显式绑定，绝不静默放宽（A4 修订记录已注明边界）。
    返回 {"ok": bool, "reasons": [...], "rinfo": {...}}。
    """
    reasons = []
    if not isinstance(receipt, dict) or not receipt:
        return {"ok": False, "rinfo": {},
                "reasons": ["无回执工件（review/receipts.json 缺失或为空）"]}
    if receipt.get("project_id") != pid:
        reasons.append("回执 project_id=%s 不是本件 %s（甲回执不掩乙件）"
                       % (receipt.get("project_id"), pid))
    if not native_sha:
        reasons.append("当前包无 native 字节可算 sha，回执无从绑定 rev")
    elif receipt.get("native_sha256") != native_sha:
        reasons.append("回执 native_sha256 不绑定当前 rev 的 native 字节"
                       "（同 pid 新 Native/STEP 配旧回执）")
    if not str(receipt.get("review_of_record") or "").strip():
        reasons.append("回执缺 review_of_record（无审核记录出处）")
    l1 = receipt.get("L1") or {}
    l2 = receipt.get("L2") or {}
    fam_top = receipt.get("family_sha")
    if family_sha:
        if l1.get("family_sha") != family_sha or l2.get("family_sha") != family_sha:
            reasons.append("回执 L1/L2 family 绑定与当前族谱模块 sha 不符")
        if fam_top is not None and fam_top != family_sha:
            reasons.append("回执顶层 family_sha 与当前族谱模块 sha 不符")
    else:
        # 收货侧没有族谱模块锚点：改为"非空 + 内部一致"。L1 整个缺失时不在这里报
        # （否则把更精确的"L1 审核决定缺失"顶掉），交给下面引擎段的 present 检查；
        # L1 存在但 family 不一致，仍然在这里拒。
        if not fam_top or l2.get("family_sha") != fam_top \
                or (l1 and l1.get("family_sha") != fam_top):
            reasons.append("回执 family_sha 缺失或 L1/L2 不一致"
                           "（收货侧无族谱锚点时必须内部一致且非空）")
    if not manifest_sha:
        reasons.append("无 manifest 锚点（export_manifest.json 或渲染规范化 digest）")
    elif receipt.get("render_manifest_sha256") != manifest_sha:
        reasons.append("回执 render_manifest_sha256 与当前 manifest 不符")
    names = set((renders or {}).keys())
    if names != set(RENDER_NAMES):
        reasons.append("包内渲染不是标准 8 视图（%d 张）——L2 无法逐张核对"
                       % len(names))
    rd = l2.get("render_sha256") or {}
    if set(rd) != set(RENDER_NAMES):
        reasons.append("回执 L2.render_sha256 未覆盖全部 8 张渲染（实际 %d 张）"
                       % len(rd))
    elif rd != (renders or {}):
        reasons.append("回执 L2 渲染 digest 与包内字节逐张不符（人审后被换图）")
    want_cv = contract_version or CONTRACT_VERSION
    if "contract_version" in require or "contract_version" in receipt:
        if receipt.get("contract_version") != want_cv:
            reasons.append("回执 contract_version=%s 与本次验收契约 %s 不符"
                           % (receipt.get("contract_version"), want_cv))
    if "rev" in require or receipt.get("rev") is not None:
        if not rev or receipt.get("rev") != rev:
            reasons.append("回执未绑定交付 revision（回执 rev=%s vs 交付 rev=%s）"
                           % (receipt.get("rev"), rev))
    if warn_ids:
        badw = sorted({str(w) for w in warn_ids
                       if str(w) not in HUMAN_CLOSABLE_WARN_IDS})
        if badw:
            reasons.append("WARN %s 不在人审合同白名单（%s）——不得人审放行"
                           % (",".join(badw), ",".join(HUMAN_CLOSABLE_WARN_IDS)))
    rinfo = {}
    if callable(adjudicate):
        eng_family = family_sha or fam_top
        rinfo = adjudicate(receipt, pid,
                           contract or receipt.get("contract_sha256"),
                           native_sha, renders or {}, eng_family,
                           bool(require_review), manifest_sha=manifest_sha) or {}
        i1 = rinfo.get("L1") or {}
        i2 = rinfo.get("L2") or {}
        if not i1.get("present"):
            reasons.append("L1 审核决定缺失（设计层人审未留痕）")
        elif not i1.get("valid"):
            reasons.append("L1 裁决不通过（判定非 PASS 或绑定不符）")
        if not i2.get("present"):
            reasons.append("L2 审核决定缺失")
        elif not i2.get("valid"):
            reasons.append("L2 裁决不通过（渲染/native 绑定或判定不符）")
    return {"ok": not reasons, "reasons": reasons, "rinfo": rinfo}


# ---------------------------------------------------------------------------
# A4 P1-3：依赖指纹（收货侧按实际 schema 重算）
# ---------------------------------------------------------------------------

def dep_identity(native_sha, step_sha, rules_sha, checker, profile_sha,
                 renders_sha=None, receipt_sha=None):
    """当前**实际**依赖值。None 记 "<none>" 而不是省略：否则"缺字段"会伪装成
    "相符"（A4 原缺陷之一）。"""
    return {"native": native_sha or "<none>", "step": step_sha or "<none>",
            "rules": rules_sha or "<none>", "checker": checker or "<none>",
            "profile": profile_sha or "<none>",
            "renders": renders_sha or "<none>",
            "receipt": receipt_sha or "<none>"}


def dep_input_sha(stage, pid, rev, identity):
    """(pid, rev, stage) 在当前实际依赖值下的依赖指纹。

    生产侧写点：fleet_runner.Recorder.dep_sha()（台账导出随 job 行落 dep_sha）；
    收货侧 merge_check 用包内字节 + versions.json + 回执工件重算同一函数。
    同 rev 的旧依赖（旧 Native/STEP、错 checker、换 profile、重写回执）都会
    显式不符 ⇒ 拒收。
    """
    return sha_str(canon({"v": DEP_CONTRACT, "stage": stage, "pid": pid,
                          "rev": rev, "dep": identity}))[:20]
