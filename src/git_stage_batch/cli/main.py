"""CLI entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import nullcontext

from ..exceptions import CommandError
from ..i18n import _
from ..runtime import dispatch_cli_mode
from ..data.session_ownership import require_no_foreign_session_owner
from ..utils.session_lock import acquire_session_lock
from .argument_parser import parse_command_line
from .pager import pager_output, should_page_output


_READ_ONLY_COMMANDS = frozenset({"check-unstaged", "list", "show", "status"})


def _command_may_mutate(args) -> bool:
    """Return whether dispatch may change worktree-local or shared state."""
    if getattr(args, "interactive_flag", False):
        return True
    if getattr(args, "interactive_command", False):
        return True
    return getattr(args, "command", None) not in _READ_ONLY_COMMANDS


def main() -> None:
    """Main entry point for git-stage-batch."""
    try:
        args = parse_command_line(sys.argv[1:], quiet=False)
        if args is not None:
            if args.working_directory is not None:
                os.chdir(args.working_directory)
            skip_session_lock = getattr(args, "prompt_format", None) is not None
            pager_context = pager_output() if should_page_output(args) else nullcontext()
            lock_context = (
                nullcontext()
                if skip_session_lock
                else acquire_session_lock()
            )
            with pager_context:
                with lock_context:
                    if not skip_session_lock and _command_may_mutate(args):
                        require_no_foreign_session_owner()
                    dispatch_cli_mode(args)
        else:
            # Parsing failed
            sys.exit(2)
    except CommandError as e:
        if e.message:
            print(e.message, file=sys.stderr)
        sys.exit(e.exit_code)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            print(e.stderr.rstrip(), file=sys.stderr)
        else:
            command = " ".join(e.cmd) if isinstance(e.cmd, list) else str(e.cmd)
            print(
                _("Command failed with exit status {status}: {command}").format(
                    status=e.returncode,
                    command=command,
                ),
                file=sys.stderr,
            )
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print(_("Interrupted."), file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
