"""Import-boundary checks for architecture-sensitive package seams."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "git_stage_batch"


def _module_name_for_path(path: Path) -> str:
    relative_path = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(("git_stage_batch", *relative_path.parts))


def _resolve_import_from_module(
    *,
    current_module: str,
    level: int,
    module: str | None,
) -> str | None:
    if level == 0:
        return module

    current_package = current_module.split(".")[:-1]
    if level - 1 > len(current_package):
        return None

    base_package = current_package[: len(current_package) - (level - 1)]
    if module:
        return ".".join((*base_package, *module.split(".")))
    return ".".join(base_package)


def _import_from_nodes(path: Path) -> list[tuple[str | None, ast.ImportFrom]]:
    current_module = _module_name_for_path(path)
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            nodes.append((
                _resolve_import_from_module(
                    current_module=current_module,
                    level=node.level,
                    module=node.module,
                ),
                node,
            ))
    return nodes


def test_replacement_payload_imports_use_core_boundary():
    """Non-batch code should not depend on batch replacement for neutral payloads."""
    neutral_names = {
        "ReplacementPayload",
        "ReplacementText",
        "coerce_replacement_payload",
        "replacement_line_bodies",
        "replacement_line_chunks",
    }
    violations = []

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.batch.replacement":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & neutral_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_diff_parser_does_not_import_snapshot_runtime_io():
    """Diff parsing should not own selected-file snapshot persistence."""
    diff_parser_path = SRC_ROOT / "core" / "diff_parser.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(diff_parser_path)
    }

    assert "git_stage_batch.data.selected_change.snapshots" not in imported_modules
    assert "git_stage_batch.utils.git" not in imported_modules
    assert "git_stage_batch.utils.journal" not in imported_modules
    assert "git_stage_batch.utils.paths" not in imported_modules
    assert not hasattr(
        __import__(
            "git_stage_batch.core.diff_parser",
            fromlist=["write_snapshots_for_selected_file_path"],
        ),
        "write_snapshots_for_selected_file_path",
    )


def test_selected_change_store_stays_below_orchestration_state():
    """Selected-change persistence should stay below orchestration state."""
    store_path = SRC_ROOT / "data" / "selected_change" / "store.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(store_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules
    assert "git_stage_batch.data.file_review.state" not in imported_modules


def test_batch_file_display_stays_below_hunk_navigation():
    """Batch file rendering should not depend on selected-change orchestration."""
    renderer_path = SRC_ROOT / "batch" / "file_display.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(renderer_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules
    assert "git_stage_batch.data.file_review.state" not in imported_modules


def test_selected_change_batch_file_cache_does_not_import_hunk_navigation():
    """Batch file selection caching should not depend on hunk navigation."""
    cache_path = SRC_ROOT / "data" / "selected_change" / "batch_file_cache.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(cache_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_file_review_state_does_not_import_hunk_navigation():
    """File-review safety state should not depend on hunk navigation."""
    review_state_path = SRC_ROOT / "data" / "file_review" / "state.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_state_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_file_review_output_does_not_import_hunk_navigation():
    """File-review output should not depend on hunk navigation."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_batch_selection_does_not_import_hunk_navigation():
    """Batch selection should use focused data helpers instead of hunk navigation."""
    selection_path = SRC_ROOT / "batch" / "selection.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(selection_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_batch_selected_changes_does_not_import_hunk_navigation():
    """Batch atomic-selection state should not depend on live hunk navigation."""
    batch_selected_path = SRC_ROOT / "data" / "batch_selected_changes.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(batch_selected_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_selected_change_lifecycle_does_not_import_hunk_navigation():
    """Selected-change lifecycle clearing should stay independent from hunk navigation."""
    lifecycle_path = SRC_ROOT / "data" / "selected_change" / "lifecycle.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(lifecycle_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_file_change_display_does_not_import_hunk_navigation():
    """Live file-change rendering should not depend on hunk navigation."""
    display_path = SRC_ROOT / "data" / "file_change_display.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(display_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_file_hunk_display_does_not_import_hunk_navigation():
    """File-scoped text rendering should not depend on hunk navigation."""
    display_path = SRC_ROOT / "data" / "file_hunk_display.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(display_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_change_freshness_does_not_import_hunk_navigation():
    """Cached change freshness checks should not depend on hunk navigation."""
    freshness_path = SRC_ROOT / "data" / "change_freshness.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(freshness_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_status_does_not_import_hunk_navigation():
    """Status should read focused data helpers instead of hunk navigation."""
    status_path = SRC_ROOT / "commands" / "status.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(status_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_argument_parser_delegates_multi_file_action_flow():
    """Parser branches should not own selected-change follow-up display."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    helper_path = (
        SRC_ROOT / "commands" / "file_scope" / "multi_file_actions.py"
    )
    helper_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(helper_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in parser_imports
    assert (
        "git_stage_batch.commands.file_scope.multi_file_actions"
        in parser_imports
    )
    assert "git_stage_batch.data.hunk_tracking" in helper_imports


def test_hunk_tracking_does_not_reexport_live_change_helpers():
    """Moved live-change helpers should not stay available from hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    moved_names = {
        "binary_file_change_is_stale",
        "gitlink_change_is_stale",
        "rename_change_is_stale",
        "render_binary_file_change",
        "render_gitlink_change",
        "render_rename_change",
        "render_text_deletion_change",
        "stream_live_git_diff",
        "text_deletion_change_is_batched",
        "text_deletion_change_is_stale",
    }

    assert moved_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_file_hunk_helpers():
    """Moved file-scoped hunk helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    moved_names = {
        "build_file_hunk_from_buffer",
        "cache_file_as_single_hunk",
        "cache_unstaged_file_as_single_hunk",
        "render_file_as_single_hunk",
        "render_unstaged_file_as_single_hunk",
    }

    assert moved_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_batch_hunk_helpers():
    """Batch display helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    moved_or_removed_names = {
        "cache_batch_as_single_hunk",
        "cache_batch_files_generator",
        "cache_rendered_batch_file_display",
        "get_batch_file_for_line_operation",
        "render_batch_file_display",
    }

    assert moved_or_removed_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_progress_helpers():
    """Progress helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    progress_names = {
        "format_id_range",
        "record_binary_hunk_skipped",
        "record_gitlink_hunk_skipped",
        "record_hunk_discarded",
        "record_hunk_included",
        "record_hunk_skipped",
        "record_hunks_discarded",
        "record_rename_hunk_skipped",
        "record_text_deletion_hunk_skipped",
    }

    assert progress_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_selected_state_helpers():
    """Selected-state helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    selected_state_names = {
        "clear_selected_change_state_files",
        "snapshots_are_stale",
        "write_snapshots_for_selected_file_path",
    }

    assert selected_state_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_batch_selection_helpers():
    """Batch selection helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    batch_selection_names = {
        "compute_batch_binary_fingerprint",
        "compute_batch_gitlink_fingerprint",
        "require_current_selected_batch_binary_file_for_batch",
        "require_current_selected_batch_gitlink_file_for_batch",
        "selected_batch_binary_batch_name",
        "selected_batch_binary_file_for_batch",
        "selected_batch_binary_matches_batch",
        "selected_batch_gitlink_file_for_batch",
        "selected_batch_gitlink_matches_batch",
    }

    assert batch_selection_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_selected_change_store_helpers():
    """Selected-change store helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    selected_store_names = {
        "SelectedChangeClearReason",
        "SelectedChangeKind",
        "SelectedChangeStateSnapshot",
        "cache_binary_file_change",
        "cache_gitlink_change",
        "cache_rename_change",
        "cache_text_deletion_change",
        "get_selected_change_file_path",
        "load_line_changes_from_patch_path",
        "load_selected_binary_file",
        "load_selected_gitlink_change",
        "load_selected_rename_change",
        "load_selected_text_deletion_change",
        "mark_selected_change_cleared_by_auto_advance_disabled",
        "mark_selected_change_cleared_by_file_list",
        "read_selected_change_kind",
        "refuse_bare_action_after_auto_advance_disabled",
        "refuse_bare_action_after_file_list",
        "refuse_bare_action_after_stale_batch_selection",
        "restore_selected_change_state",
        "selected_change_was_cleared_by_auto_advance_disabled",
        "selected_change_was_cleared_by_file_list",
        "selected_change_was_cleared_by_stale_batch_selection",
        "snapshot_selected_change_state",
        "write_line_changes_state",
        "write_selected_change_kind",
        "write_selected_hunk_patch_lines",
    }

    assert selected_store_names.isdisjoint(vars(hunk_tracking))


