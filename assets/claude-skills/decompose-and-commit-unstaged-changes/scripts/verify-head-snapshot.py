#!/usr/bin/env python3
"""Run a verification command against a detached worktree snapshot."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a git commit exactly as committed, isolated from the dirty worktree."
    )
    parser.add_argument("--repo", default=".", help="repository path")
    parser.add_argument("--ref", default="HEAD", help="commit or ref to verify")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run in the detached worktree, optionally after --",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        command = ["python", "-m", "compileall", "-q", "src", "tests"]

    repo = Path(args.repo).resolve()
    with tempfile.TemporaryDirectory(prefix="verify-head-") as tmp:
        worktree = Path(tmp) / "worktree"
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "--no-optional-locks",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    args.ref,
                ],
                check=True,
            )
            return subprocess.run(command, cwd=worktree).returncode
        finally:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "--no-optional-locks",
                    "worktree",
                    "remove",
                    "--force",
                    str(worktree),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    sys.exit(main())
