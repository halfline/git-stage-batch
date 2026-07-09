"""Import-boundary checks for architecture-sensitive package seams."""

from __future__ import annotations

import ast

from .import_boundary_helpers import (
    REPO_ROOT,
    SRC_ROOT,
    external_package_child_module_import_violations as _child_import_violations,
    import_from_nodes as _import_from_nodes,
)


def test_selected_change_display_names_data_modules_at_import_sites():
    """Selected-change display should not import data modules through the package."""
    assert _child_import_violations(
        {
            "git_stage_batch.data": {
                "line_state",
                "selected_change_clear_reasons",
                "selected_file_changes",
                "selected_change_paths",
                "selected_change_store",
            },
        }
    ) == []


def test_tui_batch_menus_name_query_module_at_import_sites():
    """TUI batch menus should not import query through the batch package."""
    assert _child_import_violations(
        {
            "git_stage_batch.batch": {
                "query",
            },
        }
    ) == []


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
    assert "git_stage_batch.utils.git_command" not in imported_modules
    assert "git_stage_batch.utils.journal" not in imported_modules
    assert "git_stage_batch.utils.paths" not in imported_modules
    assert not hasattr(
        __import__(
            "git_stage_batch.core.diff_parser",
            fromlist=["write_snapshots_for_selected_file_path"],
        ),
        "write_snapshots_for_selected_file_path",
    )


def test_diff_parser_uses_core_buffer_boundary():
    """Diff parsing should depend on the core buffer primitive."""
    diff_parser_path = SRC_ROOT / "core" / "diff_parser.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(diff_parser_path)
    }

    assert "git_stage_batch.core.buffer" in imported_modules
    assert "git_stage_batch.editor" not in imported_modules


def test_patch_header_queries_stay_in_diff_parser():
    """Include and discard should use core patch header queries."""
    include_path = SRC_ROOT / "commands" / "include.py"
    include_file_path = SRC_ROOT / "commands" / "file_scope" / "include_file.py"
    discard_path = SRC_ROOT / "commands" / "discard.py"
    selected_change_batch_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_batch_discarding.py"
    )
    selected_change_discarding_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_discarding.py"
    )
    diff_parser = __import__(
        "git_stage_batch.core.diff_parser",
        fromlist=["diff_parser"],
    )

    assert {
        "patch_is_empty_file_change",
        "patch_is_file_deletion",
        "patch_is_new_file",
    } <= vars(diff_parser).keys()

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_helpers = {
        node.name for node in ast.walk(include_tree) if isinstance(node, ast.FunctionDef)
    }
    include_file_imported_diff_names = set()
    selected_change_batch_imported_diff_names = set()
    selected_change_discarding_imported_diff_names = set()

    for imported_module, node in _import_from_nodes(include_file_path):
        if imported_module != "git_stage_batch.core.diff_parser":
            continue
        include_file_imported_diff_names |= {alias.name for alias in node.names}

    for imported_module, node in _import_from_nodes(selected_change_batch_path):
        if imported_module != "git_stage_batch.core.diff_parser":
            continue
        selected_change_batch_imported_diff_names |= {alias.name for alias in node.names}

    for imported_module, node in _import_from_nodes(selected_change_discarding_path):
        if imported_module != "git_stage_batch.core.diff_parser":
            continue
        selected_change_discarding_imported_diff_names |= {
            alias.name for alias in node.names
        }

    assert "_patch_is_text_file_path_deletion" not in include_helpers
    assert "patch_is_file_deletion" in include_file_imported_diff_names
    assert "_patch_lines_contain_line" not in discard_path.read_text()
    assert "patch_is_empty_file_change" in selected_change_batch_imported_diff_names
    assert "patch_is_new_file" in selected_change_discarding_imported_diff_names
    assert "patch_is_new_file" in selected_change_batch_imported_diff_names
    assert "@@ -0,0 +0,0 @@" not in discard_path.read_text()
    assert "--- /dev/null" not in discard_path.read_text()


def test_core_modules_do_not_import_editor():
    """Core modules should not depend on editor or Git-loading helpers."""
    violations = []

    for path in (SRC_ROOT / "core").rglob("*.py"):
        imports = _import_from_nodes(path)
        for imported_module, node in imports:
            if imported_module is None:
                continue
            if imported_module == "git_stage_batch.editor" or imported_module.startswith(
                "git_stage_batch.editor."
            ):
                relative_path = path.relative_to(REPO_ROOT)
                violations.append(f"{relative_path}:{node.lineno} imports {imported_module}")

    assert violations == []


def test_live_text_lifecycle_detection_stays_out_of_core():
    """Repository-bound lifecycle detection should live under data."""
    text_lifecycle = __import__(
        "git_stage_batch.core.text_lifecycle",
        fromlist=["text_lifecycle"],
    )
    detector = __import__(
        "git_stage_batch.data.text_lifecycle_detection",
        fromlist=["text_lifecycle_detection"],
    )

    assert "detect_empty_text_lifecycle_change" not in vars(text_lifecycle)
    assert "detect_empty_text_lifecycle_change" in vars(detector)


def test_editor_package_does_not_reexport_editor_apis():
    """Editor callers should import concrete modules instead of the package."""
    editor_path = SRC_ROOT / "editor" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(editor_path)
    }
    editor = __import__("git_stage_batch.editor", fromlist=["editor"])
    facade_names = {
        "BufferInput",
        "Cursor",
        "Editor",
        "LineBuffer",
        "buffer_byte_chunks",
        "buffer_byte_count",
        "buffer_has_data",
        "buffer_matches",
        "buffer_preview",
        "choose_line_ending",
        "detect_line_ending",
        "edit_lines_as_buffer",
        "export_lines_as_buffer",
        "restore_line_endings",
        "restore_line_endings_in_chunks",
        "write_buffer_to_path",
        "write_buffer_to_working_tree_path",
    }
    violations = []

    assert imported_modules <= {"__future__"}
    assert facade_names.isdisjoint(vars(editor))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.editor":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_editor_edit_uses_piece_table_module():
    """Editor mutations should not own line piece-table storage."""
    edit_path = SRC_ROOT / "editor" / "edit.py"
    edit_text = edit_path.read_text()
    edit_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(edit_path)
    }
    piece_table = __import__(
        "git_stage_batch.editor.piece_table",
        fromlist=["piece_table"],
    )

    assert "git_stage_batch.editor.piece_table" in edit_imports
    assert "LinePieceTable" in vars(piece_table)
    assert "LineRange" in vars(piece_table)
    assert "class LinePieceTable" not in edit_text
    assert "class LineRange" not in edit_text
    assert "from array import array" not in edit_text
    assert "bytearray(" not in edit_text


def test_editor_edit_uses_line_export_module():
    """Editor mutations should not own stateless line export helpers."""
    edit_path = SRC_ROOT / "editor" / "edit.py"
    edit_text = edit_path.read_text()
    edit_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(edit_path)
    }
    edit = __import__(
        "git_stage_batch.editor.edit",
        fromlist=["edit"],
    )
    line_export = __import__(
        "git_stage_batch.editor.line_export",
        fromlist=["line_export"],
    )

    assert "git_stage_batch.editor.line_export" in edit_imports
    assert "export_lines_as_buffer" in vars(line_export)
    assert "export_lines_as_buffer" not in vars(edit)
    assert "def export_lines_as_buffer" not in edit_text
    assert "def _line_body" not in edit_text
    assert "def _line_bytes" not in edit_text
    assert "def _line_body_chunks" not in edit_text


def test_cli_package_does_not_reexport_cli_apis():
    """CLI callers should import concrete modules instead of the package."""
    cli_path = SRC_ROOT / "cli" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(cli_path)
    }
    cli = __import__("git_stage_batch.cli", fromlist=["cli"])
    facade_names = {
        "main",
    }
    violations = []

    assert imported_modules <= {"__future__"}
    if "main" in vars(cli):
        assert (
            getattr(vars(cli)["main"], "__name__", None)
            == "git_stage_batch.cli.main"
        )

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.cli":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_tui_package_does_not_reexport_tui_apis():
    """TUI callers should import concrete modules instead of the package."""
    tui_path = SRC_ROOT / "tui" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(tui_path)
    }
    tui = __import__("git_stage_batch.tui", fromlist=["tui"])
    facade_names = {
        "FileReviewSessionState",
        "FlowLocation",
        "FlowState",
        "ReviewFileEntry",
        "handle_current_file_review",
        "handle_file_browser",
        "list_review_file_entries",
        "run_interactive",
    }
    violations = []

    assert imported_modules <= {"__future__"}
    assert "__all__" not in vars(tui)
    assert facade_names.isdisjoint(vars(tui))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.tui":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_tui_file_review_package_does_not_reexport_browser_apis():
    """TUI file-review callers should import concrete modules."""
    package_path = SRC_ROOT / "tui" / "file_review" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(package_path)
    }
    file_review = __import__(
        "git_stage_batch.tui.file_review",
        fromlist=["file_review"],
    )
    facade_names = {
        "choose_review_file",
        "FileReviewSessionState",
        "ReviewFileEntry",
        "handle_current_file_review",
        "handle_file_browser",
        "list_review_file_entries",
        "prompt_block_local_only",
    }
    violations = []

    assert imported_modules == set()
    assert "__all__" not in vars(file_review)
    assert facade_names.isdisjoint(vars(file_review))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.tui.file_review":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_data_package_does_not_reexport_data_apis():
    """Data callers should import concrete modules instead of the package."""
    data_path = SRC_ROOT / "data" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(data_path)
    }
    data = __import__("git_stage_batch.data", fromlist=["data"])
    facade_names = {
        "ASSET_GROUPS",
        "AssetGroup",
        "auto_add_untracked_files",
        "CompanionAsset",
        "copy_asset_tree",
        "estimate_remaining_hunks",
        "format_id_range",
        "get_companion_asset_source",
        "get_entry_companion_assets",
        "get_file_progress",
        "get_hunk_counts",
        "list_asset_group_entries",
        "PlannedAssetInstall",
        "plan_asset_installs",
        "record_hunk_discarded",
        "record_hunk_included",
        "record_hunk_skipped",
        "read_status_summary",
        "restore_batch_refs",
        "SelectedAssetGroup",
        "select_asset_entries",
        "snapshot_batch_refs",
        "Traversable",
        "validate_asset_destination_path",
    }

    assert imported_modules <= {"__future__"}
    assert facade_names.isdisjoint(vars(data))


def test_batch_package_does_not_reexport_batch_apis():
    """Batch callers should import concrete modules instead of the package."""
    batch_path = SRC_ROOT / "batch" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(batch_path)
    }
    batch = __import__("git_stage_batch.batch", fromlist=["batch"])
    facade_names = {
        "BatchFileUpdate",
        "add_binary_file_to_batch",
        "add_file_to_batch",
        "add_files_to_batch",
        "add_gitlink_to_batch",
        "batch_exists",
        "copy_file_from_batch_to_batch",
        "create_batch",
        "delete_batch",
        "get_batch_baseline_commit",
        "get_batch_commit_sha",
        "get_batch_tree_sha",
        "get_validated_baseline_commit",
        "list_batch_files",
        "list_batch_names",
        "read_batch_metadata",
        "read_file_from_batch",
        "read_validated_batch_metadata",
        "require_batch_metadata_sane",
        "update_batch_note",
        "validate_batch_name",
    }
    violations = []

    assert imported_modules <= {"__future__"}
    assert facade_names.isdisjoint(vars(batch))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.batch":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_output_package_does_not_reexport_output_apis():
    """Output callers should import concrete modules instead of the package."""
    output_path = SRC_ROOT / "output" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(output_path)
    }
    output = __import__("git_stage_batch.output", fromlist=["output"])
    facade_names = {
        "Colors",
        "format_hotkey",
        "format_option_list",
        "print_binary_file_change",
        "print_colored_patch",
        "print_gitlink_change",
        "print_group_install_summary",
        "print_line_level_changes",
        "print_remaining_line_changes_header",
        "print_rename_change",
        "print_status_summary",
        "print_text_file_deletion_change",
    }
    violations = []

    assert imported_modules <= {"__future__"}
    assert facade_names.isdisjoint(vars(output))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.output":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & facade_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_repository_buffer_helpers_stay_in_utils_layer():
    """Repository buffer readers should live below workflow data."""
    editor = __import__("git_stage_batch.editor", fromlist=["editor"])
    repository_buffers = __import__(
        "git_stage_batch.utils.repository_buffers",
        fromlist=["repository_buffers"],
    )
    repository_buffer_names = {
        "load_git_blob_as_buffer",
        "load_git_object_as_buffer",
        "load_git_object_as_buffer_or_empty",
        "load_git_tree_files_as_buffers",
        "load_working_tree_file_as_buffer",
    }
    violations = []

    assert not (SRC_ROOT / "data" / "repository_buffers.py").exists()
    assert not (SRC_ROOT / "editor" / "git.py").exists()
    assert repository_buffer_names <= vars(repository_buffers).keys()
    assert repository_buffer_names.isdisjoint(vars(editor))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.editor":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & repository_buffer_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_selected_change_store_stays_below_orchestration_state():
    """Selected-change persistence should stay below orchestration state."""
    store_path = SRC_ROOT / "data" / "selected_change" / "store.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(store_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules
    assert "git_stage_batch.data.file_review.state" not in imported_modules


def test_selected_file_change_cache_stays_in_file_change_module():
    """Atomic selected file-change payloads should stay out of the state store."""
    selected_file_changes = __import__(
        "git_stage_batch.data.selected_change.file_changes",
        fromlist=["file_changes"],
    )
    selected_store = __import__(
        "git_stage_batch.data.selected_change.store",
        fromlist=["store"],
    )
    moved_names = {
        "cache_binary_file_change",
        "cache_gitlink_change",
        "cache_rename_change",
        "cache_text_deletion_change",
        "load_selected_binary_file",
        "load_selected_gitlink_change",
        "load_selected_rename_change",
        "load_selected_text_deletion_change",
        "read_selected_binary_data",
        "read_selected_gitlink_data",
        "read_selected_rename_data",
        "read_selected_text_deletion_data",
    }
    violations = []

    assert moved_names <= vars(selected_file_changes).keys()
    assert moved_names.isdisjoint(vars(selected_store))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.data.selected_change.store":
                continue

            imported_names = {alias.name for alias in node.names}
            stale_imports = imported_names & moved_names
            if stale_imports:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(stale_imports))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_selected_change_clear_reasons_stay_in_clear_reason_module():
    """Selected-change clear markers should stay out of the state store."""
    clear_reasons = __import__(
        "git_stage_batch.data.selected_change.clear_reasons",
        fromlist=["clear_reasons"],
    )
    selected_store = __import__(
        "git_stage_batch.data.selected_change.store",
        fromlist=["store"],
    )
    moved_names = {
        "SelectedChangeClearReason",
        "mark_selected_change_cleared_by_auto_advance_disabled",
        "mark_selected_change_cleared_by_file_list",
        "mark_selected_change_cleared_by_stale_batch_selection",
        "refuse_bare_action_after_auto_advance_disabled",
        "refuse_bare_action_after_file_list",
        "refuse_bare_action_after_stale_batch_selection",
        "selected_change_was_cleared_by_auto_advance_disabled",
        "selected_change_was_cleared_by_file_list",
        "selected_change_was_cleared_by_stale_batch_selection",
    }
    violations = []

    assert moved_names <= vars(clear_reasons).keys()
    assert moved_names.isdisjoint(vars(selected_store))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.data.selected_change.store":
                continue

            imported_names = {alias.name for alias in node.names}
            stale_imports = imported_names & moved_names
            if stale_imports:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(stale_imports))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_selected_change_path_query_stays_in_path_module():
    """Selected-change path resolution should stay out of the state store."""
    selected_paths = __import__(
        "git_stage_batch.data.selected_change.paths",
        fromlist=["paths"],
    )
    selected_store = __import__(
        "git_stage_batch.data.selected_change.store",
        fromlist=["store"],
    )
    moved_names = {"get_selected_change_file_path"}
    violations = []

    assert moved_names <= vars(selected_paths).keys()
    assert moved_names.isdisjoint(vars(selected_store))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.data.selected_change.store":
                continue

            imported_names = {alias.name for alias in node.names}
            stale_imports = imported_names & moved_names
            if stale_imports:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(stale_imports))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_undo_ref_bookkeeping_stays_in_undo_refs():
    """Undo stack ref helpers should stay out of undo snapshot storage."""
    undo = __import__(
        "git_stage_batch.data.undo",
        fromlist=["undo"],
    )
    undo_refs = __import__(
        "git_stage_batch.data.undo_refs",
        fromlist=["undo_refs"],
    )
    ref_names = {
        "SESSION_REDO_STACK_REF",
        "SESSION_UNDO_STACK_REF",
        "checkpoint_parent",
        "clear_redo_history",
        "clear_undo_history",
        "current_redo_commit",
        "current_stack_commit",
        "current_undo_commit",
        "list_restorable_refs",
    }
    old_undo_names = {
        "REF_PREFIXES",
        "_checkpoint_parent",
        "_clear_redo_history",
        "_current_redo_commit",
        "_current_stack_commit",
        "_current_undo_commit",
        "_list_refs",
        "clear_undo_history",
    }
    session_path = SRC_ROOT / "data" / "session.py"
    session_imports_undo_refs = False

    assert ref_names <= vars(undo_refs).keys()
    assert old_undo_names.isdisjoint(vars(undo))

    for imported_module, node in _import_from_nodes(session_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.data.undo_refs"
            and "clear_undo_history" in imported_names
        ):
            session_imports_undo_refs = True

    assert session_imports_undo_refs


def test_undo_worktree_capture_stays_in_worktree_module():
    """Undo worktree capture should stay out of checkpoint orchestration."""
    undo = __import__(
        "git_stage_batch.data.undo",
        fromlist=["undo"],
    )
    undo_worktree = __import__(
        "git_stage_batch.data.undo_worktree",
        fromlist=["undo_worktree"],
    )
    worktree_names = {
        "changed_worktree_paths",
        "create_blob_from_worktree_path",
        "file_mode_for_path",
        "index_update_from_path",
        "snapshot_worktree_paths",
    }
    old_undo_names = {
        "_changed_worktree_paths",
        "_create_blob_from_path",
        "_create_blob_from_worktree_path",
        "_file_mode_for_path",
        "_gitlink_oid_from_head",
        "_gitlink_oid_from_index",
        "_index_update_from_path",
        "_is_gitlink_path",
        "_snapshot_embedded_repo_path",
        "_snapshot_gitlink_path",
        "_snapshot_worktree_paths",
        "_worktree_commit_oid",
        "_worktree_is_dirty",
    }

    assert worktree_names <= vars(undo_worktree).keys()
    assert worktree_names.isdisjoint(vars(undo))
    assert old_undo_names.isdisjoint(vars(undo))


def test_undo_snapshot_restore_stays_in_restore_module():
    """Undo snapshot restoration should stay out of checkpoint orchestration."""
    undo_path = SRC_ROOT / "data" / "undo.py"
    undo = __import__(
        "git_stage_batch.data.undo",
        fromlist=["undo"],
    )
    undo_restore = __import__(
        "git_stage_batch.data.undo_restore",
        fromlist=["undo_restore"],
    )
    restore_names = {
        "read_json_from_commit",
        "restore_intent_to_add_entries",
        "restore_refs",
        "restore_tree_prefix",
        "restore_worktree",
    }
    old_undo_names = {
        "_read_json_blob",
        "_read_json_from_commit",
        "_restore_file_mode",
        "_restore_intent_to_add_entries",
        "_restore_refs",
        "_restore_tree_prefix",
        "_restore_worktree",
        "_tree_entries",
        "_write_blob_to_path",
        "_write_blob_to_worktree_path",
    }
    undo_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(undo_path)
    }

    assert restore_names <= vars(undo_restore).keys()
    assert restore_names.isdisjoint(vars(undo))
    assert old_undo_names.isdisjoint(vars(undo))
    assert "git_stage_batch.core.buffer" not in undo_imports
    assert "git_stage_batch.utils.repository_buffers" not in undo_imports
    assert "git_stage_batch.utils.file_io" not in undo_imports


def test_batch_file_display_stays_below_hunk_navigation():
    """Batch file rendering should not depend on selected-change orchestration."""
    renderer_path = SRC_ROOT / "batch" / "file_display.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(renderer_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules
    assert "git_stage_batch.data.file_review.state" not in imported_modules


