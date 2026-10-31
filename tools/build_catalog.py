#!/usr/bin/env python3
"""500 families × 400 members = 200,000 parts (+11 already delivered).

Design of the list (this is the part that has to be defensible, not the count):

  * A *family* is one object concept (端盖 / 电磁阀体 / 焊接工装板 …). 500 of them,
    100 per machine, spread over 10 engineering domains.
  * A *member* is one point in that family's design space: 5 structural axes
    (features that exist or not, counts that change), never a uniform scale of
    another member. Size is a secondary attribute (real spec ladders,
    non-proportional: wall/bolt/seal follow standards).
  * The 200,000 part ids are NOT committed as rows: they are recomputed
    deterministically from (family, member index). Committed: the family table,
    per-family member counts + a sha over each family's id block, and the wave
    layout. `tools/emit_members.py` materialises any slice into an intake file.

Selection rule (spec 2.5 excludes vendor-catalogue / standard-part / tutorial
designs): only objects whose geometry we design.
"""
import csv
import hashlib
import json
import os
import sys
from itertools import product

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import families_500 as F                                          # noqa: E402

MACHINES = ["A", "B", "C", "D", "E"]
BATCH = {m: "fleet_%s_2026_11" % m.lower() for m in MACHINES}
PER_FAMILY = 400                     # 500 × 400 = 200,000
FAMILIES_PER_WAVE = 5                # 5 families × 400 = 2,000 parts per wave

# Structural axes. Product per archetype >= 400 so 400 members are 400 distinct
# feature signatures (checked by tools/catalog_lint.py, not assumed).
ARCHETYPES = {
    "壳体": [["卡扣 2", "卡扣 4", "螺柱 4", "螺柱 6 + 卡扣 2", "铰链翻盖"],
             ["光滑壁", "纵筋 4", "井字筋 3×3", "环筋 3", "蜂窝凹坑阵"],
             ["无散热", "散热齿 8", "散热齿 16", "百叶 6", "穿孔阵"],
             ["单进线孔", "双进线孔", "三孔 + 航空插座座", "无孔 + 护套槽"],
             ["无附件", "接地螺柱", "铭牌凹台", "铅封位"]],
    "盒盖": [["平盖", "沉台盖", "透明窗盖", "带把手盖"],
             ["周圈密封槽", "局部垫台", "卡箍槽", "无密封"],
             ["螺钉 4", "螺钉 6", "螺钉 8", "卡扣 6", "螺栓 2 + 铰链 2"],
             ["无附件", "接地螺柱", "铭牌 + 铅封", "观察窗", "排气阀孔"],
             ["无通风", "透气孔阵", "防水透气膜座", "百叶"]],
    "支架": [["无筋", "三角筋 2", "三角筋 4", "井字筋", "环形筋"],
             ["圆孔 4", "长孔 2 + 圆孔 2", "T 槽 4", "腰形孔阵", "螺栓圆阵 6"],
             ["无定位", "定位销 2", "定位销 4", "销 2 + 防转平面"],
             ["固定式", "调节垫块", "长孔调节", "丝杠调节"],
             ["无减重", "减重槽 2", "减重孔阵", "掏空 + 筋"]],
    "阀泵体": [["两通", "三通", "四通", "五通 + 回流道", "多路集成"],
               ["螺纹接口", "法兰接口", "卡箍接口", "快插接口", "焊接接口"],
               ["无执行界面", "线圈定位环", "指示窗 + 手动应急腔", "手轮方孔 + 行程限位"],
               ["直道", "弯流道", "多孔分流", "带节流孔板"],
               ["无附件", "泄放孔", "测压口", "排气堵座"]],
    "检具": [["单测头", "双测头", "四测头", "六测头 + 分流气道", "随动测头"],
             ["V 块定位", "销阵定位", "仿形定位", "夹持定位"],
             ["无防护", "翻盖防护", "全封闭 + 观察窗", "局部护罩"],
             ["手动读数", "表架 1", "表架 2", "气电转换座"],
             ["无气路", "单路气路", "双路 + 快换", "三路 + 压力表座"]],
    "工装": [["螺旋压紧", "偏心快压", "气动夹紧", "液压夹紧", "磁吸"],
             ["排屑槽直落", "排屑槽贯通", "无排屑（干式）", "吹屑孔阵"],
             ["手动", "气控", "液控", "机械随动"],
             ["固定式", "快换销 2", "快换销 4 + 定位块", "底板模块化"],
             ["单一基准", "双基准", "三基准 + 防转", "可调基准"]],
    "面板": [["单大孔", "双孔", "四孔", "栅格 4×4", "圆孔阵 12"],
             ["无标识", "标识凹台", "铭牌 + 铅封位", "背光窗口"],
             ["卡扣 4", "螺钉 6", "螺钉 8", "导轨卡装", "嵌入式", "螺栓 2 + 挂耳 2"],
             ["无密封", "周圈密封槽", "局部垫台", "双面胶带槽"]],
    "罩壳": [["全封闭", "半封闭", "开窗", "格栅", "网孔"],
             ["光滑壁", "环形加强筋", "纵筋", "蜂窝凹坑", "翻边加强"],
             ["螺钉 4", "卡扣 6", "快拆手柄 2", "铰链 + 气撑 1", "挂钩 2 + 螺钉 2"],
             ["无附件", "风道接口", "观察窗", "线缆出口", "泄压孔"]],
}

