#!/usr/bin/env python3
"""错题本摄入器（只在 A 机跑）：把审查发现合并成教训，并强制升级。

三个入口，都汇到同一张表：
  --audit cad/audit/findings_round11_*.json   本地/外部审计（codex 类审查）的发现
  --gate /tmp/mc.json                         merge_check --json 的 blocking 清单
  （以及各机 lessons/inbox/<X>.jsonl 里 lesson_add.py 记的条目）

规则（写死，不靠自觉）：
  * 同因合并：key = (where, 标题前 120 字) 的哈希，跨机器同一根因只有一条教训；
  * 出现两次、或 P0/P1、或审计里 status=unfixed ⇒ 强制方式升级为「必须做进门禁」，
    绝不允许第三条"下次注意"（WORKFLOW §7）；
  * 每次 ingest 都会改 LESSONS.md ⇒ LESSONS_ACK 变化 ⇒ 各机必须重读并重跑 recall，
    这是设计目的，不是副作用。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LESSONS = os.path.join(REPO, "LESSONS.md")
INBOX = os.path.join(REPO, "lessons", "inbox")
INDEX = os.path.join(REPO, "lessons", "index.json")
BEGIN = "<!-- AUTO-INGEST:BEGIN 这一节由 tools/lesson_ingest.py 维护，勿手改 -->"
END = "<!-- AUTO-INGEST:END -->"
NEEDS_GATE = "必须做进门禁"
SELF_CHECK = "自检（首次，待升级）"


def norm(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def rhash(where, title):
    return hashlib.sha256(("%s|%s" % (norm(where), norm(title)[:120])).encode()).hexdigest()[:10]


def from_inbox():
    for path in sorted(glob.glob(os.path.join(INBOX, "*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            yield {"hash": e.get("hash") or rhash(e.get("where", ""), e.get("title", "")),
                   "machine": e.get("machine", "?"), "source": e.get("source", "self"),
                   "severity": e.get("severity", "P2"), "title": e.get("title", "").strip(),
                   "detail": e.get("detail", ""), "where": e.get("where", ""),
                   "evidence": e.get("evidence", ""), "status": e.get("status", "open"),
                   "ts": e.get("ts", "")}


def from_audit(path):
    doc = json.load(open(path, encoding="utf-8"))
    src = "audit:" + os.path.basename(path).replace(".json", "")
    verdict = doc.get("verdict")
    for it in doc.get("new_findings", []) or []:
        yield {"hash": rhash(it.get("file", ""), it.get("title", "")), "machine": "audit",
               "source": src, "severity": it.get("severity", "P2"),
               "title": it.get("title", "")[:160], "where": it.get("file", ""),
               "detail": (it.get("what") or "")[:400],
               "evidence": ((it.get("evidence") or [{}])[0].get("command", "") if it.get("evidence") else ""),
               "status": {"unfixed": "open", "fixed": "fixed", "partially_fixed": "partial"}
                         .get(it.get("status", "unfixed"), "open"),
               "ts": doc.get("audit_time", ""), "verdict": verdict}
    for it in doc.get("recheck", []) or []:
        if it.get("status") == "fixed":
            continue
        yield {"hash": rhash(it.get("file", "") or it.get("id", ""), it.get("title", "")),
               "machine": "audit", "source": src + "/recheck",
               "severity": it.get("severity", "P2"),
               "title": ("部分通过 " + str(it.get("id", "")) + "：" + it.get("title", ""))[:160],
               "where": it.get("file", ""), "detail": (it.get("closure_scope") or it.get("what") or "")[:400],
               "evidence": "", "status": "partial", "ts": doc.get("audit_time", ""),
               "verdict": verdict}


def from_gate(path):
    doc = json.load(open(path, encoding="utf-8"))
    batch = doc.get("batch", "?")
    for b in doc.get("blocking", []) or []:
        where = ", ".join(re.findall(r"\b[a-e]_[a-z0-9_]+", b)) or batch
        yield {"hash": rhash(where, b), "machine": "gate", "source": "merge_check",
               "severity": "P1", "title": b[:160], "where": where,
               "detail": "交付被收货门禁挡下（batch %s）" % batch, "evidence": "",
               "status": "open", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


def merge(entries, index):
    """Return (index, new_cnt, recur_cnt)."""
    """count = 同因的**不同来源出现次数**（不是 ingest 运行次数）。

    重跑同一个 gate 输出、重复摄入同一份审计文件都必须幂等，否则"复发即升级"
    会被工具自己的重放刷成假警报。
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    recur = 0
    for e in entries:
        origin = "%s|%s|%s" % (e["source"], e.get("evidence") or e.get("detail", "")[:60],
                               e.get("where", ""))
        e["origin"] = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:10]
        cur = index.get(e["hash"])
        if not cur:
            index[e["hash"]] = dict(e, count=1, first_seen=e.get("ts") or now,
                                    last_seen=now, machines={e["machine"]},
                                    sources={e["source"]}, origins={e["origin"]},
                                    verdict=e.get("verdict"), _fresh=True)
        elif e["origin"] in set(cur.get("origins", [])):
            cur.setdefault("_replay", 0)               # 同一来源重放，不算复发
            continue
        else:
            cur.setdefault("origins", set()).add(e["origin"])
            cur["count"] += 1
            recur += 1
            cur["last_seen"] = now
            cur["machines"] = set(cur.get("machines", [])) | {e["machine"]}
            cur["sources"] = set(cur.get("sources", [])) | {e["source"]}
            if e.get("status") in ("open", "partial"):
                cur["status"] = e["status"]
            if e.get("severity") in ("P0", "P1"):
                cur["severity"] = e["severity"]
    new_cnt = sum(1 for v in index.values() if v.get("count") == 1 and v.get("_fresh"))
    for cur in index.values():
        cur["machines"] = sorted(cur.get("machines", []))
        cur["sources"] = sorted(cur.get("sources", []))
        cur["origins"] = sorted(cur.get("origins", []))
        if cur["status"] == "synthetic":
            cur["force"] = "留档（测试/演练数据，不计复发、不升门禁）"
            continue
        cur["force"] = NEEDS_GATE if (cur["count"] >= 2 or cur["severity"] in ("P0", "P1")
                                     or cur["status"] == "partial") else SELF_CHECK
        cur.pop("_fresh", None)
        cur.pop("_replay", None)
    return index, new_cnt, recur


