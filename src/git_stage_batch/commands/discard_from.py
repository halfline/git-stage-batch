"""Discard from batch command implementation."""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Optional

from ..batch.merge import discard_batch
from ..batch.metadata_validation import get_validated_baseline_commit, read_validated_batch_metadata
from ..batch.selection import (
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    select_batch_ownership_for_display_ids,
    translate_batch_file_gutter_ids_to_selection_ids,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_discard_change_type,
)
from ..data.file_review_state import (
    FileReviewAction,
    resolve_batch_source_action_scope,
)
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, AtomicUnitError, CommandError, MergeError, BatchMetadataError
from ..i18n import _
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command


def _baseline_file_mode(baseline_commit: str, file_path: str) -> str | None:
    """Return file mode for a path in the baseline tree, if present."""
    result = run_git_command(
        ["ls-tree", baseline_commit, "--", file_path],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split(maxsplit=1)[0]


def _restore_working_tree_file_mode(file_path: Path, file_mode: str | None) -> None:
    """Restore executable bits for a working-tree file from a Git file mode."""
    if file_mode is None:
        return
    current_mode = file_path.stat().st_mode
    if file_mode == "100755":
        file_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        file_path.chmod(current_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _discard_binary_file_from_batch(file_path: str, baseline_commit: str) -> None:
    """Discard binary file by restoring it to baseline state.

    Binary files are atomic units. Discarding means restoring the entire file
    to its state at the batch baseline commit.

    Args:
        file_path: Path to binary file
        baseline_commit: Baseline commit SHA to restore from

    Raises:
        RuntimeError: If file operations fail
    """
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    # Read file from baseline commit
    result = run_git_command(
        ["show", f"{baseline_commit}:{file_path}"],
        check=False,
        text_output=False
    )

    if result.returncode == 0:
        # File exists in baseline - restore it
        baseline_content = result.stdout
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(baseline_content)
        _restore_working_tree_file_mode(
            full_path,
            _baseline_file_mode(baseline_commit, file_path),
        )
        print(_("✓ Restored binary file to baseline: {file}").format(file=file_path), file=sys.stderr)
    else:
        # File doesn't exist in baseline - delete from working tree
        if full_path.exists():
            full_path.unlink()
            print(_("✓ Removed binary file (not in baseline): {file}").format(file=file_path), file=sys.stderr)
        else:
            # File already doesn't exist - no-op
            pass


def _discard_text_file_lifecycle_from_batch(file_path: str, baseline_commit: str) -> None:
    """Discard a whole-path text add/delete by restoring baseline state."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    result = run_git_command(
        ["show", f"{baseline_commit}:{file_path}"],
        check=False,
        text_output=False,
    )
    if result.returncode == 0:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(result.stdout)
        _restore_working_tree_file_mode(
            full_path,
            _baseline_file_mode(baseline_commit, file_path),
        )
    elif full_path.exists():
        full_path.unlink()


def command_discard_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Remove batch changes from working tree using structural merge.

    Args:
        batch_name: Name of batch to discard from
        line_ids: Optional line IDs to discard (requires single-file context)
        file: Optional file path to select from batch.
              If None, discards all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.DISCARD_FROM_BATCH,
        command_name="discard",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    file = scope_resolution.file

    # Check batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    if not all_files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "discard"
    )

    # Reject line selection for binary files (binary files are atomic units)
    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Discard the whole file instead."))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_discard = selected_ids
    rendered = None
    if selected_ids:
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        selection_ids_to_discard, rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path_for_render,
            selected_ids,
            FileReviewAction.DISCARD_FROM_BATCH,
        )

    # Get baseline commit (raises BatchMetadataError with clear message if missing)
    try:
        baseline_commit = get_validated_baseline_commit(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))
    operation_parts = ["discard", "--from", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
        # Discard all files in batch
        repo_root = get_git_repository_root_path()
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                # Snapshot file before modifying
                snapshot_file_if_untracked(file_path)

                # Binary files are atomic units - handle separately without ownership/merge logic
                if file_meta.get("file_type") == "binary":
                    _discard_binary_file_from_batch(file_path, baseline_commit)
                    continue

                text_change_type = normalized_text_change_type(file_meta.get("change_type"))
                if selected_ids is None and text_change_type in {
                    TextFileChangeType.ADDED,
                    TextFileChangeType.DELETED,
                }:
                    _discard_text_file_lifecycle_from_batch(file_path, baseline_commit)
                    continue

                # Get batch source commit content (as bytes)
                batch_source_commit = file_meta["batch_source_commit"]
                batch_source_result = run_git_command(
                    ["show", f"{batch_source_commit}:{file_path}"],
                    check=False,
                    text_output=False
                )
                if batch_source_result.returncode != 0:
                    failed_files.append(file_path)
                    continue
                batch_source_content = batch_source_result.stdout

                # Get baseline content (as bytes)
                baseline_result = run_git_command(
                    ["show", f"{baseline_commit}:{file_path}"],
                    check=False,
                    text_output=False
                )
                baseline_exists = baseline_result.returncode == 0
                baseline_content = baseline_result.stdout if baseline_exists else b""

                # Get selected working tree content (as bytes)
                full_path = repo_root / file_path
                working_exists = full_path.exists()
                if working_exists:
                    working_content = full_path.read_bytes()
                else:
                    working_content = b""
                baseline_mode = _baseline_file_mode(baseline_commit, file_path)
                restore_mode = mode_for_text_materialization(
                    baseline_mode,
                    selected_ids,
                    destination_exists=working_exists,
                )

                # Get ownership from metadata, filtered by selected selection IDs if specified
                try:
                    ownership = select_batch_ownership_for_display_ids(
                        file_meta, batch_source_content, selection_ids_to_discard
                    )
                except AtomicUnitError as e:
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(e, rendered, "discard from", batch_name)
                    exit_with_error(_("Failed to discard from batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e),
                    ))

                # If nothing selected for this file, skip it
                if ownership.is_empty():
                    continue

                # Perform structural discard (inverse of merge)
                discarded_content = discard_batch(
                    batch_source_content,
                    ownership,
                    working_content,
                    baseline_content
                )

                effective_change_type = selected_text_discard_change_type(
                    text_change_type,
                    selected_ids,
                    discarded_content,
                    baseline_exists=baseline_exists,
                )
                if effective_change_type == TextFileChangeType.DELETED:
                    if full_path.exists():
                        full_path.unlink()
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_bytes(discarded_content)
                    _restore_working_tree_file_mode(full_path, restore_mode)

            except CommandError:
                raise
            except MergeError as e:
                print(_("Error discarding {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
                failed_files.append(file_path)
            except Exception as e:
                print(_("Error discarding {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
                failed_files.append(file_path)

    if failed_files:
        exit_with_error(
            _("Failed to discard changes for some files: {files}").format(files=", ".join(failed_files))
        )

    # Success message
    if line_ids:
        print(_("✓ Discarded selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Discarded changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Discarded changes from batch '{name}'").format(name=batch_name), file=sys.stderr)

    print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