# 中文名 → archetype, first match wins. The archetype only picks which axis set
# the family is planned against; the shipped part's design.md stays authoritative.
ARCH_RULES = [
    ("检具", ["检具", "规", "样板", "治具", "标定", "校准", "测头", "试验"]),
    ("工装", ["夹具", "工装", "模", "型架", "心轴", "压板", "反力", "变位机", "吸盘"]),
    ("阀泵体", ["阀", "泵", "缸", "蓄能", "接头", "喷嘴", "喷枪", "雾化", "流量计",
                "水室", "蜗壳", "歧管", "冷板", "集块"]),
    ("面板", ["面板", "格栅", "网框", "按键", "键帽", "键盘"]),
    ("罩壳", ["罩", "护罩", "风道", "导风", "遮光", "护套", "外壳"]),
    ("盒盖", ["盖", "盒", "仓", "盘"]),
    ("支架", ["座", "架", "板", "块", "臂", "角件", "铰链", "滑座", "托", "夹", "销", "柱", "脚", "法兰", "框"]),
]
DEFAULT_ARCH = "壳体"

DELIVERED = [("iec_endcap", "pilot_ec%s" % s, "电机端盖 IEC%s 止口端" % s,
              "已交付 pilot_2026_10" + ("（买方判为计入 ec80b，不计数）" if s == "132" else ""))
             for s in ("63", "71", "80b", "90", "90s", "100", "112", "132", "132x", "160")] + [
    ("motor_end_cap", "motor_end_cap_80", "电机端盖参考件 80", "参考交付（待按新族谱规则重建）")]


def arch_of(zh):
    for arch, keys in ARCH_RULES:
        if any(k in zh for k in keys):
            return arch
    return DEFAULT_ARCH


def combos(arch):
    axes = ARCHETYPES[arch]
    space = list(product(*[range(len(a)) for a in axes]))
    space.sort(key=lambda c: (sum(c), c))       # deterministic, spread by depth
    return [tuple(axes[i][c[i]] for i in range(len(axes))) for c in space]


def member_ids(machine, fam):
    return ["%s_%s_%03d" % (machine.lower(), fam, i + 1) for i in range(PER_FAMILY)]


def members_of(fam):
    """Deterministic member expansion: (pid, size, signature, grade)."""
    cs = combos(fam["arch"])
    if len(cs) < PER_FAMILY:
        raise SystemExit("族谱 %s 的设计轴组合只有 %d 种 < %d，先扩 axes"
                         % (fam["key"], len(cs), PER_FAMILY))
    ladder = F.LADDERS[fam["ladder"]]
    out = []
    for i in range(PER_FAMILY):
        sig = cs[i]
        out.append({"pid": "%s_%s_%03d" % (fam["machine"].lower(), fam["key"], i + 1),
                    "family": fam["key"], "object": "%s %s" % (fam["zh"], ladder[i % len(ladder)]),
                    "size": ladder[i % len(ladder)], "sig": "；".join(sig),
                    "grade": "S" if i % 5 in (1, 3) else "A",
                    "category": fam["category"], "machine": fam["machine"],
                    "wave": fam["wave"]})
    return out


