#!/usr/bin/env python3
"""Append one lesson candidate to THIS machine's inbox (append-only, no conflicts).

Who calls it: the local reviewer (a codex/audit subagent, `review_scan.py`, or a
human) whenever something is found that cost — or could have cost — a part.
Nobody writes LESSONS.md directly except `lesson_ingest.py` on machine A: five
machines appending to one markdown file is a merge-conflict factory, and the
错题本 only works if it stays machine-readable too.

  tools/lesson_add.py --machine B --source audit --severity P1 \\
      --title "重建体积差随 dress-up 顺序漂移" \\
      --detail "file=validate.py:412 期望 <=1e-4 实测 3e-3" \\
      --evidence "cad/audit/findings_round11.json#N3"
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
INBOX = os.path.join(REPO, "lessons", "inbox")


def norm(text):
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return t


def root_hash(source, title, where):
    """Same root cause across machines must land on one lesson, so the key is
    (where + normalised title), not the whole Chinese sentence."""
    basis = "%s|%s" % (norm(where), norm(title)[:120])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:10]


def append(entry):
    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, entry["machine"] + ".jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", required=True, choices=list("ABCDE"))
    ap.add_argument("--source", default="self",
                    choices=["audit", "merge_check", "review_scan", "self", "buyer"])
    ap.add_argument("--severity", default="P2", choices=["P0", "P1", "P2", "P3", "note"])
    ap.add_argument("--title", required=True)
    ap.add_argument("--detail", default="")
    ap.add_argument("--where", default="", help="file:line 或 project id，用于同因合并")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--status", default="open",
                    choices=["open", "partial", "fixed", "wontfix"])
    a = ap.parse_args()

    title = a.title.strip()
    if len(title) < 6:
        print("标题太短，写清症状才好用（≥6 字）", file=sys.stderr)
        return 2
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machine": a.machine,
             "source": a.source, "severity": a.severity, "title": title,
             "detail": a.detail.strip(), "where": a.where.strip(),
             "evidence": a.evidence.strip(), "status": a.status,
             "hash": root_hash(a.source, title, a.where)}
    # 同一条重复上报（同一台、同一 hash）只保留一条，防止轮询型审查刷量
    path = os.path.join(INBOX, a.machine + ".jsonl")
    seen = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                seen.add(json.loads(line)["hash"])
            except (ValueError, KeyError):
                continue
    if entry["hash"] in seen:
        print("已记过同因错题（hash %s），本次不重复写入：%s" % (entry["hash"], title[:60]))
        return 0
    print("写入 %s\n  hash=%s severity=%s title=%s"
          % (append(entry), entry["hash"], entry["severity"], title[:70]))
    print("A 机 `python3 tools/lesson_ingest.py` 会把它并进 LESSONS.md 并升级强制方式。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