def test_hunk_tracking_does_not_reexport_line_state_helpers():
    """Line-state helpers should not stay on hunk tracking."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    line_state_names = {
        "convert_line_changes_to_serializable_dict",
        "load_line_changes_from_state",
    }

    assert line_state_names.isdisjoint(vars(hunk_tracking))


def test_recalc_handoff_stays_in_command_helper():
    """Include and discard commands should use the command refresh handoff."""
    command_paths = (
        SRC_ROOT / "commands" / "include.py",
        SRC_ROOT / "commands" / "discard.py",
    )
    forbidden_names = {
        "RecalculateSelectedHunkResult",
        "recalculate_selected_hunk_for_file",
    }
    violations = []

    for command_path in command_paths:
        imported_modules = {
            imported_module
            for imported_module, _node in _import_from_nodes(command_path)
        }
        assert "git_stage_batch.commands.selection.selected_hunk_refresh" in imported_modules

        for imported_module, node in _import_from_nodes(command_path):
            if imported_module != "git_stage_batch.data.hunk_tracking":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & forbidden_names
            if disallowed_names:
                relative_path = command_path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_action_completion_stays_in_command_helper():
    """Include, discard, and skip should use the command flow helper."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    assert "finish_selected_change_action" not in vars(hunk_tracking)

    command_paths = (
        SRC_ROOT / "commands" / "include.py",
        SRC_ROOT / "commands" / "discard.py",
        SRC_ROOT / "commands" / "skip.py",
    )
    violations = []

    for command_path in command_paths:
        imports = _import_from_nodes(command_path)
        imported_modules = {imported_module for imported_module, _node in imports}
        assert "git_stage_batch.commands.selection.action_completion" in imported_modules

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.data.hunk_tracking":
                continue

            imported_names = {alias.name for alias in node.names}
            if "finish_selected_change_action" in imported_names:
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports "
                    "finish_selected_change_action"
                )

    assert violations == []


