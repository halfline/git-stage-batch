"""Shell command escape for interactive mode."""

from __future__ import annotations

import subprocess

from ..i18n import _
from ..utils.git import get_git_repository_root_path
from .prompts import prompt_shell_command


def handle_shell_command(action: str) -> None:
    """Handle shell command execution."""
    if len(action) > 1:
        command = action[1:].strip()
    else:
        command = prompt_shell_command()

    if command:
        result = subprocess.run(
            command,
            shell=True,
            cwd=get_git_repository_root_path(),
        )
        if result.returncode != 0:
            print(_("Command exited with status {}").format(result.returncode))

        try:
            input(_("\nPress Enter to continue..."))
        except (KeyboardInterrupt, EOFError):
            pass
    else:
        print(_("No command entered"))
