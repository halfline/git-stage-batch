"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path

from .. import __version__
from ..batch.validation import batch_exists
from ..batch.replacement import ReplacementText
from .. import commands
from ..batch.query import read_batch_metadata
from ..data.file_tracking import list_untracked_files
from ..data.hunk_tracking import select_next_change_after_action, show_selected_change
from ..data.undo import undo_checkpoint
from ..exceptions import CommandError
from ..i18n import _, ngettext
from ..utils.command import run_command
from ..utils.file_patterns import list_changed_files, resolve_gitignore_style_patterns
from ..utils.git import run_git_command
from .completion import command_complete_files


class FileScopeKind(str, Enum):
    """How a command's optional file scope was requested."""

    IMPLICIT = "implicit"
    EXPLICIT = "explicit"
    PATTERN = "pattern"


@dataclass(frozen=True)
class FileScope:
    """Resolved command file scope with explicit origin and concrete files."""

    kind: FileScopeKind
    files: tuple[str, ...] = ()

    @classmethod
    def implicit(cls) -> "FileScope":
        return cls(FileScopeKind.IMPLICIT)

    @classmethod
    def explicit(cls, file_path: str) -> "FileScope":
        return cls(FileScopeKind.EXPLICIT, (file_path,))

    @classmethod
    def pattern(cls, files: list[str]) -> "FileScope":
        return cls(FileScopeKind.PATTERN, tuple(files))

    @property
    def is_implicit(self) -> bool:
        return self.kind == FileScopeKind.IMPLICIT

    @property
    def is_multiple(self) -> bool:
        return len(self.files) > 1

    def optional_file(self) -> str | None:
        """Return the single file path for this scope, or None for implicit scope."""
        if self.is_implicit:
            return None
        if self.is_multiple:
            raise ValueError("multiple file scope cannot be represented by one path")
        return self.files[0]

    def require_single_file(self, error_message: str) -> str | None:
        """Return an optional single file path, or raise for a multi-file scope."""
        if self.is_multiple:
            raise CommandError(error_message)
        return self.optional_file()


class GitHelpArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that tries to use git help for --help."""

    def __init__(
        self,
        *args,
        help_topic: str | None = None,
        **kwargs,
    ):
        self._git_help_topic = help_topic
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        """Try to use git help, fall back to argparse help."""
        if (
            self._git_help_topic is not None
            and _show_git_stage_batch_help(self._git_help_topic)
        ):
            return

        # Fall back to standard argparse help
        super().print_help(file)


def _resolve_default_manpath() -> str | None:
    """Return the default manpath as if MANPATH were unset."""
    env = os.environ.copy()
    env.pop("MANPATH", None)
    try:
        result = run_command(
            ["manpath", "-q"],
            check=False,
            env=env,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _build_manpath_with_packaged_page(man_root: Path, env: dict[str, str]) -> str:
    """Build a MANPATH preferring the packaged man page when available."""
    if env.get("MANPATH"):
        return f"{man_root}{os.pathsep}{env['MANPATH']}"

    default_manpath = _resolve_default_manpath()
    if default_manpath:
        return f"{man_root}{os.pathsep}{default_manpath}"

    return f"{man_root}{os.pathsep}{os.pathsep}"


def _try_git_help_with_environment(
    help_topic: str,
    env: dict[str, str] | None = None,
) -> bool:
    """Run git help for a git-stage-batch topic."""
    try:
        result = run_git_command(
            ["help", _git_help_name_for_help_topic(help_topic)],
            check=False,
            capture_stdout=False,
            env=env,
            requires_index_lock=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _with_real_manpath_root(manpage_path: Path):
    """Yield a manpath root that contains the requested man page."""
    if manpage_path.parent.name == "man1":
        return nullcontext(manpage_path.parent.parent)

    class _TemporaryManRoot:
        def __enter__(self):
            self._temp_dir = tempfile.TemporaryDirectory(prefix="git-stage-batch-help-")
            temp_root = Path(self._temp_dir.name)
            temp_manpage = temp_root / "man1" / manpage_path.name
            temp_manpage.parent.mkdir(parents=True, exist_ok=True)
            temp_manpage.write_bytes(manpage_path.read_bytes())
            return temp_root

        def __exit__(self, exc_type, exc, tb):
            self._temp_dir.cleanup()
            return False

    return _TemporaryManRoot()


def _manpage_name_for_help_topic(help_topic: str) -> str:
    """Return the man page filename for a git help topic."""
    return f"git-{help_topic}.1"


def _git_help_name_for_help_topic(help_topic: str) -> str:
    """Return the git help argument for a git-stage-batch topic."""
    return _manpage_name_for_help_topic(help_topic).removesuffix(".1")


def _show_git_stage_batch_help(help_topic: str = "stage-batch") -> bool:
    """Show git-stage-batch help from packaged or system man pages."""
    try:
        packaged_manpage = resources.files("git_stage_batch").joinpath(
            "assets",
            "man",
            "man1",
            _manpage_name_for_help_topic(help_topic),
        )
    except (ModuleNotFoundError, FileNotFoundError):
        packaged_manpage = None

    if packaged_manpage is not None:
        try:
            with resources.as_file(packaged_manpage) as packaged_manpage_path:
                if packaged_manpage_path.exists():
                    with _with_real_manpath_root(packaged_manpage_path) as man_root:
                        env = os.environ.copy()
                        env["MANPATH"] = _build_manpath_with_packaged_page(
                            Path(man_root),
                            env,
                        )
                        if _try_git_help_with_environment(help_topic, env):
                            return True
        except FileNotFoundError:
            pass

    return _try_git_help_with_environment(help_topic)


def _add_subcommand_parser(
    subparsers,
    command_name: str,
    **kwargs,
) -> GitHelpArgumentParser:
    """Add a subcommand parser wired to its git help topic."""
    help_topic = kwargs.pop("help_topic", f"stage-batch-{command_name}")
    return subparsers.add_parser(
        command_name,
        help_topic=help_topic,
        **kwargs,
    )


def _add_file_argument(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add a single-file argument that supports omitted values."""
    parser.add_argument(
        "--file",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=help_text,
    )
    parser.add_argument(
        "--files",
        dest="file_patterns",
        nargs="+",
        default=None,
        metavar="PATTERN",
        help=_("Resolve one or more gitignore-style PATTERNs to files."),
    )


