"""CLI entry point."""

from __future__ import annotations

import os
import sys
from contextlib import nullcontext

from ..exceptions import CommandError
from ..i18n import _
from ..utils.session_lock import acquire_session_lock
from .argument_parser import parse_command_line
from .dispatch import dispatch_args
from .pager import pager_output, should_page_output


def main() -> None:
    """Main entry point for git-stage-batch."""
    try:
        args = parse_command_line(sys.argv[1:], quiet=False)
        if args is not None:
            if args.working_directory is not None:
                os.chdir(args.working_directory)
            pager_context = pager_output() if should_page_output(args) else nullcontext()
            lock_context = (
                nullcontext()
                if getattr(args, "prompt_format", None) is not None
                else acquire_session_lock()
            )
            with pager_context:
                with lock_context:
                    dispatch_args(args)
        else:
            # Parsing failed
            sys.exit(2)
    except CommandError as e:
        if e.message:
            print(e.message, file=sys.stderr)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        print(_("Interrupted."), file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
