#!/usr/bin/env python3
"""Commit and push ``status/ledger_anchor.json``.

``fleet_runner`` writes that file at the end of a run and does not commit
or push. Run this yourself after a production run, before the next one:

    python3 tools/push_ledger_anchor.py

Only that path is added. Other dirty files in the worktree stay unstaged.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANCHOR = "status/ledger_anchor.json"


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
    commands = [
        ["git", "add", "--", ANCHOR],
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