def _add_auto_advance_arguments(parser: argparse.ArgumentParser) -> None:
    """Add controls for selecting the next hunk after an action."""
    auto_advance = parser.add_mutually_exclusive_group()
    auto_advance.add_argument(
        "--auto-advance",
        dest="auto_advance",
        action="store_true",
        default=None,
        help=_("Select the next hunk after the command completes"),
    )
    auto_advance.add_argument(
        "--no-auto-advance",
        dest="auto_advance",
        action="store_false",
        help=_("Leave no hunk selected after the command completes"),
    )


def _validate_file_inputs(
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> None:
    """Validate cross-argument rules for file-scoped operations."""
    if file_arg is not None and file_patterns is not None:
        raise CommandError(_("Cannot use --file together with --files."))


def _run_for_each_file(
    file_scope: FileScope,
    callback: Callable[[str | None], None],
    *,
    line_ids: str | None = None,
    undo_operation: str | None = None,
    worktree_paths: Sequence[str] | None = None,
) -> None:
    """Run a callback once per resolved file argument."""
    if file_scope.is_multiple and line_ids is not None:
        raise CommandError(_("Cannot use --lines with multiple files."))
    if file_scope.is_multiple:
        checkpoint = (
            _multi_file_undo_checkpoint(
                undo_operation,
                file_scope.files,
                worktree_paths=worktree_paths,
            )
            if undo_operation is not None else
            nullcontext()
        )
        with checkpoint:
            for file in file_scope.files:
                callback(file)
        return
    callback(file_scope.optional_file())


def _format_multi_file_operation(command: str, files: Sequence[str]) -> str:
    """Return a readable undo operation for a resolved multi-file command."""
    return f"{command} --files {' '.join(shlex.quote(file) for file in files)}"


def _multi_file_undo_checkpoint(
    command: str,
    files: Sequence[str],
    *,
    worktree_paths: Sequence[str] | None = None,
) -> AbstractContextManager[None]:
    """Create one undo checkpoint for a resolved multi-file command."""
    paths = list(worktree_paths) if worktree_paths is not None else None
    return undo_checkpoint(
        _format_multi_file_operation(command, files),
        worktree_paths=paths,
    )


def _include_each_resolved_file(
    files: list[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage a multi-file live scope and report one aggregate summary."""
    total_hunks = 0
    staged_files: list[str] = []

    with _multi_file_undo_checkpoint("include", files):
        for file_path in files:
            staged_hunks = commands.command_include_file(
                file_path,
                quiet=True,
                advance=False,
            )
            if staged_hunks > 0:
                total_hunks += staged_hunks
                staged_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks staged from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)

    if len(staged_files) == 1:
        file_summary = staged_files[0]
    else:
        file_summary = ngettext(
            "{count} file",
            "{count} files",
            len(staged_files),
        ).format(count=len(staged_files))

    print(
        ngettext(
            "✓ Staged {count} hunk from {files}",
            "✓ Staged {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def _skip_each_resolved_file(
    files: list[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Skip a multi-file live scope and report one aggregate summary."""
    total_hunks = 0
    skipped_files: list[str] = []

    with _multi_file_undo_checkpoint("skip", files):
        for file_path in files:
            skipped_hunks = commands.command_skip_file(
                file_path,
                quiet=True,
                advance=False,
            )
            if skipped_hunks > 0:
                total_hunks += skipped_hunks
                skipped_files.append(file_path)

    if total_hunks == 0:
        print(_("No hunks skipped from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)

    if len(skipped_files) == 1:
        file_summary = skipped_files[0]
    else:
        file_summary = ngettext(
            "{count} file",
            "{count} files",
            len(skipped_files),
        ).format(count=len(skipped_files))

    print(
        ngettext(
            "✓ Skipped {count} hunk from {files}",
            "✓ Skipped {count} hunks from {files}",
            total_hunks,
        ).format(count=total_hunks, files=file_summary),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def _discard_to_batch_each_resolved_file(
    batch_name: str,
    files: list[str],
    *,
    auto_advance: bool | None = None,
) -> None:
    """Save a multi-file live scope to a batch and report one aggregate summary."""
    total_hunks = 0
    discarded_files: list[str] = []

    operation = f"discard --to {shlex.quote(batch_name)}"
    with _multi_file_undo_checkpoint(operation, files, worktree_paths=files):
        result = commands.command_discard_files_to_batch(
            batch_name,
            files,
            quiet=True,
            advance=False,
            auto_advance=auto_advance,
        )
        total_hunks = result.discarded_hunks
        discarded_files = result.discarded_files

    if total_hunks == 0:
        print(_("No hunks saved to batch from matched files."), file=sys.stderr)
        return

    should_show_next = select_next_change_after_action(auto_advance=auto_advance)

    if len(discarded_files) == 1:
        file_summary = discarded_files[0]
    else:
        file_summary = ngettext(
            "{count} file",
            "{count} files",
            len(discarded_files),
        ).format(count=len(discarded_files))

    print(
        ngettext(
            "✓ Saved {count} hunk from {files} to batch '{batch}' and discarded it",
            "✓ Saved {count} hunks from {files} to batch '{batch}' and discarded them",
            total_hunks,
        ).format(count=total_hunks, files=file_summary, batch=batch_name),
        file=sys.stderr,
    )
    if should_show_next:
        show_selected_change()


def _resolve_live_file_scope(
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> FileScope:
    """Resolve single-file or pattern-based live file scope."""
    _validate_file_inputs(file_arg, file_patterns)
    if file_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit(file_arg)

    candidate_files = list(dict.fromkeys([*list_changed_files(), *list_untracked_files()]))
    resolved_files = resolve_gitignore_style_patterns(candidate_files, file_patterns)
    if not resolved_files:
        raise CommandError(
            _("No changed files matched: {patterns}").format(
                patterns=", ".join(file_patterns),
            )
        )
    return FileScope.pattern(resolved_files)


def _resolve_batch_file_scope(
    batch_name: str,
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> FileScope:
    """Resolve single-file or pattern-based batch file scope."""
    _validate_file_inputs(file_arg, file_patterns)
    if file_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit(file_arg)
    if not batch_exists(batch_name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=batch_name))

    metadata = read_batch_metadata(batch_name)
    resolved_files = resolve_gitignore_style_patterns(metadata.get("files", {}).keys(), file_patterns)
    if not resolved_files:
        raise CommandError(
            _("No files in batch '{name}' matched: {patterns}").format(
                name=batch_name,
                patterns=", ".join(file_patterns),
            )
        )
    return FileScope.pattern(resolved_files)


def _resolve_replacement_text(args: argparse.Namespace) -> str | None:
    """Return replacement text from `--as` or exact stdin content."""
    if getattr(args, "as_text", None) is not None and getattr(args, "as_stdin", False):
        raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
    if getattr(args, "as_stdin", False):
        data = sys.stdin.buffer.read()
        return ReplacementText(
            data.decode("utf-8", errors="surrogateescape"),
            data=data,
            exact=True,
        )
    as_text = getattr(args, "as_text", None)
    if as_text is not None:
        return ReplacementText(as_text, exact=True)
    return None


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    # Mapping from shortcuts to their expanded forms
    quick_actions = {
        '?': ['--help'],
        'if': ['include', '--file'],
        'il': ['include', '--line'],
        'sf': ['skip', '--file'],
        'sl': ['skip', '--line'],
        'df': ['discard', '--file'],
        'dl': ['discard', '--line'],
    }

    # Expand quick actions
    expanded = []
    for arg in args:
        if arg in quick_actions:
            expanded.extend(quick_actions[arg])
        else:
            expanded.append(arg)

    # Create parser
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
        help_topic="stage-batch",
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )
    parser.add_argument(
        "-C",
        dest="working_directory",
        metavar="path",
        default=None,
        help=_("Run as if started in path"),
    )
    parser.add_argument(
        "-i",
        dest="interactive_flag",
        action="store_true",
        help=_("Start interactive mode"),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help=_("Available commands"),
    )

    # start - Start a new batch staging session
    parser_start = _add_subcommand_parser(
        subparsers,
        "start",
        help=_("Start a new batch staging session"),
    )
    parser_start.add_argument(
        "-U",
        "--unified",
        dest="context_lines",
        type=int,
        metavar="N",
        help=_("Number of context lines in diff output (default: 3)"),
    )
    _add_auto_advance_arguments(parser_start)
    parser_start.set_defaults(
        func=lambda args: commands.command_start(
            context_lines=args.context_lines,
            auto_advance=args.auto_advance,
        )
    )

    # interactive - Start interactive hunk-by-hunk mode
    parser_interactive = _add_subcommand_parser(
        subparsers,
        "interactive",
        help=_("Start interactive hunk-by-hunk mode"),
    )
    parser_interactive.set_defaults(func=lambda _: commands.command_interactive())

    # stop - Stop the selected session and clear state
    parser_stop = _add_subcommand_parser(
        subparsers,
        "stop",
        help=_("Stop the selected session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: commands.command_stop())

    # again - Clear state and start a fresh pass
    parser_again = _add_subcommand_parser(
        subparsers,
        "again",
        aliases=["a"],
        help=_("Clear state and start a fresh pass"),
    )
    _add_auto_advance_arguments(parser_again)
    parser_again.set_defaults(
        func=lambda args: commands.command_again(auto_advance=args.auto_advance)
    )

    # undo - Undo the most recent undoable session operation
    parser_undo = _add_subcommand_parser(
        subparsers,
        "undo",
        aliases=["u", "back"],
        help=_("Undo the most recent undoable session operation"),
    )
    parser_undo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo checkpoint"),
    )
    parser_undo.set_defaults(func=lambda args: commands.command_undo(force=args.force))

    # redo - Redo the most recently undone session operation
    parser_redo = _add_subcommand_parser(
        subparsers,
        "redo",
        aliases=["forward"],
        help=_("Redo the most recently undone session operation"),
    )
    parser_redo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo"),
    )
    parser_redo.set_defaults(func=lambda args: commands.command_redo(force=args.force))

    # show - Show the selected hunk
    parser_show = _add_subcommand_parser(
        subparsers,
        "show",
        help=_("Show the selected hunk"),
    )
    parser_show.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Show changes from batch"),
    )
    parser_show.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Show only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_show.add_argument(
        "--page",
        "--pages",
        metavar="PAGES",
        dest="page",
        help=_("Show page selection for a file review, e.g. '3', '3-5', '1,3,5-7', or 'all'."),
    )
    _add_file_argument(
        parser_show,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_show.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    def dispatch_show(args: argparse.Namespace) -> None:
        resolved_file_scope = (
            _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            if args.from_batch
            else _resolve_live_file_scope(args.file, args.file_patterns)
        )
        if args.page is not None:
            if args.from_batch and not batch_exists(args.from_batch):
                raise CommandError(_("Batch '{name}' does not exist").format(name=args.from_batch))
            if resolved_file_scope.is_implicit:
                if not (
                    args.from_batch
                    and batch_exists(args.from_batch)
                    and len(read_batch_metadata(args.from_batch).get("files", {})) == 1
                ):
                    raise CommandError(
                        _(
                            "`show --page` requires `--file` or a single-file `--files` match, "
                            "unless `--from` names a single-file batch."
                        )
                    )
            if resolved_file_scope.is_multiple:
                raise CommandError(_("`show --page` requires exactly one resolved file."))
            if args.line_ids is not None:
                raise CommandError(_("Cannot use `show --page` together with `show --line`."))
            if args.porcelain:
                raise CommandError(_("Cannot use `show --page` with `--porcelain`."))
        if args.from_batch:
            if resolved_file_scope.is_multiple:
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                commands.command_show_from_batch(
                    args.from_batch,
                    args.line_ids,
                    patterns=args.file_patterns,
                    page=args.page,
                )
            else:
                commands.command_show_from_batch(
                    args.from_batch,
                    args.line_ids,
                    resolved_file_scope.optional_file(),
                    page=args.page,
                )
            return
        if args.line_ids or not resolved_file_scope.is_implicit:
            if resolved_file_scope.is_multiple and args.porcelain:
                raise CommandError(_("Cannot use --porcelain with multiple files."))
            if resolved_file_scope.is_multiple:
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                commands.command_show_file_list(list(resolved_file_scope.files))
            else:
                commands.command_show(
                    file=resolved_file_scope.optional_file(),
                    page=args.page,
                    porcelain=args.porcelain,
                )
            return
        commands.command_show(porcelain=args.porcelain)

    parser_show.set_defaults(func=dispatch_show)

    # status - Show selected session status
    parser_status = _add_subcommand_parser(
        subparsers,
        "status",
        aliases=["st"],
        help=_("Show selected session status"),
    )
    status_output = parser_status.add_mutually_exclusive_group()
    status_output.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    status_output.add_argument(
        "--for-prompt",
        dest="prompt_format",
        nargs="?",
        const=commands.DEFAULT_PROMPT_FORMAT,
        metavar="FORMAT",
        help=_("Print FORMAT only when a session is active, for shell prompts"),
    )
    parser_status.set_defaults(
        func=lambda args: commands.command_status(
            porcelain=args.porcelain,
            prompt_format=args.prompt_format,
        )
    )

    # include - Stage the selected hunk
    parser_include = _add_subcommand_parser(
        subparsers,
        "include",
        aliases=["i"],
        help=_("Stage the selected hunk"),
    )
    parser_include.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Stage only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
        parser_include,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, stages entire file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_include.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Include changes from batch"),
    )
    parser_include.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Include changes to batch"),
    )
    parser_include.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Replace selected lines, or full file with --file, using TEXT before staging"),
    )
    parser_include.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement text from standard input exactly, preserving trailing newlines"),
    )
    parser_include.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_("Do not strip unchanged edge-overlap lines from replacement text used with --as"),
    )
    parser_include.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    _add_auto_advance_arguments(parser_include)

    def dispatch_include(args: argparse.Namespace) -> None:
        replacement_requested = args.as_text is not None or args.as_stdin
        if replacement_requested:
            if args.as_text is not None and args.as_stdin:
                raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
            if args.line_ids and args.from_batch and not args.to_batch:
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` only applies to live `include --line --as` operations."))
                resolved_batch_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
                resolved_file = resolved_batch_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                commands.command_include_from_batch(
                    args.from_batch,
                    args.line_ids,
                    file=resolved_file,
                    replacement_text=replacement_text,
                )
                return
            if args.line_ids and not args.from_batch and not args.to_batch:
                resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
                resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                commands.command_include_line_as(
                    args.line_ids,
                    replacement_text,
                    file=resolved_file,
                    no_edge_overlap=args.no_edge_overlap,
                    auto_advance=args.auto_advance,
                )
                return
            if (
                args.line_ids is None
                and args.from_batch is None
                and args.to_batch is None
            ):
                resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
                if resolved_live_scope.is_implicit:
                    raise CommandError(
                        _("`include --as` requires `--file` or `--line` and does not support `--to`.")
                    )
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` requires `include --line --as`."))
                if resolved_live_scope.is_multiple:
                    raise CommandError(_("Cannot use --as with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                commands.command_include_file_as(
                    replacement_text,
                    file=resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
                return
            raise CommandError(
                _("`include --as` requires `--file` or `--line` and does not support `--to`.")
            )
        if args.no_edge_overlap:
            raise CommandError(_("`--no-edge-overlap` requires `include --line --as`."))
        if args.from_batch:
            resolved_batch_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            _run_for_each_file(
                resolved_batch_scope,
                lambda file: commands.command_include_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
                undo_operation=f"include --from {shlex.quote(args.from_batch)}",
                worktree_paths=resolved_batch_scope.files,
            )
        elif args.to_batch:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            _run_for_each_file(
                resolved_live_scope,
                lambda file: commands.command_include_to_batch(
                    args.to_batch,
                    args.line_ids,
                    file,
                    auto_advance=args.auto_advance,
                ),
                line_ids=args.line_ids,
                undo_operation=f"include --to {shlex.quote(args.to_batch)}",
            )
        elif args.line_ids:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
            commands.command_include_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        else:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            if resolved_live_scope.is_multiple:
                _include_each_resolved_file(
                    list(resolved_live_scope.files),
                    auto_advance=args.auto_advance,
                )
            elif not resolved_live_scope.is_implicit:
                commands.command_include_file(
                    resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
            else:
                commands.command_include(auto_advance=args.auto_advance)

    parser_include.set_defaults(func=dispatch_include)

    # skip - Skip the selected hunk without staging
    parser_skip = _add_subcommand_parser(
        subparsers,
        "skip",
        aliases=["s"],
        help=_("Skip the selected hunk without staging"),
    )
    parser_skip.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Skip only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
        parser_skip,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, skips all hunks from the file."),
    )
    _add_auto_advance_arguments(parser_skip)

    def dispatch_skip(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_live_file_scope(args.file, args.file_patterns)
        if args.line_ids:
            resolved_file = resolved_file_scope.require_single_file(_("Cannot use --lines with multiple files."))
            commands.command_skip_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        elif not resolved_file_scope.is_implicit:
            if resolved_file_scope.is_multiple:
                _skip_each_resolved_file(
                    list(resolved_file_scope.files),
                    auto_advance=args.auto_advance,
                )
            else:
                commands.command_skip_file(
                    resolved_file_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
        else:
            commands.command_skip(auto_advance=args.auto_advance)

    parser_skip.set_defaults(func=dispatch_skip)

    # discard - Remove the selected hunk from working tree
    parser_discard = _add_subcommand_parser(
        subparsers,
        "discard",
        aliases=["d"],
        help=_("Remove the selected hunk from working tree"),
    )
    parser_discard.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Discard only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
        parser_discard,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, discards entire file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_discard.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Discard changes from batch"),
    )
    parser_discard.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Discard changes to batch"),
    )
    parser_discard.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Replace selected lines, or full file with --file, using TEXT"),
    )
    parser_discard.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement text from standard input exactly, preserving trailing newlines"),
    )
    parser_discard.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_("Do not strip unchanged edge-overlap lines from replacement text used with --as"),
    )
    parser_discard.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    _add_auto_advance_arguments(parser_discard)

    def dispatch_discard(args: argparse.Namespace) -> None:
        replacement_requested = args.as_text is not None or args.as_stdin
        if replacement_requested:
            if args.as_text is not None and args.as_stdin:
                raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
            if args.to_batch and args.line_ids and not args.from_batch:
                resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
                resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                commands.command_discard_line_as_to_batch(
                    args.to_batch,
                    args.line_ids,
                    replacement_text,
                    file=resolved_file,
                    no_edge_overlap=args.no_edge_overlap,
                    auto_advance=args.auto_advance,
                )
                return
            if (
                args.to_batch is None
                and args.from_batch is None
                and args.line_ids is None
            ):
                resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
                if resolved_live_scope.is_implicit:
                    raise CommandError(
                        _("`discard --as` requires `--file`, or `--to` with `--line`.")
                    )
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` requires `discard --to --line --as`."))
                if resolved_live_scope.is_multiple:
                    raise CommandError(_("Cannot use --as with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                commands.command_discard_file_as(
                    replacement_text,
                    file=resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
                return
            raise CommandError(
                _("`discard --as` requires `--file`, or `--to` with `--line`.")
            )
        if args.no_edge_overlap:
            raise CommandError(_("`--no-edge-overlap` requires `discard --to --line --as`."))
        if args.from_batch:
            resolved_batch_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            _run_for_each_file(
                resolved_batch_scope,
                lambda file: commands.command_discard_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
                undo_operation=f"discard --from {shlex.quote(args.from_batch)}",
                worktree_paths=resolved_batch_scope.files,
            )
        elif args.to_batch:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            if resolved_live_scope.is_multiple and args.line_ids is None:
                _discard_to_batch_each_resolved_file(
                    args.to_batch,
                    list(resolved_live_scope.files),
                    auto_advance=args.auto_advance,
                )
            else:
                _run_for_each_file(
                    resolved_live_scope,
                    lambda file: commands.command_discard_to_batch(
                        args.to_batch,
                        args.line_ids,
                        file,
                        auto_advance=args.auto_advance,
                    ),
                    line_ids=args.line_ids,
                    undo_operation=f"discard --to {shlex.quote(args.to_batch)}",
                    worktree_paths=resolved_live_scope.files,
                )
        elif args.line_ids:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
            commands.command_discard_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        else:
            resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
            if not resolved_live_scope.is_implicit:
                _run_for_each_file(
                    resolved_live_scope,
                    lambda file: commands.command_discard_file(
                        file,
                        auto_advance=args.auto_advance,
                    ),
                    undo_operation="discard",
                    worktree_paths=resolved_live_scope.files,
                )
            else:
                commands.command_discard(auto_advance=args.auto_advance)

    parser_discard.set_defaults(func=dispatch_discard)

    # abort - Restore repository to pre-session state
    parser_abort = _add_subcommand_parser(
        subparsers,
        "abort",
        help=_("Restore repository to pre-session state"),
    )
    parser_abort.set_defaults(func=lambda _: commands.command_abort())

    # block-file - Permanently exclude a file
    parser_block_file = _add_subcommand_parser(
        subparsers,
        "block-file",
        aliases=["bf"],
        help=_("Permanently exclude a file (adds to .gitignore)"),
    )
    parser_block_file.add_argument(
        "file_path",
        nargs="?",
        default="",
        help=_("Path to the file to block (defaults to selected hunk's file)"),
    )
    parser_block_file.set_defaults(func=lambda args: commands.command_block_file(args.file_path))

    # unblock-file - Remove a file from blocked list
    parser_unblock_file = _add_subcommand_parser(
        subparsers,
        "unblock-file",
        aliases=["ubf"],
        help=_("Remove a file from the blocked list"),
    )
    parser_unblock_file.add_argument(
        "file_path",
        help=_("Path to the file to unblock"),
    )
    parser_unblock_file.set_defaults(func=lambda args: commands.command_unblock_file(args.file_path))

    # suggest-fixup - Suggest which commit the selected hunk should be fixed up to
    parser_suggest_fixup = _add_subcommand_parser(
        subparsers,
        "suggest-fixup",
        aliases=["x"],
        help=_("Suggest which commit the selected hunk should be fixed up to"),
    )
    parser_suggest_fixup.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Analyze only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_suggest_fixup.add_argument(
        "--reset",
        action="store_true",
        help=_("Reset state and start search over from most recent"),
    )
    parser_suggest_fixup.add_argument(
        "--abort",
        action="store_true",
        help=_("Clear state and exit without showing candidates"),
    )
    parser_suggest_fixup.add_argument(
        "--last",
        action="store_true",
        help=_("Re-show the last candidate without advancing"),
    )
    parser_suggest_fixup.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_("Git ref to use as lower bound for commit search (default: @{upstream})"),
    )
    parser_suggest_fixup.set_defaults(func=lambda args: (
        commands.command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        ) if args.line_ids else
        commands.command_suggest_fixup(
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        )
    ))

    # new - Create a new batch
    parser_new = _add_subcommand_parser(
        subparsers,
        "new",
        help=_("Create a new batch"),
    )
    parser_new.add_argument(
        "batch_name",
        help=_("Name of the batch to create"),
    )
    parser_new.add_argument(
        "-m", "--note",
        default="",
        help=_("Optional description for the batch"),
    )
    parser_new.set_defaults(func=lambda args: commands.command_new_batch(args.batch_name, args.note))

    # list - List all batches
    parser_list = _add_subcommand_parser(
        subparsers,
        "list",
        help=_("List all batches"),
    )
    parser_list.set_defaults(func=lambda _: commands.command_list_batches())

    # drop - Delete a batch
    parser_drop = _add_subcommand_parser(
        subparsers,
        "drop",
        help=_("Delete a batch"),
    )
    parser_drop.add_argument(
        "batch_name",
        help=_("Name of the batch to delete"),
    )
    parser_drop.set_defaults(func=lambda args: commands.command_drop_batch(args.batch_name))

    # annotate - Add/update batch description
    parser_annotate = _add_subcommand_parser(
        subparsers,
        "annotate",
        help=_("Add or update batch description"),
    )
    parser_annotate.add_argument(
        "batch_name",
        help=_("Name of the batch"),
    )
    parser_annotate.add_argument(
        "note",
        help=_("Description text"),
    )
    parser_annotate.set_defaults(func=lambda args: commands.command_annotate_batch(args.batch_name, args.note))

    # apply - Apply batch changes to working tree
    parser_apply = _add_subcommand_parser(
        subparsers,
        "apply",
        help=_("Apply batch changes to working tree"),
    )
    parser_apply.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Apply changes from batch to working tree"),
    )
    parser_apply.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Apply only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
        parser_apply,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses first file in batch (sorted order). "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_apply(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        _run_for_each_file(
            resolved_file_scope,
            lambda file: commands.command_apply_from_batch(
                args.from_batch,
                line_ids=args.line_ids if hasattr(args, "line_ids") else None,
                file=file,
            ),
            line_ids=args.line_ids if hasattr(args, "line_ids") else None,
            undo_operation=f"apply --from {shlex.quote(args.from_batch)}",
            worktree_paths=resolved_file_scope.files,
        )

    parser_apply.set_defaults(func=dispatch_apply)

    # reset - Remove claims from batch
    parser_reset = _add_subcommand_parser(
        subparsers,
        "reset",
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Move reset claims to another batch"),
    )
    parser_reset.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Reset only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
        parser_reset,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_reset(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        command_parts = ["reset", "--from", shlex.quote(args.from_batch)]
        if args.to_batch is not None:
            command_parts.extend(["--to", shlex.quote(args.to_batch)])
        if args.line_ids is not None:
            command_parts.extend(["--line", shlex.quote(args.line_ids)])
        _run_for_each_file(
            resolved_file_scope,
            lambda file: commands.command_reset_from_batch(
                args.from_batch,
                args.line_ids,
                file,
                None,
                args.to_batch,
            ),
            line_ids=args.line_ids,
            undo_operation=" ".join(command_parts),
        )

    parser_reset.set_defaults(func=dispatch_reset)

    # sift - Reconcile batch against current tip
    parser_sift = _add_subcommand_parser(
        subparsers,
        "sift",
        help=_("Remove already-present portions from a batch"),
    )
    parser_sift.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Source batch to sift"),
    )
    parser_sift.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        required=True,
        help=_("Destination batch (may equal source for in-place sift)"),
    )
    parser_sift.set_defaults(func=lambda args: commands.command_sift_batch(args.from_batch, args.to_batch))

    parser_install_assets = _add_subcommand_parser(
        subparsers,
        "install-assets",
        help=_("Install bundled assistant assets into the repository"),
    )
    parser_install_assets.add_argument(
        "asset_group",
        choices=["claude-agents", "claude-skills", "codex-skills"],
        nargs="?",
        help=_("Bundled asset group to install"),
    )
    parser_install_assets.add_argument(
        "--filter",
        dest="filters",
        metavar="PATTERN",
        nargs="+",
        help=_("Install only bundled assets whose names match one or more gitignore-style PATTERNs"),
    )
    parser_install_assets.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite an existing installed asset"),
    )
    parser_install_assets.set_defaults(
        func=lambda args: commands.command_install_assets(
            args.asset_group,
            args.filters,
            force=args.force,
        )
    )

    parser_complete_files = subparsers.add_parser(
        "__complete-files",
        help=argparse.SUPPRESS,
    )
    parser_complete_files.add_argument(
        "current_token",
        nargs="?",
        default="",
    )
    parser_complete_files.add_argument(
        "--from",
        dest="from_batch",
        default=None,
    )
    parser_complete_files.set_defaults(
        func=lambda args: command_complete_files(args.current_token, from_batch=args.from_batch)
    )

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
    except SystemExit as e:
        if quiet and e.code != 0:
            return None
        raise
