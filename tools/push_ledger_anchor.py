#!/usr/bin/env python3
"""Commit and push the ledger anchor, and the restore record when present.

``fleet_runner`` writes ``status/ledger_anchor.json`` at the end of a run
and does not commit or push. Run this yourself after a production run,
before the next one:

    python3 tools/push_ledger_anchor.py

``restore_to_anchor`` commits and pushes that anchor together with
``status/ledger_restore.json`` itself. Only those paths are added. Other
dirty files in the worktree stay unstaged.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANCHOR = "status/ledger_anchor.json"
RESTORE = "status/ledger_restore.json"


def paths_to_publish(repo):
    paths = [ANCHOR]
    if os.path.isfile(os.path.join(repo, RESTORE)):
        paths.append(RESTORE)
    return paths


def commit_and_push(repo, remote="origin", refspec="HEAD", paths=None):
    """``git add`` the given paths, commit, and push. Raises on failure."""
    if paths is None:
        paths = paths_to_publish(repo)
    subprocess.check_call(["git", "add", "--"] + list(paths), cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "ledger anchor"], cwd=repo)
    subprocess.check_call(["git", "push", remote, refspec], cwd=repo)


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
    paths = paths_to_publish(args.repo)
    commands = [
        ["git", "add", "--"] + paths,
        ["git", "commit", "-m", "ledger anchor"],
        ["git", "push", args.remote, "HEAD"],
    ]
    if args.dry_run:
        for cmd in commands:
            print(" ".join(cmd))
        return 0
    commit_and_push(args.repo, remote=args.remote, paths=paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