def load():
    fs = F.families()
    per_machine = {m: 0 for m in MACHINES}
    for f in fs:
        f["arch"] = arch_of(f["zh"])
        f["wave"] = per_machine[f["machine"]] // FAMILIES_PER_WAVE + 1
        per_machine[f["machine"]] += 1
        f["members"] = PER_FAMILY
        f["axis_space"] = len(combos(f["arch"]))
        f["id_prefix"] = "%s_%s_" % (f["machine"].lower(), f["key"])
    return fs


def sha_members(fs):
    ids = member_ids(fs["machine"], fs["key"])
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def write_family_files(fs):
    d = os.path.join(ROOT, "catalog")
    os.makedirs(d, exist_ok=True)
    total = sum(f["members"] for f in fs)
    doc = {"catalog": "cad-fleet 族谱总清单（500 族谱 × %d 件 = %d 件）" % (PER_FAMILY, total),
           "spec": "CAD 数据采购需求说明 v1.0",
           "grading": "S>=200, A=100-199, B=50-99, C<50 有效建模步骤（规格 1）",
           "selection_rule": "只做几何由我方设计决定的物件；厂商样本件/标准件库件/教程范例不进清单（规格 2.5）",
           "member_rule": "同族每成员在 5 条结构轴上给出不同组合，禁止全模型等比缩放；尺寸档只作次要属性",
           "wave_rule": "每台每波 %d 个族谱（%d 件）；上一波全部 ACCEPT 且指纹并入底表后才准开下一波"
                        % (FAMILIES_PER_WAVE, FAMILIES_PER_WAVE * PER_FAMILY),
           "enumeration": "件号由 (machine, family, 序号) 确定性重算，不入库；展开用 tools/emit_members.py",
           "total_parts": total,
           "delivered_parts": len(DELIVERED),
           "machines": {m: {"batch": BATCH[m],
                            "families": sum(1 for f in fs if f["machine"] == m),
                            "parts": sum(f["members"] for f in fs if f["machine"] == m),
                            "waves": sorted({f["wave"] for f in fs if f["machine"] == m})}
                        for m in MACHINES},
           "archetype_space": {a: len(combos(a)) for a in ARCHETYPES},
           "families": fs}
    open(os.path.join(d, "families.json"), "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n")

    with open(os.path.join(d, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["family", "machine", "domain", "object", "category", "archetype",
                    "ladder", "members", "axis_space", "id_prefix", "wave", "ids_sha256_16"])
        for f in fs:
            w.writerow([f["key"], f["machine"], f["domain_name"], f["zh"], f["category"],
                        f["arch"], f["ladder"], f["members"], f["axis_space"],
                        f["id_prefix"], f["wave"], sha_members(f)])

    L = ["# 族谱总清单 — 500 个族谱 × %d 件 = %d 件（+已交付 %d 件）" % (PER_FAMILY, total, len(DELIVERED)), "",
         "由 `tools/build_catalog.py` 生成，不要手改。件号**不逐条入库**：`{机器}_{族谱}_{三位序号}`，",
         "任意切片用 `python3 tools/emit_members.py --machine A --wave 1` 展开成 intake（确定性重算）。",
         "",
         "**每个成员在同一族内于 5 条结构轴上取不同组合**（结构轴组合数见 `axis_space` 列，均 ≥ 400），",
         "尺寸档只是次要属性；**禁止全模型等比缩放**——2.5 判 `scaled_same` 不计数。",
         "筛子：只做几何由我方设计决定的物件（轴承座、联轴器、气缸、导轨滑块、紧固件等厂商样本件不进清单）。",
         "", "| 机器 | 批次 | 族谱 | 件数 | 波次 | 每波件数 |", "|---|---|---|---|---|---|"]
    for m in MACHINES:
        mf = [f for f in fs if f["machine"] == m]
        L.append("| **%s** | `%s` | %d | %d | 1–%d | %d |"
                 % (m, BATCH[m], len(mf), sum(f["members"] for f in mf), max(f["wave"] for f in mf),
                    FAMILIES_PER_WAVE * PER_FAMILY))
    L.append("| **合计** | 5 个批次 | **%d** | **%d** | 1–%d | %d |"
             % (len(fs), total, max(f["wave"] for f in fs), FAMILIES_PER_WAVE * PER_FAMILY))
    for m in MACHINES:
        L += ["", "## 机器 %s — `%s`" % (m, BATCH[m]), ""]
        done = [p for (fam, p, o, note) in DELIVERED if m == "A"]
        if done:
            L += ["已交付登记（几何已被占用，不得再做同拓扑件）：" + ", ".join("`%s`" % p for p in done), ""]
        by_dom = {}
        for f in fs:
            if f["machine"] == m:
                by_dom.setdefault(f["domain_name"], []).append(f)
        for dom, fl in by_dom.items():
            L += ["### %s（%d 族谱 / %d 件）" % (dom, len(fl), sum(x["members"] for x in fl)), "",
                  "| 族谱 | 物件 | 类目 | 设计轴族 | 件数 | 波次 | project id 前缀 |",
                  "|---|---|---|---|---|---|---|"]
            for f in fl:
                L.append("| `%s` | %s | %s | %s | %d | w%d | `%s` |"
                         % (f["key"], f["zh"], f["category"], f["arch"], f["members"],
                            f["wave"], f["id_prefix"]))
    open(os.path.join(d, "FAMILIES.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump({"delivered": [{"family": a, "pid": b, "object": c, "note": d, "machine": "A"}
                             for (a, b, c, d) in DELIVERED]},
              open(os.path.join(d, "delivered.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return total


def write_intake(fs):
    d = os.path.join(ROOT, "intake")
    os.makedirs(d, exist_ok=True)
    for m in MACHINES:
        mf = [f for f in fs if f["machine"] == m]
        waves = {}
        for f in mf:
            waves.setdefault(f["wave"], []).append(f["key"])
        doc = {"batch": BATCH[m], "machine": m, "buyer": "internal",
               "spec": "CAD 数据采购需求说明 v1.0 (procurement_requirements.pdf)",
               "issued_by": "machine-A dsh-cad-agent", "created": "2026-10-31",
               "quota": {"families": len(mf), "parts": sum(f["members"] for f in mf),
                         "per_family": PER_FAMILY, "per_wave_families": FAMILIES_PER_WAVE,
                         "per_wave_parts": FAMILIES_PER_WAVE * PER_FAMILY,
                         "waves": len(waves)},
               "families": [{"family": f["key"], "object": f["zh"], "category": f["category"],
                             "archetype": f["arch"], "ladder": f["ladder"], "wave": f["wave"],
                             "members": f["members"], "id_prefix": f["id_prefix"]} for f in mf],
               "wave_layout": {str(k): v for k, v in sorted(waves.items())},
               "how_to_materialise": "python3 tools/emit_members.py --machine %s --wave N "
                                     "> intake/waves/%s_wN.json" % (m, BATCH[m]),
               "wave_rule": "一次只做一波（%d 件）；上一波全部 ACCEPT 且指纹并入底表才准开下一波"
                            % (FAMILIES_PER_WAVE * PER_FAMILY),
               "notes": "成员在 5 条结构轴上取不同组合；禁止等比缩放；关掉的特征组其参数不得留在 params 表。"}
        open(os.path.join(d, BATCH[m] + ".json"), "w", encoding="utf-8").write(
            json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def write_briefs(fs):
    d = os.path.join(ROOT, "briefs")
    os.makedirs(d, exist_ok=True)
    cp = os.path.join(ROOT, "briefs-common.txt")
    common = open(cp, encoding="utf-8").read().strip() if os.path.exists(cp) else ""
    for m in MACHINES:
        mf = [f for f in fs if f["machine"] == m]
        w1 = [f for f in mf if f["wave"] == 1]
        lines = ["任务：你是 CAD 流水线的机器 %s，批次 %s：%d 个族谱 × %d 件 = %d 件（20 波）"
                 % (m, BATCH[m], len(mf), PER_FAMILY, len(mf) * PER_FAMILY), "",
                 "第一步（强制，不许跳）：`cd cad-repo && python3 tools/recall.py --machine %s`" % m,
                 "  打印教训全文 + 项目记忆 + 版本握手 + 你的切片，并给出 LESSONS_ACK；",
                 "  回传 REPORT.md 必须带 `LESSONS_ACK: <那 8 位>`，merge_check 核对，缺或过期直接 REJECT。",
                 "第二步：`python3 tools/registry_sync.py --root ~/cad-fleet`（非 0 就停下喊人）",
                 "第三步（开工前）：`python3 tools/emit_members.py --machine %s --wave 1 > intake/waves/%s_w1.json`"
                 % (m, BATCH[m]),
                 "再读：WORKFLOW.md（流程、所有权、波次）、WORK-ORDER.md（技术口径）。",
                 "",
                 "**本轮只做 wave 1 的 %d 件（%d 个族谱）**；回传并被 A 机 ACCEPT、指纹并入底表之后才准开 wave 2。"
                 % (len(w1) * PER_FAMILY, len(w1)),
                 "wave 1 族谱与件号区间："]
        for f in w1:
            lines.append("  - `%s…001–%s…%03d`  %s（%s｜%s 轴族｜%d 件）"
                         % (f["id_prefix"], f["id_prefix"], PER_FAMILY, f["zh"],
                            f["category"], f["arch"], f["members"]))
        lines += ["", "成员展开规则：同一族谱的 400 个成员在 5 条结构轴上逐一取不同组合",
                  "（`emit_members.py` 会给每件写出 sig 与尺寸档），design.md 必须把 sig 落成具体特征数。",
                  "", "全部 20 个波次（每波开工前先 emit 该波的 intake）："]
        for w in sorted({f["wave"] for f in mf}):
            lines.append("")
            lines.append("wave %d —— `python3 tools/emit_members.py --machine %s --wave %d`"
                         % (w, m, w))
            for f in [x for x in mf if x["wave"] == w]:
                lines.append("  - `%s…001–…%03d`  %s（%s｜%s）"
                             % (f["id_prefix"], PER_FAMILY, f["zh"], f["category"], f["arch"]))
        lines += ["", "以上之外的件一律不做（别的机器在产）。通用规则：", common]
        open(os.path.join(d, "BRIEF-FOR-%s.txt" % m), "w", encoding="utf-8").write("\n".join(lines) + "\n")


def write_sample(fs):
    """Commit one materialised wave as proof the enumeration is real."""
    os.makedirs(os.path.join(ROOT, "catalog"), exist_ok=True)
    fams = [f for f in fs if f["machine"] == "A" and f["wave"] == 1]
    rows = []
    for f in fams:
        rows += members_of(f)
    p = os.path.join(ROOT, "catalog", "sample_A_wave1.csv")
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["project_id", "object", "category", "grade", "family", "size", "topo_signature"])
        for r in rows:
            w.writerow([r["pid"], r["object"], r["category"], r["grade"], r["family"],
                        r["size"], r["sig"]])
    return len(rows)


def main():
    fs = load()
    total = write_family_files(fs)
    write_intake(fs)
    write_briefs(fs)
    sample = write_sample(fs)
    sig_bad = []
    for f in fs:
        ms = members_of(f)
        if len({m["sig"] for m in ms}) != PER_FAMILY:
            sig_bad.append(f["key"])
    print("族谱 %d 个 / 计划件数 %d / 已交付 %d；每台 %d 族谱 × %d 件，20 波"
          % (len(fs), total, len(DELIVERED), len(fs) // 5, PER_FAMILY))
    print("样本展开已写 catalog/sample_A_wave1.csv（%d 行）" % sample)
    print("签名唯一性:", "OK" if not sig_bad else "失败 %s" % sig_bad)


if __name__ == "__main__":
    main()
