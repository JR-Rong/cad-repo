#!/usr/bin/env python3
"""Commit and push the ledger anchor, and the restore record when present.

``fleet_runner`` writes ``status/ledger_anchor.json`` at the end of a run
and does not commit or push. Run this yourself after a production run,
before the next one:

    python3 tools/push_ledger_anchor.py

``restore_to_anchor`` uses the same helper. The checkout must already be
``main``, even with ``origin/main``, and the index must be clean. The
commit contains only the named anchor files. The push does not prompt.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANCHOR = "status/ledger_anchor.json"
RESTORE = "status/ledger_restore.json"
DEFAULT_PUSH_TIMEOUT = 60


class PublishRefused(Exception):
    """The checkout is not a safe place to publish. Nothing was changed."""


class PublishFailed(Exception):
    """Fetch, commit, or push did not finish."""


def paths_to_publish(repo):
    paths = [ANCHOR]
    if os.path.isfile(os.path.join(repo, RESTORE)):
        paths.append(RESTORE)
    return paths


def push_env(base=None):
    """No credential prompt. BatchMode is appended to any existing ssh command."""
    env = dict(os.environ if base is None else base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    extra = "-o BatchMode=yes"
    current = env.get("GIT_SSH_COMMAND", "").strip()
    if not current:
        env["GIT_SSH_COMMAND"] = "ssh " + extra
    elif extra not in current:
        env["GIT_SSH_COMMAND"] = current + " " + extra
    return env


def _run(cmd, cwd, timeout, env):
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, timeout=timeout, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.TimeoutExpired as exc:
        raise PublishFailed("git timed out: %s" % " ".join(cmd)) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise PublishFailed(
            "git failed (%s): %s" % (" ".join(cmd), detail)) from exc


def preflight(repo, remote="origin", timeout=DEFAULT_PUSH_TIMEOUT):
    """Refuse unless this checkout can publish without taking other commits.

    The current branch is ``main``. After ``git fetch <remote> main``,
    ``HEAD`` is that remote main. The index has nothing staged. A failure
    here must leave the repository and the ledger untouched.
    """
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, text=True).strip()
    if branch != "main":
        raise PublishRefused("current branch is %s, not main" % branch)
    env = push_env()
    _run(["git", "fetch", remote, "main"], repo, timeout, env)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    try:
        origin = subprocess.check_output(
            ["git", "rev-parse", "%s/main" % remote],
            cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError as exc:
        raise PublishRefused("%s/main is missing" % remote) from exc
    if head != origin:
        raise PublishRefused("HEAD is not %s/main" % remote)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo)
    if diff.returncode != 0:
        raise PublishRefused("index is not clean")


def commit_and_push(repo, remote="origin", paths=None,
                    timeout=DEFAULT_PUSH_TIMEOUT):
    """Commit only ``paths`` and push ``main``. Raises on failure.

    ``git commit -- <paths>`` does not take other staged files. The push
    sets ``GIT_TERMINAL_PROMPT=0`` and ssh BatchMode, and it is killed if
    it runs longer than ``timeout`` seconds.
    """
    preflight(repo, remote=remote, timeout=timeout)
    if paths is None:
        paths = paths_to_publish(repo)
    env = push_env()
    _run(["git", "add", "--"] + list(paths), repo, timeout, env)
    _run(["git", "commit", "-m", "ledger anchor", "--"] + list(paths),
         repo, timeout, env)
    _run(["git", "push", remote, "main"], repo, timeout, env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--remote", default="origin")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_PUSH_TIMEOUT,
        help="seconds before fetch or push is killed (default 60)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the git commands and do not commit or push")
    args = parser.parse_args(argv)
    path = os.path.join(args.repo, ANCHOR)
    if not os.path.isfile(path):
        print("没有 %s。先跑完一轮 fleet_runner，它会写这个文件。" % ANCHOR)
        return 2
    try:
        preflight(args.repo, remote=args.remote, timeout=args.timeout)
    except (PublishRefused, PublishFailed) as exc:
        print(exc)
        return 2
    paths = paths_to_publish(args.repo)
    commands = [
        ["git", "add", "--"] + paths,
        ["git", "commit", "-m", "ledger anchor", "--"] + paths,
        ["git", "push", args.remote, "main"],
    ]
    if args.dry_run:
        for cmd in commands:
            print(" ".join(cmd))
        return 0
    try:
        commit_and_push(
            args.repo, remote=args.remote, paths=paths, timeout=args.timeout)
    except (PublishRefused, PublishFailed) as exc:
        print(exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