def render(index):
    open_e = [v for v in index.values() if v["status"] in ("open", "partial")]
    open_e.sort(key=lambda v: (v["force"] != NEEDS_GATE, v["severity"], -v["count"], v["first_seen"]))
    L = [BEGIN, "",
         "## 四、错题本（自动摄入，%d 条未关闭 / 共 %d 条）" % (len(open_e), len(index)), "",
         "由 `tools/lesson_ingest.py` 从审计发现、`merge_check` 挡单与各机上报合并；",
         "`复发` = 同因出现次数；`≥2 或 P0/P1 或部分通过` ⇒ 强制方式自动升级为**必须做进门禁**。",
         "",
         "| hash | 严重 | 状态 | 复发 | 症状 | 根因位置 | 强制 | 来源 |",
         "|---|---|---|---|---|---|---|---|"]
    for v in open_e:
        L.append("| `%s` | %s | %s | %d | %s | %s | %s | %s |"
                 % (v["hash"], v["severity"], v["status"], v["count"],
                    v["title"].replace("|", "/")[:90],
                    (v.get("where") or "-").replace("|", "/")[-46:], v["force"],
                    ",".join(v["sources"])[:40]))
    L += ["", "<details><summary>已关闭 / wontfix（保留可查）</summary>", ""]
    for v in sorted((x for x in index.values() if x["status"] not in ("open", "partial")),
                    key=lambda x: x["last_seen"], reverse=True)[:40]:
        L.append("- `%s` %s ｜ %s ｜ %s" % (v["hash"], v["status"], v["severity"], v["title"][:80]))
    L += ["", "</details>", END]
    return "\n".join(L)


def splice(text, block):
    if BEGIN in text and END in text:
        # 标题现在在标记之内：把历史遗留的重复标题行清掉（早期版本每摄入一次多一行）
        head = "\n".join(l for l in text.split(BEGIN)[0].splitlines()
                         if not l.startswith("## 四、错题本")).rstrip("\n")
        tail = text.split(END, 1)[1].lstrip("\n")
        return head + "\n\n" + block + "\n" + ("\n" + tail if tail else "")
    return text.rstrip("\n") + "\n\n" + block + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="append", default=[], help="审计 findings JSON（可多次）")
    ap.add_argument("--gate", action="append", default=[], help="merge_check --json 输出（可多次）")
    ap.add_argument("--synthetic", action="append", default=[],
                    help="测试/演练来源的文件：只留档，不计复发、不升门禁")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(LESSONS):
        print("缺 LESSONS.md", file=sys.stderr)
        return 2
    index = {}
    if os.path.exists(INDEX):
        index = json.load(open(INDEX, encoding="utf-8"))

    syn = {os.path.abspath(x) for x in a.synthetic}

    def tag(got, path):
        if os.path.abspath(path) in syn:
            for g in got:
                g["status"] = "synthetic"
        return got

    entries = list(from_inbox())
    for p in a.audit:
        entries += tag(list(from_audit(p)), p)
    for p in a.gate:
        entries += tag(list(from_gate(p)), p)
    if not entries:
        print("没有新条目（inbox 为空且未给 --audit/--gate）")

    before = set(index)
    index, new_cnt, recur_cnt = merge(entries, index)
    new = [h for h in index if h not in before]
    recurred = []
    escalated = [v for v in index.values() if v["force"] == NEEDS_GATE and v["count"] >= 2]

    if a.dry_run:
        print("dry-run：新增 %d / 复发 %d / 需升级门禁 %d"
              % (new_cnt, recur_cnt, len(escalated)))
        return 0

    text = open(LESSONS, encoding="utf-8").read()
    open(LESSONS, "w", encoding="utf-8").write(splice(text, render(index)))
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    tmp = INDEX + ".tmp"
    json.dump(index, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, INDEX)

    ack = hashlib.sha256(open(LESSONS, "rb").read()).hexdigest()[:8]
    open_cnt = sum(1 for v in index.values() if v["status"] in ("open", "partial"))
    gate_cnt = sum(1 for v in index.values() if v["force"] == NEEDS_GATE
                   and v["status"] in ("open", "partial"))
    print("错题本：共 %d 条，未关闭 %d 条，其中 %d 条已升级为「必须做进门禁」"
          % (len(index), open_cnt, gate_cnt))
    print("本次：新增 %d 条，同因复发 %d 条（只有**不同来源**再犯才算复发）"
          % (new_cnt, recur_cnt))
    print("LESSONS_ACK 现在 = %s（各机必须重跑 recall 并在 REPORT.md 里更新）" % ack)
    if escalated:
        print("需要立刻做进门禁的复发项：")
        for v in escalated[:10]:
            print("  - %s %s（%d 次，%s）" % (v["hash"], v["title"][:70], v["count"],
                                             ",".join(v["machines"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