def test_advance_display_stays_in_command_helper():
    """Block and unblock should use the command flow helper."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    assert "advance_to_and_show_next_change" not in vars(hunk_tracking)

    command_paths = (
        SRC_ROOT / "commands" / "block_file.py",
        SRC_ROOT / "commands" / "unblock_file.py",
    )
    violations = []

    for command_path in command_paths:
        imports = _import_from_nodes(command_path)
        imported_modules = {imported_module for imported_module, _node in imports}
        assert "git_stage_batch.commands.selection.action_completion" in imported_modules

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.data.hunk_tracking":
                continue

            imported_names = {alias.name for alias in node.names}
            if "advance_to_and_show_next_change" in imported_names:
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports "
                    "advance_to_and_show_next_change"
                )

    assert violations == []


def test_line_action_refresh_header_stays_in_command_helper():
    """Include and discard line actions should use the command refresh helper."""
    command_paths = (
        SRC_ROOT / "commands" / "include.py",
        SRC_ROOT / "commands" / "discard.py",
    )
    violations = []

    for command_path in command_paths:
        imports = _import_from_nodes(command_path)
        imports_refresh_helper = False
        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.selection.selected_hunk_refresh"
                and "refresh_selected_hunk_after_line_action" in imported_names
            ):
                imports_refresh_helper = True

            if imported_module != "git_stage_batch.output":
                continue
            if "print_remaining_line_changes_header" in imported_names:
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports "
                    "print_remaining_line_changes_header"
                )

        assert imports_refresh_helper

    assert violations == []


def test_hunk_tracking_does_not_import_show_command():
    """Hunk navigation state should not depend on the show command."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }

    assert "git_stage_batch.commands.show" not in imported_modules
