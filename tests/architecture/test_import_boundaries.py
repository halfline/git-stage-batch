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
    discard_path = SRC_ROOT / "commands" / "discard.py"
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
    imported_diff_names = set()
    discard_imported_diff_names = set()

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.core.diff_parser":
            continue
        imported_diff_names |= {alias.name for alias in node.names}

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.core.diff_parser":
            continue
        discard_imported_diff_names |= {alias.name for alias in node.names}

    assert "_patch_is_text_file_path_deletion" not in include_helpers
    assert "patch_is_file_deletion" in imported_diff_names
    assert "_patch_lines_contain_line" not in discard_path.read_text()
    assert "patch_is_empty_file_change" in discard_imported_diff_names
    assert "patch_is_new_file" in discard_imported_diff_names
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


def test_data_package_does_not_reexport_data_apis():
    """Data callers should import concrete modules instead of the package."""
    data_path = SRC_ROOT / "data" / "__init__.py"
    imported_modules = {
        imported_module
        for imported_module, _node in _import_from_nodes(data_path)
    }
    data = __import__("git_stage_batch.data", fromlist=["data"])
    facade_names = {
        "auto_add_untracked_files",
        "format_id_range",
        "get_file_progress",
        "get_hunk_counts",
        "record_hunk_discarded",
        "record_hunk_included",
        "record_hunk_skipped",
        "restore_batch_refs",
        "snapshot_batch_refs",
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
        "print_line_level_changes",
        "print_remaining_line_changes_header",
        "print_rename_change",
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
    git_utils = __import__(
        "git_stage_batch.utils.git",
        fromlist=["git"],
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
    assert public_names.isdisjoint(vars(git_utils))

    for path in SRC_ROOT.rglob("*.py"):
        if path == SRC_ROOT / "data" / "ignore_files.py":
            continue

        imports = _import_from_nodes(path)
        imported_public_names = set()

        for imported_module, node in imports:
            imported_names = {alias.name for alias in node.names}
            if imported_module == "git_stage_batch.data.ignore_files":
                imported_public_names |= imported_names & public_names
            if imported_module == "git_stage_batch.utils.git":
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
        SRC_ROOT / "output" / "file_review.py": public_names,
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
        SRC_ROOT / "commands" / "status.py": {
            "selected_change_matches_review_state",
        },
        SRC_ROOT / "data" / "file_review" / "state.py": {
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
        SRC_ROOT / "commands" / "status.py": {
            "shown_review_selections_for_action",
        },
        SRC_ROOT / "data" / "file_review" / "batch_selection.py": {
            "validate_review_scoped_line_selection",
        },
        SRC_ROOT / "data" / "file_review" / "state.py": public_names,
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
        SRC_ROOT / "commands" / "reset.py": {
            "FileReviewAction",
            "ReviewSource",
        },
        SRC_ROOT / "commands" / "show.py": {"ReviewSource"},
        SRC_ROOT / "commands" / "show_from.py": {
            "FileReviewAction",
            "ReviewSource",
        },
        SRC_ROOT / "commands" / "skip.py": {"FileReviewAction"},
        SRC_ROOT / "commands" / "status.py": {
            "FileReviewAction",
            "ReviewSource",
        },
        SRC_ROOT / "data" / "file_review" / "batch_selection.py": {"FileReviewAction"},
        SRC_ROOT / "output" / "file_review.py": {
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
        SRC_ROOT / "data" / "file_review" / "state.py": public_names,
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
        SRC_ROOT / "commands" / "apply_from.py": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
        SRC_ROOT / "commands" / "discard_from.py": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
        SRC_ROOT / "commands" / "include_from.py": {
            "translate_batch_file_gutter_ids_to_selection_ids",
        },
        SRC_ROOT / "commands" / "reset.py": {
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
    review_path = SRC_ROOT / "tui" / "file_review" / "__init__.py"
    tree = ast.parse(review_path.read_text(), filename=str(review_path))
    class_names = {
        node.name
        for node in tree.body
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

    for imported_module, node in _import_from_nodes(review_path):
        if imported_module != "git_stage_batch.data.file_review.state":
            continue
        imported_state_names |= {alias.name for alias in node.names}

    assert "FileReviewState" in vars(records)
    assert "FileReviewState" not in vars(persisted_state)
    assert "FileReviewSessionState" in class_names
    assert "FileReviewState" not in class_names
    assert "FileReviewState" not in imported_state_names


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
    assert "git_stage_batch.commands.show" in imported_modules
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
    tui_paths = (
        SRC_ROOT / "tui" / "fixup_menu.py",
        SRC_ROOT / "tui" / "file_review" / "__init__.py",
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

    for tui_path in tui_paths:
        imports = _import_from_nodes(tui_path)
        imported_modules = {imported_module for imported_module, _node in imports}
        assert "git_stage_batch.data.suggest_fixup_state" in imported_modules

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
        SRC_ROOT / "commands" / "discard.py": {
            "refresh_selected_lines_against_source_lines",
        },
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
        SRC_ROOT / "commands" / "discard.py": public_names,
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
        SRC_ROOT / "commands" / "discard.py": {
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
        SRC_ROOT / "commands" / "sift.py": public_names,
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
        SRC_ROOT / "commands" / "sift.py": public_names,
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
        SRC_ROOT / "commands" / "reset.py",
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
        SRC_ROOT / "commands" / "include.py": moved_names,
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
        SRC_ROOT / "commands" / "include.py": moved_names,
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
        SRC_ROOT / "commands" / "include.py": {"detect_file_mode"},
        SRC_ROOT / "commands" / "discard.py": {
            "detect_file_mode",
            "detect_file_mode_from_root",
        },
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
    imported_status_names = set()

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.data.file_change_status":
            continue
        imported_status_names |= {alias.name for alias in node.names}

    status_imported_names = set()
    for _imported_module, node in _import_from_nodes(status_path):
        status_imported_names |= {alias.name for alias in node.names}

    assert old_include_names.isdisjoint(include_helpers)
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


def test_include_selected_change_staging_stays_in_command_helper():
    """Include should use the selected-change staging helper module."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_staging",
        fromlist=["selected_change_staging"],
    )
    public_names = {
        "stage_gitlink_change",
        "stage_rename_change",
        "stage_text_deletion_change",
    }
    old_include_names = {
        "_stage_rename_change",
        "_stage_text_deletion_change",
        "_update_index_for_gitlink_change",
    }

    assert public_names <= vars(helper).keys()

    tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_helpers = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    imported_staging_names = set()

    for imported_module, node in _import_from_nodes(include_path):
        if (
            imported_module
            != "git_stage_batch.commands.selection.selected_change_staging"
        ):
            continue
        imported_staging_names |= {alias.name for alias in node.names}

    assert old_include_names.isdisjoint(include_helpers)
    assert public_names <= imported_staging_names


def test_include_line_selection_stays_in_command_helper():
    """Include line-selection support should stay out of the command entrypoint."""
    include_path = SRC_ROOT / "commands" / "include.py"
    helper = __import__(
        "git_stage_batch.commands.selection.include_line_selection",
        fromlist=["include_line_selection"],
    )
    public_names = {
        "TransientIncludeFailureReason",
        "TransientIncludeResult",
        "annotate_line_changes_with_working_tree_source",
        "line_sequence_ends_with_lf",
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
        "_record_baseline_references_for_additions",
        "_restore_session_batch_sources_file",
        "_selected_file_view_is_fresh_for",
        "_selected_file_view_targets",
        "_snapshot_session_batch_sources_file",
        "_stage_live_line_target_buffer",
        "_transient_include_failure_message",
        "_try_build_index_content_via_transient_batch",
    }
    include_imports_helper = False

    assert public_names <= vars(helper).keys()

    tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(include_path):
        if imported_module != "git_stage_batch.commands.selection":
            continue
        imported_names = {alias.name for alias in node.names}
        if "include_line_selection" in imported_names:
            include_imports_helper = True

    assert old_include_names.isdisjoint(include_names)
    assert include_imports_helper


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
        "apply_include_line_replacement",
        "translate_file_view_replacement_to_unstaged_diff",
    }
    old_include_names = {
        "_apply_include_line_replacement",
        "_line_identity_for_live_replacement",
        "_translate_file_view_replacement_to_unstaged_diff",
    }
    helper_imports = {
        "build_target_index_buffer_with_replaced_lines",
        "parse_line_selection",
        "record_consumed_selection",
        "render_unstaged_file_as_single_hunk",
        "require_line_selection_in_view",
        "update_index_with_blob_buffer",
    }

    assert public_names <= vars(helper).keys()

    include_tree = ast.parse(include_path.read_text(), filename=str(include_path))
    include_names = {
        node.name
        for node in ast.walk(include_tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
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

    assert old_include_names.isdisjoint(include_names)
    assert include_imports_helper
    assert helper_imports <= helper_imported_names


def test_discard_selected_change_discarding_stays_in_command_helper():
    """Discard should use the selected-change discarding helper module."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    helper_path = (
        SRC_ROOT / "commands" / "selection" / "selected_change_discarding.py"
    )
    helper = __import__(
        "git_stage_batch.commands.selection.selected_change_discarding",
        fromlist=["selected_change_discarding"],
    )
    public_names = {
        "discard_gitlink_change",
        "discard_rename_change",
        "discard_text_deletion_change",
    }
    old_discard_names = {
        "_discard_gitlink_change",
        "_discard_rename_change",
        "_discard_text_deletion_change",
    }
    helper_imports = {
        "discard_submodule_pointer_from_batch",
        "git_update_gitlink",
        "git_update_index",
    }

    assert public_names <= vars(helper).keys()

    discard_tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_helpers = {
        node.name for node in ast.walk(discard_tree) if isinstance(node, ast.FunctionDef)
    }
    imported_discarding_names = set()

    for imported_module, node in _import_from_nodes(discard_path):
        if (
            imported_module
            != "git_stage_batch.commands.selection.selected_change_discarding"
        ):
            continue
        imported_discarding_names |= {alias.name for alias in node.names}

    helper_imported_names = set()
    for _imported_module, node in _import_from_nodes(helper_path):
        helper_imported_names |= {alias.name for alias in node.names}

    assert old_discard_names.isdisjoint(discard_helpers)
    assert public_names <= imported_discarding_names
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


def test_discard_uses_file_io_path_empty_helper():
    """Discard should use file I/O utilities for generic path byte checks."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    file_io = __import__(
        "git_stage_batch.utils.file_io",
        fromlist=["file_io"],
    )
    imported_file_io_names = set()

    assert "path_is_empty" in vars(file_io)

    tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_helpers = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(discard_path):
        if imported_module != "git_stage_batch.utils.file_io":
            continue
        imported_file_io_names |= {alias.name for alias in node.names}

    assert "_path_is_empty" not in discard_helpers
    assert "path_is_empty" in imported_file_io_names


def test_discard_uses_core_buffer_newline_helper():
    """Discard should use core buffer helpers for trailing newline checks."""
    discard_path = SRC_ROOT / "commands" / "discard.py"
    core_buffer = __import__(
        "git_stage_batch.core.buffer",
        fromlist=["buffer"],
    )
    imported_buffer_names = set()

    assert "buffer_ends_with_lf" in vars(core_buffer)

    tree = ast.parse(discard_path.read_text(), filename=str(discard_path))
    discard_helpers = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    for imported_module, node in _import_from_nodes(discard_path):
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
    discard_path = SRC_ROOT / "commands" / "discard.py"
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
    }
    old_include_names = {
        "_build_leading_replacement_addition_selection_error",
        "_build_partial_structural_run_selection_error",
        "_derive_replacement_line_runs",
        "_expand_replacement_selection_ids",
    }
    command_paths = (
        include_path,
        discard_path,
    )
    violations = []

    for public_name in public_names:
        assert public_name in vars(helper)
    assert old_include_names.isdisjoint(vars(include))
    for old_name in old_include_names:
        assert f"def {old_name}" not in include_path.read_text()

    for command_path in command_paths:
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

            if command_path == discard_path and imported_module == "git_stage_batch.commands.include":
                relative_path = command_path.relative_to(REPO_ROOT)
                violations.append(f"{relative_path}:{node.lineno} imports include")

        assert imports_helper_namespace

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
