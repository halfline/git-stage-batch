"""CLI entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import AbstractContextManager, nullcontext

from ..data.session_ownership import require_no_foreign_session_owner
from ..exceptions import CommandError
from ..i18n import _
from ..runtime import dispatch_cli_mode
from ..utils.git_repository import require_git_repository
from ..utils.journal import flush_journal
from .argument_parser import parse_command_line
from .command_policy import (
    SessionOwnershipPolicy,
    policy_for_args,
    policy_requires_repository,
    policy_uses_session_lock,
)
from .pager import pager_output, should_page_output


def _error_stream_text(stream: str | bytes | None) -> str:
    """Return subprocess diagnostics as printable text."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def acquire_session_lock() -> AbstractContextManager[None]:
    """Load the POSIX lock backend only after the platform check."""
    from ..utils.session_lock import acquire_session_lock as acquire_lock

    return acquire_lock()


def _configure_terminal_streams() -> None:
    """Preserve arbitrary filesystem bytes when writing terminal output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="surrogateescape")
        except (OSError, ValueError):
            # Captured or already-detached streams may not be reconfigurable.
            pass


def main() -> None:
    """Main entry point for git-stage-batch."""
    _configure_terminal_streams()
    try:
        if os.name != "posix":
            raise CommandError(
                _("git-stage-batch currently supports POSIX operating systems only.")
            )
        args = parse_command_line(sys.argv[1:], quiet=False)
        if args is not None:
            if args.working_directory is not None:
                os.chdir(args.working_directory)
            policy = policy_for_args(args)
            if policy_requires_repository(policy, args):
                require_git_repository()
            pager_context = (
                pager_output()
                if should_page_output(args)
                else nullcontext()
            )
            lock_context = (
                acquire_session_lock()
                if policy_uses_session_lock(policy, args)
                else nullcontext()
            )
            with pager_context:
                with lock_context:
                    if (
                        policy.session_ownership
                        is SessionOwnershipPolicy.REQUIRE_AVAILABLE
                    ):
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
        stderr = _error_stream_text(e.stderr)
        if stderr:
            print(stderr.rstrip(), file=sys.stderr)
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
    finally:
        flush_journal()


if __name__ == "__main__":
    main()
