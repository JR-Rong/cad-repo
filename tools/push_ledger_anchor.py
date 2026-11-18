#!/usr/bin/env python3
"""Commit and push the ledger anchor, and the restore record when present.

``fleet_runner`` writes ``status/ledger_anchor.json`` at the end of a run
and does not commit or push. ``restore_to_anchor`` also writes
``status/ledger_restore.json``. Run this yourself after a production run
or a restore, before the next run:

    python3 tools/push_ledger_anchor.py

Only those paths are added. Other dirty files in the worktree stay unstaged.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANCHOR = "status/ledger_anchor.json"
RESTORE = "status/ledger_restore.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--remote", default="origin")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the git commands and do not commit or push")
    args = parser.parse_args(argv)
    path = os.path.join(args.repo, ANCHOR)
    if not os.path.isfile(path):
        print("没有 %s。先跑完一轮 fleet_runner，它会写这个文件。" % ANCHOR)
        return 2
    paths = [ANCHOR]
    if os.path.isfile(os.path.join(args.repo, RESTORE)):
        paths.append(RESTORE)
    commands = [
        ["git", "add", "--"] + paths,
        ["git", "commit", "-m", "ledger anchor"],
        ["git", "push", args.remote, "HEAD"],
    ]
    if args.dry_run:
        for cmd in commands:
            print(" ".join(cmd))
        return 0
    for cmd in commands:
        subprocess.check_call(cmd, cwd=args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