def test_ignore_file_helpers_stay_in_data_layer():
    """Ignore-file editing should stay below command execution utilities."""
    ignore_files = __import__(
        "git_stage_batch.data.ignore_files",
        fromlist=["ignore_files"],
    )
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    public_names = {
        "add_file_to_gitignore",
        "add_file_to_local_exclude",
        "get_gitignore_path",
        "get_local_exclude_path",
        "promote_directory_to_glob_in_gitignore",
        "promote_directory_to_glob_in_local_exclude",
        "read_gitignore_lines",
        "remove_file_from_gitignore",
        "remove_file_from_local_exclude",
        "write_gitignore_lines",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "block_file.py": {
            "add_file_to_gitignore",
            "add_file_to_local_exclude",
        },
        SRC_ROOT / "commands" / "unblock_file.py": {
            "add_file_to_gitignore",
            "add_file_to_local_exclude",
            "promote_directory_to_glob_in_gitignore",
            "promote_directory_to_glob_in_local_exclude",
            "remove_file_from_gitignore",
            "remove_file_from_local_exclude",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(ignore_files)
    assert public_names.isdisjoint(vars(git_command))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "ignore_files.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.ignore_files":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.utils.git_command":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(
                        f"{relative_path}:{node.lineno} imports {names}"
                    )

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_git_command_module_uses_specific_filename():
    """Git command execution should not use the generic git module name."""
    assert not (SRC_ROOT / "utils" / "git.py").exists()
    assert (SRC_ROOT / "utils" / "git_command.py").exists()


def test_git_index_lock_waiting_stays_out_of_git_command_module():
    """Git command execution should delegate index-lock waiting."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    git_text = git_path.read_text()
    git_imports = _import_from_nodes(git_path)
    imports_lock_module = any(
        imported_module == "git_stage_batch.utils"
        and any(alias.name == "git_index_lock" for alias in node.names)
        for imported_module, node in git_imports
    )
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_index_lock = __import__(
        "git_stage_batch.utils.git_index_lock",
        fromlist=["git_index_lock"],
    )

    assert imports_lock_module
    assert "wait_for_git_index_lock" in vars(git_index_lock)
    assert "DEFAULT_INDEX_LOCK_WAIT_SECONDS" in vars(git_index_lock)
    assert "wait_for_git_index_lock" not in vars(git_command)
    assert "def wait_for_git_index_lock" not in git_text
    assert "def _git_index_lock_path" not in git_text
    assert "def _custom_index_lock_path" not in git_text


def test_git_object_io_stays_out_of_git_command_module():
    """Git object IO should live beside the command wrapper."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    object_io_path = SRC_ROOT / "utils" / "git_object_io.py"
    git_text = git_path.read_text()
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_object_io = __import__(
        "git_stage_batch.utils.git_object_io",
        fromlist=["git_object_io"],
    )
    public_names = {
        "GitTreeBlob",
        "create_git_blob",
        "read_git_blob",
        "read_git_blobs_as_bytes",
        "list_git_tree_blobs",
    }
    violations = []

    assert public_names <= vars(git_object_io).keys()
    assert public_names.isdisjoint(vars(git_command))

    for public_name in public_names:
        assert f"def {public_name}" not in git_text
        assert f"class {public_name}" not in git_text

    git_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_path)
    }
    assert "git_stage_batch.utils.git_object_io" not in git_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == object_io_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.git_command":
                continue

            moved_names = {alias.name for alias in node.names} & public_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_git_repository_helpers_stay_out_of_git_command_module():
    """Git repository helpers should live beside the command wrapper."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    repository_path = SRC_ROOT / "utils" / "git_repository.py"
    git_text = git_path.read_text()
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_repository = __import__(
        "git_stage_batch.utils.git_repository",
        fromlist=["git_repository"],
    )
    public_names = {
        "require_git_repository",
        "get_git_repository_root_path",
        "get_git_directory_path",
        "resolve_file_path_to_repo_relative",
    }
    violations = []

    assert public_names <= vars(git_repository).keys()
    assert public_names.isdisjoint(vars(git_command))

    for public_name in public_names:
        assert f"def {public_name}" not in git_text

    git_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_path)
    }
    assert "git_stage_batch.utils.git_repository" not in git_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == repository_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.git_command":
                continue

            moved_names = {alias.name for alias in node.names} & public_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_git_index_helpers_stay_out_of_git_command_module():
    """Git index helpers should live beside the command wrapper."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    index_path = SRC_ROOT / "utils" / "git_index.py"
    git_text = git_path.read_text()
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_index = __import__(
        "git_stage_batch.utils.git_index",
        fromlist=["git_index"],
    )
    public_names = {
        "GitIndexEntryUpdate",
        "temp_git_index",
        "git_read_tree",
        "git_update_index",
        "git_refresh_index",
        "git_update_gitlink",
        "git_update_index_entries",
        "git_write_tree",
        "git_commit_tree",
        "git_apply_to_index",
        "git_add_paths",
        "git_reset_paths",
    }
    violations = []

    assert public_names <= vars(git_index).keys()
    assert public_names.isdisjoint(vars(git_command))

    for public_name in public_names:
        assert f"def {public_name}" not in git_text
        assert f"class {public_name}" not in git_text

    git_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_path)
    }
    assert "git_stage_batch.utils.git_index" not in git_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == index_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.git_command":
                continue

            moved_names = {alias.name for alias in node.names} & public_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_git_worktree_helpers_stay_out_of_git_command_module():
    """Git worktree helpers should live beside the command wrapper."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    worktree_path = SRC_ROOT / "utils" / "git_worktree.py"
    git_text = git_path.read_text()
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_worktree = __import__(
        "git_stage_batch.utils.git_worktree",
        fromlist=["git_worktree"],
    )
    public_names = {
        "git_apply_to_worktree",
        "git_checkout_paths",
        "git_checkout_detached",
        "git_remove_paths",
        "git_reset_hard",
        "git_apply_stash",
        "git_submodule_update_checkout",
    }
    violations = []

    assert public_names <= vars(git_worktree).keys()
    assert public_names.isdisjoint(vars(git_command))

    for public_name in public_names:
        assert f"def {public_name}" not in git_text

    git_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_path)
    }
    assert "git_stage_batch.utils.git_worktree" not in git_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == worktree_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.git_command":
                continue

            moved_names = {alias.name for alias in node.names} & public_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_git_ref_helpers_stay_out_of_git_command_module():
    """Git ref helpers should live beside the command wrapper."""
    git_path = SRC_ROOT / "utils" / "git_command.py"
    refs_path = SRC_ROOT / "utils" / "git_refs.py"
    git_text = git_path.read_text()
    git_command = __import__(
        "git_stage_batch.utils.git_command",
        fromlist=["git_command"],
    )
    git_refs = __import__(
        "git_stage_batch.utils.git_refs",
        fromlist=["git_refs"],
    )
    public_names = {"update_git_refs"}
    violations = []

    assert public_names <= vars(git_refs).keys()
    assert public_names.isdisjoint(vars(git_command))
    assert "_git_ref_exists" in vars(git_refs)

    assert "def update_git_refs" not in git_text
    assert "def _git_ref_exists" not in git_text

    git_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_path)
    }
    assert "git_stage_batch.utils.git_refs" not in git_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == refs_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.git_command":
                continue

            moved_names = {alias.name for alias in node.names} & public_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_command_event_models_stay_out_of_command_runner():
    """Command event models should live beside the subprocess runner."""
    command_runner = __import__(
        "git_stage_batch.utils.command",
        fromlist=["command"],
    )
    command_events = __import__(
        "git_stage_batch.utils.command_events",
        fromlist=["command_events"],
    )
    command_path = SRC_ROOT / "utils" / "command.py"
    command_events_path = SRC_ROOT / "utils" / "command_events.py"
    event_names = {
        "CapturedFd",
        "CommandEvent",
        "CommandEventRole",
        "ExitEvent",
        "OutputEvent",
        "StdinClosedEvent",
    }
    runner_names = {
        "run_command",
        "start_command",
        "stream_command",
    }
    command_imports = _import_from_nodes(command_path)
    imports_event_module = any(
        imported_module == "git_stage_batch.utils"
        and any(alias.name == "command_events" for alias in node.names)
        for imported_module, node in command_imports
    )
    violations = []

    assert event_names <= vars(command_events).keys()
    assert event_names.isdisjoint(vars(command_runner))
    assert runner_names <= vars(command_runner).keys()
    assert "StreamingProcess" not in vars(command_runner)
    assert imports_event_module

    for path in SRC_ROOT.rglob("*.py"):
        if path == command_events_path:
            continue

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.utils.command":
                continue

            moved_names = {alias.name for alias in node.names} & event_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_command_streaming_process_state_stays_out_of_command_runner():
    """Command process state should live beside the subprocess runner."""
    command_runner = __import__(
        "git_stage_batch.utils.command",
        fromlist=["command"],
    )
    command_streaming = __import__(
        "git_stage_batch.utils.command_streaming",
        fromlist=["command_streaming"],
    )
    command_path = SRC_ROOT / "utils" / "command.py"
    command_streaming_path = SRC_ROOT / "utils" / "command_streaming.py"
    command_text = command_path.read_text()
    command_streaming_text = command_streaming_path.read_text()
    process_names = {
        "SpawnedProcess",
        "StreamingProcess",
        "terminate_then_kill",
    }
    runner_names = {
        "run_command",
        "start_command",
        "stream_command",
    }
    command_imports = _import_from_nodes(command_path)
    command_streaming_imports = _import_from_nodes(command_streaming_path)
    imports_streaming_module = any(
        imported_module == "git_stage_batch.utils"
        and any(alias.name == "command_streaming" for alias in node.names)
        for imported_module, node in command_imports
    )
    streaming_imports_events = any(
        imported_module == "git_stage_batch.utils"
        and any(alias.name == "command_events" for alias in node.names)
        for imported_module, node in command_streaming_imports
    )

    assert process_names <= vars(command_streaming).keys()
    assert process_names.isdisjoint(vars(command_runner))
    assert runner_names <= vars(command_runner).keys()
    assert imports_streaming_module
    assert streaming_imports_events
    assert "import selectors" not in command_text
    assert "import signal" not in command_text
    assert "class StreamingProcess" not in command_text
    assert "class StreamingProcess" in command_streaming_text


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


def test_file_review_fingerprints_stay_out_of_state_module():
    """File-review fingerprinting should live beside persisted state."""
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    fingerprints = __import__(
        "git_stage_batch.data.file_review.fingerprints",
        fromlist=["fingerprints"],
    )
    public_names = {
        "compute_current_file_review_diff_fingerprint",
        "fingerprint_selected_file_view",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "suggest_fixup.py": {
            "compute_current_file_review_diff_fingerprint",
        },
        SRC_ROOT / "data" / "file_review" / "freshness.py": public_names,
        SRC_ROOT / "output" / "file_review_state_builder.py": public_names,
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(fingerprints)
    assert public_names.isdisjoint(vars(review_state))

    state_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(
            SRC_ROOT / "data" / "file_review" / "state.py"
        )
    }
    assert "git_stage_batch.core.buffer" not in state_imports
    assert "git_stage_batch.data.line_state" not in state_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "fingerprints.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.fingerprints":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_file_review_freshness_stays_out_of_state_module():
    """File-review freshness checks should live beside validation state."""
    freshness = __import__(
        "git_stage_batch.data.file_review.freshness",
        fromlist=["freshness"],
    )
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    public_names = {
        "review_state_matches_action",
        "selected_batch_review_matches_reset_state",
        "selected_change_kind_matches_review_source",
        "selected_change_matches_review_state",
    }
    expected_imports = {
        SRC_ROOT / "data" / "status_summary.py": {
            "selected_change_matches_review_state",
        },
        SRC_ROOT / "data" / "file_review" / "action_scope.py": {
            "review_state_matches_action",
            "selected_change_kind_matches_review_source",
            "selected_change_matches_review_state",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(freshness)
    assert public_names.isdisjoint(vars(review_state))

    state_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(
            SRC_ROOT / "data" / "file_review" / "state.py"
        )
    }
    assert "git_stage_batch.batch" not in state_imports
    assert "git_stage_batch.data.file_review.fingerprints" not in state_imports
    assert "git_stage_batch.data.selected_change.snapshots" not in state_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "freshness.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.freshness":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_file_review_selection_validation_stays_out_of_state_module():
    """File-review selection validation should live beside state."""
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    selection_validation = __import__(
        "git_stage_batch.data.file_review.selection_validation",
        fromlist=["selection_validation"],
    )
    public_names = {
        "shown_review_selections_for_action",
        "validate_review_scoped_line_selection",
    }
    expected_imports = {
        SRC_ROOT / "data" / "status_summary.py": {
            "shown_review_selections_for_action",
        },
        SRC_ROOT / "data" / "file_review" / "batch_selection.py": {
            "validate_review_scoped_line_selection",
        },
        SRC_ROOT / "data" / "file_review" / "action_scope.py": public_names,
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(selection_validation)
    assert public_names.isdisjoint(vars(review_state))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "selection_validation.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.selection_validation":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_file_review_records_stay_out_of_state_module():
    """File-review record types should live beside validation state."""
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    records = __import__(
        "git_stage_batch.data.file_review.records",
        fromlist=["records"],
    )
    public_names = {
        "ActionScopeResolution",
        "FileReviewAction",
        "FileReviewSelectionState",
        "FileReviewState",
        "ImplicitLiveToBatchFileActionResult",
        "ReviewSource",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "apply_from.py": {"FileReviewAction"},
        SRC_ROOT / "commands" / "discard.py": {
            "FileReviewAction",
            "ReviewSource",
        },
        SRC_ROOT / "commands" / "discard_from.py": {"FileReviewAction"},
        SRC_ROOT / "commands" / "include.py": {"FileReviewAction"},
        SRC_ROOT / "commands" / "include_from.py": {"FileReviewAction"},
        SRC_ROOT / "commands" / "batch_source" / "reset_selection.py": {
            "FileReviewAction",
        },
        SRC_ROOT / "commands" / "show.py": {"ReviewSource"},
        SRC_ROOT / "commands" / "show_from.py": {"ReviewSource"},
        SRC_ROOT / "commands" / "batch_source" / "replacement_previews.py": {
            "FileReviewAction",
        },
        SRC_ROOT / "commands" / "skip.py": {"FileReviewAction"},
        SRC_ROOT / "data" / "status_summary.py": {
            "FileReviewAction",
            "ReviewSource",
        },
        SRC_ROOT / "data" / "file_review" / "batch_selection.py": {"FileReviewAction"},
        SRC_ROOT / "output" / "file_review.py": {
            "FileReviewAction",
            "FileReviewState",
            "ReviewSource",
        },
        SRC_ROOT / "output" / "file_review_action_selections.py": {
            "ReviewSource",
        },
        SRC_ROOT / "output" / "file_review_state_builder.py": {
            "FileReviewAction",
            "FileReviewSelectionState",
            "FileReviewState",
            "ReviewSource",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(records)
    assert public_names.isdisjoint(vars(review_state))

    state_text = (SRC_ROOT / "data" / "file_review" / "state.py").read_text()
    assert "from . import records as _records" in state_text

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "records.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.records":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_file_review_output_does_not_import_hunk_navigation():
    """File-review output should not depend on hunk navigation."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_file_review_state_builder_uses_page_selection_module():
    """File-review state assembly should not own page-spec parsing."""
    review_state_builder_path = SRC_ROOT / "output" / "file_review_state_builder.py"
    review_state_builder_text = review_state_builder_path.read_text()
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_state_builder_path)
    }
    file_review_pages = __import__(
        "git_stage_batch.data.file_review.pages",
        fromlist=["file_review_pages"],
    )

    assert "git_stage_batch.data.file_review.pages" in imported_modules
    assert "parse_page_selection" in vars(file_review_pages)
    assert "normalize_page_spec" in vars(file_review_pages)
    assert "def parse_page_selection" not in review_state_builder_text
    assert "def normalize_page_spec" not in review_state_builder_text
    assert "parse_positive_selection" not in review_state_builder_text


def test_file_review_model_builder_uses_layout_module():
    """File-review model construction should not own terminal layout sizing."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_output_text = review_output_path.read_text()
    review_model_builder_path = SRC_ROOT / "output" / "file_review_model_builder.py"
    review_model_builder_text = review_model_builder_path.read_text()
    imports = _import_from_nodes(review_model_builder_path)
    imports_layout_module = any(
        imported_module == "git_stage_batch.output"
        and any(alias.name == "file_review_layout" for alias in node.names)
        for imported_module, node in imports
    )
    file_review_model_builder = __import__(
        "git_stage_batch.output.file_review_model_builder",
        fromlist=["file_review_model_builder"],
    )
    file_review_layout = __import__(
        "git_stage_batch.output.file_review_layout",
        fromlist=["file_review_layout"],
    )

    assert imports_layout_module
    assert "body_budget" in vars(file_review_layout)
    assert "body_budget" not in vars(file_review_model_builder)
    assert "git_stage_batch.output.file_review_layout" not in {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }
    assert "def _body_budget" not in review_model_builder_text
    assert "review_terminal_size" not in review_model_builder_text
    assert "DEFAULT_NON_TTY_REVIEW_LINES" not in review_model_builder_text
    assert "REVIEW_HEADER_LINES" not in review_model_builder_text
    assert "file_review_layout" not in review_output_text


def test_file_review_output_uses_model_module():
    """File-review output should not own passive model records."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_output_text = review_output_path.read_text()
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }
    file_review_model = __import__(
        "git_stage_batch.output.file_review_model",
        fromlist=["file_review_model"],
    )
    model_names = {
        "FileReviewModel",
        "FileReviewPage",
        "FileReviewView",
        "ReviewChange",
        "ReviewChangeFragment",
    }

    assert "git_stage_batch.output.file_review_model" in imported_modules
    assert model_names <= vars(file_review_model).keys()
    for model_name in model_names:
        assert f"class {model_name}" not in review_output_text
    assert "from dataclasses import dataclass" not in review_output_text


def test_file_review_output_uses_display_id_module():
    """File-review output should not own row display-ID mapping."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_output_text = review_output_path.read_text()
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }
    file_review_display_ids = __import__(
        "git_stage_batch.output.file_review_display_ids",
        fromlist=["file_review_display_ids"],
    )

    assert "git_stage_batch.output.file_review_display_ids" in imported_modules
    assert "display_ids_for_rows" in vars(file_review_display_ids)
    assert "def display_ids_for_rows" not in review_output_text
    assert "def _display_ids_for_rows" not in review_output_text


def test_file_review_callers_use_model_builder():
    """File-review callers should not import model construction from rendering."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_output_text = review_output_path.read_text()
    caller_paths = (
        SRC_ROOT / "commands" / "show.py",
        SRC_ROOT / "commands" / "show_from.py",
        SRC_ROOT / "output" / "file_review_list.py",
    )
    file_review_model_builder = __import__(
        "git_stage_batch.output.file_review_model_builder",
        fromlist=["file_review_model_builder"],
    )
    public_names = {"build_file_review_model"}
    direct_model_builder_imports: dict[str, set[str]] = {}
    old_renderer_imports: dict[str, set[str]] = {}

    for path in caller_paths:
        relative_path = str(path.relative_to(REPO_ROOT))
        direct_model_builder_imports[relative_path] = set()
        old_renderer_imports[relative_path] = set()
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.output.file_review_model_builder":
                direct_model_builder_imports[relative_path] |= (
                    imported_names & public_names
                )
            if imported_module == "git_stage_batch.output.file_review":
                old_renderer_imports[relative_path] |= imported_names & public_names

    assert public_names <= vars(file_review_model_builder).keys()
    assert "def build_file_review_model" not in review_output_text
    assert direct_model_builder_imports == {
        "src/git_stage_batch/commands/show.py": public_names,
        "src/git_stage_batch/commands/show_from.py": public_names,
        "src/git_stage_batch/output/file_review_list.py": public_names,
    }
    assert old_renderer_imports == {
        "src/git_stage_batch/commands/show.py": set(),
        "src/git_stage_batch/commands/show_from.py": set(),
        "src/git_stage_batch/output/file_review_list.py": set(),
    }


