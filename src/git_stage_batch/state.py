"""State management and filesystem utilities for git-stage-batch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .i18n import _


# --------------------------- Utility: git and filesystem ---------------------------

def run_git_command(arguments: list[str],
                    check: bool = True,
                    text_output: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], check=check, text=text_output, capture_output=True)

def require_git_repository() -> None:
    try:
        run_git_command(["rev-parse", "--git-dir"])
    except subprocess.CalledProcessError as error:
        # Print git's actual error message which contains helpful context
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        exit_with_error(_("Not inside a git repository."))

def get_git_repository_root_path() -> Path:
    output = run_git_command(["rev-parse", "--show-toplevel"]).stdout.strip()
    return Path(output)

def exit_with_error(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(exit_code)
