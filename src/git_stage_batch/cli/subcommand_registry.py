"""Top-level subcommand registration."""

from __future__ import annotations

from .asset_subcommands import add_install_assets_subcommand
from .batch_subcommands import (
    add_annotate_subcommand,
    add_apply_subcommand,
    add_drop_subcommand,
    add_validate_subcommand,
    add_list_subcommand,
    add_new_subcommand,
    add_reset_subcommand,
    add_sift_subcommand,
)
from .completion import add_completion_subcommand
from .file_blocking_subcommands import (
    add_block_file_subcommand,
    add_unblock_file_subcommand,
)
from .fixup_subcommands import add_suggest_fixup_subcommand
from .journal_subcommands import add_journal_subcommand
from .selection_subcommands import (
    add_discard_subcommand,
    add_include_subcommand,
    add_show_subcommand,
    add_skip_subcommand,
)
from .session_subcommands import (
    add_abort_subcommand,
    add_again_subcommand,
    add_check_unstaged_subcommand,
    add_redo_subcommand,
    add_start_subcommand,
    add_status_subcommand,
    add_stop_subcommand,
    add_undo_subcommand,
)
from .tui_subcommands import add_interactive_subcommand


def add_cli_subcommands(subparsers) -> None:
    """Register all public and hidden CLI subcommands in display order."""
    add_check_unstaged_subcommand(subparsers)
    add_start_subcommand(subparsers)
    add_interactive_subcommand(subparsers)
    add_stop_subcommand(subparsers)
    add_again_subcommand(subparsers)
    add_undo_subcommand(subparsers)
    add_redo_subcommand(subparsers)
    add_show_subcommand(subparsers)
    add_status_subcommand(subparsers)
    add_include_subcommand(subparsers)
    add_skip_subcommand(subparsers)
    add_discard_subcommand(subparsers)
    add_abort_subcommand(subparsers)
    add_block_file_subcommand(subparsers)
    add_unblock_file_subcommand(subparsers)
    add_suggest_fixup_subcommand(subparsers)
    add_new_subcommand(subparsers)
    add_list_subcommand(subparsers)
    add_validate_subcommand(subparsers)
    add_journal_subcommand(subparsers)
    add_drop_subcommand(subparsers)
    add_annotate_subcommand(subparsers)
    add_apply_subcommand(subparsers)
    add_reset_subcommand(subparsers)
    add_sift_subcommand(subparsers)
    add_install_assets_subcommand(subparsers)
    add_completion_subcommand(subparsers)