def test_file_review_output_uses_action_selection_module():
    """File-review output should not own page action selection mapping."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_state_builder_path = SRC_ROOT / "output" / "file_review_state_builder.py"
    review_output_text = review_output_path.read_text()
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_output_path)
    }
    state_builder_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(review_state_builder_path)
    }
    file_review_action_selections = __import__(
        "git_stage_batch.output.file_review_action_selections",
        fromlist=["file_review_action_selections"],
    )
    public_names = {
        "change_index_containing_review_display_ids",
        "change_is_live_splittable",
        "display_ids_for_change_pages",
        "pages_containing_review_display_ids",
        "selection_ids_for_display_ids",
        "shown_line_action_selections",
    }

    assert "git_stage_batch.output.file_review_action_selections" in imported_modules
    assert "git_stage_batch.output.file_review_action_selections" in state_builder_imports
    assert public_names <= vars(file_review_action_selections).keys()
    assert "def _shown_line_action_selections" not in review_output_text
    assert "def _display_ids_for_change_pages" not in review_output_text
    assert "def _pages_containing_review_display_ids" not in review_output_text
    assert "def _selection_ids_for_display_ids" not in review_output_text


def test_show_commands_use_file_review_state_builder():
    """Show commands should not import review state assembly from rendering."""
    review_output_path = SRC_ROOT / "output" / "file_review.py"
    review_output_text = review_output_path.read_text()
    show_paths = (
        SRC_ROOT / "commands" / "show.py",
        SRC_ROOT / "commands" / "show_from.py",
    )
    file_review_state_builder = __import__(
        "git_stage_batch.output.file_review_state_builder",
        fromlist=["file_review_state_builder"],
    )
    public_names = {
        "make_file_review_state",
        "resolve_default_review_pages",
    }
    direct_state_builder_imports: dict[str, set[str]] = {}
    old_renderer_imports: dict[str, set[str]] = {}

    for path in show_paths:
        direct_state_builder_imports[str(path.relative_to(REPO_ROOT))] = set()
        old_renderer_imports[str(path.relative_to(REPO_ROOT))] = set()
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.output.file_review_state_builder":
                direct_state_builder_imports[str(path.relative_to(REPO_ROOT))] |= (
                    imported_names & public_names
                )
            if imported_module == "git_stage_batch.output.file_review":
                old_renderer_imports[str(path.relative_to(REPO_ROOT))] |= (
                    imported_names & public_names
                )

    assert public_names <= vars(file_review_state_builder).keys()
    assert "def make_file_review_state" not in review_output_text
    assert "def resolve_default_review_pages" not in review_output_text
    assert direct_state_builder_imports == {
        "src/git_stage_batch/commands/show.py": public_names,
        "src/git_stage_batch/commands/show_from.py": public_names,
    }
    assert old_renderer_imports == {
        "src/git_stage_batch/commands/show.py": set(),
        "src/git_stage_batch/commands/show_from.py": set(),
    }


def test_file_review_action_commands_stay_out_of_state_module():
    """File-review command text should live beside validation state."""
    action_commands = __import__(
        "git_stage_batch.data.file_review.action_commands",
        fromlist=["action_commands"],
    )
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    public_names = {
        "batch_source_action_command",
        "line_action_command",
        "live_to_batch_action_command",
        "show_command_for_review_state",
    }
    expected_imports = {
        SRC_ROOT / "data" / "file_review" / "action_scope.py": public_names,
        SRC_ROOT / "output" / "file_review.py": {"line_action_command"},
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(action_commands)
    assert public_names.isdisjoint(vars(review_state))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "action_commands.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.action_commands":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_file_review_action_scope_stays_out_of_state_module():
    """File-review action scope should live beside persisted state."""
    action_scope = __import__(
        "git_stage_batch.data.file_review.action_scope",
        fromlist=["action_scope"],
    )
    review_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )
    public_names = {
        "ReviewScopedSelectionError",
        "finish_review_scoped_line_action",
        "fresh_batch_review_selections_for_action",
        "line_action_came_from_partial_review",
        "refuse_ambiguous_bare_action_after_partial_file_review",
        "refuse_live_action_for_batch_selection",
        "resolve_batch_source_action_scope",
        "resolve_live_line_action_scope",
        "resolve_live_to_batch_action_scope",
        "resolve_review_file_for_bare_whole_file_action",
        "validate_implicit_live_to_batch_file_action",
        "validate_pathless_review_line_action",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "batch_source" / "action_completion.py": {
            "finish_review_scoped_line_action",
        },
        SRC_ROOT / "commands" / "batch_source" / "action_context.py": {
            "resolve_batch_source_action_scope",
        },
        SRC_ROOT / "commands" / "batch_source" / "reset_selection.py": {
            "resolve_batch_source_action_scope",
        },
        SRC_ROOT / "commands" / "discard.py": {
            "finish_review_scoped_line_action",
            "refuse_ambiguous_bare_action_after_partial_file_review",
            "refuse_live_action_for_batch_selection",
            "resolve_live_line_action_scope",
            "resolve_live_to_batch_action_scope",
        },
        SRC_ROOT / "commands" / "include.py": {
            "finish_review_scoped_line_action",
            "refuse_ambiguous_bare_action_after_partial_file_review",
            "refuse_live_action_for_batch_selection",
            "resolve_live_line_action_scope",
            "resolve_live_to_batch_action_scope",
        },
        SRC_ROOT / "commands" / "skip.py": {
            "finish_review_scoped_line_action",
            "refuse_ambiguous_bare_action_after_partial_file_review",
            "refuse_live_action_for_batch_selection",
            "resolve_live_line_action_scope",
        },
        SRC_ROOT / "data" / "file_review" / "batch_selection.py": {
            "fresh_batch_review_selections_for_action",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(action_scope)
    assert public_names.isdisjoint(vars(review_state))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "action_scope.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.action_scope":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.data.file_review.state":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_selection_does_not_import_hunk_navigation():
    """Batch selection should use focused data helpers instead of hunk navigation."""
    selection_path = SRC_ROOT / "batch" / "selection.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(selection_path)
    }

    assert "git_stage_batch.data.hunk_tracking" not in imported_modules


def test_batch_review_selection_translation_stays_in_file_review_package():
    """Review-aware batch selection translation should live under file_review."""
    batch_selection = __import__(
        "git_stage_batch.batch.selection",
        fromlist=["selection"],
    )
    review_selection = __import__(
        "git_stage_batch.data.file_review.batch_selection",
        fromlist=["batch_selection"],
    )
    public_names = {
        "translate_batch_file_gutter_ids_to_selection_ids",
        "translate_reset_batch_file_gutter_ids_to_selection_ranges",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "batch_source" / "action_selection.py": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
        SRC_ROOT / "commands" / "batch_source" / "reset_selection.py": {
            "translate_reset_batch_file_gutter_ids_to_selection_ranges",
        },
        SRC_ROOT / "commands" / "show_from.py": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(review_selection)
    assert public_names.isdisjoint(vars(batch_selection))

    selection_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(
            SRC_ROOT / "batch" / "selection.py"
        )
    }
    assert "git_stage_batch.data.file_review.state" not in selection_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "file_review" / "batch_selection.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.file_review.batch_selection":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.batch.selection":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


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


def test_status_remaining_hunk_estimate_stays_in_data_module():
    """Status summary should delegate live-diff remaining hunk accounting."""
    remaining_hunks = __import__(
        "git_stage_batch.data.remaining_hunks",
        fromlist=["remaining_hunks"],
    )
    status = __import__(
        "git_stage_batch.commands.status",
        fromlist=["status"],
    )
    status_path = SRC_ROOT / "commands" / "status.py"
    status_summary_path = SRC_ROOT / "data" / "status_summary.py"
    public_names = {
        "estimate_remaining_hunks",
    }
    disallowed_imports = {
        "git_stage_batch.core.diff_parser": {
            "acquire_unified_diff",
        },
        "git_stage_batch.core.hashing": {
            "compute_binary_file_hash",
            "compute_gitlink_change_hash",
            "compute_rename_change_hash",
            "compute_stable_hunk_hash_from_lines",
            "compute_text_file_deletion_hash",
        },
        "git_stage_batch.core.models": {
            "BinaryFileChange",
            "GitlinkChange",
            "RenameChange",
            "TextFileDeletionChange",
        },
        "git_stage_batch.data.change_freshness": {
            "text_deletion_change_is_batched",
        },
        "git_stage_batch.data.live_diff": {
            "stream_live_git_diff",
        },
        "git_stage_batch.utils.file_io": {
            "is_path_blocked",
            "read_file_paths_file",
            "read_text_file_line_set",
        },
        "git_stage_batch.utils.paths": {
            "get_block_list_file_path",
            "get_blocked_files_file_path",
            "get_context_lines",
        },
    }
    inspected_paths = {
        status_path,
        status_summary_path,
    }
    direct_estimate_imports = {
        path: set()
        for path in inspected_paths
    }
    remaining_imports = {
        path: set()
        for path in inspected_paths
    }

    for path in inspected_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.remaining_hunks":
                remaining_imports[path] |= {
                    alias.asname or alias.name
                    for alias in node.names
                }
            direct_estimate_imports[path] |= imported_names & disallowed_imports.get(
                imported_module,
                set(),
            )

    assert public_names <= vars(remaining_hunks).keys()
    assert "estimate_remaining_hunks" not in vars(status)
    assert remaining_imports == {
        status_path: set(),
        status_summary_path: {"_estimate_remaining_hunks"},
    }
    assert direct_estimate_imports == {
        status_path: set(),
        status_summary_path: set(),
    }


def test_status_summary_reader_owns_status_payload_assembly():
    """Status payload assembly should live outside the command module."""
    status_summary = __import__(
        "git_stage_batch.data.status_summary",
        fromlist=["status_summary"],
    )
    status = __import__(
        "git_stage_batch.commands.status",
        fromlist=["status"],
    )
    status_path = SRC_ROOT / "commands" / "status.py"
    public_names = {
        "read_status_summary",
    }
    old_status_names = {
        "_read_batch_review_display_ids",
        "_read_file_review_summary",
        "_read_live_review_display_ids",
        "_read_selected_change_summary",
        "_read_skipped_hunks",
        "_read_status_summary",
        "_selected_change_is_stale",
    }
    disallowed_imports = {
        "git_stage_batch.batch.query": {
            "read_batch_metadata",
        },
        "git_stage_batch.data.batch_selected_changes": {
            "selected_batch_binary_batch_name",
            "selected_batch_binary_file_for_batch",
        },
        "git_stage_batch.data.change_freshness": {
            "binary_file_change_is_stale",
            "gitlink_change_is_stale",
            "rename_change_is_stale",
            "text_deletion_change_is_stale",
        },
        "git_stage_batch.data.file_review.freshness": {
            "selected_change_matches_review_state",
        },
        "git_stage_batch.data.file_review.records": {
            "FileReviewAction",
            "ReviewSource",
        },
        "git_stage_batch.data.file_review.selection_validation": {
            "shown_review_selections_for_action",
        },
        "git_stage_batch.data.file_review.state": {
            "read_last_file_review_state",
        },
        "git_stage_batch.data.line_state": {
            "load_line_changes_from_state",
        },
        "git_stage_batch.data.remaining_hunks": {
            "estimate_remaining_hunks",
        },
        "git_stage_batch.data.selected_change.clear_reasons": {
            "mark_selected_change_cleared_by_stale_batch_selection",
        },
        "git_stage_batch.data.selected_change.file_changes": {
            "load_selected_binary_file",
            "load_selected_gitlink_change",
            "load_selected_rename_change",
            "load_selected_text_deletion_change",
        },
        "git_stage_batch.data.selected_change.lifecycle": {
            "clear_selected_change_state_files",
        },
        "git_stage_batch.data.selected_change.snapshots": {
            "snapshots_are_stale",
        },
        "git_stage_batch.data.selected_change.store": {
            "read_selected_change_kind",
        },
        "git_stage_batch.data.session": {
            "get_iteration_count",
        },
        "git_stage_batch.utils.file_io": {
            "count_nonblank_text_file_lines",
            "stream_text_file_lines",
        },
        "git_stage_batch.utils.paths": {
            "get_discarded_hunks_file_path",
            "get_included_hunks_file_path",
            "get_line_changes_json_file_path",
            "get_selected_hunk_patch_file_path",
            "get_skipped_hunks_jsonl_file_path",
        },
    }
    status_tree = ast.parse(status_path.read_text(), filename=str(status_path))
    status_names = {
        node.name
        for node in ast.walk(status_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    imported_status_summary_names = set()
    direct_payload_imports = set()

    for imported_module, node in _import_from_nodes(status_path):
        imported_names = {alias.name for alias in node.names}
        if imported_module == "git_stage_batch.data.status_summary":
            imported_status_summary_names |= {
                alias.asname or alias.name
                for alias in node.names
            }
        direct_payload_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    assert public_names <= vars(status_summary).keys()
    assert "read_status_summary" not in vars(status)
    assert old_status_names.isdisjoint(status_names)
    assert "_read_status_summary" in imported_status_summary_names
    assert direct_payload_imports == set()


def test_active_session_query_stays_in_session_data():
    """Callers should ask session data whether a session is active."""
    caller_paths = (
        SRC_ROOT / "cli" / "execution.py",
        SRC_ROOT / "commands" / "block_file.py",
        SRC_ROOT / "commands" / "start.py",
        SRC_ROOT / "commands" / "status.py",
        SRC_ROOT / "commands" / "unblock_file.py",
        SRC_ROOT / "tui" / "interactive.py",
    )
    session = __import__(
        "git_stage_batch.data.session",
        fromlist=["session"],
    )
    public_names = {
        "active_session_marker_path",
        "session_is_active",
    }

    assert public_names <= vars(session).keys()

    for caller_path in caller_paths:
        caller_tree = ast.parse(caller_path.read_text(), filename=str(caller_path))
        caller_names = {
            node.name
            for node in ast.walk(caller_tree)
            if isinstance(node, ast.ClassDef | ast.FunctionDef)
        }
        caller_imported_session_names = set()
        caller_imported_path_names = set()

        for imported_module, node in _import_from_nodes(caller_path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.session":
                caller_imported_session_names |= imported_names
            if imported_module == "git_stage_batch.utils.paths":
                caller_imported_path_names |= imported_names

        assert "_session_marker_path" not in caller_names
        assert "session_is_active" in caller_imported_session_names
        assert "get_abort_head_file_path" not in caller_imported_path_names
        assert "get_state_directory_path" not in caller_imported_path_names
        assert "session/abort/head.txt" not in caller_path.read_text()


def test_status_prompt_rendering_stays_in_output_module():
    """Status prompt formatting should stay out of the command module."""
    status_path = SRC_ROOT / "commands" / "status.py"
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    prompt = __import__(
        "git_stage_batch.output.status_prompt",
        fromlist=["status_prompt"],
    )
    public_names = {
        "DEFAULT_PROMPT_FORMAT",
        "prompt_needs_status_summary",
        "render_prompt_status",
    }
    old_status_names = {
        "DEFAULT_PROMPT_FORMAT",
        "_LIGHT_PROMPT_FIELDS",
        "_PROMPT_FIELDS",
        "_prompt_field_names",
        "_prompt_values",
        "_render_prompt_status",
    }
    status_module = __import__(
        "git_stage_batch.commands.status",
        fromlist=["status"],
    )

    assert public_names <= vars(prompt).keys()

    status_tree = ast.parse(status_path.read_text(), filename=str(status_path))
    status_names = {
        node.name
        for node in ast.walk(status_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    status_imported_prompt_names = set()
    parser_imported_prompt_names = set()

    for imported_module, node in _import_from_nodes(status_path):
        if imported_module != "git_stage_batch.output.status_prompt":
            continue
        status_imported_prompt_names |= {alias.name for alias in node.names}

    for imported_module, node in _import_from_nodes(parser_path):
        if imported_module != "git_stage_batch.output.status_prompt":
            continue
        parser_imported_prompt_names |= {alias.name for alias in node.names}

    assert old_status_names.isdisjoint(vars(status_module))
    assert old_status_names.isdisjoint(status_names)
    assert {
        "prompt_needs_status_summary",
        "render_prompt_status",
    } <= status_imported_prompt_names
    assert "DEFAULT_PROMPT_FORMAT" in parser_imported_prompt_names


def test_status_summary_rendering_stays_in_output_module():
    """Human-readable status rendering should stay out of the command module."""
    status_output = __import__(
        "git_stage_batch.output.status",
        fromlist=["status"],
    )
    command_status = __import__(
        "git_stage_batch.commands.status",
        fromlist=["status"],
    )
    status_path = SRC_ROOT / "commands" / "status.py"
    public_names = {
        "print_status_summary",
    }
    old_status_names = {
        "_selected_kind_label",
    }
    old_status_snippets = {
        "Progress this iteration:",
        "Skipped hunks:",
        "Current hunk:",
        "Last file review:",
    }
    disallowed_imports = {
        "git_stage_batch.data.progress": {
            "format_id_range",
        },
        "git_stage_batch.data.selected_change.store": {
            "SelectedChangeKind",
        },
    }
    status_tree = ast.parse(status_path.read_text(), filename=str(status_path))
    status_names = {
        node.name
        for node in ast.walk(status_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    imported_status_output_names = set()
    direct_render_imports = set()

    for imported_module, node in _import_from_nodes(status_path):
        imported_names = {alias.name for alias in node.names}
        if imported_module == "git_stage_batch.output.status":
            imported_status_output_names |= {
                alias.asname or alias.name
                for alias in node.names
            }
        direct_render_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    status_text = status_path.read_text()

    assert public_names <= vars(status_output).keys()
    assert "print_status_summary" not in vars(command_status)
    assert old_status_names.isdisjoint(status_names)
    assert all(snippet not in status_text for snippet in old_status_snippets)
    assert "_print_status_summary" in imported_status_output_names
    assert direct_render_imports == set()


def test_install_asset_summary_rendering_stays_in_output_module():
    """Install asset summary rendering should stay out of the command module."""
    install_output = __import__(
        "git_stage_batch.output.install_assets",
        fromlist=["install_assets"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    public_names = {
        "print_group_install_summary",
    }
    old_command_names = {
        "_print_group_install_summary",
    }
    old_command_snippets = {
        "✓ Installed",
        "Installed {kind}",
        "file=sys.stderr",
        "import sys",
    }
    command_tree = ast.parse(command_path.read_text(), filename=str(command_path))
    command_names = {
        node.name
        for node in ast.walk(command_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    imported_install_output_names = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.output.install_assets":
            imported_install_output_names |= {
                alias.asname or alias.name
                for alias in node.names
            }

    command_text = command_path.read_text()

    assert public_names <= vars(install_output).keys()
    assert "print_group_install_summary" not in vars(install_assets)
    assert old_command_names.isdisjoint(command_names)
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert "_print_group_install_summary" in imported_install_output_names


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
    assert "git_stage_batch.data.undo" not in parser_imports
    assert (
        "git_stage_batch.commands.file_scope.multi_file_actions"
        in parser_imports
    )
    assert "git_stage_batch.data.hunk_tracking" in helper_imports
    assert "git_stage_batch.data.undo" in helper_imports
    assert not hasattr(
        __import__(
            "git_stage_batch.cli.argument_parser",
            fromlist=["argument_parser"],
        ),
        "_run_for_each_file",
    )


def test_argument_parser_delegates_git_help_display():
    """Parser construction should not own Git help manpage lookup."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    git_help_path = SRC_ROOT / "cli" / "git_help.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    git_help_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(git_help_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    git_help = __import__(
        "git_stage_batch.cli.git_help",
        fromlist=["git_help"],
    )
    moved_names = {
        "_build_manpath_with_packaged_page",
        "_git_help_name_for_help_topic",
        "_manpage_name_for_help_topic",
        "_resolve_default_manpath",
        "_show_git_stage_batch_help",
        "_try_git_help_with_environment",
        "_with_real_manpath_root",
    }

    assert "git_stage_batch.cli.git_help" in parser_imports
    assert "git_stage_batch.utils.command" not in parser_imports
    assert "git_stage_batch.utils.git_command" not in parser_imports
    assert "git_stage_batch.utils.command" in git_help_imports
    assert "git_stage_batch.utils.git_command" in git_help_imports
    assert "GitHelpArgumentParser" in vars(git_help)
    assert moved_names.isdisjoint(vars(parser))
    assert "_show_git_stage_batch_help(" not in parser_text
    assert "import tempfile" not in parser_text


def test_argument_parser_delegates_quick_action_expansion():
    """Parser construction should not own shortcut token expansion."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    quick_actions = __import__(
        "git_stage_batch.cli.quick_actions",
        fromlist=["quick_actions"],
    )

    assert "git_stage_batch.cli.quick_actions" in parser_imports
    assert "expand_quick_actions" in vars(quick_actions)
    assert "QUICK_ACTIONS" in vars(quick_actions)
    assert "QUICK_ACTIONS" not in vars(parser)
    assert "quick_actions = {" not in parser_text
    assert "'if': ['include', '--file']" not in parser_text


def test_argument_parser_delegates_show_dispatch():
    """Parser construction should not own show workflow dispatch."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    show_dispatch_path = SRC_ROOT / "cli" / "show_dispatch.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    show_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(show_dispatch_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    show_dispatch = __import__(
        "git_stage_batch.cli.show_dispatch",
        fromlist=["show_dispatch"],
    )
    dispatch_helper_names = {
        "_dispatch_show_from_batch",
        "_dispatch_show_live",
        "_validate_show_page_request",
    }
    show_runtime_imports = {
        "git_stage_batch.batch.query",
        "git_stage_batch.batch.source_selector",
        "git_stage_batch.batch.validation",
        "git_stage_batch.commands.show",
        "git_stage_batch.commands.show_from",
    }

    assert "git_stage_batch.cli.show_dispatch" in parser_imports
    assert show_runtime_imports.isdisjoint(parser_imports)
    assert show_runtime_imports <= show_dispatch_imports
    assert "git_stage_batch.cli.file_scope" in show_dispatch_imports
    assert "git_stage_batch.cli.replacement_input" in show_dispatch_imports
    assert "dispatch_show_command" in vars(show_dispatch)
    assert dispatch_helper_names <= vars(show_dispatch).keys()
    assert dispatch_helper_names.isdisjoint(vars(parser))
    assert "def dispatch_show(" not in parser_text
    assert "read_batch_metadata(" not in parser_text
    assert "command_show_from_batch(" not in parser_text
    assert "command_show(" not in parser_text


def test_argument_parser_delegates_skip_dispatch():
    """Parser construction should not own skip workflow dispatch."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    skip_dispatch_path = SRC_ROOT / "cli" / "skip_dispatch.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    skip_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(skip_dispatch_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    skip_dispatch = __import__(
        "git_stage_batch.cli.skip_dispatch",
        fromlist=["skip_dispatch"],
    )

    assert "git_stage_batch.cli.skip_dispatch" in parser_imports
    assert "git_stage_batch.commands.skip" not in parser_imports
    assert "git_stage_batch.commands.skip" in skip_dispatch_imports
    assert (
        "git_stage_batch.commands.file_scope.multi_file_actions"
        in skip_dispatch_imports
    )
    assert "git_stage_batch.cli.file_scope" in skip_dispatch_imports
    assert "dispatch_skip_command" in vars(skip_dispatch)
    assert "command_skip" not in vars(parser)
    assert "command_skip_file" not in vars(parser)
    assert "command_skip_line" not in vars(parser)
    assert "def dispatch_skip(" not in parser_text
    assert "skip_each_resolved_file(" not in parser_text
    assert "command_skip(" not in parser_text
    assert "command_skip_file(" not in parser_text
    assert "command_skip_line(" not in parser_text


def test_argument_parser_delegates_apply_dispatch():
    """Parser construction should not own apply workflow dispatch."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    apply_dispatch_path = SRC_ROOT / "cli" / "apply_dispatch.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    apply_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(apply_dispatch_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    apply_dispatch = __import__(
        "git_stage_batch.cli.apply_dispatch",
        fromlist=["apply_dispatch"],
    )
    apply_runtime_imports = {
        "git_stage_batch.commands.apply_from",
        "git_stage_batch.commands.file_scope.multi_file_actions",
    }

    assert "git_stage_batch.cli.apply_dispatch" in parser_imports
    assert "git_stage_batch.commands.apply_from" not in parser_imports
    assert apply_runtime_imports <= apply_dispatch_imports
    assert "git_stage_batch.cli.file_scope" in apply_dispatch_imports
    assert "dispatch_apply_command" in vars(apply_dispatch)
    assert "command_apply_from_batch" not in vars(parser)
    assert "def dispatch_apply(" not in parser_text
    assert "command_apply_from_batch(" not in parser_text
    assert "apply --from" not in parser_text


def test_argument_parser_delegates_replacement_input_decoding():
    """Parser branches should not own replacement payload decoding."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    replacement_input_path = SRC_ROOT / "cli" / "replacement_input.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    replacement_input_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(replacement_input_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    replacement_input = __import__(
        "git_stage_batch.cli.replacement_input",
        fromlist=["replacement_input"],
    )

    assert "git_stage_batch.cli.replacement_input" in parser_imports
    assert "git_stage_batch.core.replacement" not in parser_imports
    assert "git_stage_batch.core.replacement" in replacement_input_imports
    assert "resolve_replacement_text" in vars(replacement_input)
    assert "_resolve_replacement_text" not in vars(parser)
    assert "ReplacementText" not in parser_text
    assert "stdin.buffer.read" not in parser_text


def test_file_scope_discard_to_batch_owns_multi_file_pipeline():
    """Multi-file discard-to-batch support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    multi_file_actions_path = (
        SRC_ROOT / "commands" / "file_scope" / "multi_file_actions.py"
    )
    helper_path = SRC_ROOT / "commands" / "file_scope" / "discard_to_batch.py"
    helper = __import__(
        "git_stage_batch.commands.file_scope.discard_to_batch",
        fromlist=["discard_to_batch"],
    )
    public_names = {
        "DiscardFilesToBatchResult",
        "discard_files_to_batch",
    }
    moved_names = {
        "DiscardFilesToBatchResult",
        "_collect_text_file_discard_inputs",
        "_discard_prepared_text_files_to_batch",
        "_prepare_text_file_discard_to_batch",
        "_run_reverse_apply_for_prepared_discards",
        "discard_files_to_batch",
    }
    internal_record_names = {
        "_CollectedTextFileDiscards",
        "_PreparedPatchDiscard",
        "_PreparedTextFileDiscardToBatch",
        "_TextFileDiscardInput",
    }
    internal_state_names = {
        "_DiscardFilesToBatchSession",
    }
    old_public_record_names = {
        "CollectedTextFileDiscards",
        "PreparedPatchDiscard",
        "PreparedTextFileDiscardToBatch",
        "TextFileDiscardInput",
    }
    helper_imports = {
        "BatchFileUpdate",
        "add_files_to_batch",
        "detect_file_mode_from_root",
        "record_hunks_discarded",
        "snapshot_files_if_untracked",
    }

    assert public_names <= vars(helper).keys()
    assert internal_record_names <= vars(helper).keys()
    assert internal_state_names <= vars(helper).keys()
    assert old_public_record_names.isdisjoint(vars(helper).keys())

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    helper_tree = ast.parse(helper_path.read_text(), filename=str(helper_path))
    discard_names = {
        node.name
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    discard_files_to_batch_def = next(
        node
        for node in ast.walk(helper_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "discard_files_to_batch"
    )
    nested_helper_names = {
        node.name
        for node in ast.walk(discard_files_to_batch_def)
        if isinstance(node, ast.FunctionDef) and node is not discard_files_to_batch_def
    }
    multi_file_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(multi_file_actions_path)
    }
    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert moved_names.isdisjoint(discard_names)
    assert nested_helper_names == set()
    assert (
        "git_stage_batch.commands.file_scope.discard_to_batch"
        in multi_file_action_imports
    )
    assert helper_imports <= helper_imported_names


def test_file_scope_single_file_discard_to_batch_breaks_command_import_cycle():
    """File-scope fallback support should not depend on discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    multi_file_helper_path = (
        SRC_ROOT / "commands" / "file_scope" / "discard_to_batch.py"
    )
    single_file_helper = __import__(
        "git_stage_batch.commands.file_scope.discard_file_to_batch",
        fromlist=["discard_file_to_batch"],
    )
    whole_file_helper = __import__(
        "git_stage_batch.commands.selection.whole_file_batch_discarding",
        fromlist=["whole_file_batch_discarding"],
    )
    file_scope_support_names = {
        "discard_file_to_batch",
    }
    whole_file_support_names = {
        "discard_binary_to_batch",
        "discard_text_deletion_to_batch",
    }
    old_command_helper_names = {
        "_command_discard_binary_to_batch",
        "_command_discard_file_to_batch",
        "_command_discard_text_deletion_to_batch",
    }

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_names = {
        node.name
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    discard_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(discard_path)
    }
    multi_file_helper_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(multi_file_helper_path)
    }
    single_file_helper_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(
            SRC_ROOT / "commands" / "file_scope" / "discard_file_to_batch.py"
        )
    }
    single_file_helper_imported_names = set()
    for imported_module, node in _import_from_nodes(
        SRC_ROOT / "commands" / "file_scope" / "discard_file_to_batch.py"
    ):
        if imported_module == "git_stage_batch.commands.selection":
            single_file_helper_imported_names |= {alias.name for alias in node.names}

    assert file_scope_support_names <= vars(single_file_helper).keys()
    assert whole_file_support_names <= vars(whole_file_helper).keys()
    assert whole_file_support_names.isdisjoint(vars(single_file_helper).keys())
    assert old_command_helper_names.isdisjoint(vars(single_file_helper).keys())
    assert old_command_helper_names.isdisjoint(vars(whole_file_helper).keys())
    assert old_command_helper_names.isdisjoint(discard_names)
    assert (
        "git_stage_batch.commands.file_scope.discard_file_to_batch"
        in discard_imports
    )
    assert (
        "git_stage_batch.commands.selection.whole_file_batch_discarding"
        in discard_imports
    )
    assert (
        "git_stage_batch.commands.file_scope.discard_file_to_batch"
        in multi_file_helper_imports
    )
    assert "git_stage_batch.commands.selection" in single_file_helper_imports
    assert "whole_file_batch_discarding" in single_file_helper_imported_names
    assert "git_stage_batch.commands.discard" not in multi_file_helper_imports


def test_whole_file_batch_staging_owns_include_pipeline():
    """Whole-file include-to-batch support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "whole_file_batch_staging.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.whole_file_batch_staging",
        fromlist=["whole_file_batch_staging"],
    )
    public_names = {
        "include_binary_to_batch",
        "include_gitlink_to_batch",
        "include_text_deletion_to_batch",
        "save_empty_text_lifecycle_to_batch",
    }
    old_command_helper_names = {
        "_command_include_binary_to_batch",
        "_command_include_gitlink_to_batch",
        "_command_include_text_deletion_to_batch",
        "_save_empty_text_lifecycle_to_batch",
    }
    moved_import_names = {
        "BatchOwnership",
        "TextFileChangeType",
        "add_binary_file_to_batch",
        "add_gitlink_to_batch",
        "detect_empty_text_lifecycle_change",
        "record_binary_hunk_skipped",
        "record_gitlink_hunk_skipped",
        "record_text_deletion_hunk_skipped",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(include_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    include_imported_names = set()
    include_selection_imports = set()
    for imported_module, node in _import_from_nodes(include_path):
        imported_names = {alias.name for alias in node.names}
        include_imported_names |= imported_names
        if imported_module == "git_stage_batch.commands.selection":
            include_selection_imports |= imported_names

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert old_command_helper_names.isdisjoint(include_names)
    assert old_command_helper_names.isdisjoint(vars(helper).keys())
    assert "whole_file_batch_staging" in include_selection_imports
    assert moved_import_names.isdisjoint(include_imported_names)
    assert moved_import_names <= helper_imported_names


def test_selected_change_batch_staging_owns_include_pipeline():
    """Selected change include-to-batch support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_batch_staging.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_batch_staging",
        fromlist=["selected_change_batch_staging"],
    )
    public_names = {
        "include_selected_change_to_batch",
    }
    old_command_helper_names = {
        "_command_include_hunk_to_batch",
    }
    moved_import_names = {
        "acquire_batch_ownership_update_for_selection",
        "add_file_to_batch",
        "annotate_with_batch_source",
        "append_lines_to_file",
        "batch_exists",
        "build_line_changes_from_patch_lines",
        "create_batch",
        "detect_file_mode",
        "get_block_list_file_path",
        "read_batch_metadata",
        "read_text_file_line_set",
        "record_hunk_skipped",
    }
    helper_imports = moved_import_names | {
        "stream_live_git_diff",
        "whole_file_batch_staging",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(include_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    include_imported_names = set()
    include_selection_imports = set()
    for imported_module, node in _import_from_nodes(include_path):
        imported_names = {alias.name for alias in node.names}
        include_imported_names |= imported_names
        if imported_module == "git_stage_batch.commands.selection":
            include_selection_imports |= imported_names

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert old_command_helper_names.isdisjoint(include_names)
    assert old_command_helper_names.isdisjoint(vars(helper).keys())
    assert "selected_change_batch_staging" in include_selection_imports
    assert moved_import_names.isdisjoint(include_imported_names)
    assert helper_imports <= helper_imported_names


def test_file_scope_include_file_owns_file_pipeline():
    """Explicit file-scope include support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = SRC_ROOT / "commands" / "file_scope" / "include_file.py"
    helper = __import__(
        "git_stage_batch.commands.file_scope.include_file",
        fromlist=["include_file"],
    )
    public_names = {
        "include_file_changes",
    }
    moved_names = {
        "acquire_unified_diff",
        "auto_add_untracked_files",
        "compute_binary_file_hash",
        "compute_gitlink_change_hash",
        "compute_rename_change_hash",
        "compute_stable_hunk_hash_from_lines",
        "compute_text_file_deletion_hash",
        "git_add_paths",
        "git_apply_to_index",
        "ngettext",
        "patch_is_file_deletion",
        "record_hunk_included",
        "run_git_command",
        "stream_live_git_diff",
        "update_index_with_blob_buffer",
    }
    helper_imports = moved_names | {
        "LineBuffer",
        "finish_selected_change_action",
        "get_selected_change_file_path",
        "selected_change_staging",
        "undo_checkpoint",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_functions = {
        node.name: node
        for node in ast.walk(include_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_include_file_names = {
        node.id
        for node in ast.walk(include_functions["command_include_file"])
        if isinstance(node, ast.Name)
    }
    command_include_file_attributes = {
        node.attr
        for node in ast.walk(include_functions["command_include_file"])
        if isinstance(node, ast.Attribute)
    }
    include_file_scope_imports = set()
    for imported_module, node in _import_from_nodes(include_path):
        if imported_module == "git_stage_batch.commands.file_scope":
            include_file_scope_imports |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert "include_file" in include_file_scope_imports
    assert "include_file_changes" in command_include_file_attributes
    assert moved_names.isdisjoint(command_include_file_names)
    assert helper_imports <= helper_imported_names


def test_file_scope_include_to_batch_owns_file_pipeline():
    """Explicit file-scope include-to-batch support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = SRC_ROOT / "commands" / "file_scope" / "include_file_to_batch.py"
    helper = __import__(
        "git_stage_batch.commands.file_scope.include_file_to_batch",
        fromlist=["include_file_to_batch"],
    )
    public_names = {
        "include_file_to_batch",
    }
    old_command_helper_names = {
        "_command_include_file_to_batch",
    }
    moved_names = {
        "acquire_batch_ownership_update_for_selection",
        "add_file_to_batch",
        "read_batch_metadata",
        "render_binary_file_change",
        "render_gitlink_change",
        "render_text_deletion_change",
        "snapshot_file_if_untracked",
    }
    helper_imports = moved_names | {
        "annotate_with_batch_source",
        "auto_add_untracked_files",
        "stream_live_git_diff",
        "whole_file_batch_staging",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_functions = {
        node.name: node
        for node in ast.walk(include_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_include_to_batch_names = {
        node.id
        for node in ast.walk(include_functions["command_include_to_batch"])
        if isinstance(node, ast.Name)
    }
    include_names = set(include_functions)
    include_file_scope_imports = set()
    for imported_module, node in _import_from_nodes(include_path):
        if imported_module == "git_stage_batch.commands.file_scope":
            include_file_scope_imports |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert old_command_helper_names.isdisjoint(include_names)
    assert "include_file_to_batch" in include_file_scope_imports
    assert moved_names.isdisjoint(command_include_to_batch_names)
    assert helper_imports <= helper_imported_names


def test_file_scope_discard_owns_file_pipeline():
    """Explicit file-scope discard support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = SRC_ROOT / "commands" / "file_scope" / "discard_file.py"
    helper = __import__(
        "git_stage_batch.commands.file_scope.discard_file",
        fromlist=["discard_file"],
    )
    public_names = {
        "discard_file_changes",
    }
    moved_names = {
        "auto_add_untracked_files",
        "git_remove_paths",
        "render_gitlink_change",
        "render_rename_change",
        "render_text_deletion_change",
        "stream_live_git_diff",
    }

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_functions = {
        node.name: node
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_discard_file_names = {
        node.id
        for node in ast.walk(discard_functions["command_discard_file"])
        if isinstance(node, ast.Name)
    }
    discard_imported_names = set()
    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module == "git_stage_batch.commands.file_scope":
            discard_imported_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert "discard_file" in discard_imported_names
    assert moved_names.isdisjoint(command_discard_file_names)
    assert moved_names <= helper_imported_names


def test_file_scope_discard_replacement_owns_file_pipeline():
    """File-scope discard replacement support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "file_scope" / "discard_file_replacement.py"
    )
    helper = __import__(
        "git_stage_batch.commands.file_scope.discard_file_replacement",
        fromlist=["discard_file_replacement"],
    )
    public_names = {
        "discard_file_as_replacement",
    }
    moved_names = {
        "clear_last_file_review_state_if_file_matches",
        "coerce_replacement_payload",
        "get_git_repository_root_path",
        "restore_selected_change_state",
        "snapshot_file_if_untracked",
        "snapshot_selected_change_state",
    }
    moved_attributes = {
        "mkdir",
        "write_bytes",
    }

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_functions = {
        node.name: node
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_discard_file_as_names = {
        node.id
        for node in ast.walk(discard_functions["command_discard_file_as"])
        if isinstance(node, ast.Name)
    }
    command_discard_file_as_attributes = {
        node.attr
        for node in ast.walk(discard_functions["command_discard_file_as"])
        if isinstance(node, ast.Attribute)
    }
    discard_imported_names = set()
    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module == "git_stage_batch.commands.file_scope":
            discard_imported_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert "discard_file_replacement" in discard_imported_names
    assert moved_names.isdisjoint(command_discard_file_as_names)
    assert moved_attributes.isdisjoint(command_discard_file_as_attributes)
    assert moved_names <= helper_imported_names


def test_file_scope_include_replacement_owns_file_pipeline():
    """File-scope include replacement support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "file_scope" / "include_file_replacement.py"
    )
    helper = __import__(
        "git_stage_batch.commands.file_scope.include_file_replacement",
        fromlist=["include_file_replacement"],
    )
    public_names = {
        "include_file_as_replacement",
    }
    moved_names = {
        "clear_last_file_review_state_if_file_matches",
        "coerce_replacement_payload",
        "file_has_staged_changes",
        "file_has_unstaged_changes",
        "load_line_changes_from_state",
        "restore_selected_change_state",
        "snapshot_selected_change_state",
        "update_index_with_blob_buffer",
        "write_line_ids_file",
    }
    moved_attributes = {
        "from_bytes",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_functions = {
        node.name: node
        for node in ast.walk(include_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_include_file_as_names = {
        node.id
        for node in ast.walk(include_functions["command_include_file_as"])
        if isinstance(node, ast.Name)
    }
    command_include_file_as_attributes = {
        node.attr
        for node in ast.walk(include_functions["command_include_file_as"])
        if isinstance(node, ast.Attribute)
    }
    include_imported_names = set()
    for imported_module, node in _import_from_nodes(include_path):
        if imported_module == "git_stage_batch.commands.file_scope":
            include_imported_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert "include_file_replacement" in include_imported_names
    assert moved_names.isdisjoint(command_include_file_as_names)
    assert moved_attributes.isdisjoint(command_include_file_as_attributes)
    assert moved_names <= helper_imported_names


def test_selected_change_batch_discarding_owns_hunk_pipeline():
    """Selected change batch support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_batch_discarding.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_batch_discarding",
        fromlist=["selected_change_batch_discarding"],
    )
    public_names = {
        "discard_selected_change_to_batch",
    }
    internal_names = {
        "_discard_text_hunk_to_batch",
    }
    old_command_helper_names = {
        "_command_discard_hunk_to_batch",
        "_command_discard_text_hunk_to_batch",
    }
    helper_imports = {
        "acquire_batch_ownership_update_for_selection",
        "add_file_to_batch",
        "annotate_with_batch_source",
        "fetch_next_change",
        "patch_is_empty_file_change",
        "patch_is_new_file",
        "record_hunk_discarded",
    }

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_names = {
        node.name
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    discard_imported_names = set()
    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module == "git_stage_batch.commands.selection":
            discard_imported_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert internal_names <= vars(helper).keys()
    assert old_command_helper_names.isdisjoint(discard_names)
    assert "selected_change_batch_discarding" in discard_imported_names
    assert helper_imports <= helper_imported_names


def test_selected_file_discarding_owns_selected_file_pipeline():
    """Selected file-scope discard support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_file_discarding.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_file_discarding",
        fromlist=["selected_file_discarding"],
    )
    public_names = {
        "discard_selected_file",
    }
    old_command_helper_names = {
        "_command_discard_selected_file",
    }
    helper_imports = {
        "finish_selected_change_action",
        "get_selected_change_file_path",
        "snapshot_file_if_untracked",
        "undo_checkpoint",
    }

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_names = {
        node.name
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    discard_imported_names = set()
    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module == "git_stage_batch.commands.selection":
            discard_imported_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert old_command_helper_names.isdisjoint(discard_names)
    assert "selected_file_discarding" in discard_imported_names
    assert helper_imports <= helper_imported_names


def test_argument_parser_uses_file_scope_resolver_module():
    """Parser branches should not own repository file-scope resolution."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    file_scope_path = SRC_ROOT / "cli" / "file_scope.py"
    file_scope_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(file_scope_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    parser_local_names = {
        "FileScope",
        "FileScopeKind",
        "_resolve_live_file_scope",
        "_resolve_batch_file_scope",
    }

    assert "git_stage_batch.cli.file_scope" in parser_imports
    assert "git_stage_batch.data.file_tracking" not in parser_imports
    assert "git_stage_batch.utils.file_patterns" not in parser_imports
    assert "git_stage_batch.data.file_tracking" in file_scope_imports
    assert "git_stage_batch.utils.file_patterns" in file_scope_imports
    assert parser_local_names.isdisjoint(vars(parser))


def test_argument_parser_uses_file_argument_module():
    """Parser branches should not own shared file option normalization."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    file_arguments = __import__(
        "git_stage_batch.cli.file_arguments",
        fromlist=["file_arguments"],
    )
    moved_names = {
        "_add_file_argument",
        "_flatten_file_patterns",
        "_normalize_parsed_file_arguments",
    }

    assert "git_stage_batch.cli.file_arguments" in parser_imports
    assert "add_file_argument" in vars(file_arguments)
    assert "normalize_parsed_file_arguments" in vars(file_arguments)
    assert moved_names.isdisjoint(vars(parser))
    assert "dest=\"file_patterns\"" not in parser_text
    assert "nargs=\"*\"" not in parser_text


def test_argument_parser_uses_auto_advance_option_module():
    """Parser branches should not own shared auto-advance option setup."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_text = parser_path.read_text()
    parser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }
    parser = __import__(
        "git_stage_batch.cli.argument_parser",
        fromlist=["argument_parser"],
    )
    auto_advance_options = __import__(
        "git_stage_batch.cli.auto_advance_options",
        fromlist=["auto_advance_options"],
    )

    assert "git_stage_batch.cli.auto_advance_options" in parser_imports
    assert "add_auto_advance_arguments" in vars(auto_advance_options)
    assert "_add_auto_advance_arguments" not in vars(parser)
    assert 'dest="auto_advance"' not in parser_text
    assert "\"--auto-advance\"" not in parser_text
    assert "Select the next hunk after the command completes" not in parser_text
    assert "Leave no hunk selected after the command completes" not in parser_text


def test_cli_dispatch_delegates_noninteractive_execution():
    """CLI dispatch should launch TUI or delegate parsed command execution."""
    dispatch_path = SRC_ROOT / "cli" / "dispatch.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(dispatch_path)
    }

    assert "git_stage_batch.commands" not in imported_modules
    assert "git_stage_batch.commands.interactive" not in imported_modules
    assert "git_stage_batch.cli.execution" in imported_modules
    assert "git_stage_batch.tui.interactive" in imported_modules


def test_tui_cli_escape_does_not_import_dispatch():
    """TUI command escape should execute parsed args without importing launcher dispatch."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    cli_escape_path = SRC_ROOT / "tui" / "cli_escape.py"
    execution_path = SRC_ROOT / "cli" / "execution.py"
    dispatch_path = SRC_ROOT / "cli" / "dispatch.py"
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    cli_escape_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(cli_escape_path)
    }
    execution_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(execution_path)
    }
    dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(dispatch_path)
    }

    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.cli_escape" in action_dispatch_imports
    assert "git_stage_batch.cli.argument_parser" in cli_escape_imports
    assert "git_stage_batch.cli.execution" in cli_escape_imports
    assert "git_stage_batch.cli.dispatch" not in cli_escape_imports
    assert "git_stage_batch.tui.interactive" not in execution_imports
    assert "git_stage_batch.tui.interactive" in dispatch_imports


def test_tui_batch_menu_owns_batch_management_actions():
    """TUI batch management should live in the batch menu adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    batch_menu_path = SRC_ROOT / "tui" / "batch_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    batch_menu = __import__(
        "git_stage_batch.tui.batch_menu",
        fromlist=["batch_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    batch_menu_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(batch_menu_path)
    }
    moved_names = {
        "_batch_apply",
        "_batch_create",
        "_batch_drop",
        "_batch_edit",
        "_batch_sift",
        "_prompt_select_batch",
    }
    menu_command_modules = {
        "git_stage_batch.commands.annotate",
        "git_stage_batch.commands.apply_from",
        "git_stage_batch.commands.drop",
        "git_stage_batch.commands.sift",
    }

    assert "handle_batch_menu" in vars(batch_menu)
    assert moved_names.isdisjoint(vars(interactive))
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.batch_menu" in action_dispatch_imports

    for imported_module in menu_command_modules:
        assert imported_module not in interactive_imports
        assert imported_module not in action_dispatch_imports
        assert imported_module in batch_menu_imports


def test_tui_asset_menu_owns_install_assets_action():
    """TUI asset installation should live in the asset menu adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    asset_menu_path = SRC_ROOT / "tui" / "asset_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    asset_menu = __import__(
        "git_stage_batch.tui.asset_menu",
        fromlist=["asset_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    asset_menu_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(asset_menu_path)
    }

    assert "handle_asset_menu" in vars(asset_menu)
    assert "handle_install_assets" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.asset_menu" in action_dispatch_imports
    assert "git_stage_batch.commands.install_assets" not in interactive_imports
    assert "git_stage_batch.commands.install_assets" not in action_dispatch_imports
    assert "git_stage_batch.commands.install_assets" in asset_menu_imports


def test_install_asset_catalog_owns_asset_groups():
    """Install asset group definitions should live in the data catalog."""
    asset_catalog = __import__(
        "git_stage_batch.data.asset_catalog",
        fromlist=["asset_catalog"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    asset_menu_path = SRC_ROOT / "tui" / "asset_menu.py"
    public_names = {
        "ASSET_GROUPS",
        "AssetGroup",
        "CompanionAsset",
        "Traversable",
    }
    old_command_names = {
        "AssetGroup",
        "CompanionAsset",
        "Traversable",
    }
    old_command_snippets = {
        "ASSET_GROUPS: dict",
        "@dataclass",
        "class AssetGroup",
        "class CompanionAsset",
        "class Traversable",
    }
    disallowed_imports = {
        "dataclasses": {"dataclass"},
        "typing": {"Protocol"},
    }
    inspected_paths = {asset_menu_path, command_path}
    catalog_imports = {path: set() for path in inspected_paths}
    direct_definition_imports = {path: set() for path in inspected_paths}

    for path in inspected_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.asset_catalog":
                catalog_imports[path] |= {
                    alias.asname or alias.name for alias in node.names
                }
            direct_definition_imports[path] |= (
                imported_names & disallowed_imports.get(imported_module, set())
            )

    command_tree = ast.parse(command_path.read_text(), filename=str(command_path))
    command_names = {
        node.name
        for node in ast.walk(command_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    command_text = command_path.read_text()

    assert public_names <= vars(asset_catalog).keys()
    assert "ASSET_GROUPS" not in vars(install_assets)
    assert old_command_names.isdisjoint(command_names)
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert catalog_imports == {
        asset_menu_path: {"ASSET_GROUPS"},
        command_path: set(),
    }
    assert direct_definition_imports == {
        asset_menu_path: set(),
        command_path: set(),
    }


def test_asset_installation_owns_asset_tree_copying():
    """Packaged asset tree copying should live in the data helper."""
    asset_installation = __import__(
        "git_stage_batch.data.asset_installation",
        fromlist=["asset_installation"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    data_path = SRC_ROOT / "data" / "asset_installation.py"
    old_command_names = {
        "_copy_traversable_tree",
        "_should_skip_asset_entry",
    }
    old_command_snippets = {
        "def _copy_traversable_tree",
        "def _should_skip_asset_entry",
        "write_file_bytes",
    }
    command_imports = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.data.asset_installation":
            command_imports |= {alias.asname or alias.name for alias in node.names}

    command_tree = ast.parse(command_path.read_text(), filename=str(command_path))
    data_tree = ast.parse(data_path.read_text(), filename=str(data_path))
    command_names = {
        node.name
        for node in ast.walk(command_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    data_names = {
        node.name
        for node in ast.walk(data_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    command_text = command_path.read_text()

    assert {"copy_asset_tree", "_should_skip_asset_entry"} <= data_names
    assert "copy_asset_tree" in vars(asset_installation)
    assert "copy_asset_tree" not in vars(install_assets)
    assert old_command_names.isdisjoint(vars(install_assets))
    assert old_command_names.isdisjoint(command_names)
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert "_copy_asset_tree" in command_imports


def test_asset_installation_owns_destination_validation():
    """Asset destination validation should live in the data helper."""
    asset_installation = __import__(
        "git_stage_batch.data.asset_installation",
        fromlist=["asset_installation"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    plan_path = SRC_ROOT / "data" / "asset_install_plan.py"
    public_names = {
        "validate_asset_destination_path",
    }
    old_command_names = {
        "_validate_destination_path_shape",
    }
    old_command_snippets = {
        "destination.parents",
        "Cannot install bundled assets because",
        "is not a directory",
        "is a directory",
    }
    command_installation_names = set()
    plan_installation_names = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.data.asset_installation":
            command_installation_names |= {
                alias.asname or alias.name for alias in node.names
            }

    for imported_module, node in _import_from_nodes(plan_path):
        if imported_module == "git_stage_batch.data.asset_installation":
            plan_installation_names |= {
                alias.asname or alias.name for alias in node.names
            }

    command_tree = ast.parse(command_path.read_text(), filename=str(command_path))
    command_names = {
        node.name
        for node in ast.walk(command_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    command_text = command_path.read_text()

    assert public_names <= vars(asset_installation).keys()
    assert public_names.isdisjoint(vars(install_assets))
    assert old_command_names.isdisjoint(command_names)
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert command_installation_names == {"_copy_asset_tree"}
    assert "validate_asset_destination_path" in plan_installation_names


def test_asset_inventory_owns_packaged_asset_lookup():
    """Packaged asset lookup should live in the data inventory helper."""
    asset_inventory = __import__(
        "git_stage_batch.data.asset_inventory",
        fromlist=["asset_inventory"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    plan_path = SRC_ROOT / "data" / "asset_install_plan.py"
    public_names = {
        "get_companion_asset_source",
        "get_entry_companion_assets",
        "list_asset_group_entries",
    }
    old_command_names = {
        "_get_companion_source",
        "_get_entry_companion_assets",
        "_get_group_root",
        "_get_install_entry_name",
        "_list_group_entries",
    }
    old_command_snippets = {
        "from importlib import resources",
        "resources.files",
        "CompanionAsset",
    }
    command_inventory_names = set()
    plan_inventory_names = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.data.asset_inventory":
            command_inventory_names |= {
                alias.asname or alias.name for alias in node.names
            }

    for imported_module, node in _import_from_nodes(plan_path):
        if imported_module == "git_stage_batch.data.asset_inventory":
            plan_inventory_names |= {
                alias.asname or alias.name for alias in node.names
            }

    command_tree = ast.parse(command_path.read_text(), filename=str(command_path))
    command_names = {
        node.name
        for node in ast.walk(command_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    command_text = command_path.read_text()

    assert public_names <= vars(asset_inventory).keys()
    assert public_names.isdisjoint(vars(install_assets))
    assert old_command_names.isdisjoint(command_names)
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert command_inventory_names == set()
    assert plan_inventory_names == {
        "get_companion_asset_source",
        "get_entry_companion_assets",
    }


def test_asset_install_plan_owns_install_assembly():
    """Install assembly should live in the data install planner."""
    asset_install_plan = __import__(
        "git_stage_batch.data.asset_install_plan",
        fromlist=["asset_install_plan"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    public_names = {
        "PlannedAssetInstall",
        "plan_asset_installs",
    }
    old_command_snippets = {
        "Refusing to overwrite",
        "_companion_asset_source",
        "_entry_companion_assets",
        "_validate_asset_destination",
        "destination.exists",
        "planned_installs: list",
        "target_root",
    }
    imported_plan_names = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.data.asset_install_plan":
            imported_plan_names |= {
                alias.asname or alias.name for alias in node.names
            }

    command_text = command_path.read_text()

    assert public_names <= vars(asset_install_plan).keys()
    assert public_names.isdisjoint(vars(install_assets))
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert imported_plan_names == {"_plan_asset_installs"}


def test_asset_selection_owns_group_filter_selection():
    """Asset group and filter selection should live in the data selector."""
    asset_selection = __import__(
        "git_stage_batch.data.asset_selection",
        fromlist=["asset_selection"],
    )
    install_assets = __import__(
        "git_stage_batch.commands.install_assets",
        fromlist=["install_assets"],
    )
    command_path = SRC_ROOT / "commands" / "install_assets.py"
    public_names = {
        "SelectedAssetGroup",
        "select_asset_entries",
    }
    old_command_snippets = {
        "ASSET_GROUPS",
        "AssetGroup",
        "No bundled assets in",
        "Unknown asset group",
        "resolve_gitignore_style_patterns",
        "subprocess",
        "_asset_group_entries",
        "selected_groups",
    }
    imported_selection_names = set()

    for imported_module, node in _import_from_nodes(command_path):
        if imported_module == "git_stage_batch.data.asset_selection":
            imported_selection_names |= {
                alias.asname or alias.name for alias in node.names
            }

    command_text = command_path.read_text()

    assert public_names <= vars(asset_selection).keys()
    assert public_names.isdisjoint(vars(install_assets))
    assert all(snippet not in command_text for snippet in old_command_snippets)
    assert imported_selection_names == {"_select_asset_entries"}


def test_tui_flow_menu_owns_batch_selection_menus():
    """TUI flow selection should live in the flow menu adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    flow_actions_path = SRC_ROOT / "tui" / "flow_actions.py"
    flow_menu_path = SRC_ROOT / "tui" / "flow_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    flow_actions = __import__(
        "git_stage_batch.tui.flow_actions",
        fromlist=["flow_actions"],
    )
    flow_menu = __import__(
        "git_stage_batch.tui.flow_menu",
        fromlist=["flow_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    flow_actions_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(flow_actions_path)
    }
    flow_menu_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(flow_menu_path)
    }

    assert "handle_flow_action" in vars(flow_actions)
    assert {"handle_from_menu", "handle_to_menu"} <= vars(flow_menu).keys()
    assert "_handle_from" not in vars(interactive)
    assert "_handle_to" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.flow_actions" in action_dispatch_imports
    assert "git_stage_batch.tui.flow_menu" in flow_actions_imports
    assert "git_stage_batch.batch.query" not in interactive_imports
    assert "git_stage_batch.batch.query" not in action_dispatch_imports
    assert "git_stage_batch.commands.new" not in interactive_imports
    assert "git_stage_batch.commands.new" not in action_dispatch_imports
    assert "git_stage_batch.batch.query" in flow_menu_imports
    assert "git_stage_batch.commands.new" in flow_menu_imports


def test_tui_hunk_actions_own_direct_hunk_commands():
    """TUI direct hunk actions should live in the hunk actions adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    hunk_actions_path = SRC_ROOT / "tui" / "hunk_actions.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    hunk_actions = __import__(
        "git_stage_batch.tui.hunk_actions",
        fromlist=["hunk_actions"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    hunk_actions_imported_names = set()
    hunk_action_names = {
        "handle_hunk_discard",
        "handle_hunk_include",
        "handle_hunk_skip",
    }
    hunk_command_names = {
        "command_discard",
        "command_discard_from_batch",
        "command_discard_to_batch",
        "command_include",
        "command_include_from_batch",
        "command_include_to_batch",
        "command_skip",
    }

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(hunk_actions_path):
        hunk_actions_imported_names |= {alias.name for alias in node.names}

    assert hunk_action_names <= vars(hunk_actions).keys()
    assert "_handle_include" not in vars(interactive)
    assert "_handle_skip" not in vars(interactive)
    assert "_handle_discard" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.hunk_actions" in action_dispatch_imports
    assert hunk_command_names.isdisjoint(interactive_imported_names)
    assert hunk_command_names <= hunk_actions_imported_names


def test_tui_history_actions_own_undo_redo_actions():
    """TUI history actions should live in the history action adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    history_actions_path = SRC_ROOT / "tui" / "history_actions.py"
    action_dispatch = __import__(
        "git_stage_batch.tui.action_dispatch",
        fromlist=["action_dispatch"],
    )
    history_actions = __import__(
        "git_stage_batch.tui.history_actions",
        fromlist=["history_actions"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    history_actions_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(history_actions_path)
    }
    old_dispatch_names = {
        "_handle_redo",
        "_handle_undo",
    }
    command_modules = {
        "git_stage_batch.commands.redo",
        "git_stage_batch.commands.undo",
    }

    assert {"handle_redo", "handle_undo"} <= vars(history_actions).keys()
    assert old_dispatch_names.isdisjoint(vars(action_dispatch))
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.history_actions" in action_dispatch_imports

    for imported_module in command_modules:
        assert imported_module not in interactive_imports
        assert imported_module not in action_dispatch_imports
        assert imported_module in history_actions_imports


def test_tui_again_action_owns_iteration_restart():
    """TUI again action should live in the again action adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    again_action_path = SRC_ROOT / "tui" / "again_action.py"
    action_dispatch = __import__(
        "git_stage_batch.tui.action_dispatch",
        fromlist=["action_dispatch"],
    )
    again_action = __import__(
        "git_stage_batch.tui.again_action",
        fromlist=["again_action"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    again_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(again_action_path)
    }
    old_dispatch_imports = {
        "git_stage_batch.data.file_tracking",
        "git_stage_batch.data.hunk_tracking",
        "git_stage_batch.utils.paths",
    }

    assert "handle_again" in vars(again_action)
    assert "_handle_again" not in vars(action_dispatch)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.again_action" in action_dispatch_imports
    assert "git_stage_batch.commands.again" not in interactive_imports
    assert "git_stage_batch.commands.again" not in action_dispatch_imports
    assert action_dispatch_imports.isdisjoint(old_dispatch_imports)
    assert "git_stage_batch.commands.again" in again_action_imports


def test_tui_status_action_owns_status_command():
    """TUI status action should live in the status action adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    status_action_path = SRC_ROOT / "tui" / "status_action.py"
    action_dispatch = __import__(
        "git_stage_batch.tui.action_dispatch",
        fromlist=["action_dispatch"],
    )
    status_action = __import__(
        "git_stage_batch.tui.status_action",
        fromlist=["status_action"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    status_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(status_action_path)
    }

    assert "handle_status" in vars(status_action)
    assert "_handle_status" not in vars(action_dispatch)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.status_action" in action_dispatch_imports
    assert "git_stage_batch.commands.status" not in interactive_imports
    assert "git_stage_batch.commands.status" not in action_dispatch_imports
    assert "git_stage_batch.commands.status" in status_action_imports


def test_tui_fixup_menu_owns_suggest_fixup_submenu():
    """TUI suggest-fixup selection should live in the fixup menu."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    fixup_menu_path = SRC_ROOT / "tui" / "fixup_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    fixup_menu = __import__(
        "git_stage_batch.tui.fixup_menu",
        fromlist=["fixup_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    fixup_menu_imported_names = set()
    fixup_menu_names = {
        "clear_suggest_fixup_state",
        "command_suggest_fixup",
        "prompt_fixup_action",
        "read_suggest_fixup_state",
    }

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(fixup_menu_path):
        fixup_menu_imported_names |= {alias.name for alias in node.names}

    assert "handle_fixup_menu" in vars(fixup_menu)
    assert "handle_fixup_selection" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.fixup_menu" in action_dispatch_imports
    assert fixup_menu_names.isdisjoint(interactive_imported_names)
    assert fixup_menu_names <= fixup_menu_imported_names


def test_tui_shell_command_owns_shell_escape():
    """TUI shell escapes should live in the shell command adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    shell_command_path = SRC_ROOT / "tui" / "shell_command.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    shell_command = __import__(
        "git_stage_batch.tui.shell_command",
        fromlist=["shell_command"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    shell_command_imported_names = set()
    interactive_plain_imports = set()
    shell_command_plain_imports = set()
    shell_command_names = {
        "get_git_repository_root_path",
        "prompt_shell_command",
    }

    for node in ast.walk(ast.parse(interactive_path.read_text())):
        if isinstance(node, ast.Import):
            interactive_plain_imports |= {alias.name for alias in node.names}

    for node in ast.walk(ast.parse(shell_command_path.read_text())):
        if isinstance(node, ast.Import):
            shell_command_plain_imports |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(shell_command_path):
        shell_command_imported_names |= {alias.name for alias in node.names}

    assert "handle_shell_command" in vars(shell_command)
    assert "_handle_shell" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.shell_command" in action_dispatch_imports
    assert "subprocess" not in interactive_plain_imports
    assert "subprocess" in shell_command_plain_imports
    assert shell_command_names.isdisjoint(interactive_imported_names)
    assert shell_command_names <= shell_command_imported_names


def test_tui_cli_escape_owns_command_fallback():
    """TUI CLI command fallback should live in the CLI escape adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    cli_escape_path = SRC_ROOT / "tui" / "cli_escape.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    cli_escape = __import__(
        "git_stage_batch.tui.cli_escape",
        fromlist=["cli_escape"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    cli_escape_imported_names = set()
    interactive_plain_imports = set()
    cli_escape_plain_imports = set()
    cli_escape_names = {
        "execute_non_interactive_args",
        "parse_command_line",
    }

    for node in ast.walk(ast.parse(interactive_path.read_text())):
        if isinstance(node, ast.Import):
            interactive_plain_imports |= {alias.name for alias in node.names}

    for node in ast.walk(ast.parse(cli_escape_path.read_text())):
        if isinstance(node, ast.Import):
            cli_escape_plain_imports |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(cli_escape_path):
        cli_escape_imported_names |= {alias.name for alias in node.names}

    assert "handle_cli_escape" in vars(cli_escape)
    assert "_handle_cli_command" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.cli_escape" in action_dispatch_imports
    assert "shlex" not in interactive_plain_imports
    assert "shlex" in cli_escape_plain_imports
    assert cli_escape_names.isdisjoint(interactive_imported_names)
    assert cli_escape_names <= cli_escape_imported_names


def test_tui_session_quit_owns_smart_quit_actions():
    """TUI smart quit handling should live in the session quit adapter."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    session_quit_path = SRC_ROOT / "tui" / "session_quit.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    session_quit = __import__(
        "git_stage_batch.tui.session_quit",
        fromlist=["session_quit"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    session_quit_imported_names = set()
    session_quit_names = {
        "command_abort",
        "command_stop",
        "prompt_quit_session",
        "read_text_file_contents",
    }

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(session_quit_path):
        session_quit_imported_names |= {alias.name for alias in node.names}

    assert "handle_quit" in vars(session_quit)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.session_quit" in action_dispatch_imports
    assert session_quit_names.isdisjoint(interactive_imported_names)
    assert session_quit_names <= session_quit_imported_names


def test_tui_file_selection_menu_owns_whole_file_actions():
    """TUI whole-file selection should live in the file selection menu."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    file_menu_path = SRC_ROOT / "tui" / "file_selection_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    file_menu = __import__(
        "git_stage_batch.tui.file_selection_menu",
        fromlist=["file_selection_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    file_menu_imported_names = set()
    whole_file_command_names = {
        "command_discard_file",
        "command_include_file",
        "command_skip_file",
    }

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(file_menu_path):
        file_menu_imported_names |= {alias.name for alias in node.names}

    assert "handle_file_selection_menu" in vars(file_menu)
    assert "handle_file_selection" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.file_selection_menu" in action_dispatch_imports
    assert whole_file_command_names.isdisjoint(interactive_imported_names)
    assert whole_file_command_names <= file_menu_imported_names


def test_tui_line_selection_menu_owns_line_actions():
    """TUI line selection should live in the line selection menu."""
    interactive_path = SRC_ROOT / "tui" / "interactive.py"
    action_dispatch_path = SRC_ROOT / "tui" / "action_dispatch.py"
    line_menu_path = SRC_ROOT / "tui" / "line_selection_menu.py"
    interactive = __import__(
        "git_stage_batch.tui.interactive",
        fromlist=["interactive"],
    )
    line_menu = __import__(
        "git_stage_batch.tui.line_selection_menu",
        fromlist=["line_selection_menu"],
    )
    interactive_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(interactive_path)
    }
    interactive_imported_names = set()
    action_dispatch_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(action_dispatch_path)
    }
    line_menu_imported_names = set()
    line_command_names = {
        "command_discard_line",
        "command_include_line",
        "command_skip_line",
    }

    for _imported_module, node in _import_from_nodes(interactive_path):
        interactive_imported_names |= {alias.name for alias in node.names}

    for _imported_module, node in _import_from_nodes(line_menu_path):
        line_menu_imported_names |= {alias.name for alias in node.names}

    assert "handle_line_selection_menu" in vars(line_menu)
    assert "handle_line_selection" not in vars(interactive)
    assert "git_stage_batch.tui.action_dispatch" in interactive_imports
    assert "git_stage_batch.tui.line_selection_menu" in action_dispatch_imports
    assert line_command_names.isdisjoint(interactive_imported_names)
    assert line_command_names <= line_menu_imported_names


def test_tui_file_review_state_name_does_not_shadow_persisted_state():
    """TUI review state should not reuse the persisted file-review state name."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    session_path = SRC_ROOT / "tui" / "file_review" / "session.py"
    browser_tree = ast.parse(browser_path.read_text(), filename=str(browser_path))
    session_tree = ast.parse(session_path.read_text(), filename=str(session_path))
    review_class_names = {
        node.name
        for node in browser_tree.body
        if isinstance(node, ast.ClassDef)
    }
    session_class_names = {
        node.name
        for node in session_tree.body
        if isinstance(node, ast.ClassDef)
    }
    imported_state_names = set()
    records = __import__(
        "git_stage_batch.data.file_review.records",
        fromlist=["records"],
    )
    persisted_state = __import__(
        "git_stage_batch.data.file_review.state",
        fromlist=["state"],
    )

    for imported_module, node in _import_from_nodes(browser_path):
        if imported_module != "git_stage_batch.data.file_review.state":
            continue
        imported_state_names |= {alias.name for alias in node.names}

    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }

    assert "FileReviewState" in vars(records)
    assert "FileReviewState" not in vars(persisted_state)
    assert "FileReviewSessionState" in session_class_names
    assert "FileReviewSessionState" not in review_class_names
    assert "git_stage_batch.tui.file_review.session" in browser_imports
    assert "FileReviewState" not in review_class_names
    assert "FileReviewState" not in session_class_names
    assert "FileReviewState" not in imported_state_names


def test_tui_file_review_modules_share_session_state():
    """TUI file review modules should use the shared session state."""
    owner_import = "git_stage_batch.tui.file_review.session"
    reviewed_paths = {
        SRC_ROOT / "tui" / "file_review" / "browser.py": {
            "FileReviewActionState",
            "FileReviewBatchActionState",
            "FileReviewCandidateState",
            "FileReviewLiveActionState",
        },
        SRC_ROOT / "tui" / "file_review" / "action_router.py": {
            "FileReviewActionState",
        },
        SRC_ROOT / "tui" / "file_review" / "batch_actions.py": {
            "FileReviewBatchActionState",
        },
        SRC_ROOT / "tui" / "file_review" / "candidates.py": {
            "FileReviewCandidateState",
        },
        SRC_ROOT / "tui" / "file_review" / "live_actions.py": {
            "FileReviewLiveActionState",
        },
    }

    for path, old_names in reviewed_paths.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        class_names = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        imported_modules = {
            imported_module
            for imported_module, _node in _import_from_nodes(path)
        }

        assert old_names.isdisjoint(class_names)
        assert owner_import in imported_modules


def test_tui_file_review_display_owns_review_rendering():
    """TUI file review display should own reviewed-file rendering."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    display_path = SRC_ROOT / "tui" / "file_review" / "display.py"
    browser = __import__(
        "git_stage_batch.tui.file_review.browser",
        fromlist=["browser"],
    )
    display = __import__(
        "git_stage_batch.tui.file_review.display",
        fromlist=["display"],
    )
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    display_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(display_path)
    }
    old_review_names = {
        "_render_review",
        "get_hunk_counts",
        "print_status_bar",
    }

    assert "render_file_review" in vars(display)
    assert old_review_names.isdisjoint(vars(browser))
    assert "git_stage_batch.tui.file_review.display" in browser_imports
    assert "git_stage_batch.commands.show" not in browser_imports
    assert "git_stage_batch.data.progress" not in browser_imports
    assert "git_stage_batch.tui.display" not in browser_imports
    assert "git_stage_batch.commands.show" in display_imports
    assert "git_stage_batch.commands.show_from" in display_imports
    assert "git_stage_batch.data.progress" in display_imports
    assert "git_stage_batch.tui.display" in display_imports


def test_tui_file_review_page_navigation_owns_page_specs():
    """TUI file review page navigation should own page prompts."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    page_navigation_path = SRC_ROOT / "tui" / "file_review" / "page_navigation.py"
    page_navigation = __import__(
        "git_stage_batch.tui.file_review.page_navigation",
        fromlist=["page_navigation"],
    )
    browser_tree = ast.parse(browser_path.read_text(), filename=str(browser_path))
    browser_function_names = {
        node.name
        for node in ast.walk(browser_tree)
        if isinstance(node, ast.FunctionDef)
    }
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    page_navigation_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(page_navigation_path)
    }
    public_names = {
        "next_page_spec",
        "previous_page_spec",
        "prompt_page_spec",
    }
    moved_function_names = {
        "_next_page_spec",
        "_previous_page_spec",
        "_prompt_page_spec",
    }
    old_browser_snippets = {
        "Already at the first file review page.",
        "Already at the last file review page.",
        "No file review page state is available.",
        "Page(s), for example",
        "read_last_file_review_state",
    }
    browser_text = browser_path.read_text()

    assert public_names <= vars(page_navigation).keys()
    assert moved_function_names.isdisjoint(browser_function_names)
    assert all(snippet not in browser_text for snippet in old_browser_snippets)
    assert "git_stage_batch.tui.file_review.page_navigation" in browser_imports
    assert "git_stage_batch.data.file_review.state" not in browser_imports
    assert "git_stage_batch.data.file_review.state" in page_navigation_imports


def test_tui_file_review_candidates_own_candidate_browser():
    """TUI file review candidates should own batch candidate browsing."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    candidates_path = SRC_ROOT / "tui" / "file_review" / "candidates.py"
    browser = __import__(
        "git_stage_batch.tui.file_review.browser",
        fromlist=["browser"],
    )
    candidates = __import__(
        "git_stage_batch.tui.file_review.candidates",
        fromlist=["candidates"],
    )
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    candidate_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(candidates_path)
    }
    old_review_names = {
        "_browse_candidates",
        "_execute_candidate",
        "_preview_candidate",
        "_prompt_candidate_action",
        "_prompt_candidate_operation",
    }

    assert "browse_candidates" in vars(candidates)
    assert old_review_names.isdisjoint(vars(browser))
    assert "git_stage_batch.tui.file_review.candidates" in browser_imports
    assert "git_stage_batch.commands.apply_from" not in browser_imports
    assert "git_stage_batch.commands.show_from" not in browser_imports
    assert "git_stage_batch.commands.apply_from" in candidate_imports
    assert "git_stage_batch.commands.include_from" in candidate_imports
    assert "git_stage_batch.commands.show_from" in candidate_imports


def test_tui_file_review_file_browser_owns_file_selection():
    """TUI file review file browser should own file choice state."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    file_browser_path = SRC_ROOT / "tui" / "file_review" / "file_browser.py"
    browser = __import__(
        "git_stage_batch.tui.file_review.browser",
        fromlist=["browser"],
    )
    file_browser = __import__(
        "git_stage_batch.tui.file_review.file_browser",
        fromlist=["file_browser"],
    )
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    file_browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(file_browser_path)
    }
    public_names = {
        "ReviewFileEntry",
        "choose_review_file",
        "list_review_file_entries",
        "prompt_block_local_only",
    }
    moved_names = {
        "ReviewFileEntry",
        "_apply_marked_file_action",
        "_file_choice_to_path",
        "_mark_file_choice",
        "_normalize_marked_file_action",
        "_unmark_file_choice",
        "list_review_file_entries",
    }
    old_browser_snippets = {
        "File number, /pattern",
        "No files marked.",
        "list_changed_files",
        "list_untracked_files",
        "read_batch_metadata",
        "resolve_gitignore_style_patterns",
    }
    browser_text = browser_path.read_text()

    assert public_names <= vars(file_browser).keys()
    assert moved_names.isdisjoint(vars(browser))
    assert all(snippet not in browser_text for snippet in old_browser_snippets)
    assert "git_stage_batch.tui.file_review.file_browser" in browser_imports
    assert "git_stage_batch.batch.query" not in browser_imports
    assert "git_stage_batch.data.file_tracking" not in browser_imports
    assert "git_stage_batch.utils.file_patterns" not in browser_imports
    assert "git_stage_batch.batch.query" in file_browser_imports
    assert "git_stage_batch.data.file_tracking" in file_browser_imports
    assert "git_stage_batch.utils.file_patterns" in file_browser_imports


def test_tui_file_review_batch_actions_own_batch_transfers():
    """TUI file review batch actions should own batch transfer commands."""
    file_browser_path = SRC_ROOT / "tui" / "file_review" / "file_browser.py"
    batch_actions_path = SRC_ROOT / "tui" / "file_review" / "batch_actions.py"
    file_browser = __import__(
        "git_stage_batch.tui.file_review.file_browser",
        fromlist=["file_browser"],
    )
    batch_actions = __import__(
        "git_stage_batch.tui.file_review.batch_actions",
        fromlist=["batch_actions"],
    )
    file_browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(file_browser_path)
    }
    batch_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(batch_actions_path)
    }
    old_review_names = {
        "_apply_batch_file_action",
        "_apply_batch_line_action",
        "_apply_batch_replacement_action",
    }

    assert {
        "apply_batch_file_action",
        "apply_batch_line_action",
        "apply_batch_replacement_action",
    } <= vars(batch_actions).keys()
    assert old_review_names.isdisjoint(vars(file_browser))
    assert "git_stage_batch.tui.file_review.batch_actions" in file_browser_imports
    assert "git_stage_batch.commands.discard_from" not in file_browser_imports
    assert "git_stage_batch.commands.include_from" not in file_browser_imports
    assert "git_stage_batch.commands.discard_from" in batch_action_imports
    assert "git_stage_batch.commands.include_from" in batch_action_imports


def test_tui_file_review_live_actions_own_live_transfers():
    """TUI file review live actions should own live transfer commands."""
    file_browser_path = SRC_ROOT / "tui" / "file_review" / "file_browser.py"
    live_actions_path = SRC_ROOT / "tui" / "file_review" / "live_actions.py"
    file_browser = __import__(
        "git_stage_batch.tui.file_review.file_browser",
        fromlist=["file_browser"],
    )
    live_actions = __import__(
        "git_stage_batch.tui.file_review.live_actions",
        fromlist=["live_actions"],
    )
    file_browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(file_browser_path)
    }
    live_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(live_actions_path)
    }
    old_review_names = {
        "_apply_live_file_action",
        "_apply_live_line_action",
        "_apply_live_replacement_action",
    }

    assert {
        "apply_live_file_action",
        "apply_live_line_action",
        "apply_live_replacement_action",
    } <= vars(live_actions).keys()
    assert old_review_names.isdisjoint(vars(file_browser))
    assert "git_stage_batch.tui.file_review.live_actions" in file_browser_imports
    assert "git_stage_batch.commands.discard" not in file_browser_imports
    assert "git_stage_batch.commands.include" not in file_browser_imports
    assert "git_stage_batch.commands.skip" not in file_browser_imports
    assert "git_stage_batch.commands.discard" in live_action_imports
    assert "git_stage_batch.commands.include" in live_action_imports
    assert "git_stage_batch.commands.skip" in live_action_imports


def test_tui_file_review_block_actions_own_block_commands():
    """TUI file review block actions should own block command calls."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    file_browser_path = SRC_ROOT / "tui" / "file_review" / "file_browser.py"
    block_actions_path = SRC_ROOT / "tui" / "file_review" / "block_actions.py"
    block_actions = __import__(
        "git_stage_batch.tui.file_review.block_actions",
        fromlist=["block_actions"],
    )
    browser_tree = ast.parse(browser_path.read_text(), filename=str(browser_path))
    browser_function_names = {
        node.name
        for node in ast.walk(browser_tree)
        if isinstance(node, ast.FunctionDef)
    }
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    file_browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(file_browser_path)
    }
    block_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(block_actions_path)
    }
    old_browser_snippets = {
        "This will add the reviewed file to ignore state.",
        "block_review_file",
        "confirm_destructive_operation",
        "prompt_block_local_only",
        "unblock_review_file",
    }
    browser_text = browser_path.read_text()

    assert {
        "apply_block_action",
        "block_review_file",
        "unblock_review_file",
    } <= vars(block_actions).keys()
    assert "_apply_block_action" not in browser_function_names
    assert all(snippet not in browser_text for snippet in old_browser_snippets)
    assert "git_stage_batch.tui.file_review.block_actions" in browser_imports
    assert "git_stage_batch.tui.file_review.block_actions" in file_browser_imports
    assert "git_stage_batch.commands.block_file" not in browser_imports
    assert "git_stage_batch.commands.unblock_file" not in browser_imports
    assert "git_stage_batch.commands.block_file" not in file_browser_imports
    assert "git_stage_batch.commands.unblock_file" not in file_browser_imports
    assert "git_stage_batch.commands.block_file" in block_action_imports
    assert "git_stage_batch.commands.unblock_file" in block_action_imports
    assert "git_stage_batch.tui.file_review.file_browser" in block_action_imports
    assert "git_stage_batch.tui.prompts" in block_action_imports


def test_tui_file_review_fixup_actions_own_line_fixups():
    """TUI file review fixup actions should own line-fixup command calls."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    fixup_actions_path = SRC_ROOT / "tui" / "file_review" / "fixup_actions.py"
    fixup_actions = __import__(
        "git_stage_batch.tui.file_review.fixup_actions",
        fromlist=["fixup_actions"],
    )
    browser_tree = ast.parse(browser_path.read_text(), filename=str(browser_path))
    browser_function_names = {
        node.name
        for node in ast.walk(browser_tree)
        if isinstance(node, ast.FunctionDef)
    }
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    fixup_action_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(fixup_actions_path)
    }
    old_browser_snippets = {
        "Create fixup commit with:",
        "clear_file_review_fixup_state",
        "prompt_fixup_action",
        "prompt_line_ids",
        "read_last_fixup_commit_hash",
        "suggest_fixup_for_lines",
    }
    browser_text = browser_path.read_text()

    assert {
        "apply_fixup_action",
        "clear_file_review_fixup_state",
        "read_last_fixup_commit_hash",
        "suggest_fixup_for_lines",
    } <= vars(fixup_actions).keys()
    assert "_apply_fixup_action" not in browser_function_names
    assert all(snippet not in browser_text for snippet in old_browser_snippets)
    assert "git_stage_batch.tui.file_review.fixup_actions" in browser_imports
    assert "git_stage_batch.commands.suggest_fixup" not in browser_imports
    assert "git_stage_batch.data.suggest_fixup_state" not in browser_imports
    assert "git_stage_batch.commands.suggest_fixup" in fixup_action_imports
    assert "git_stage_batch.data.suggest_fixup_state" in fixup_action_imports
    assert "git_stage_batch.tui.prompts" in fixup_action_imports


def test_tui_file_review_prompts_own_action_vocabulary():
    """TUI file review prompts should own action text and parsing."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    prompts_path = SRC_ROOT / "tui" / "file_review" / "prompts.py"
    browser = __import__(
        "git_stage_batch.tui.file_review.browser",
        fromlist=["browser"],
    )
    prompts = __import__(
        "git_stage_batch.tui.file_review.prompts",
        fromlist=["prompts"],
    )
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    prompt_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(prompts_path)
    }
    old_review_names = {
        "_normalize_review_action",
        "_print_review_help",
        "_prompt_review_action",
    }
    prompt_text = prompts_path.read_text()
    browser_text = browser_path.read_text()

    assert {
        "normalize_review_action",
        "print_review_help",
        "prompt_review_action",
    } <= vars(prompts).keys()
    assert old_review_names.isdisjoint(vars(browser))
    assert "git_stage_batch.tui.file_review.prompts" in browser_imports
    assert "git_stage_batch.tui.prompts" in prompt_imports
    assert "Review action:" not in browser_text
    assert "Review action:" in prompt_text


def test_tui_file_review_action_router_owns_standard_actions():
    """TUI file review action router should own standard action gates."""
    browser_path = SRC_ROOT / "tui" / "file_review" / "browser.py"
    router_path = SRC_ROOT / "tui" / "file_review" / "action_router.py"
    browser = __import__(
        "git_stage_batch.tui.file_review.browser",
        fromlist=["browser"],
    )
    router = __import__(
        "git_stage_batch.tui.file_review.action_router",
        fromlist=["action_router"],
    )
    browser_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(browser_path)
    }
    router_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(router_path)
    }
    old_review_names = {
        "_apply_file_action",
        "_apply_line_action",
        "_apply_replacement_action",
        "_prompt_replacement_text",
    }
    browser_text = browser_path.read_text()
    router_text = router_path.read_text()

    assert {
        "apply_file_action",
        "apply_line_action",
        "apply_replacement_action",
    } <= vars(router).keys()
    assert old_review_names.isdisjoint(vars(browser))
    assert "git_stage_batch.tui.file_review.action_router" in browser_imports
    assert "git_stage_batch.tui.file_review.batch_actions" in router_imports
    assert "git_stage_batch.tui.file_review.live_actions" in router_imports
    assert "Replacement text (empty cancels):" not in browser_text
    assert "Replacement text (empty cancels):" in router_text


def test_commands_do_not_import_tui():
    """Command modules should not launch or depend on TUI modules."""
    violations = []

    for path in (SRC_ROOT / "commands").rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module is None:
                continue
            if imported_module == "git_stage_batch.tui" or imported_module.startswith(
                "git_stage_batch.tui."
            ):
                relative_path = path.relative_to(REPO_ROOT)
                violations.append(f"{relative_path}:{node.lineno} imports {imported_module}")

    assert violations == []


def test_commands_package_does_not_reexport_command_apis():
    """The commands package should not act as a command facade."""
    commands_path = SRC_ROOT / "commands" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(commands_path)
    }
    commands = __import__("git_stage_batch.commands", fromlist=["commands"])
    command_exports = {
        name
        for name in vars(commands)
        if name.startswith("command_")
        or name in {"DEFAULT_PROMPT_FORMAT", "DiscardFilesToBatchResult"}
    }

    assert imported_modules <= {"__future__"}
    assert command_exports == set()


def test_argument_parser_does_not_import_command_facade():
    """Argument parsing should import exact command modules for dispatch."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    parser_text = parser_path.read_text()
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(parser_path)
    }

    assert "git_stage_batch.commands" not in imported_modules
    assert "from .. import commands" not in parser_text
    assert "commands.command_" not in parser_text
    assert "commands.DEFAULT_PROMPT_FORMAT" not in parser_text
    assert "git_stage_batch.cli.show_dispatch" in imported_modules
    assert "git_stage_batch.commands.show" not in imported_modules
    assert "git_stage_batch.commands.show_from" not in imported_modules
    assert "git_stage_batch.commands.include" in imported_modules
    assert "git_stage_batch.commands.discard" in imported_modules
    assert "git_stage_batch.commands.interactive" not in imported_modules
    assert "git_stage_batch.commands.status" in imported_modules


def test_argument_parser_command_entries_stay_in_commands_modules():
    """CLI command functions should enter through command modules."""
    parser_path = SRC_ROOT / "cli" / "argument_parser.py"
    violations = []

    for imported_module, node in _import_from_nodes(parser_path):
        command_names = {
            alias.name for alias in node.names if alias.name.startswith("command_")
        }
        if not command_names:
            continue
        if imported_module == "git_stage_batch.cli.completion":
            continue
        if imported_module is None or not imported_module.startswith(
            "git_stage_batch.commands."
        ):
            relative_path = parser_path.relative_to(REPO_ROOT)
            names = ", ".join(sorted(command_names))
            violations.append(f"{relative_path}:{node.lineno} imports {names}")
            continue

        module_path = SRC_ROOT.joinpath(
            *imported_module.removeprefix("git_stage_batch.").split(".")
        ).with_suffix(".py")
        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        defined_functions = {
            child.name
            for child in tree.body
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef))
        }
        missing_names = command_names - defined_functions
        if missing_names:
            relative_path = parser_path.relative_to(REPO_ROOT)
            names = ", ".join(sorted(missing_names))
            violations.append(
                f"{relative_path}:{node.lineno} imports {names} from non-entry module"
            )

    assert violations == []


def test_suggest_fixup_state_stays_in_data_layer():
    """Suggest-fixup state persistence should stay below command and TUI flows."""
    command_path = SRC_ROOT / "commands" / "suggest_fixup.py"
    data_path = SRC_ROOT / "data" / "suggest_fixup_state.py"
    tui_command_paths = (
        SRC_ROOT / "tui" / "fixup_menu.py",
        SRC_ROOT / "tui" / "file_review" / "__init__.py",
        SRC_ROOT / "tui" / "file_review" / "fixup_actions.py",
    )
    tui_state_paths = (
        SRC_ROOT / "tui" / "fixup_menu.py",
        SRC_ROOT / "tui" / "file_review" / "fixup_actions.py",
    )
    command_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(command_path)
    }
    data_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(data_path)
    }
    suggest_fixup = __import__(
        "git_stage_batch.commands.suggest_fixup",
        fromlist=["suggest_fixup"],
    )
    command_state_names = {
        "_load_suggest_fixup_state",
        "_save_suggest_fixup_state",
        "_reset_suggest_fixup_state",
        "_should_reset_suggest_fixup_state",
    }
    violations = []

    for tui_path in tui_state_paths:
        imports = _import_from_nodes(tui_path)
        imported_modules = {imported_module for imported_module, _node in imports}
        assert "git_stage_batch.data.suggest_fixup_state" in imported_modules

    for tui_path in tui_command_paths:
        imports = _import_from_nodes(tui_path)
        for imported_module, node in imports:
            if imported_module != "git_stage_batch.commands.suggest_fixup":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & command_state_names
            if disallowed_names:
                relative_path = tui_path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert "git_stage_batch.data.suggest_fixup_state" in command_imports
    assert "git_stage_batch.utils.paths" in data_imports
    assert command_state_names.isdisjoint(vars(suggest_fixup))
    assert violations == []


def test_selected_line_source_refresh_uses_public_api():
    """Cross-module source refresh callers should import public helpers."""
    source_refresh = __import__(
        "git_stage_batch.batch.source_refresh",
        fromlist=["source_refresh"],
    )
    public_names = {
        "refresh_selected_lines_against_new_source",
        "refresh_selected_lines_against_source_lines",
    }
    private_names = {
        "_refresh_selected_lines_against_new_source",
        "_refresh_selected_lines_against_source_lines",
    }
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "selection"
        / "discard_line_replacement.py": {"refresh_selected_lines_against_source_lines"},
        SRC_ROOT / "data" / "consumed_selections.py": public_names,
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(source_refresh)
    assert private_names.isdisjoint(vars(source_refresh))

    for path in SRC_ROOT.rglob("*.py"):
        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.source_refresh":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_lineage_uses_public_data_types():
    """Batch modules should import public lineage data types."""
    lineage = __import__(
        "git_stage_batch.batch.lineage",
        fromlist=["lineage"],
    )
    public_names = {
        "BatchSourceLineage",
        "LineageRun",
    }
    private_names = {
        "_BatchSourceLineage",
        "_LineageRun",
    }
    expected_imports = {
        SRC_ROOT / "batch" / "ownership.py": {
            "BatchSourceLineage",
        },
        SRC_ROOT / "batch" / "source_advancement.py": public_names,
        SRC_ROOT / "batch" / "source_refresh.py": {
            "BatchSourceLineage",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(lineage)
    assert private_names.isdisjoint(vars(lineage))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "lineage.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.lineage":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_ownership_uses_public_lineage_remapping():
    """Cross-module ownership callers should import public lineage remapping."""
    ownership = __import__(
        "git_stage_batch.batch.ownership",
        fromlist=["ownership"],
    )
    public_names = {
        "remap_batch_ownership_with_lineage",
    }
    private_names = {
        "_remap_batch_ownership_with_lineage",
    }
    moved_names = {
        "advance_source_lines_preserving_existing_presence",
        "advance_batch_source_for_file_with_provenance",
        "SourceContentWithLineProvenance",
        "BatchSourceAdvanceResult",
    }
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "selection"
        / "discard_line_replacement.py": public_names,
        SRC_ROOT / "batch" / "source_advancement.py": public_names,
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(ownership)
    assert private_names.isdisjoint(vars(ownership))
    assert moved_names.isdisjoint(vars(ownership))

    ownership_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(
            SRC_ROOT / "batch" / "ownership.py"
        )
    }
    assert "git_stage_batch.batch.merge" not in ownership_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "ownership.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.ownership":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_source_advancement_uses_public_entry_helpers():
    """Source advancement callers should import public advancement helpers."""
    source_advancement = __import__(
        "git_stage_batch.batch.source_advancement",
        fromlist=["source_advancement"],
    )
    public_names = {
        "advance_source_lines_preserving_existing_presence",
        "advance_batch_source_for_file_with_provenance",
    }
    private_names = {
        "_advance_source_lines_preserving_existing_presence",
        "_advance_batch_source_for_file_with_provenance",
    }
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "selection"
        / "discard_line_replacement.py": {
            "advance_source_lines_preserving_existing_presence",
        },
        SRC_ROOT / "batch" / "source_refresh.py": {
            "advance_batch_source_for_file_with_provenance",
        },
        SRC_ROOT / "data" / "consumed_selections.py": {
            "advance_batch_source_for_file_with_provenance",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(source_advancement)
    assert private_names.isdisjoint(vars(source_advancement))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "source_advancement.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.source_advancement":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_ownership_uses_public_absence_builder():
    """Cross-module ownership callers should import the public absence builder."""
    ownership = __import__(
        "git_stage_batch.batch.ownership",
        fromlist=["ownership"],
    )
    public_names = {
        "AbsenceContentBuilder",
    }
    private_names = {
        "_AbsenceContentBuilder",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "batch_transform" / "sift_results.py": public_names,
    }
    violations = []

    assert "AbsenceContentBuilder" in vars(ownership)
    assert private_names.isdisjoint(vars(ownership))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "ownership.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.ownership":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_storage_uses_public_content_helpers():
    """Cross-module storage callers should import public content helpers."""
    storage = __import__(
        "git_stage_batch.batch.storage",
        fromlist=["storage"],
    )
    public_names = {
        "add_binary_file_to_batch",
        "build_realized_buffer_from_lines",
        "remove_file_from_batch_commit",
        "update_batch_commit",
    }
    private_names = {
        "_build_realized_buffer_from_lines",
        "_remove_file_from_batch_commit",
        "_update_batch_commit",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "batch_transform" / "sift_results.py": {
            "build_realized_buffer_from_lines",
        },
        SRC_ROOT / "commands" / "batch_transform" / "sift_persistence.py": {
            "add_binary_file_to_batch",
            "remove_file_from_batch_commit",
            "update_batch_commit",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(storage)
    assert private_names.isdisjoint(vars(storage))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "storage.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.storage":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_transform_sift_results_own_result_planning():
    """Sift result planning should live outside the command entry point."""
    sift_results = __import__(
        "git_stage_batch.commands.batch_transform.sift_results",
        fromlist=["sift_results"],
    )
    sift_path = SRC_ROOT / "commands" / "sift.py"
    public_names = {
        "SiftedBinaryFileResult",
        "SiftedFileResult",
        "SiftedTextFileResult",
        "build_ownership_from_working_and_target_lines",
        "compute_sifted_binary_file",
        "compute_sifted_text_file",
        "validate_sifted_text_file_result_from_lines",
    }
    disallowed_imports = {
        "git_stage_batch.batch.comparison": {
            "SemanticChangeKind",
            "derive_semantic_change_runs",
        },
        "git_stage_batch.batch.merge": {
            "merge_batch_from_line_sequences_as_buffer",
        },
        "git_stage_batch.batch.ownership": {
            "AbsenceClaim",
            "AbsenceContentBuilder",
        },
        "git_stage_batch.batch.storage": {
            "build_realized_buffer_from_lines",
        },
        "git_stage_batch.core.buffer": {
            "buffer_byte_count",
            "buffer_matches",
        },
        "git_stage_batch.core.line_selection": {
            "LineRanges",
        },
        "git_stage_batch.core.models": {
            "BinaryFileChange",
        },
        "git_stage_batch.core.text_lifecycle": {
            "sifted_empty_text_path_change_type",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_git_object_as_buffer_or_empty",
            "load_working_tree_file_as_buffer",
        },
        "git_stage_batch.utils.text": {
            "normalize_line_sequence_endings",
        },
    }
    old_helper_names = {
        "_compute_sifted_binary_file",
        "_compute_sifted_text_file",
        "build_ownership_from_working_and_target_lines",
        "validate_sifted_text_file_result_from_lines",
    }
    imports_sift_results = False
    direct_result_imports = set()

    for imported_module, node in _import_from_nodes(sift_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_transform"
            and "sift_results" in imported_names
        ):
            imports_sift_results = True
        direct_result_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    sift_tree = ast.parse(sift_path.read_text(), filename=str(sift_path))
    sift_helpers = {
        node.name
        for node in ast.walk(sift_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    result_mapping_accesses = []

    for node in ast.walk(sift_tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "result"
        ):
            result_mapping_accesses.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "result"
        ):
            result_mapping_accesses.append(node.lineno)

    assert public_names <= vars(sift_results).keys()
    assert imports_sift_results
    assert direct_result_imports == set()
    assert old_helper_names.isdisjoint(sift_helpers)
    assert result_mapping_accesses == []

    for class_name in ("SiftedBinaryFileResult", "SiftedTextFileResult"):
        result_class = getattr(sift_results, class_name)
        result_methods = {
            name
            for name, value in vars(result_class).items()
            if callable(value)
        }
        assert {"__contains__", "__getitem__", "get"}.isdisjoint(result_methods)


def test_batch_transform_sift_persistence_owns_file_writes():
    """Sift file persistence should live outside the command entry point."""
    sift_persistence = __import__(
        "git_stage_batch.commands.batch_transform.sift_persistence",
        fromlist=["sift_persistence"],
    )
    sift_path = SRC_ROOT / "commands" / "sift.py"
    public_names = {
        "RetainedSiftedFile",
        "add_sifted_file_to_batch",
        "add_sifted_text_file_to_batch",
        "create_synthetic_batch_source_commit",
        "replace_batch_with_sifted_files",
    }
    disallowed_imports = {
        "git_stage_batch.batch.ownership": {
            "BatchOwnership",
        },
        "git_stage_batch.batch.query": {
            "get_batch_baseline_commit",
        },
        "git_stage_batch.batch.state_refs": {
            "delete_batch_state_refs",
            "get_batch_content_ref_name",
            "sync_batch_state_refs",
        },
        "git_stage_batch.batch.storage": {
            "add_binary_file_to_batch",
            "remove_file_from_batch_commit",
            "update_batch_commit",
        },
        "git_stage_batch.core.text_lifecycle": {
            "TextFileChangeType",
            "normalized_text_change_type",
        },
        "git_stage_batch.utils.git_command": {
            "run_git_command",
        },
        "git_stage_batch.utils.git_index": {
            "git_commit_tree",
            "git_read_tree",
            "git_update_index",
            "git_write_tree",
            "temp_git_index",
        },
        "git_stage_batch.utils.git_object_io": {
            "create_git_blob",
        },
        "git_stage_batch.utils.file_io": {
            "write_text_file_contents",
        },
    }
    old_helper_names = {
        "_perform_atomic_in_place_sift",
        "_source_buffers_from_sift_results",
        "_target_buffer_from_sift_result",
        "add_sifted_text_file_to_batch",
        "create_synthetic_batch_source_commit",
    }
    imports_sift_persistence = False
    direct_persistence_imports = set()
    persistence_calls = set()

    for imported_module, node in _import_from_nodes(sift_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_transform"
            and "sift_persistence" in imported_names
        ):
            imports_sift_persistence = True
        direct_persistence_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    sift_tree = ast.parse(sift_path.read_text(), filename=str(sift_path))
    sift_helpers = {
        node.name
        for node in ast.walk(sift_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    for node in ast.walk(sift_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_sift_persistence"
        ):
            persistence_calls.add(node.func.attr)

    assert public_names <= vars(sift_persistence).keys()
    assert imports_sift_persistence
    assert direct_persistence_imports == set()
    assert old_helper_names.isdisjoint(sift_helpers)
    assert "add_sifted_file_to_batch" in persistence_calls
    assert "replace_batch_with_sifted_files" in persistence_calls
    assert "add_sifted_text_file_to_batch" not in persistence_calls


def test_batch_ownership_units_bridge_keeps_display_out_of_ownership():
    """Source-line unit construction should live in the bridge module."""
    ownership = __import__(
        "git_stage_batch.batch.ownership",
        fromlist=["ownership"],
    )
    bridge = __import__(
        "git_stage_batch.batch.ownership_units",
        fromlist=["ownership_units"],
    )
    ownership_path = SRC_ROOT / "batch" / "ownership.py"
    bridge_path = SRC_ROOT / "batch" / "ownership_units.py"
    expected_bridge_callers = {
        SRC_ROOT / "batch" / "selection.py",
        SRC_ROOT / "commands" / "batch_source" / "reset_claims.py",
    }
    moved_name = "build_ownership_units_from_batch_source_lines"
    violations = []

    assert moved_name not in vars(ownership)
    assert moved_name in vars(bridge)

    ownership_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(ownership_path)
    }
    bridge_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(bridge_path)
    }

    assert "git_stage_batch.batch.display" not in ownership_imports
    assert "git_stage_batch.batch.display" in bridge_imports
    assert "git_stage_batch.batch.ownership" in bridge_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path in {ownership_path, bridge_path}:
            continue

        imports = _import_from_nodes(path)
        imports_bridge = False

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.batch.ownership"
                and moved_name in imported_names
            ):
                relative_path = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports {moved_name}"
                )
            if (
                imported_module == "git_stage_batch.batch.ownership_units"
                and moved_name in imported_names
            ):
                imports_bridge = True

        if path in expected_bridge_callers:
            assert imports_bridge

    assert violations == []


def test_batch_realized_entries_uses_public_entry_helpers():
    """Batch callers should import public realized-entry helpers."""
    realized_entries = __import__(
        "git_stage_batch.batch.realized_entries",
        fromlist=["realized_entries"],
    )
    public_names = {
        "RealizedEntry",
        "realized_entry_content_chunks",
    }
    private_names = {
        "_realized_entry_content_chunks",
    }
    expected_imports = {
        SRC_ROOT / "batch" / "merge.py": public_names,
        SRC_ROOT / "batch" / "source_advancement.py": {
            "realized_entry_content_chunks",
        },
        SRC_ROOT / "batch" / "storage.py": {
            "realized_entry_content_chunks",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(realized_entries)
    assert private_names.isdisjoint(vars(realized_entries))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "realized_entries.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.realized_entries":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_batch_merge_candidates_uses_public_data_types():
    """Batch callers should import public merge-candidate data types."""
    merge_candidates = __import__(
        "git_stage_batch.batch.merge_candidates",
        fromlist=["merge_candidates"],
    )
    merge = __import__(
        "git_stage_batch.batch.merge",
        fromlist=["merge"],
    )
    public_names = {
        "MergeCandidate",
        "MergeCandidateSet",
        "MergeResolution",
        "MergeResolutionDecision",
    }
    private_names = {
        "_MergeCandidate",
        "_MergeCandidateSet",
        "_MergeResolution",
        "_MergeResolutionDecision",
    }
    expected_imports = {
        SRC_ROOT / "batch" / "merge.py": public_names,
        SRC_ROOT / "batch" / "operation_candidates.py": {
            "MergeCandidate",
            "MergeResolution",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(merge_candidates)
    assert private_names.isdisjoint(vars(merge_candidates))
    assert public_names.isdisjoint(vars(merge))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "merge_candidates.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.batch.merge_candidates":
                imported_public_names |= imported_names & public_names
                disallowed_names = imported_names & private_names
                if disallowed_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(disallowed_names))
                    violations.append(
                        f"{relative_path}:{node.lineno} imports {names}"
                    )
            if imported_module == "git_stage_batch.batch.merge":
                moved_names = imported_names & public_names
                if moved_names:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_names))
                    violations.append(
                        f"{relative_path}:{node.lineno} imports {names}"
                    )

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


def test_operation_candidates_owns_candidate_preview_count():
    """Candidate preview count state should live with operation candidates."""
    operation_candidates = __import__(
        "git_stage_batch.batch.operation_candidates",
        fromlist=["operation_candidates"],
    )
    public_names = {
        "CandidatePreviewCount",
    }
    private_names = {
        "_ApplyCandidateCount",
        "_IncludeCandidateCount",
    }
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "batch_source"
        / "candidate_preview_counts.py": public_names,
        SRC_ROOT / "commands" / "batch_source" / "candidate_refusals.py": public_names,
    }

    assert public_names <= vars(operation_candidates).keys()
    assert private_names.isdisjoint(vars(operation_candidates))

    for path, expected_names in expected_imports.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        helper_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        imported_candidate_names = set()

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.batch.operation_candidates":
                continue
            imported_candidate_names |= {alias.name for alias in node.names}

        assert private_names.isdisjoint(helper_names)
        assert expected_names <= imported_candidate_names


def test_output_owns_operation_candidate_preview_rendering():
    """Operation candidate preview rendering should live in output."""
    candidate_preview = __import__(
        "git_stage_batch.output.candidate_preview",
        fromlist=["candidate_preview"],
    )
    candidate_preview_summary = __import__(
        "git_stage_batch.output.candidate_preview_summary",
        fromlist=["candidate_preview_summary"],
    )
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    candidate_preview_path = SRC_ROOT / "output" / "candidate_preview.py"
    candidate_summary_path = SRC_ROOT / "output" / "candidate_preview_summary.py"
    public_renderer_names = {
        "render_operation_candidate",
        "render_operation_candidate_overview",
    }
    renderer_helper_names = {
        "_execute_candidate_command",
        "_print_candidate_buffer_diff",
        "_show_candidate_command",
    }
    summary_names = {
        "CandidateSnippetLine",
        "CandidateTargetSummary",
        "candidate_overview_subject",
        "candidate_target_summary",
        "common_candidate_target_indexes",
        "plain_candidate_snippet_lines",
        "summarize_ambiguity_block",
    }
    old_renderer_summary_names = {
        "_CandidateSnippetLine",
        "_CandidateTargetSummary",
        "_candidate_overview_subject",
        "_summarize_ambiguity_block",
        "_summarize_candidate_target",
    }
    show_from_tree = ast.parse(show_from_path.read_text(), filename=str(show_from_path))
    show_from_helpers = {
        node.name
        for node in ast.walk(show_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    show_from_renderer_imports = set()
    candidate_preview_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(candidate_preview_path)
    }
    candidate_summary_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(candidate_summary_path)
    }

    for imported_module, node in _import_from_nodes(show_from_path):
        if imported_module != "git_stage_batch.output.candidate_preview":
            continue
        show_from_renderer_imports |= {alias.name for alias in node.names}

    assert public_renderer_names <= vars(candidate_preview).keys()
    assert renderer_helper_names <= vars(candidate_preview).keys()
    assert summary_names <= vars(candidate_preview_summary).keys()
    assert public_renderer_names <= show_from_renderer_imports
    assert renderer_helper_names.isdisjoint(show_from_helpers)
    assert summary_names.isdisjoint(vars(candidate_preview))
    assert old_renderer_summary_names.isdisjoint(vars(candidate_preview))
    assert "git_stage_batch.output" in candidate_preview_imports
    assert "git_stage_batch.output.colors" in candidate_preview_imports
    assert "git_stage_batch.core.diff_parser" in candidate_preview_imports
    assert "git_stage_batch.output.colors" not in candidate_summary_imports
    assert "git_stage_batch.core.diff_parser" not in candidate_summary_imports


def test_batch_owns_atomic_file_change_metadata_conversion():
    """Stored atomic file metadata conversion should live in batch."""
    atomic_file_changes = __import__(
        "git_stage_batch.batch.atomic_file_changes",
        fromlist=["atomic_file_changes"],
    )
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    public_names = {
        "binary_change_from_batch_file_metadata",
        "gitlink_change_from_batch_file_metadata",
    }
    old_names = {
        "_render_batch_binary_file_change",
        "_render_batch_gitlink_change",
    }
    model_names = {
        "BinaryFileChange",
        "GitlinkChange",
    }
    show_from_tree = ast.parse(show_from_path.read_text(), filename=str(show_from_path))
    show_from_helpers = {
        node.name
        for node in ast.walk(show_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    show_from_atomic_imports = set()
    show_from_model_imports = set()

    for imported_module, node in _import_from_nodes(show_from_path):
        imported_names = {alias.name for alias in node.names}
        if imported_module == "git_stage_batch.batch.atomic_file_changes":
            show_from_atomic_imports |= imported_names
        if imported_module == "git_stage_batch.core.models":
            show_from_model_imports |= imported_names & model_names

    assert public_names <= vars(atomic_file_changes).keys()
    assert model_names <= vars(atomic_file_changes).keys()
    assert public_names <= show_from_atomic_imports
    assert old_names.isdisjoint(show_from_helpers)
    assert show_from_model_imports == set()


def test_batch_owns_binary_file_content_loading():
    """Stored binary batch content loading should live in batch."""
    binary_file_content = __import__(
        "git_stage_batch.batch.binary_file_content",
        fromlist=["binary_file_content"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "read_binary_file_from_batch",
    }
    old_names = {
        "_read_binary_file_from_batch",
    }
    apply_from_tree = ast.parse(apply_from_path.read_text(), filename=str(apply_from_path))
    apply_from_helpers = {
        node.name
        for node in ast.walk(apply_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    include_from_tree = ast.parse(
        include_from_path.read_text(),
        filename=str(include_from_path),
    )
    include_from_helpers = {
        node.name
        for node in ast.walk(include_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    loader_imports_by_path = {
        apply_from_path: set(),
        include_from_path: set(),
    }

    for path in loader_imports_by_path:
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.batch.binary_file_content":
                continue
            loader_imports_by_path[path] |= {alias.name for alias in node.names}

    assert public_names <= vars(binary_file_content).keys()
    assert public_names <= loader_imports_by_path[apply_from_path]
    assert public_names <= loader_imports_by_path[include_from_path]
    assert old_names.isdisjoint(apply_from_helpers)
    assert old_names.isdisjoint(include_from_helpers)


def test_batch_source_action_plans_own_resource_plans():
    """Shared batch-source action plans should live outside command entries."""
    action_plans = __import__(
        "git_stage_batch.commands.batch_source.action_plans",
        fromlist=["action_plans"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "ApplyTextFileActionPlan",
        "BatchSourceActionPlan",
        "BinaryFileActionPlan",
        "IncludeTextFileActionPlan",
        "SubmodulePointerActionPlan",
        "close_action_plans",
    }
    old_apply_names = {
        "_ApplyBinaryPlan",
        "_ApplyTextPlan",
        "_ApplySubmodulePlan",
        "_close_apply_plans",
    }
    old_include_names = {
        "_IncludeBinaryPlan",
        "_IncludeTextPlan",
        "_IncludeSubmodulePlan",
        "_close_include_plans",
    }
    apply_from_tree = ast.parse(apply_from_path.read_text(), filename=str(apply_from_path))
    apply_from_helpers = {
        node.name
        for node in ast.walk(apply_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    include_from_tree = ast.parse(
        include_from_path.read_text(),
        filename=str(include_from_path),
    )
    include_from_helpers = {
        node.name
        for node in ast.walk(include_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    imports_action_plans = {
        apply_from_path: False,
        include_from_path: False,
    }

    for path in imports_action_plans:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "action_plans" in imported_names
            ):
                imports_action_plans[path] = True

    assert public_names <= vars(action_plans).keys()
    assert imports_action_plans[apply_from_path]
    assert imports_action_plans[include_from_path]
    assert old_apply_names.isdisjoint(apply_from_helpers)
    assert old_include_names.isdisjoint(include_from_helpers)


def test_batch_source_text_plan_builders_own_apply_text_planning():
    """Regular apply-from text plan construction should live in batch-source support."""
    text_plan_builders = __import__(
        "git_stage_batch.commands.batch_source.text_plan_builders",
        fromlist=["text_plan_builders"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    public_names = {
        "ApplyTextPlanBuildResult",
        "build_apply_text_file_action_plan",
    }
    disallowed_imports = {
        "git_stage_batch.batch.merge": {
            "merge_batch_from_line_sequences_as_buffer",
        },
        "git_stage_batch.batch.selection": {
            "acquire_batch_ownership_for_display_ids_from_lines",
        },
        "git_stage_batch.core.buffer": {
            "LineBuffer",
        },
        "git_stage_batch.core.text_lifecycle": {
            "TextFileChangeType",
            "mode_for_text_materialization",
            "normalized_text_change_type",
            "selected_text_target_change_type",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_git_object_as_buffer",
            "load_working_tree_file_as_buffer",
        },
        "git_stage_batch.utils.git_repository": {
            "get_git_repository_root_path",
        },
    }
    imports_text_plan_builders = False
    direct_plan_imports = set()

    for imported_module, node in _import_from_nodes(apply_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "text_plan_builders" in imported_names
        ):
            imports_text_plan_builders = True
        direct_plan_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    command_text = apply_from_path.read_text()

    assert public_names <= vars(text_plan_builders).keys()
    assert imports_text_plan_builders
    assert direct_plan_imports == set()
    assert "build_apply_text_file_action_plan(" in command_text
    assert "merge_batch_from_line_sequences_as_buffer(" not in command_text
    assert "selected_text_target_change_type(" not in command_text


def test_batch_source_text_plan_builders_own_include_text_planning():
    """Regular include-from text plan construction should live in batch-source support."""
    text_plan_builders = __import__(
        "git_stage_batch.commands.batch_source.text_plan_builders",
        fromlist=["text_plan_builders"],
    )
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "IncludeTextPlanBuildResult",
        "build_include_text_file_action_plan",
    }
    disallowed_imports = {
        "git_stage_batch.batch.merge": {
            "merge_batch_from_line_sequences_as_buffer",
        },
        "git_stage_batch.batch.replacement": {
            "build_replacement_batch_view_from_lines",
        },
        "git_stage_batch.batch.selection": {
            "acquire_batch_ownership_for_display_ids_from_lines",
        },
        "git_stage_batch.core.buffer": {
            "LineBuffer",
        },
        "git_stage_batch.core.text_lifecycle": {
            "TextFileChangeType",
            "mode_for_text_materialization",
            "normalized_text_change_type",
            "selected_text_target_change_type",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_git_object_as_buffer",
            "load_working_tree_file_as_buffer",
        },
        "git_stage_batch.utils.git_repository": {
            "get_git_repository_root_path",
        },
    }
    imports_text_plan_builders = False
    direct_plan_imports = set()

    for imported_module, node in _import_from_nodes(include_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "text_plan_builders" in imported_names
        ):
            imports_text_plan_builders = True
        direct_plan_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    command_text = include_from_path.read_text()

    assert public_names <= vars(text_plan_builders).keys()
    assert imports_text_plan_builders
    assert direct_plan_imports == set()
    assert "build_include_text_file_action_plan(" in command_text
    assert "merge_batch_from_line_sequences_as_buffer(" not in command_text
    assert "build_replacement_batch_view_from_lines(" not in command_text
    assert "selected_text_target_change_type(" not in command_text


def test_batch_source_text_plan_builders_own_discard_text_planning():
    """Regular discard-from text plan construction should live in batch-source support."""
    text_plan_builders = __import__(
        "git_stage_batch.commands.batch_source.text_plan_builders",
        fromlist=["text_plan_builders"],
    )
    discard_from_path = SRC_ROOT / "commands" / "discard_from.py"
    public_names = {
        "DiscardTextPlanBuildResult",
        "build_discard_text_file_action_plan",
    }
    disallowed_imports = {
        "git_stage_batch.batch.merge": {
            "discard_batch_from_line_sequences_as_buffer",
        },
        "git_stage_batch.batch.selection": {
            "acquire_batch_ownership_for_display_ids_from_lines",
        },
        "git_stage_batch.core.buffer": {
            "LineBuffer",
        },
        "git_stage_batch.core.text_lifecycle": {
            "TextFileChangeType",
            "mode_for_text_materialization",
            "normalized_text_change_type",
            "selected_text_discard_change_type",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_working_tree_file_as_buffer",
        },
    }
    imports_text_plan_builders = False
    direct_plan_imports = set()

    for imported_module, node in _import_from_nodes(discard_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "text_plan_builders" in imported_names
        ):
            imports_text_plan_builders = True
        direct_plan_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    command_text = discard_from_path.read_text()

    assert public_names <= vars(text_plan_builders).keys()
    assert imports_text_plan_builders
    assert direct_plan_imports == set()
    assert "build_discard_text_file_action_plan(" in command_text
    assert "_discard_text_file_lifecycle_from_batch" not in command_text
    assert "discard_batch_from_line_sequences_as_buffer(" not in command_text
    assert "selected_text_discard_change_type(" not in command_text


def test_batch_source_candidate_previews_own_candidate_preview_checks():
    """Shared candidate preview checks should live outside command entries."""
    candidate_previews = __import__(
        "git_stage_batch.commands.batch_source.candidate_previews",
        fromlist=["candidate_previews"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    public_names = {
        "candidate_preview_for_ordinal",
        "candidate_preview_state_matches",
        "close_candidate_previews",
        "require_candidate_preview_for_ordinal",
        "require_candidate_preview_state",
    }
    command_paths = {
        apply_from_path,
        include_from_path,
        show_from_path,
    }
    imports_candidate_previews = {
        path: False
        for path in command_paths
    }
    direct_state_imports = {
        path: set()
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_previews" in imported_names
            ):
                imports_candidate_previews[path] = True
            if imported_module == "git_stage_batch.batch.operation_candidates":
                direct_state_imports[path] |= (
                    imported_names & {"load_candidate_preview_state"}
                )

    assert public_names <= vars(candidate_previews).keys()
    assert imports_candidate_previews == {
        apply_from_path: False,
        include_from_path: False,
        show_from_path: True,
    }
    assert direct_state_imports == {
        apply_from_path: set(),
        include_from_path: set(),
        show_from_path: set(),
    }
    for path in command_paths:
        command_text = path.read_text()
        assert "_resolve_candidate_ordinal" not in command_text
        assert ".candidate_preview_for_ordinal(" not in command_text
        assert ".candidate_preview_state_matches(" not in command_text


def test_batch_source_candidate_preview_builders_own_show_candidate_construction():
    """Show-from candidate construction should live in batch-source support."""
    candidate_preview_builders = __import__(
        "git_stage_batch.commands.batch_source.candidate_preview_builders",
        fromlist=["candidate_preview_builders"],
    )
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    public_names = {
        "build_batch_source_candidate_previews",
    }
    old_function_names = {
        "_build_candidate_previews",
    }
    old_import_names = {
        "build_apply_candidate_previews",
        "build_include_candidate_previews",
    }
    show_from_tree = ast.parse(
        show_from_path.read_text(),
        filename=str(show_from_path),
    )
    show_from_helpers = {
        node.name
        for node in ast.walk(show_from_tree)
        if isinstance(node, ast.FunctionDef)
    }
    imports_candidate_preview_builders = False
    direct_operation_builder_imports = set()

    for imported_module, node in _import_from_nodes(show_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "candidate_preview_builders" in imported_names
        ):
            imports_candidate_preview_builders = True
        if imported_module == "git_stage_batch.batch.operation_candidates":
            direct_operation_builder_imports |= imported_names & old_import_names

    assert public_names <= vars(candidate_preview_builders).keys()
    assert imports_candidate_preview_builders
    assert old_function_names.isdisjoint(show_from_helpers)
    assert direct_operation_builder_imports == set()


def test_batch_source_candidate_preview_counts_own_failure_enumeration():
    """Apply/include candidate count enumeration should live in batch-source support."""
    candidate_preview_counts = __import__(
        "git_stage_batch.commands.batch_source.candidate_preview_counts",
        fromlist=["candidate_preview_counts"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "count_apply_candidate_previews_for_file",
        "count_include_candidate_previews_for_file",
    }
    old_function_names = {
        apply_from_path: {
            "_apply_candidate_count_for_file",
        },
        include_from_path: {
            "_include_candidate_count_for_file",
        },
    }
    old_import_names = {
        "CandidateEnumerationLimitError",
        "CandidatePreviewCount",
    }
    command_paths = set(old_function_names)
    imports_candidate_preview_counts = {
        path: False
        for path in command_paths
    }
    direct_count_imports = {
        path: set()
        for path in command_paths
    }
    helper_names = {}

    for path in command_paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        helper_names[path] = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_preview_counts" in imported_names
            ):
                imports_candidate_preview_counts[path] = True
            if imported_module == "git_stage_batch.batch.operation_candidates":
                direct_count_imports[path] |= imported_names & old_import_names

    assert public_names <= vars(candidate_preview_counts).keys()
    assert imports_candidate_preview_counts == {
        apply_from_path: True,
        include_from_path: True,
    }
    assert direct_count_imports == {
        apply_from_path: set(),
        include_from_path: set(),
    }
    for path, old_names in old_function_names.items():
        assert old_names.isdisjoint(helper_names[path])


def test_batch_source_candidate_inputs_own_text_candidate_metadata():
    """Candidate input metadata should live in batch-source support."""
    candidate_inputs = __import__(
        "git_stage_batch.commands.batch_source.candidate_inputs",
        fromlist=["candidate_inputs"],
    )
    support_paths = {
        SRC_ROOT / "commands" / "batch_source" / "candidate_preview_builders.py",
        SRC_ROOT / "commands" / "batch_source" / "candidate_preview_counts.py",
        SRC_ROOT / "commands" / "batch_source" / "candidate_materialization.py",
    }
    public_names = {
        "CandidateBatchSourceRef",
        "CandidateIndexTarget",
        "CandidateWorktreeTarget",
        "candidate_batch_source_ref",
        "candidate_index_text_target",
        "candidate_worktree_text_target",
        "is_text_candidate_entry",
        "require_candidate_batch_source_ref",
    }
    disallowed_imports = {
        "git_stage_batch.batch.submodule_pointer": {
            "is_batch_submodule_pointer",
        },
        "git_stage_batch.core.text_lifecycle": {
            "mode_for_text_materialization",
            "normalized_text_change_type",
        },
        "git_stage_batch.utils.git_repository": {
            "get_git_repository_root_path",
        },
    }
    imports_candidate_inputs = {
        path: False
        for path in support_paths
    }
    direct_metadata_imports = {
        path: set()
        for path in support_paths
    }

    for path in support_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_inputs" in imported_names
            ):
                imports_candidate_inputs[path] = True
            direct_metadata_imports[path] |= imported_names & disallowed_imports.get(
                imported_module,
                set(),
            )

    assert public_names <= vars(candidate_inputs).keys()
    assert imports_candidate_inputs == {
        path: True
        for path in support_paths
    }
    assert direct_metadata_imports == {
        path: set()
        for path in support_paths
    }


def test_batch_source_candidate_materialization_owns_reviewed_candidate_loading():
    """Apply/include reviewed candidate loading should live in batch-source support."""
    candidate_materialization = __import__(
        "git_stage_batch.commands.batch_source.candidate_materialization",
        fromlist=["candidate_materialization"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    materialization_path = (
        SRC_ROOT / "commands" / "batch_source" / "candidate_materialization.py"
    )
    public_names = {
        "ApplyCandidateMaterialization",
        "IncludeCandidateMaterialization",
        "materialize_apply_candidate",
        "materialize_include_candidate",
    }
    builder_names = {
        "build_apply_candidate_previews",
        "build_include_candidate_previews",
    }
    command_paths = {
        apply_from_path,
        include_from_path,
    }
    imports_candidate_materialization = {
        path: False
        for path in command_paths
    }
    imports_candidate_previews = {
        path: False
        for path in command_paths
    }
    direct_builder_imports = {
        path: set()
        for path in command_paths
    }
    materialization_imports_candidate_previews = False
    materialization_builder_imports = set()

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_materialization" in imported_names
            ):
                imports_candidate_materialization[path] = True
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_previews" in imported_names
            ):
                imports_candidate_previews[path] = True
            if imported_module == "git_stage_batch.batch.operation_candidates":
                direct_builder_imports[path] |= imported_names & builder_names

    for imported_module, node in _import_from_nodes(materialization_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "candidate_previews" in imported_names
        ):
            materialization_imports_candidate_previews = True
        if imported_module == "git_stage_batch.batch.operation_candidates":
            materialization_builder_imports |= imported_names & builder_names

    assert public_names <= vars(candidate_materialization).keys()
    assert imports_candidate_materialization == {
        apply_from_path: True,
        include_from_path: True,
    }
    assert imports_candidate_previews == {
        apply_from_path: False,
        include_from_path: False,
    }
    assert direct_builder_imports == {
        apply_from_path: set(),
        include_from_path: set(),
    }
    assert materialization_imports_candidate_previews
    assert materialization_builder_imports == builder_names

    for path in command_paths:
        command_text = path.read_text()
        assert ".require_candidate_preview_for_ordinal(" not in command_text
        assert ".require_candidate_preview_state(" not in command_text
        assert ".close_candidate_previews(" not in command_text
        assert "build_apply_candidate_previews(" not in command_text
        assert "build_include_candidate_previews(" not in command_text


def test_batch_source_replacement_previews_own_show_replacement_preview():
    """Show-from replacement preview rendering should live in batch-source support."""
    replacement_previews = __import__(
        "git_stage_batch.commands.batch_source.replacement_previews",
        fromlist=["replacement_previews"],
    )
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    public_names = {
        "print_batch_source_replacement_preview",
    }
    old_function_names = {
        "_preview_replacement_batch_view",
    }
    old_import_names_by_module = {
        "git_stage_batch.batch.operation_candidates": {
            "render_candidate_buffer_diff",
        },
        "git_stage_batch.batch.replacement": {
            "build_replacement_batch_view_from_lines",
        },
        "git_stage_batch.batch.selection": {
            "acquire_batch_ownership_for_display_ids_from_lines",
        },
        "git_stage_batch.commands.selection": {
            "replacement_selection",
        },
        "git_stage_batch.core.replacement": {
            "coerce_replacement_payload",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_git_object_as_buffer",
        },
    }
    show_from_tree = ast.parse(
        show_from_path.read_text(),
        filename=str(show_from_path),
    )
    show_from_helpers = {
        node.name
        for node in ast.walk(show_from_tree)
        if isinstance(node, ast.FunctionDef)
    }
    imports_replacement_previews = False
    direct_preview_imports = set()

    for imported_module, node in _import_from_nodes(show_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "replacement_previews" in imported_names
        ):
            imports_replacement_previews = True
        direct_preview_imports |= imported_names & old_import_names_by_module.get(
            imported_module,
            set(),
        )

    assert public_names <= vars(replacement_previews).keys()
    assert imports_replacement_previews
    assert old_function_names.isdisjoint(show_from_helpers)
    assert direct_preview_imports == set()


def test_batch_source_candidate_refusals_own_candidate_count_refusals():
    """Shared candidate count refusals should live outside command entries."""
    candidate_refusals = __import__(
        "git_stage_batch.commands.batch_source.candidate_refusals",
        fromlist=["candidate_refusals"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "refuse_candidate_conflicts",
    }
    old_snippets_by_path = {
        apply_from_path: {
            "too many apply candidates",
            "Cannot enumerate apply candidates",
            "multiple files need apply decisions",
        },
        include_from_path: {
            "too many include candidates",
            "Cannot enumerate include candidates",
            "multiple files need include decisions",
        },
    }
    command_paths = set(old_snippets_by_path)
    imports_candidate_refusals = {
        path: False
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_refusals" in imported_names
            ):
                imports_candidate_refusals[path] = True

    assert public_names <= vars(candidate_refusals).keys()
    assert imports_candidate_refusals == {
        apply_from_path: True,
        include_from_path: True,
    }
    for path, old_snippets in old_snippets_by_path.items():
        command_text = path.read_text()
        for snippet in old_snippets:
            assert snippet not in command_text


def test_batch_source_action_context_owns_action_prologue():
    """Shared batch-source setup should live outside action entries."""
    action_context = __import__(
        "git_stage_batch.commands.batch_source.action_context",
        fromlist=["action_context"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    discard_from_path = SRC_ROOT / "commands" / "discard_from.py"
    public_names = {
        "BatchSourceActionContext",
        "resolve_batch_source_action_context",
        "resolve_plain_batch_source_action_context",
    }
    disallowed_imports = {
        "git_stage_batch.batch.metadata_validation": {
            "read_validated_batch_metadata",
        },
        "git_stage_batch.batch.source_selector": {
            "require_plain_batch_name",
        },
        "git_stage_batch.batch.validation": {
            "batch_exists",
        },
        "git_stage_batch.data.file_review.action_scope": {
            "resolve_batch_source_action_scope",
        },
    }
    disallowed_imports_by_path = {
        apply_from_path: {
            "git_stage_batch.exceptions": {
                "BatchMetadataError",
            },
        },
        include_from_path: {
            "git_stage_batch.exceptions": {
                "BatchMetadataError",
            },
        },
        discard_from_path: {},
    }
    command_paths = {
        apply_from_path,
        discard_from_path,
        include_from_path,
    }
    imports_action_context = {
        path: False
        for path in command_paths
    }
    direct_setup_imports = {
        path: set()
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "action_context" in imported_names
            ):
                imports_action_context[path] = True
            direct_setup_imports[path] |= imported_names & disallowed_imports.get(
                imported_module,
                set(),
            )
            path_disallowed_imports = disallowed_imports_by_path[path]
            direct_setup_imports[path] |= imported_names & path_disallowed_imports.get(
                imported_module,
                set(),
            )

    assert public_names <= vars(action_context).keys()
    assert imports_action_context == {
        apply_from_path: True,
        discard_from_path: True,
        include_from_path: True,
    }
    assert direct_setup_imports == {
        apply_from_path: set(),
        discard_from_path: set(),
        include_from_path: set(),
    }


def test_batch_source_action_selection_owns_file_line_selection():
    """Shared batch-source file and line selection should live outside entries."""
    action_selection = __import__(
        "git_stage_batch.commands.batch_source.action_selection",
        fromlist=["action_selection"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    discard_from_path = SRC_ROOT / "commands" / "discard_from.py"
    public_names = {
        "BatchSourceActionSelection",
        "resolve_apply_action_selection",
        "resolve_discard_action_selection",
        "resolve_include_action_selection",
    }
    disallowed_imports = {
        "git_stage_batch.batch.selection": {
            "require_single_file_context_for_line_selection",
            "resolve_batch_file_scope",
            "resolve_current_batch_binary_file_scope",
        },
        "git_stage_batch.batch.submodule_pointer": {
            "refuse_batch_submodule_pointer_lines",
        },
        "git_stage_batch.commands.selection": {
            "replacement_selection",
        },
        "git_stage_batch.data.file_review.batch_selection": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
    }
    command_paths = {
        apply_from_path,
        discard_from_path,
        include_from_path,
    }
    imports_action_selection = {
        path: False
        for path in command_paths
    }
    direct_selection_imports = {
        path: set()
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "action_selection" in imported_names
            ):
                imports_action_selection[path] = True
            direct_selection_imports[path] |= imported_names & disallowed_imports.get(
                imported_module,
                set(),
            )

    assert public_names <= vars(action_selection).keys()
    assert imports_action_selection == {
        apply_from_path: True,
        discard_from_path: True,
        include_from_path: True,
    }
    assert direct_selection_imports == {
        apply_from_path: set(),
        discard_from_path: set(),
        include_from_path: set(),
    }


def test_batch_source_action_completion_owns_review_finalization():
    """Shared apply/include review completion should live outside entries."""
    action_completion = __import__(
        "git_stage_batch.commands.batch_source.action_completion",
        fromlist=["action_completion"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "finish_batch_source_action_review",
    }
    command_paths = {
        apply_from_path,
        include_from_path,
    }
    imports_action_completion = {
        path: False
        for path in command_paths
    }
    direct_review_imports = {
        path: set()
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "action_completion" in imported_names
            ):
                imports_action_completion[path] = True
            if imported_module == "git_stage_batch.data.file_review.action_scope":
                direct_review_imports[path] |= (
                    imported_names & {"finish_review_scoped_line_action"}
                )

    assert public_names <= vars(action_completion).keys()
    assert imports_action_completion == {
        apply_from_path: True,
        include_from_path: True,
    }
    assert direct_review_imports == {
        apply_from_path: set(),
        include_from_path: set(),
    }


def test_batch_source_selection_state_cleanup_owns_reset_cleanup():
    """Reset selected-state cleanup should live outside the reset entry."""
    selection_state_cleanup = __import__(
        "git_stage_batch.commands.batch_source.selection_state_cleanup",
        fromlist=["selection_state_cleanup"],
    )
    reset_path = SRC_ROOT / "commands" / "reset.py"
    public_names = {
        "clear_selected_batch_state_after_batch_mutation",
    }
    disallowed_imports = {
        "git_stage_batch.data.batch_selected_changes": {
            "selected_batch_binary_matches_batch",
            "selected_batch_gitlink_matches_batch",
        },
        "git_stage_batch.data.file_review.records": {
            "ReviewSource",
        },
        "git_stage_batch.data.file_review.state": {
            "read_last_file_review_state",
        },
        "git_stage_batch.data.selected_change.clear_reasons": {
            "mark_selected_change_cleared_by_stale_batch_selection",
        },
        "git_stage_batch.data.selected_change.lifecycle": {
            "clear_selected_change_state_files",
        },
        "git_stage_batch.data.selected_change.paths": {
            "get_selected_change_file_path",
        },
        "git_stage_batch.data.selected_change.store": {
            "SelectedChangeKind",
            "read_selected_change_kind",
        },
    }
    reset_tree = ast.parse(reset_path.read_text(), filename=str(reset_path))
    reset_helpers = {
        node.name
        for node in ast.walk(reset_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    imports_selection_state_cleanup = False
    direct_cleanup_imports = set()

    for imported_module, node in _import_from_nodes(reset_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "selection_state_cleanup" in imported_names
        ):
            imports_selection_state_cleanup = True
        direct_cleanup_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    assert public_names <= vars(selection_state_cleanup).keys()
    assert imports_selection_state_cleanup
    assert direct_cleanup_imports == set()
    assert "_clear_selected_batch_state_after_batch_mutation" not in reset_helpers


def test_batch_source_reset_claims_own_reset_mutations():
    """Reset claim mutation should live outside the reset entry."""
    reset_claims = __import__(
        "git_stage_batch.commands.batch_source.reset_claims",
        fromlist=["reset_claims"],
    )
    reset_path = SRC_ROOT / "commands" / "reset.py"
    public_names = {
        "move_claims_between_batches",
        "partition_line_ownership_units",
        "reset_all_claims_from_batch",
        "reset_file_claims_from_batch",
        "reset_line_claims_for_file",
        "reset_line_claims_from_batch",
        "reset_pattern_claims_from_batch",
    }
    old_helper_names = {
        "_acquire_line_ownership_for_file",
        "_add_ownership_to_destination",
        "_ensure_destination_batch",
        "_move_claims_between_batches",
        "_partition_line_ownership_units",
        "_reset_all_claims_from_batch",
        "_reset_file_claims_from_batch",
        "_reset_line_claims_for_file",
        "_reset_line_claims_from_batch",
        "_reset_pattern_claims_from_batch",
    }
    disallowed_imports = {
        "git_stage_batch.batch.operations": {
            "create_batch",
        },
        "git_stage_batch.batch.ownership": {
            "BatchOwnership",
            "acquire_detached_batch_ownership",
            "filter_ownership_units_by_display_ids",
            "merge_batch_ownership",
            "rebuild_ownership_from_units",
            "validate_ownership_units",
        },
        "git_stage_batch.batch.ownership_units": {
            "build_ownership_units_from_batch_source_lines",
        },
        "git_stage_batch.batch.selection": {
            "require_display_ids_available",
        },
        "git_stage_batch.batch.state_refs": {
            "sync_batch_state_refs",
        },
        "git_stage_batch.batch.storage": {
            "add_file_to_batch",
            "copy_file_from_batch_to_batch",
            "remove_file_from_batch",
        },
        "git_stage_batch.batch.submodule_pointer": {
            "is_batch_submodule_pointer",
            "refuse_batch_submodule_pointer_lines",
        },
        "git_stage_batch.utils.repository_buffers": {
            "load_git_object_as_buffer",
        },
        "git_stage_batch.utils.file_io": {
            "write_text_file_contents",
        },
        "git_stage_batch.utils.paths": {
            "get_batch_metadata_file_path",
        },
    }
    reset_tree = ast.parse(reset_path.read_text(), filename=str(reset_path))
    reset_helpers = {
        node.name
        for node in ast.walk(reset_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    imports_reset_claims = False
    direct_claim_imports = set()

    for imported_module, node in _import_from_nodes(reset_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "reset_claims" in imported_names
        ):
            imports_reset_claims = True
        direct_claim_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    assert public_names <= vars(reset_claims).keys()
    assert imports_reset_claims
    assert direct_claim_imports == set()
    assert old_helper_names.isdisjoint(reset_helpers)


def test_batch_source_reset_selection_owns_reset_scope():
    """Reset scope setup should live outside the reset entry."""
    reset_selection = __import__(
        "git_stage_batch.commands.batch_source.reset_selection",
        fromlist=["reset_selection"],
    )
    reset_path = SRC_ROOT / "commands" / "reset.py"
    public_names = {
        "ResetClaimSelection",
        "resolve_reset_claim_selection",
    }
    disallowed_imports = {
        "git_stage_batch.batch.query": {
            "read_batch_metadata",
        },
        "git_stage_batch.batch.selection": {
            "resolve_batch_file_scope",
            "resolve_current_batch_binary_file_scope",
        },
        "git_stage_batch.batch.source_selector": {
            "require_plain_batch_name",
        },
        "git_stage_batch.batch.validation": {
            "batch_exists",
            "validate_batch_name",
        },
        "git_stage_batch.data.file_review.batch_selection": {
            "translate_reset_batch_file_gutter_ids_to_selection_ranges",
        },
        "git_stage_batch.data.file_review.records": {
            "FileReviewAction",
        },
        "git_stage_batch.data.file_review.action_scope": {
            "resolve_batch_source_action_scope",
        },
        "git_stage_batch.exceptions": {
            "exit_with_error",
        },
    }
    reset_tree = ast.parse(reset_path.read_text(), filename=str(reset_path))
    reset_helpers = {
        node.name
        for node in ast.walk(reset_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    imports_reset_selection = False
    direct_selection_imports = set()

    for imported_module, node in _import_from_nodes(reset_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "reset_selection" in imported_names
        ):
            imports_reset_selection = True
        direct_selection_imports |= imported_names & disallowed_imports.get(
            imported_module,
            set(),
        )

    assert public_names <= vars(reset_selection).keys()
    assert imports_reset_selection
    assert direct_selection_imports == set()
    assert "_operation_parts" not in reset_helpers


def test_batch_source_candidate_selectors_own_action_selector_validation():
    """Shared candidate selector validation should live outside action entries."""
    candidate_selectors = __import__(
        "git_stage_batch.commands.batch_source.candidate_selectors",
        fromlist=["candidate_selectors"],
    )
    action_context_path = (
        SRC_ROOT / "commands" / "batch_source" / "action_context.py"
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "resolve_batch_source_action_selector",
    }
    old_source_selector_names = {
        "parse_batch_source_selector",
        "require_candidate_operation",
    }
    old_snippets_by_path = {
        apply_from_path: {
            "names the apply candidate preview set",
            "requires --file in this implementation",
        },
        include_from_path: {
            "names the include candidate preview set",
            "requires --file in this implementation",
        },
    }
    inspected_paths = set(old_snippets_by_path) | {action_context_path}
    imports_candidate_selectors = False
    command_selector_imports = {
        path: False
        for path in old_snippets_by_path
    }
    direct_selector_imports = {
        path: set()
        for path in inspected_paths
    }

    for path in inspected_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                path == action_context_path
                and imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_selectors" in imported_names
            ):
                imports_candidate_selectors = True
            if (
                path in command_selector_imports
                and imported_module == "git_stage_batch.commands.batch_source"
                and "candidate_selectors" in imported_names
            ):
                command_selector_imports[path] = True
            if imported_module == "git_stage_batch.batch.source_selector":
                direct_selector_imports[path] |= (
                    imported_names & old_source_selector_names
                )

    assert public_names <= vars(candidate_selectors).keys()
    assert imports_candidate_selectors
    assert command_selector_imports == {
        apply_from_path: False,
        include_from_path: False,
    }
    assert direct_selector_imports == {
        action_context_path: set(),
        apply_from_path: set(),
        include_from_path: set(),
    }
    for path, old_snippets in old_snippets_by_path.items():
        command_text = path.read_text()
        for snippet in old_snippets:
            assert snippet not in command_text


def test_batch_source_merge_refusals_own_merge_failure_refusals():
    """Shared merge failure refusals should live outside command entries."""
    merge_refusals = __import__(
        "git_stage_batch.commands.batch_source.merge_refusals",
        fromlist=["merge_refusals"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "refuse_batch_source_merge_failures",
    }
    old_snippets_by_path = {
        apply_from_path: {
            "gutter_to_selection_id",
            "Failed for: {files}",
        },
        include_from_path: {
            "gutter_to_selection_id",
            "Failed for: {files}",
        },
    }
    command_paths = set(old_snippets_by_path)
    imports_merge_refusals = {
        path: False
        for path in command_paths
    }
    direct_display_imports = {
        path: set()
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "merge_refusals" in imported_names
            ):
                imports_merge_refusals[path] = True
            if imported_module == "git_stage_batch.batch.file_display":
                direct_display_imports[path] |= (
                    imported_names & {"render_batch_file_display"}
                )

    assert public_names <= vars(merge_refusals).keys()
    assert imports_merge_refusals == {
        apply_from_path: True,
        include_from_path: True,
    }
    assert direct_display_imports == {
        apply_from_path: set(),
        include_from_path: set(),
    }
    for path, old_snippets in old_snippets_by_path.items():
        command_text = path.read_text()
        for snippet in old_snippets:
            assert snippet not in command_text


def test_batch_source_worktree_refusals_own_execution_refusals():
    """Shared worktree execution refusals should live outside command entries."""
    worktree_refusals = __import__(
        "git_stage_batch.commands.batch_source.worktree_refusals",
        fromlist=["worktree_refusals"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "refuse_incompatible_worktree_action",
    }
    old_snippets_by_path = {
        apply_from_path: {
            "contains changes to {file} that are incompatible",
            "one or more files that are incompatible",
        },
        include_from_path: {
            "contains changes to {file} that are incompatible",
            "one or more files that are incompatible",
        },
    }
    command_paths = set(old_snippets_by_path)
    imports_worktree_refusals = {
        path: False
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "worktree_refusals" in imported_names
            ):
                imports_worktree_refusals[path] = True

    assert public_names <= vars(worktree_refusals).keys()
    assert imports_worktree_refusals == {
        apply_from_path: True,
        include_from_path: True,
    }
    for path, old_snippets in old_snippets_by_path.items():
        command_text = path.read_text()
        for snippet in old_snippets:
            assert snippet not in command_text


def test_batch_source_text_actions_own_text_file_mutations():
    """Shared text file actions should live outside command entries."""
    text_file_actions = __import__(
        "git_stage_batch.commands.batch_source.text_file_actions",
        fromlist=["text_file_actions"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    discard_from_path = SRC_ROOT / "commands" / "discard_from.py"
    public_names = {
        "stage_text_file_to_index",
        "write_discarded_text_file_to_worktree",
        "write_text_file_to_worktree",
    }
    old_names = {
        "_discard_text_file_lifecycle_from_batch",
        "_stage_text_file_from_batch",
        "_write_text_file_from_batch",
    }
    command_paths = {
        apply_from_path,
        discard_from_path,
        include_from_path,
    }
    helpers_by_path = {
        path: {
            node.name
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        for path in command_paths
    }
    imports_text_file_actions = {
        path: False
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "text_file_actions" in imported_names
            ):
                imports_text_file_actions[path] = True

    assert public_names <= vars(text_file_actions).keys()
    assert imports_text_file_actions == {
        apply_from_path: True,
        discard_from_path: True,
        include_from_path: True,
    }
    for helpers in helpers_by_path.values():
        assert old_names.isdisjoint(helpers)


def test_batch_source_binary_actions_own_index_mutation():
    """Shared binary index actions should live outside include-from."""
    binary_file_actions = __import__(
        "git_stage_batch.commands.batch_source.binary_file_actions",
        fromlist=["binary_file_actions"],
    )
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "stage_binary_file_to_index",
    }
    old_names = {
        "_stage_binary_file_from_batch",
    }
    include_from_tree = ast.parse(
        include_from_path.read_text(),
        filename=str(include_from_path),
    )
    include_from_helpers = {
        node.name
        for node in ast.walk(include_from_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }
    imports_binary_file_actions = False

    for imported_module, node in _import_from_nodes(include_from_path):
        imported_names = {alias.name for alias in node.names}
        if (
            imported_module == "git_stage_batch.commands.batch_source"
            and "binary_file_actions" in imported_names
        ):
            imports_binary_file_actions = True

    assert public_names <= vars(binary_file_actions).keys()
    assert imports_binary_file_actions
    assert old_names.isdisjoint(include_from_helpers)


def test_batch_source_binary_actions_own_worktree_mutation():
    """Shared binary working-tree actions should live outside command entries."""
    binary_file_actions = __import__(
        "git_stage_batch.commands.batch_source.binary_file_actions",
        fromlist=["binary_file_actions"],
    )
    apply_from_path = SRC_ROOT / "commands" / "apply_from.py"
    discard_from_path = SRC_ROOT / "commands" / "discard_from.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    public_names = {
        "BinaryWorktreeAction",
        "discard_binary_file_to_worktree",
        "write_binary_file_to_worktree",
    }
    old_names = {
        "_discard_binary_file_from_batch",
        "_write_binary_file_from_batch",
    }
    command_paths = {
        apply_from_path,
        discard_from_path,
        include_from_path,
    }
    helpers_by_path = {
        path: {
            node.name
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        for path in command_paths
    }
    imports_binary_file_actions = {
        path: False
        for path in command_paths
    }

    for path in command_paths:
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.batch_source"
                and "binary_file_actions" in imported_names
            ):
                imports_binary_file_actions[path] = True

    assert public_names <= vars(binary_file_actions).keys()
    assert imports_binary_file_actions == {
        apply_from_path: True,
        discard_from_path: True,
        include_from_path: True,
    }
    for helpers in helpers_by_path.values():
        assert old_names.isdisjoint(helpers)


def test_batch_merge_does_not_reexport_merge_exceptions():
    """Merge exceptions should stay on the shared exception boundary."""
    merge = __import__(
        "git_stage_batch.batch.merge",
        fromlist=["merge"],
    )
    exception_names = {
        "AmbiguousAnchorError",
        "MergeError",
        "MissingAnchorError",
    }
    show_from_exception_imports = set()
    violations = []

    assert exception_names.isdisjoint(vars(merge))

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.exceptions":
                if path == SRC_ROOT / "commands" / "show_from.py":
                    show_from_exception_imports |= imported_names & exception_names
                continue

            if imported_module != "git_stage_batch.batch.merge":
                continue

            moved_names = imported_names & exception_names
            if moved_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert "MergeError" in show_from_exception_imports
    assert violations == []


def test_batch_merge_uses_public_entry_helpers():
    """Batch callers should import public merge entry helpers."""
    merge = __import__(
        "git_stage_batch.batch.merge",
        fromlist=["merge"],
    )
    public_names = {
        "apply_presence_constraints",
        "satisfy_constraints",
    }
    private_names = {
        "_apply_presence_constraints",
        "_satisfy_constraints",
    }
    moved_names = {
        "RealizedEntry",
        "realized_entry_content_chunks",
    }
    expected_imports = {
        SRC_ROOT / "batch" / "source_advancement.py": {
            "apply_presence_constraints",
        },
        SRC_ROOT / "batch" / "storage.py": {
            "satisfy_constraints",
        },
    }
    violations = []

    for public_name in public_names:
        assert public_name in vars(merge)
    assert private_names.isdisjoint(vars(merge))
    assert moved_names.isdisjoint(vars(merge))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "batch" / "merge.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.batch.merge":
                continue

            imported_names = {alias.name for alias in node.names}
            imported_public_names |= imported_names & public_names
            disallowed_names = imported_names & private_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_public_names

    assert violations == []


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


def test_selected_change_loading_stays_out_of_hunk_tracking():
    """Selected-change readers should live outside hunk navigation."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    moved_names = {
        "load_selected_change",
        "require_selected_hunk",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "include.py": {"load_selected_change"},
        SRC_ROOT
        / "commands"
        / "selection"
        / "include_line_batching.py": {"require_selected_hunk"},
        SRC_ROOT / "commands" / "discard.py": moved_names,
        SRC_ROOT / "commands" / "skip.py": moved_names,
        SRC_ROOT / "commands" / "suggest_fixup.py": {"require_selected_hunk"},
    }
    violations = []

    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    selected_loading = __import__(
        "git_stage_batch.data.selected_change.loading",
        fromlist=["loading"],
    )
    hunk_tracking_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }

    assert moved_names <= vars(selected_loading).keys()
    assert moved_names.isdisjoint(vars(hunk_tracking))
    assert "git_stage_batch.data.selected_change.loading" not in hunk_tracking_imports

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "selected_change" / "loading.py":
            continue

        imports = _import_from_nodes(path)
        imported_loading_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.selected_change.loading":
                imported_loading_names |= imported_names & moved_names
            if imported_module == "git_stage_batch.data.hunk_tracking":
                moved_imports = imported_names & moved_names
                if moved_imports:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_imports))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_loading_names

    assert violations == []


def test_selected_hunk_filtering_stays_out_of_hunk_tracking():
    """Cached hunk filtering should live outside hunk navigation."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    moved_names = {
        "apply_line_level_batch_filter_to_cached_hunk",
    }
    expected_imports = {
        SRC_ROOT / "commands" / "show.py": moved_names,
    }
    violations = []

    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    selected_filtering = __import__(
        "git_stage_batch.data.selected_change.hunk_filtering",
        fromlist=["hunk_filtering"],
    )
    hunk_tracking_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }

    assert moved_names <= vars(selected_filtering).keys()
    assert moved_names.isdisjoint(vars(hunk_tracking))
    assert "git_stage_batch.batch.attribution" not in hunk_tracking_imports
    assert (
        "git_stage_batch.data.consumed_replacement_masks"
        not in hunk_tracking_imports
    )

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "selected_change" / "hunk_filtering.py":
            continue

        imports = _import_from_nodes(path)
        imported_filtering_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.selected_change.hunk_filtering":
                imported_filtering_names |= imported_names & moved_names
            if imported_module == "git_stage_batch.data.hunk_tracking":
                moved_imports = imported_names & moved_names
                if moved_imports:
                    relative_path = path.relative_to(REPO_ROOT)
                    names = ", ".join(sorted(moved_imports))
                    violations.append(f"{relative_path}:{node.lineno} imports {names}")

        if path in expected_imports:
            assert expected_imports[path] <= imported_filtering_names

    assert violations == []


def test_consumed_replacement_masks_stay_out_of_hunk_tracking():
    """Consumed replacement metadata should stay outside hunk navigation."""
    filtering_path = SRC_ROOT / "data" / "selected_change" / "hunk_filtering.py"
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    filtering_imports = _import_from_nodes(filtering_path)
    filtering_imported_modules = {
        imported_module for imported_module, _node in filtering_imports
    }
    hunk_tracking_imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }
    imports_mask_module_alias = any(
        imported_module == "git_stage_batch.data"
        and any(alias.name == "consumed_replacement_masks" for alias in node.names)
        for imported_module, node in filtering_imports
    )
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    consumed_masks = __import__(
        "git_stage_batch.data.consumed_replacement_masks",
        fromlist=["consumed_replacement_masks"],
    )

    assert imports_mask_module_alias
    assert (
        "git_stage_batch.data.consumed_replacement_masks"
        not in filtering_imported_modules
    )
    assert (
        "git_stage_batch.data.consumed_selections"
        not in hunk_tracking_imported_modules
    )
    assert (
        "git_stage_batch.data.consumed_replacement_masks"
        not in hunk_tracking_imported_modules
    )
    assert "filter_consumed_replacement_masks" in vars(consumed_masks)
    assert "filter_consumed_replacement_masks" not in vars(hunk_tracking)
    assert "_filter_consumed_replacement_masks" not in vars(hunk_tracking)
    assert "read_consumed_file_metadata" not in vars(hunk_tracking)


def test_selected_hunk_recalculation_stays_out_of_hunk_tracking():
    """Selected-hunk recalculation should stay outside hunk navigation."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    refresh_path = SRC_ROOT / "commands" / "selection" / "selected_hunk_refresh.py"
    moved_names = {
        "RecalculateSelectedHunkResult",
        "recalculate_selected_hunk_for_file",
    }
    refresh_imported_names = set()
    stale_import_violations = []
    hunk_tracking_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    recalculation = __import__(
        "git_stage_batch.data.selected_change.hunk_recalculation",
        fromlist=["hunk_recalculation"],
    )

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            if (
                path == refresh_path
                and imported_module == "git_stage_batch.data.selected_change.hunk_recalculation"
            ):
                refresh_imported_names |= imported_names & moved_names

            if imported_module != "git_stage_batch.data.hunk_tracking":
                continue

            moved_imports = imported_names & moved_names
            if moved_imports:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(moved_imports))
                stale_import_violations.append(
                    f"{relative_path}:{node.lineno} imports {names}"
                )

    assert moved_names <= vars(recalculation).keys()
    assert moved_names.isdisjoint(vars(hunk_tracking))
    assert (
        "git_stage_batch.data.selected_change.hunk_recalculation"
        not in hunk_tracking_imports
    )
    assert refresh_imported_names == moved_names
    assert stale_import_violations == []


def test_selected_hunk_cache_writes_stay_in_selected_change_store():
    """Navigation and recalculation should delegate text-hunk state writes."""
    selected_store = __import__(
        "git_stage_batch.data.selected_change.store",
        fromlist=["store"],
    )
    caller_paths = (
        SRC_ROOT / "data" / "hunk_tracking.py",
        SRC_ROOT / "data" / "selected_change" / "hunk_recalculation.py",
        SRC_ROOT / "commands" / "show.py",
    )
    forbidden_imports = {
        "git_stage_batch.data.selected_change.snapshots": {
            "write_snapshots_for_selected_file_path",
        },
        "git_stage_batch.utils.file_io": {
            "write_text_file_contents",
        },
        "git_stage_batch.utils.paths": {
            "get_line_changes_json_file_path",
            "get_selected_hunk_hash_file_path",
        },
    }
    forbidden_calls = {
        "write_selected_change_kind",
        "write_selected_hunk_patch_lines",
    }
    violations = []

    assert "cache_hunk_change" in vars(selected_store)

    for path in caller_paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        call_names = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        call_names |= {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        relative_path = path.relative_to(REPO_ROOT)

        assert "cache_hunk_change" in call_names
        disallowed_calls = call_names & forbidden_calls
        if disallowed_calls:
            names = ", ".join(sorted(disallowed_calls))
            violations.append(f"{relative_path} calls {names}")

        for imported_module, node in _import_from_nodes(path):
            imported_names = {alias.name for alias in node.names}
            disallowed_imports = (
                imported_names & forbidden_imports.get(imported_module, set())
            )
            if disallowed_imports:
                names = ", ".join(sorted(disallowed_imports))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []


def test_batch_file_mode_detection_stays_in_data_module():
    """Include and discard should use the shared file-mode detector."""
    file_modes = __import__(
        "git_stage_batch.data.file_modes",
        fromlist=["file_modes"],
    )
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "selection"
        / "selected_change_batch_staging.py": {"detect_file_mode"},
        SRC_ROOT
        / "commands"
        / "selection"
        / "selected_change_batch_discarding.py": {"detect_file_mode"},
        SRC_ROOT
        / "commands"
        / "file_scope"
        / "discard_to_batch.py": {"detect_file_mode_from_root"},
    }
    forbidden_helpers = {
        "_detect_file_mode",
        "_detect_file_mode_from_root",
    }

    assert {"detect_file_mode", "detect_file_mode_from_root"} <= vars(file_modes).keys()

    for path, expected_names in expected_imports.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        helper_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        imported_file_mode_names = set()

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.data.file_modes":
                continue
            imported_file_mode_names |= {alias.name for alias in node.names}

        assert forbidden_helpers.isdisjoint(helper_names)
        assert expected_names <= imported_file_mode_names


def test_file_change_status_queries_stay_in_data_module():
    """Include should use data helpers for file change status probes."""
    include_path = SRC_ROOT / "commands" / "include.py"
    replacement_path = (
        SRC_ROOT / "commands" / "file_scope" / "include_file_replacement.py"
    )
    status_path = SRC_ROOT / "data" / "file_change_status.py"
    file_change_status = __import__(
        "git_stage_batch.data.file_change_status",
        fromlist=["file_change_status"],
    )
    public_names = {
        "file_has_staged_changes",
        "file_has_unstaged_changes",
    }
    old_include_names = {
        "_file_has_staged_changes",
        "_file_has_unstaged_changes",
    }

    assert public_names <= vars(file_change_status).keys()

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_helpers = {
        node.name for node in ast.walk(include_tree) if isinstance(node, ast.FunctionDef)
    }
    include_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(include_path)
    }
    imported_status_names = set()

    for imported_module, node in _import_from_nodes(replacement_path):
        if imported_module != "git_stage_batch.data.file_change_status":
            continue
        imported_status_names |= {alias.name for alias in node.names}

    status_imported_names = set()
    for _imported_module, node in _import_from_nodes(status_path):
        status_imported_names |= {alias.name for alias in node.names}

    assert old_include_names.isdisjoint(include_helpers)
    assert "git_stage_batch.data.file_change_status" not in include_imports
    assert public_names <= imported_status_names
    assert "run_git_command" in status_imported_names


def test_index_entry_lookup_stays_in_data_module():
    """Index-entry parsing should stay behind the shared data helper."""
    index_entries = __import__(
        "git_stage_batch.data.index_entries",
        fromlist=["index_entries"],
    )
    expected_imports = {
        SRC_ROOT
        / "commands"
        / "selection"
        / "selected_change_staging.py": {"read_index_entry"},
        SRC_ROOT / "data" / "staged_renames.py": {"read_index_entry"},
    }

    assert {"IndexEntry", "read_index_entry"} <= vars(index_entries).keys()

    for path, expected_names in expected_imports.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        helper_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        imported_index_names = set()

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.data.index_entries":
                continue
            imported_index_names |= {alias.name for alias in node.names}

        assert "_index_entry_for_path" not in helper_names
        assert expected_names <= imported_index_names


def test_selected_change_staging_owns_include_pipeline():
    """Selected change include support should stay out of include.py."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_staging.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_staging",
        fromlist=["selected_change_staging"],
    )
    public_names = {
        "include_selected_change",
        "stage_gitlink_change",
        "stage_rename_change",
        "stage_text_deletion_change",
    }
    internal_names = {
        "_include_loaded_selected_change",
    }
    old_include_names = {
        "_stage_rename_change",
        "_stage_text_deletion_change",
        "_update_index_for_gitlink_change",
    }
    moved_names = {
        "NoMoreHunks",
        "fetch_next_change",
        "get_selected_hunk_hash_file_path",
        "get_selected_hunk_patch_file_path",
        "git_apply_to_index",
        "git_add_paths",
        "load_selected_change",
        "patch_is_file_deletion",
        "read_text_file_contents",
        "record_hunk_included",
        "update_index_with_blob_buffer",
    }
    helper_imports = moved_names | {
        "BinaryFileChange",
        "LineBuffer",
        "finish_selected_change_action",
        "undo_checkpoint",
    }

    assert public_names <= vars(helper).keys()
    assert internal_names <= vars(helper).keys()

    tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_include_names = {
        node.id
        for node in ast.walk(include_functions["command_include"])
        if isinstance(node, ast.Name)
    }
    include_selection_imports = set()

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        include_selection_imports |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert old_include_names.isdisjoint(include_functions)
    assert "selected_change_staging" in include_selection_imports
    assert moved_names.isdisjoint(command_include_names)
    assert helper_imports <= helper_imported_names


def test_include_line_selection_stays_in_command_helper():
    """Include line-selection support should stay out of the command entrypoint."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_selection.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.include_line_selection",
        fromlist=["include_line_selection"],
    )
    public_names = {
        "IncludeLineSelectionContext",
        "TransientIncludeFailureReason",
        "TransientIncludeResult",
        "annotate_line_changes_with_working_tree_source",
        "line_sequence_ends_with_lf",
        "load_include_line_selection_context",
        "record_baseline_references_for_additions",
        "selected_file_view_is_fresh_for",
        "selected_file_view_targets",
        "stage_live_line_target_buffer",
        "transient_include_failure_message",
        "try_build_index_content_via_transient_batch",
    }
    old_include_names = {
        "TransientIncludeFailureReason",
        "TransientIncludeResult",
        "_annotate_line_changes_with_working_tree_source",
        "_line_sequence_ends_with_lf",
        "_load_include_line_selection_context",
        "_record_baseline_references_for_additions",
        "_restore_session_batch_sources_file",
        "_selected_file_view_is_fresh_for",
        "_selected_file_view_targets",
        "_snapshot_session_batch_sources_file",
        "_stage_live_line_target_buffer",
        "_transient_include_failure_message",
        "_try_build_index_content_via_transient_batch",
    }
    line_selection_resolution_names = {
        "annotate_line_changes_with_working_tree_source",
        "auto_add_untracked_files",
        "cache_unstaged_file_as_single_hunk",
        "load_line_changes_from_state",
        "require_selected_hunk",
        "selected_file_view_is_fresh_for",
        "selected_file_view_targets",
        "snapshot_selected_change_state",
    }
    helper_imports = {
        "auto_add_untracked_files",
        "cache_unstaged_file_as_single_hunk",
        "load_line_changes_from_state",
        "require_selected_hunk",
        "snapshot_selected_change_state",
    }
    include_imports_helper = False

    assert public_names <= vars(helper).keys()

    tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    include_functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "include_line_selection" in imported_names:
            include_imports_helper = True

    command_include_line_names = {
        node.id
        for node in ast.walk(include_functions["command_include_line"])
        if isinstance(node, ast.Name)
    }
    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert old_include_names.isdisjoint(include_names)
    assert include_imports_helper
    assert line_selection_resolution_names.isdisjoint(command_include_line_names)
    assert helper_imports <= helper_imported_names


def test_include_line_replacement_stays_in_command_helper():
    """Include line-replacement support should stay out of the command entrypoint."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_replacement.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.include_line_replacement",
        fromlist=["include_line_replacement"],
    )
    public_names = {
        "IncludeLineReplacementFileSelection",
        "IncludeLineReplacementSelection",
        "apply_include_line_replacement",
        "prepare_file_include_line_replacement",
        "prepare_pathless_include_line_replacement",
        "translate_file_view_replacement_to_unstaged_diff",
    }
    old_include_names = {
        "_apply_include_line_replacement",
        "_line_identity_for_live_replacement",
        "_prepare_file_include_line_replacement",
        "_prepare_pathless_include_line_replacement",
        "_translate_file_view_replacement_to_unstaged_diff",
    }
    helper_imports = {
        "SelectedChangeKind",
        "annotate_with_batch_source",
        "build_target_index_buffer_with_replaced_lines",
        "cache_unstaged_file_as_single_hunk",
        "format_line_ids",
        "get_index_snapshot_file_path",
        "get_selected_change_file_path",
        "get_working_tree_snapshot_file_path",
        "load_git_object_as_buffer_or_empty",
        "load_line_changes_from_state",
        "load_working_tree_file_as_buffer",
        "parse_line_selection",
        "read_selected_change_kind",
        "record_consumed_selection",
        "render_unstaged_file_as_single_hunk",
        "require_line_selection_in_view",
        "require_selected_hunk",
        "snapshot_selected_change_state",
        "update_index_with_blob_buffer",
    }
    replacement_context_names = {
        "annotate_with_batch_source",
        "cache_unstaged_file_as_single_hunk",
        "format_line_ids",
        "get_index_snapshot_file_path",
        "get_selected_change_file_path",
        "get_working_tree_snapshot_file_path",
        "load_git_object_as_buffer",
        "load_line_changes_from_state",
        "load_working_tree_file_as_buffer",
        "require_selected_hunk",
        "selected_file_view_is_fresh_for",
        "selected_file_view_targets",
        "snapshot_selected_change_state",
    }

    assert public_names <= vars(helper).keys()

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(include_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    include_functions = {
        node.name: node
        for node in ast.walk(include_tree)
        if isinstance(node, ast.FunctionDef)
    }
    include_imports_helper = False

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "include_line_replacement" in imported_names:
            include_imports_helper = True

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    command_line_as_names = {
        node.id
        for node in ast.walk(include_functions["command_include_line_as"])
        if isinstance(node, ast.Name)
    }
    command_line_as_attributes = {
        node.attr
        for node in ast.walk(include_functions["command_include_line_as"])
        if isinstance(node, ast.Attribute)
    }

    assert old_include_names.isdisjoint(include_names)
    assert include_imports_helper
    assert "prepare_file_include_line_replacement" in command_line_as_attributes
    assert "prepare_pathless_include_line_replacement" in command_line_as_attributes
    assert replacement_context_names.isdisjoint(command_line_as_names)
    assert replacement_context_names.isdisjoint(command_line_as_attributes)
    assert helper_imports <= helper_imported_names


def test_selected_change_discarding_owns_discard_pipeline():
    """Selected change discard support should stay out of discard.py."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_discarding.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_discarding",
        fromlist=["selected_change_discarding"],
    )
    public_names = {
        "discard_selected_change",
        "discard_gitlink_change",
        "discard_rename_change",
        "discard_text_deletion_change",
    }
    internal_names = {
        "_discard_binary_change",
        "_discard_loaded_selected_change",
        "_discard_text_hunk",
    }
    old_discard_names = {
        "_discard_gitlink_change",
        "_discard_rename_change",
        "_discard_text_deletion_change",
    }
    moved_names = {
        "CommandError",
        "NoMoreHunks",
        "append_lines_to_file",
        "build_line_changes_from_patch_lines",
        "fetch_next_change",
        "get_block_list_file_path",
        "get_git_repository_root_path",
        "get_selected_hunk_hash_file_path",
        "get_selected_hunk_patch_file_path",
        "git_apply_to_worktree",
        "git_checkout_paths",
        "path_is_empty",
        "patch_is_new_file",
        "read_text_file_contents",
        "record_hunk_discarded",
        "snapshot_file_if_untracked",
    }
    helper_imports = moved_names | {
        "BinaryFileChange",
        "LineBuffer",
        "discard_submodule_pointer_from_batch",
        "finish_selected_change_action",
        "git_update_gitlink",
        "git_update_index",
        "load_selected_change",
        "undo_checkpoint",
    }

    assert public_names <= vars(helper).keys()
    assert internal_names <= vars(helper).keys()

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_functions = {
        node.name: node
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.FunctionDef)
    }
    command_discard_names = {
        node.id
        for node in ast.walk(discard_functions["command_discard"])
        if isinstance(node, ast.Name)
    }
    discard_selection_imports = set()

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        discard_selection_imports |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert old_discard_names.isdisjoint(discard_functions)
    assert "selected_change_discarding" in discard_selection_imports
    assert moved_names.isdisjoint(command_discard_names)
    assert helper_imports <= helper_imported_names


def test_discard_file_selection_stays_in_command_helper():
    """Discard file selection should stay out of the command entrypoint."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "discard_file_selection.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.discard_file_selection",
        fromlist=["discard_file_selection"],
    )
    public_names = {"load_explicit_file_selection"}
    old_discard_names = {"_load_explicit_file_selection"}
    helper_imports = {
        "auto_add_untracked_files",
        "cache_unstaged_file_as_single_hunk",
        "get_selected_change_file_path",
        "load_line_changes_from_state",
        "read_selected_change_kind",
    }

    assert public_names <= vars(helper).keys()

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_helpers = {
        node.name for node in ast.walk(discard_tree) if isinstance(node, ast.FunctionDef)
    }
    discard_imports_helper = False

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "discard_file_selection" in imported_names:
            discard_imports_helper = True

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert old_discard_names.isdisjoint(discard_helpers)
    assert discard_imports_helper
    assert helper_imports <= helper_imported_names


def test_include_file_selection_stays_in_command_helper():
    """Include file selection should stay out of the command entrypoint."""
    line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_batching.py"
    )
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "include_file_selection.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.include_file_selection",
        fromlist=["include_file_selection"],
    )
    public_names = {"load_explicit_file_selection"}
    helper_imports = {
        "cache_unstaged_file_as_single_hunk",
        "load_line_changes_from_state",
        "render_gitlink_change",
        "selected_file_view_targets",
    }

    assert public_names <= vars(helper).keys()

    tree = ast.parse(line_batching_path.read_text(), filename=str(line_batching_path))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    line_batching_imports_helper = False

    for imported_module, node in _import_from_nodes(line_batching_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "include_file_selection" in imported_names:
            line_batching_imports_helper = True

    line_function_names = {
        node.id
        for node in ast.walk(functions["include_file_lines_to_batch"])
        if isinstance(node, ast.Name)
    }
    line_function_attributes = {
        node.attr
        for node in ast.walk(functions["include_file_lines_to_batch"])
        if isinstance(node, ast.Attribute)
    }
    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert line_batching_imports_helper
    assert "load_explicit_file_selection" in line_function_attributes
    assert helper_imports.isdisjoint(line_function_names)
    assert helper_imports <= helper_imported_names


def test_include_line_batching_stays_in_command_helper():
    """Include line-to-batch support should stay out of the command entrypoint."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_batching.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.include_line_batching",
        fromlist=["include_line_batching"],
    )
    public_names = {
        "include_file_lines_to_batch",
        "include_selected_lines_to_batch",
    }
    old_include_names = {
        "_command_include_file_lines_to_batch",
        "_command_include_lines_to_batch",
        "_filter_selected_hunk_excluding_batched_lines",
    }
    helper_imports = {
        "annotate_with_batch_source",
        "batch_line_selection",
        "batch_line_updates",
        "include_file_selection",
        "include_line_selection",
        "load_line_changes_from_state",
        "recalculate_selected_hunk_for_command",
        "require_selected_hunk",
    }

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name for node in ast.walk(include_tree) if isinstance(node, ast.FunctionDef)
    }
    include_imports_helper = False
    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "include_line_batching" in imported_names:
            include_imports_helper = True

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert public_names <= vars(helper).keys()
    assert old_include_names.isdisjoint(include_names)
    assert old_include_names.isdisjoint(vars(helper).keys())
    assert include_imports_helper
    assert helper_imports <= helper_imported_names


def test_discard_line_selection_stays_in_command_helper():
    """Discard line-selection editing should stay out of the command entrypoint."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_selection.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.discard_line_selection",
        fromlist=["discard_line_selection"],
    )
    public_names = {"discard_worktree_line_selection"}
    command_level_names = {
        "build_target_working_tree_buffer_from_lines",
        "load_working_tree_file_as_buffer",
        "parse_line_selection",
        "require_line_selection_in_view",
        "write_buffer_to_path",
    }
    helper_imports = command_level_names | {
        "buffer_ends_with_lf",
        "get_git_repository_root_path",
        "get_selected_change_file_path",
        "load_line_changes_from_state",
        "require_selected_hunk",
    }

    assert public_names <= vars(helper).keys()

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    command_discard_line = next(
        node
        for node in ast.walk(discard_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "command_discard_line"
    )
    command_names = {
        node.id for node in ast.walk(command_discard_line) if isinstance(node, ast.Name)
    }
    discard_imports_helper = False

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "discard_line_selection" in imported_names:
            discard_imports_helper = True

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert command_level_names.isdisjoint(command_names)
    assert discard_imports_helper
    assert helper_imports <= helper_imported_names


def test_discard_line_replacement_stays_in_command_helper():
    """Discard line-replacement support should stay out of the command entrypoint."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_replacement.py"
    )
    line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_batching.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.discard_line_replacement",
        fromlist=["discard_line_replacement"],
    )
    line_batching = __import__(
        "git_stage_batch.commands.selection.discard_line_batching",
        fromlist=["discard_line_batching"],
    )
    public_names = {
        "DiscardLineReplacementSelection",
        "add_discard_line_replacement_to_batch",
        "build_discard_line_replacement_target_buffer",
        "derive_live_replacement_line_runs",
        "prepare_discard_line_replacement_selection",
    }
    line_batching_names = {
        "discard_file_lines_to_batch",
        "discard_lines_as_to_batch",
        "discard_selected_lines_to_batch",
    }
    old_discard_names = {
        "_add_discard_line_replacement_to_batch",
        "_derive_live_replacement_line_runs",
        "_command_discard_file_lines_to_batch",
        "_command_discard_lines_to_batch",
        "_command_discard_lines_to_batch_as",
        "_select_rewritten_replacement_lines",
    }
    helper_imports = {
        "BatchOwnership",
        "acquire_batch_ownership_update_for_selection",
        "add_file_to_batch",
        "advance_source_lines_preserving_existing_presence",
        "annotate_with_batch_source_working_lines",
        "batch_exists",
        "build_file_hunk_from_buffer",
        "build_target_working_tree_buffer_from_lines",
        "build_target_working_tree_buffer_with_replaced_lines",
        "coerce_replacement_payload",
        "create_batch",
        "create_batch_source_commit",
        "detect_file_mode",
        "derive_replacement_line_runs_from_lines",
        "load_git_object_as_buffer",
        "load_git_object_as_buffer_or_empty",
        "load_line_changes_from_state",
        "load_session_batch_sources",
        "load_working_tree_file_as_buffer",
        "merge_batch_ownership",
        "parse_line_selection",
        "read_batch_metadata",
        "refresh_selected_lines_against_source_lines",
        "replacement_selection",
        "remap_batch_ownership_with_lineage",
        "require_line_selection_in_view",
        "save_session_batch_sources",
        "snapshot_file_if_untracked",
        "translate_lines_to_batch_ownership",
    }
    moved_batch_update_names = {
        "advance_source_lines_preserving_existing_presence",
        "create_batch_source_commit",
        "load_git_object_as_buffer",
        "load_session_batch_sources",
        "merge_batch_ownership",
        "refresh_selected_lines_against_source_lines",
        "remap_batch_ownership_with_lineage",
        "save_session_batch_sources",
        "translate_lines_to_batch_ownership",
    }

    assert public_names <= vars(helper).keys()
    assert line_batching_names <= vars(line_batching).keys()

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    line_batching_tree = ast.parse(
        line_batching_path.read_text(),
        filename=str(line_batching_path),
    )
    discard_helpers = {
        node.name for node in ast.walk(discard_tree) if isinstance(node, ast.FunctionDef)
    }
    line_batching_functions = {
        node.name: node
        for node in ast.walk(line_batching_tree)
        if isinstance(node, ast.FunctionDef)
    }
    discard_imports_line_batching = False
    line_batching_imports_helper = False

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "discard_line_batching" in imported_names:
            discard_imports_line_batching = True

    for imported_module, node in _import_from_nodes(line_batching_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "discard_line_replacement" in imported_names:
            line_batching_imports_helper = True

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}
    command_update_names = {
        node.id
        for node in ast.walk(line_batching_functions["discard_lines_as_to_batch"])
        if isinstance(node, ast.Name)
    }

    assert old_discard_names.isdisjoint(discard_helpers)
    assert moved_batch_update_names.isdisjoint(command_update_names)
    assert discard_imports_line_batching
    assert line_batching_imports_helper
    assert helper_imports <= helper_imported_names


def test_batch_line_selection_stays_in_command_helper():
    """Batch line-selection validation should live in command selection support."""
    include_line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_batching.py"
    )
    discard_line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_batching.py"
    )
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "batch_line_selection.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.batch_line_selection",
        fromlist=["batch_line_selection"],
    )
    public_names = {
        "BatchLineSelection",
        "select_lines_for_batch_action",
    }
    command_level_names = {
        "parse_line_selection",
        "require_line_selection_in_view",
    }
    guarded_functions = {
        include_line_batching_path: {
            "include_file_lines_to_batch",
            "include_selected_lines_to_batch",
        },
        discard_line_batching_path: {
            "discard_file_lines_to_batch",
            "discard_selected_lines_to_batch",
        },
    }

    assert public_names <= vars(helper).keys()

    for path, function_names in guarded_functions.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        imports_helper = False

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.commands.selection":
                continue
            imported_names = {alias.name for alias in node.names}
            if "batch_line_selection" in imported_names:
                imports_helper = True

        assert imports_helper
        for function_name in function_names:
            function = functions[function_name]
            function_names_used = {
                node.id for node in ast.walk(function) if isinstance(node, ast.Name)
            }
            assert command_level_names.isdisjoint(function_names_used)

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert command_level_names <= helper_imported_names


def test_batch_line_updates_stays_in_command_helper():
    """Batch line updates should live in command selection support."""
    include_line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "include_line_batching.py"
    )
    discard_line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_batching.py"
    )
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "batch_line_updates.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.batch_line_updates",
        fromlist=["batch_line_updates"],
    )
    public_names = {
        "add_selected_lines_to_batch",
    }
    moved_names = {
        "acquire_batch_ownership_update_for_selection",
        "add_file_to_batch",
        "batch_exists",
        "create_batch",
        "detect_file_mode",
        "read_batch_metadata",
        "snapshot_file_if_untracked",
    }
    guarded_functions = {
        include_line_batching_path: {
            "include_file_lines_to_batch",
            "include_selected_lines_to_batch",
        },
        discard_line_batching_path: {
            "discard_file_lines_to_batch",
            "discard_selected_lines_to_batch",
        },
    }

    assert public_names <= vars(helper).keys()

    for path, function_names in guarded_functions.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        imports_helper = False

        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.commands.selection":
                continue
            imported_names = {alias.name for alias in node.names}
            if "batch_line_updates" in imported_names:
                imports_helper = True

        assert imports_helper
        for function_name in function_names:
            function = functions[function_name]
            function_names_used = {
                node.id for node in ast.walk(function) if isinstance(node, ast.Name)
            }
            attribute_names_used = {
                node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
            }
            assert "add_selected_lines_to_batch" in attribute_names_used
            assert moved_names.isdisjoint(function_names_used)

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert moved_names <= helper_imported_names


def test_discard_uses_file_io_path_empty_helper():
    """Discard should use file I/O utilities for generic path byte checks."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_discarding.py"
    )
    file_io = __import__(
        "git_stage_batch.utils.file_io",
        fromlist=["file_io"],
    )
    imported_file_io_names = set()

    assert "path_is_empty" in vars(file_io)

    tree = ast.parse(helper_path.read_text(), filename=str(helper_path))
    discard_helpers = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(helper_path):
        if imported_module != "git_stage_batch.utils.file_io":
            continue
        imported_file_io_names |= {alias.name for alias in node.names}

    assert "_path_is_empty" not in discard_helpers
    assert "path_is_empty" in imported_file_io_names


def test_discard_uses_core_buffer_newline_helper():
    """Discard should use core buffer helpers for trailing newline checks."""
    line_batching_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_batching.py"
    )
    core_buffer = __import__(
        "git_stage_batch.core.buffer",
        fromlist=["buffer"],
    )
    imported_buffer_names = set()

    assert "buffer_ends_with_lf" in vars(core_buffer)

    tree = ast.parse(
        line_batching_path.read_text(),
        filename=str(line_batching_path),
    )
    discard_helpers = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(line_batching_path):
        if imported_module != "git_stage_batch.core.buffer":
            continue
        imported_buffer_names |= {alias.name for alias in node.names}

    assert "_buffer_ends_with_lf" not in discard_helpers
    assert "buffer_ends_with_lf" in imported_buffer_names


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


def test_selected_change_display_stays_in_command_helper():
    """Command flows should not render selected changes from data helpers."""
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )
    assert "show_selected_change" not in vars(hunk_tracking)

    command_paths = (
        SRC_ROOT / "commands" / "start.py",
        SRC_ROOT / "commands" / "file_scope" / "multi_file_actions.py",
        SRC_ROOT / "commands" / "selection" / "action_completion.py",
        SRC_ROOT / "commands" / "session" / "iteration.py",
    )
    violations = []

    for command_path in command_paths:
        imports = _import_from_nodes(command_path)
        imported_modules = {imported_module for imported_module, _node in imports}
        assert "git_stage_batch.commands.selection.selected_change_display" in imported_modules

        for imported_module, node in imports:
            if imported_module != "git_stage_batch.data.hunk_tracking":
                continue

            imported_names = {alias.name for alias in node.names}
            if "show_selected_change" in imported_names:
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports show_selected_change"
                )

    assert violations == []


def test_hunk_tracking_does_not_import_output():
    """Hunk tracking should return state outcomes instead of rendering output."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }
    output_names = {
        "print_binary_file_change",
        "print_gitlink_change",
        "print_line_level_changes",
        "print_rename_change",
        "print_text_file_deletion_change",
    }
    hunk_tracking = __import__(
        "git_stage_batch.data.hunk_tracking",
        fromlist=["hunk_tracking"],
    )

    assert "git_stage_batch.output" not in imported_modules
    assert output_names.isdisjoint(vars(hunk_tracking))


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


def test_replacement_selection_stays_in_command_helper():
    """Include and discard should use the replacement-selection helper module."""
    include_path = SRC_ROOT / "commands" / "include.py"
    include_from_path = SRC_ROOT / "commands" / "include_from.py"
    action_selection_path = (
        SRC_ROOT / "commands" / "batch_source" / "action_selection.py"
    )
    discard_path = SRC_ROOT / "commands" / "discard.py"
    show_from_path = SRC_ROOT / "commands" / "show_from.py"
    replacement_previews_path = (
        SRC_ROOT / "commands" / "batch_source" / "replacement_previews.py"
    )
    discard_replacement_path = (
        SRC_ROOT / "commands" / "selection" / "discard_line_replacement.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.replacement_selection",
        fromlist=["replacement_selection"],
    )
    include = __import__(
        "git_stage_batch.commands.include",
        fromlist=["include"],
    )
    public_names = {
        "build_leading_replacement_addition_selection_error",
        "build_partial_structural_run_selection_error",
        "derive_replacement_line_runs",
        "expand_replacement_selection_ids",
        "require_contiguous_display_selection",
    }
    old_include_names = {
        "_build_leading_replacement_addition_selection_error",
        "_build_partial_structural_run_selection_error",
        "_derive_replacement_line_runs",
        "_expand_replacement_selection_ids",
    }
    old_show_from_names = {
        "_require_contiguous_display_selection",
    }
    helper_user_paths = (
        include_path,
        action_selection_path,
        discard_replacement_path,
        replacement_previews_path,
    )
    violations = []

    for public_name in public_names:
        assert public_name in vars(helper)
    assert old_include_names.isdisjoint(vars(include))
    for old_name in old_include_names:
        assert f"def {old_name}" not in include_path.read_text()
    for old_name in old_show_from_names:
        assert f"def {old_name}" not in show_from_path.read_text()
        assert f"def {old_name}" not in include_from_path.read_text()

    for command_path in helper_user_paths:
        imports = _import_from_nodes(command_path)
        imports_helper_namespace = False

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if (
                imported_module == "git_stage_batch.commands.selection"
                and "replacement_selection" in imported_names
            ):
                imports_helper_namespace = True

            if imported_module == "git_stage_batch.commands.selection.replacement_selection":
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} imports replacement names directly"
                )

        assert imports_helper_namespace

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module == "git_stage_batch.commands.include":
            relative_path = discard_path.relative_to(REPO_ROOT)
            violations.append(f"{relative_path}:{node.lineno} imports include")

    assert violations == []


def test_start_again_use_session_iteration_helper():
    """Start and again should share iteration flow through session support."""
    start_path = SRC_ROOT / "commands" / "start.py"
    again_path = SRC_ROOT / "commands" / "again.py"
    iteration = __import__(
        "git_stage_batch.commands.session.iteration",
        fromlist=["iteration"],
    )

    assert "restart_iteration_pass" in vars(iteration)

    for command_path in (start_path, again_path):
        imported_modules = {
            imported_module
            for imported_module, _node in _import_from_nodes(command_path)
        }
        assert "git_stage_batch.commands.session.iteration" in imported_modules

    start_imports = {
        imported_module
        for imported_module, _node in _import_from_nodes(start_path)
    }
    assert "git_stage_batch.commands.again" not in start_imports


def test_hunk_tracking_does_not_import_show_command():
    """Hunk navigation state should not depend on the show command."""
    hunk_tracking_path = SRC_ROOT / "data" / "hunk_tracking.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(hunk_tracking_path)
    }

    assert "git_stage_batch.commands.show" not in imported_modules
