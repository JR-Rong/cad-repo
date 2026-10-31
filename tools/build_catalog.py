#!/usr/bin/env python3
"""Fleet catalog source → catalog/parts.json, catalog/CATALOG.md, intake/, briefs/.

Authoring table lives here; every generated view is derived from it so the five
work orders, the five intake manifests and the human-readable table can never
disagree with each other. Re-run after editing TABLE, then commit the outputs.

Selection rule (spec 2.5): only objects whose geometry is decided by OUR design,
never vendor-catalogue / standard-part objects (bearing housings, couplings,
timing pulleys, cylinders, guide rails, fasteners…). Every entry must state the
topology that distinguishes it from its own family sibling — a scaled copy is
not a part, it is a duplicate.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # handoff/
MACHINES = ["A", "B", "C", "D", "E"]
BATCH = {"A": "fleet_a_2026_11", "B": "fleet_b_2026_11", "C": "fleet_c_2026_11",
         "D": "fleet_d_2026_11", "E": "fleet_e_2026_11"}

# machine, family, pid, object(中文), category, grade, topo-difference vs sibling, status
TABLE = [
    # ---------- A：电机与驱动外壳（本机已验证的族谱能力） ----------
    ("A", "iec_endcap", "pilot_ec63", "电机端盖 IEC63 止口端", "机电产品", "A",
     "已交付（批次 pilot_2026_10）", "delivered"),
    ("A", "iec_endcap", "pilot_ec71", "电机端盖 IEC71 止口端", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec80b", "电机端盖 IEC80 短机座", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec90", "电机端盖 IEC90 止口端", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec90s", "电机端盖 IEC90 短机座", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec100", "电机端盖 IEC100 止口端", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec112", "电机端盖 IEC112 止口端", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec132", "电机端盖 IEC132 止口端", "机电产品", "A",
     "已交付（买方判为计入 ec80b，不计数）", "delivered"),
    ("A", "iec_endcap", "pilot_ec132x", "电机端盖 IEC132 圆盖型", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "iec_endcap", "pilot_ec160", "电机端盖 IEC160 止口端", "机电产品", "A",
     "已交付", "delivered"),
    ("A", "motor_end_cap", "motor_end_cap_80", "电机端盖参考件 80", "机电产品", "A",
     "参考交付（待按新族谱规则重建）", "delivered"),
    ("A", "iec_endcap_ext", "a_endcap_enc", "IEC 端盖·编码器法兰端", "机电产品", "A",
     "中心通孔+编码器止口+编码器螺柱圆阵 6+穿线孔 2；无风扇座（与 sibling 反向拓扑）", "planned"),
    ("A", "iec_endcap_ext", "a_endcap_fan", "IEC 端盖·风罩端", "机电产品", "S",
     "径向通风筋 8+风扇凸缘+轴穿透槽+风罩卡爪 4；无编码器孔", "planned"),
    ("A", "servo_rear_cover", "a_srvcover_52", "52 框伺服电机后盖", "机电产品", "A",
     "编码器舱+航空插头座 1+制动释放孔+密封沟槽+周圈螺柱 4", "planned"),
    ("A", "servo_rear_cover", "a_srvcover_80", "80 框伺服电机后盖（带传感器）", "机电产品", "S",
     "插头座 2+温度传感器孔 2+周圈筋 6+接地螺柱；螺柱 6", "planned"),
    ("A", "drive_enclosure", "a_drvenc_din", "小型驱动器外壳·DIN 导轨卡装", "机电产品", "A",
     "DIN35 卡扣+散热齿 12+端子开窗+LED 导光柱孔 4+接地螺柱", "planned"),
    ("A", "drive_enclosure", "a_drvenc_wall", "驱动器外壳·壁挂式", "机电产品", "S",
     "安装耳 4+散热齿 20+内盖板卡槽+电缆密封孔 3+铭牌凹台；无导光柱", "planned"),
    ("A", "inverter_panel", "a_invpanel_louvre", "变频器通风面板", "机电产品", "A",
     "百叶 18+过滤棉导槽+风机电窗口+铭牌凹台+周圈螺栓 12", "planned"),
    ("A", "inverter_panel", "a_invpanel_filter", "变频器快换滤网框", "机电产品", "A",
     "快换卡扣 4+滤网筋阵 24+排水槽+密封唇；无百叶", "planned"),
    ("A", "motor_mount", "a_motmount_ff", "电机立装 FF 机座", "机电产品", "S",
     "法兰止口+凸台筋 6+减重槽 4+穿线孔+地脚长孔 4", "planned"),
    ("A", "motor_mount", "a_motmount_b5", "电机 B5 法兰盘", "机电产品", "A",
     "大止口+螺柱圆阵 8+定位销 2+中心通孔+背面筋 8；无地脚长孔", "planned"),

    # ---------- B：仪器外壳与人机界面件 ----------
    ("B", "snap_enclosure", "b_snapenc120_r3", "卡扣仪器外壳·下壳", "消费品", "A",
     "筋 3+卡扣 6+PCB 铜柱 4+线缆出口 2；无数显窗", "planned"),
    ("B", "snap_enclosure", "b_snapenc120_r4w", "卡扣仪器外壳·带窗上壳", "机电产品", "A",
     "筋 4+数显透光窗+按键孔 3+卡扣 8+接地螺柱", "planned"),
    ("B", "terminal_block", "b_termblk_4p", "螺钉式接线端子 4P", "电子元器件封装", "A",
     "极腔 4+导体压板腔+导轨卡扣+防手指绝缘壁+标记牌槽；无可拆分隔板", "planned"),
    ("B", "terminal_block", "b_termblk_6p_s", "螺钉式接线端子 6P 带分隔板", "电子元器件封装", "S",
     "极腔 6+分隔板槽 5+端挡板+标记槽 2+螺钉 6", "planned"),
    ("B", "handheld_meter_case", "b_meter_hh45", "手持表壳 45 框", "消费品", "A",
     "电池仓+表笔插座 2+表头窗+胶套槽+背部磁吸板", "planned"),
    ("B", "handheld_meter_case", "b_meter_hh60", "手持表壳 60 框（翻盖支架）", "消费品", "S",
     "翻盖+支架腿槽+探头收纳腔+硅胶护套筋 6+防护圈", "planned"),
    ("B", "panel_box", "b_panelbox_hm", "膜键面板盒", "机电产品", "A",
     "膜键窗+铰链座 2+密封槽+电缆入口 3+PCB 铜柱 6", "planned"),
    ("B", "panel_box", "b_panelbox_jb", "接线盒（卡扣盖）", "机电产品", "A",
     "隔筋 2+端子导轨+敲落孔 4+接地螺柱 2；无铰链", "planned"),
    ("B", "instrument_hood", "b_hood_top", "桌面仪器罩·顶置提手", "机电产品", "S",
     "提手+散热风道 2+观察窗+底部减震柱窝 4+内隔板卡槽", "planned"),
    ("B", "instrument_hood", "b_hood_side", "仪器罩·侧开门", "机电产品", "S",
     "侧门铰链 3+气弹簧座 2+散热格栅+背面线缆切口；无提手", "planned"),

    # ---------- C：工装夹具与检具 ----------
    ("C", "turn_fixture_base", "c_turnfx_d3", "三爪车削夹具座", "工装夹具", "A",
     "短爪定位 3+中心螺钉+平衡块槽 4+液压通道 2+排屑槽", "planned"),
    ("C", "turn_fixture_base", "c_turnfx_d6", "六爪车削夹具座", "工装夹具", "S",
     "爪位 6+气密腔+快换接口+中心通孔；无液压通道", "planned"),
    ("C", "weld_facing_plate", "c_weldfp_a", "焊接工装板（销阵式）", "工装夹具", "A",
     "定位销 8+压紧器螺孔阵+让位槽+基准刻线槽+吊装孔 2", "planned"),
    ("C", "weld_facing_plate", "c_weldfp_b", "焊接工装板（V 块式）", "工装夹具", "S",
     "V 块 2+支撑钉 6+异形轮廓让位+气管孔 4；销位 5", "planned"),
    ("C", "press_fixture", "c_pressf_guide4", "四导柱压装模", "工装夹具", "A",
     "上/下模+导套 4+顶出孔+限位垫+起吊螺钉孔 2", "planned"),
    ("C", "press_fixture", "c_pressf_collar", "轴肩压套工装", "工装夹具", "A",
     "V 形定位块+可调行程挡块+弹簧顶销 2+深度量表座；无导套", "planned"),
    ("C", "air_gauge_check", "c_airgauge_vblock", "气动量仪检具（V 块）", "工装夹具", "A",
     "V 块+测头座 2+标准件收纳腔+防护罩铰座+排屑孔", "planned"),
    ("C", "air_gauge_check", "c_airgauge_multi", "多孔位多测头检具", "工装夹具", "S",
     "测头位 6+快换定位销 4+分流气道的孔阵+盖板卡槽", "planned"),
    ("C", "flip_fixture", "c_flip_180", "翻转工装 180°", "工装夹具", "S",
     "转轴+180°定位齿 12+配重槽 2+手柄+锁销", "planned"),
    ("C", "flip_fixture", "c_flip_360", "翻转工装 360° 自锁", "工装夹具", "S",
     "蜗轮自锁+刻度圈+锁紧手柄+过线滑环腔；无定位齿", "planned"),

    # ---------- D：结构件与机架 ----------
    ("D", "alu_corner_bracket", "d_acorner_40", "4040 型材角件", "结构件", "A",
     "槽匹配齿 4+沉孔 8+减重切角 2+加强筋 2", "planned"),
    ("D", "alu_corner_bracket", "d_acorner_80", "4080 T 槽角板", "结构件", "A",
     "T 槽面 2+铰链孔阵 6+长孔 4+筋 6；无沉孔", "planned"),
    ("D", "motor_bracket", "d_mbracket_l", "L 型电机支架", "结构件", "A",
     "筋 4+地脚长孔 2+减重窗 3+出线孔+定位销孔 2", "planned"),
    ("D", "motor_bracket", "d_mbracket_ff", "圆法兰立装支架", "结构件", "A",
     "止口+周圈 8+中心落料孔+防转平面+背面筋 6；无减重窗", "planned"),
    ("D", "machine_foot", "d_foot_heavy", "设备重型底脚", "结构件", "A",
     "地脚板+调节螺钉座+叉车口+减振柱窝 4+水平泡座", "planned"),
    ("D", "machine_foot", "d_foot_damp", "隔振脚", "结构件", "S",
     "弹簧腔+阻尼孔阵 9+限位柱 3+安装板+防尘罩槽；无叉车口", "planned"),
    ("D", "cantilever_arm", "d_cant_pipe", "管状悬臂支架", "结构件", "S",
     "管体穿线孔 3+端部法兰+三角加强筋 6+端盖卡口", "planned"),
    ("D", "cantilever_arm", "d_cant_tslot", "T 槽悬臂支架", "结构件", "S",
     "四面 T 槽+端连接板+气路孔 2+走线盖板槽+刚性筋 8", "planned"),
    ("D", "access_door_frame", "d_doorframe_400", "检修门框 400", "结构件", "A",
     "铰链座 3+密封槽+限位缓冲孔+排水孔 2+折边加强", "planned"),
    ("D", "access_door_frame", "d_doorpanel_lock", "带锁检修面板", "结构件", "A",
     "转舌锁孔+观察窗框+加强筋 4+敲落孔 3；无铰链座", "planned"),

    # ---------- E：流体与气路壳体 ----------
    ("E", "gear_pump_housing", "e_gpump_25", "外啮合齿轮泵壳体 25", "机械零件", "S",
     "主/侧齿轮腔+卸荷槽 2+进出油口+O 形圈槽+轴封腔+紧固螺孔 8", "planned"),
    ("E", "gear_pump_housing", "e_gpump_63", "大排量齿轮泵壳体 63", "机械零件", "S",
     "溢流阀孔道+端盖止口+测压口 2+法兰油口；卸荷槽 4", "planned"),
    ("E", "solenoid_valve_body", "e_solbody_nc", "常闭先导式电磁阀体", "机械零件", "S",
     "膜片腔+弹簧座+进/出/泄三口+线圈定位环+指示灯窗", "planned"),
    ("E", "solenoid_valve_body", "e_solbody_22", "2/2 通电磁阀体（手动应急）", "机械零件", "S",
     "手动按钮腔+滤网腔+DIN 插座座+先导孔阵 6；无指示窗", "planned"),
    ("E", "filter_housing", "e_filter_cup", "杯式过滤器壳体", "机械零件", "A",
     "滤芯腔+排污阀口+压差表座+卡箍槽+支架耳 2+密封唇", "planned"),
    ("E", "filter_housing", "e_filter_hv", "高压过滤器壳体", "机械零件", "S",
     "法兰口+滤芯端盖+安全销孔+排气螺钉座+铭牌凹台；卡箍改螺栓 6", "planned"),
    ("E", "pneumatic_manifold", "e_manifold_5", "5 位气动歧管块", "机械零件", "A",
     "串接槽式密封+排气汇流道+进气道+工作口 5+标识槽 5", "planned"),
    ("E", "pneumatic_manifold", "e_manifold_8", "8 位高压歧管块", "机械零件", "S",
     "独立排气孔 8+高压进气道+导轨卡槽+堵板位 2；工作口 8", "planned"),
    ("E", "metering_pump_head", "e_mpspd_single", "单缸隔膜计量泵液力端", "机械零件", "S",
     "隔膜腔+进/出口单向阀座+补液阀+排气孔+卡箍法兰", "planned"),
    ("E", "metering_pump_head", "e_mpspd_triple", "三缸计量泵液力端", "机械零件", "S",
     "三腔+脉动阻尼器接口+安全膜片腔+冲洗口；阀座 6", "planned"),
]

FIELDS = ["machine", "family", "pid", "object", "category", "grade", "topo", "status"]


def rows():
    return [dict(zip(FIELDS, r)) for r in TABLE]


def write_json(rs):
    d = os.path.join(ROOT, "catalog")
    os.makedirs(d, exist_ok=True)
    doc = {"catalog": "cad-fleet 零件总清单",
           "spec": "CAD 数据采购需求说明 v1.0",
           "grading": "S>=200, A=100-199, B=50-99, C<50 有效建模步骤（规格 1）",
           "selection_rule": "只做几何由我方设计决定的物件；厂商样本件/标准件库件/教程范例一律不进清单（规格 2.5）",
           "machines": {m: {"batch": BATCH[m],
                            "families": sorted({r["family"] for r in rs
                                                if r["machine"] == m and r["status"] == "planned"})}
                        for m in MACHINES},
           "parts": rs}
    open(os.path.join(d, "parts.json"), "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def write_md(rs, doc):
    lines = ["# 零件总清单（按机器分工）",
             "",
             "规格依据：1 分级报价 / 2.1 建模范围 / 2.3 交付清单 / 2.4 验收 / 2.5 判重。",
             "筛子：**只做几何由我方设计决定的物件** —— 轴承座、联轴器、同步带轮、气缸、导轨滑块、紧固件这类",
             "厂商样本件/标准件库件一律不进清单（2.5 明文不收，且五台机器一起产最容易在这里撞车）。",
             "每台机器一个前缀（`a_`…`e_`），族谱内部变体**必须换拓扑**，纯缩放会被判重复不计数。",
             ""]
    for m in MACHINES:
        mine = [r for r in rs if r["machine"] == m]
        planned = [r for r in mine if r["status"] == "planned"]
        done = [r for r in mine if r["status"] == "delivered"]
        cat = sorted({r["category"] for r in planned})
        lines.append("## 机器 %s — 批次 `%s`（待做 %d 件，已交付 %d 件）｜类目：%s"
                     % (m, BATCH[m], len(planned), len(done), "、".join(cat) or "-"))
        lines.append("")
        if planned:
            lines.append("| project id | 族谱 | 物件 | 类目 | 目标 | 与同族兄弟件的拓扑差异 |")
            lines.append("|---|---|---|---|---|---|")
            for r in planned:
                lines.append("| `%s` | %s | %s | %s | %s | %s |"
                             % (r["pid"], r["family"], r["object"], r["category"],
                                r["grade"], r["topo"]))
        if done:
            lines.append("")
            lines.append("已交付（登记在全局去重底表里，任何人不得再做同拓扑件）："
                         + ", ".join("`%s`" % r["pid"] for r in done))
        lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 机器 | 待做 | 其中 S 级 | 已交付 | 族谱数 |")
    lines.append("|---|---|---|---|---|")
    for m in MACHINES:
        mine = [r for r in rs if r["machine"] == m]
        planned = [r for r in mine if r["status"] == "planned"]
        lines.append("| %s | %d | %d | %d | %d |"
                     % (m, len(planned), sum(1 for r in planned if r["grade"] == "S"),
                        sum(1 for r in mine if r["status"] == "delivered"),
                        len({r["family"] for r in planned})))
    lines.append("| **合计** | **%d** | **%d** | **%d** | %d 个族谱 |"
                 % (sum(1 for r in rs if r["status"] == "planned"),
                    sum(1 for r in rs if r["status"] == "planned" and r["grade"] == "S"),
                    sum(1 for r in rs if r["status"] == "delivered"),
                    len({r["family"] for r in rs if r["status"] == "planned"})))
    open(os.path.join(ROOT, "catalog", "CATALOG.md"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n")


def write_intake(rs):
    d = os.path.join(ROOT, "intake")
    os.makedirs(d, exist_ok=True)
    for m in MACHINES:
        mine = [r for r in rs if r["machine"] == m and r["status"] == "planned"]
        if not mine:
            continue
        doc = {"batch": BATCH[m], "machine": m, "buyer": "internal",
               "spec": "CAD 数据采购需求说明 v1.0 (procurement_requirements.pdf)",
               "issued_by": "machine-A dsh-cad-agent",
               "created": "2026-10-31",
               "parts": [r["pid"] for r in mine],
               "detail": [{"project_id": r["pid"], "title": r["object"], "family": r["family"],
                           "category": r["category"], "grade": r["grade"], "unit": "mm",
                           "type": "part", "topo_signature": r["topo"]} for r in mine],
               "notes": "两两拓扑必须不同（2.5）；关掉的特征组其参数不得留在 params 表（探针逐格验证）。"}
        open(os.path.join(d, BATCH[m] + ".json"), "w", encoding="utf-8").write(
            json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def brief(m, rs, common):
    mine = [r for r in rs if r["machine"] == m and r["status"] == "planned"]
    fams = {}
    for r in mine:
        fams.setdefault(r["family"], []).append(r)
    lines = ["任务：你是 CAD 流水线的机器 %s，本批次 %s（%d 件，全新族谱）" % (m, BATCH[m], len(mine)),
             "",
             "仓库：git clone git@github.com:JR-Rong/cad-repo.git cad-repo（或你已有的本地 clone 先 git pull）",
             "先读：cad-repo/WORK-ORDER.md（通用规则），再读下面你自己的件清单；"
             "规格原文在 cad-repo/shared/spec-extract.txt。",
             "开工前必须先把全局去重底表同步进来：python3 cad-repo/tools/registry_sync.py --root ~/cad-fleet",
             "", "你的件（project id 一字不差；同族两件之间拓扑必须不同）："]
    for fam, items in sorted(fams.items()):
        lines.append("")
        lines.append("族谱 `%s`" % fam)
        for r in items:
            lines.append("  - %s  目标 %s 级｜%s｜拓扑差异：%s" % (r["pid"], r["grade"], r["object"], r["topo"]))
    lines += ["", "以上之外的件都不要做（其他机器在产）。其余规则照通用工单执行：", common]
    return "\n".join(lines) + "\n"


def write_briefs(rs):
    d = os.path.join(ROOT, "briefs")
    os.makedirs(d, exist_ok=True)
    common_path = os.path.join(ROOT, "briefs-common.txt")
    common = open(common_path, encoding="utf-8").read().strip() if os.path.exists(common_path) else \
        "见 cad-repo/WORK-ORDER.md（内存纪律、动作序列、回传格式、停下问人的条件）"
    for m in MACHINES:
        if any(r["machine"] == m and r["status"] == "planned" for r in rs):
            open(os.path.join(d, "BRIEF-FOR-%s.txt" % m), "w", encoding="utf-8").write(brief(m, rs, common))


def main():
    rs = rows()
    doc = write_json(rs)
    write_md(rs, doc)
    write_intake(rs)
    write_briefs(rs)
    planned = [r for r in rs if r["status"] == "planned"]
    print("catalog: %d 件（待做 %d，S 级 %d，已交付 %d）/ 族谱 %d 个"
          % (len(rs), len(planned), sum(1 for r in planned if r["grade"] == "S"),
             len(rs) - len(planned), len({r["family"] for r in planned})))
    for m in MACHINES:
        p = [r for r in planned if r["machine"] == m]
        print("  %s: %d 件  batch %s  families=%s" % (m, len(p), BATCH[m],
              ",".join(sorted({r["family"] for r in p}))))


if __name__ == "__main__":
    sys.exit(main())
