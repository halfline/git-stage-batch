"""Include command implementation."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..batch.selection import (
    require_line_selection_in_view,
)
from ..core.diff_parser import (
    acquire_unified_diff,
    patch_is_file_deletion,
)
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.line_selection import (
    parse_line_selection,
    read_line_ids_file,
    write_line_ids_file,
)
from ..core.models import BinaryFileChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ..data.selected_change.loading import (
    load_selected_change,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ..data.selected_change.paths import get_selected_change_file_path
from ..data.selected_change.clear_reasons import (
    refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list,
)
from ..data.file_hunk_display import (
    cache_unstaged_file_as_single_hunk,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.state import (
    finish_review_scoped_line_action,
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
)
from ..data.file_tracking import auto_add_untracked_files
from ..data.live_diff import stream_live_git_diff
from ..data.progress import (
    record_hunk_included,
)
from ..data.session import require_session_started
from ..data.undo import undo_checkpoint
from ..core.buffer import (
    LineBuffer,
    buffer_matches,
)
from ..data.repository_buffers import (
    load_git_object_as_buffer,
)
from ..exceptions import exit_with_error
from ..i18n import _, ngettext
from ..staging.operations import (
    build_target_index_buffer_from_lines,
    update_index_with_blob_buffer,
)
from ..utils.git import (
    git_add_paths,
    git_apply_to_index,
    require_git_repository,
    run_git_command,
)
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_context_lines,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from .selection import include_line_selection as _include_line_selection
from .selection import include_line_batching as _include_line_batching
from .selection import include_line_replacement as _include_line_replacement
from .selection import replacement_selection
from .selection import selected_change_batch_staging as _selected_change_batch_staging
from .selection import selected_change_staging as _selected_change_staging
from .selection import whole_file_batch_staging as _whole_file_batch_staging
from .file_scope import include_file_replacement as _file_scope_include_file_replacement
from .file_scope import include_file_to_batch as _file_scope_include_file_to_batch
from .selection.selected_hunk_refresh import (
    recalculate_selected_hunk_for_command,
    refresh_selected_hunk_after_line_action,
)
from .selection.action_completion import finish_selected_change_action


def command_include(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Include (stage) the selected hunk or binary file."""

    log_journal("command_include_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
        return 0
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
        return 0
    refuse_bare_action_after_file_list("include")
    refuse_bare_action_after_auto_advance_disabled("include")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        command_include_file("", auto_advance=auto_advance)
        return 0

    return _selected_change_staging.include_selected_change(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def command_include_file(
    file: str,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Include (stage) all hunks from the specified file.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.
        quiet: Suppress per-file status output while preserving selection state.
        advance: When quiet, advance the selection after staging this file.
        auto_advance: Whether to select the next hunk after this action.

    Returns:
        Number of hunks staged from the requested file.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
            return 0
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
            return 0
        refuse_bare_action_after_file_list("include --file")
        refuse_bare_action_after_auto_advance_disabled("include --file")

    # Determine target file
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            diff_result = run_git_command(
                [
                    "-c",
                    "diff.ignoreSubmodules=none",
                    "diff",
                    "--ignore-submodules=none",
                    "--quiet",
                ],
                check=False,
                requires_index_lock=False,
            )
            if diff_result.returncode == 0:
                print(_("No changes to stage."), file=sys.stderr)
            else:
                print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
            return 0
    else:
        # Explicit path provided
        target_file = file
    auto_add_untracked_files([target_file])
    with undo_checkpoint(f"include --file {file}".rstrip()):
        # Stream through the remaining unstaged hunks for this file.
        #
        # Included hunks do not need blocklist entries because staging removes
        # them from `git diff` naturally. Keeping them in the processed blocklist
        # makes later manual unstaging look like stale skipped work, which breaks
        # follow-up `show --files` / `include --files` passes in the same session.
        hunks_staged = 0
        submodule_pointers_staged = 0
        renames_staged = 0
        staged_rename_pairs: set[tuple[str, str]] = set()
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue

                    _selected_change_staging.stage_rename_change(patch)
                    result = git_add_paths([patch.new_path], check=False)
                    if result.returncode != 0:
                        print(_("Failed to stage renamed file: {error}").format(error=result.stderr), file=sys.stderr)
                        break
                    record_hunk_included(compute_rename_change_hash(patch))
                    hunks_staged += 1
                    renames_staged += 1
                    staged_rename_pairs.add((patch.old_path, patch.new_path))
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue

                    _selected_change_staging.stage_text_deletion_change(patch)
                    record_hunk_included(compute_text_file_deletion_hash(patch))
                    hunks_staged += 1
                    continue

                if isinstance(patch, GitlinkChange):
                    if patch.path() == target_file:
                        result = _selected_change_staging.stage_gitlink_change(patch)
                        if result.returncode != 0:
                            print(
                                _("Failed to stage submodule pointer: {error}").format(
                                    error=result.stderr,
                                ),
                                file=sys.stderr,
                            )
                            break
                        record_hunk_included(compute_gitlink_change_hash(patch))
                        hunks_staged += 1
                        submodule_pointers_staged += 1
                    continue

                if isinstance(patch, BinaryFileChange):
                    file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                    if file_path != target_file:
                        continue

                    result = git_add_paths([file_path], check=False)
                    if result.returncode != 0:
                        print(_("Failed to stage binary file: {error}").format(error=result.stderr), file=sys.stderr)
                        break

                    record_hunk_included(compute_binary_file_hash(patch))
                    hunks_staged += 1
                    continue

                patch_paths = {
                    path for path in (patch.old_path, patch.new_path)
                    if path != "/dev/null"
                }
                if target_file not in patch_paths:
                    continue

                if (patch.old_path, patch.new_path) in staged_rename_pairs:
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                if patch.old_path != patch.new_path:
                    result = git_add_paths(sorted(patch_paths), check=False)
                    if result.returncode != 0:
                        print(_("Failed to stage file: {error}").format(error=result.stderr), file=sys.stderr)
                        break

                    record_hunk_included(patch_hash)
                    hunks_staged += 1
                    continue

                if patch_is_file_deletion(patch.lines):
                    with LineBuffer.from_bytes(b"") as empty_buffer:
                        update_index_with_blob_buffer(target_file, empty_buffer)
                    apply_result = None
                else:
                    apply_result = git_apply_to_index(patch.lines, check=False)
                if apply_result is None or apply_result.returncode == 0:
                    # Record for progress tracking
                    record_hunk_included(patch_hash)

                    hunks_staged += 1
                else:
                    print(_("Failed to apply hunk: {error}").format(error=apply_result.stderr), file=sys.stderr)
                    break

    if hunks_staged == 0:
        if not quiet:
            print(_("No hunks staged from {file}").format(file=target_file), file=sys.stderr)
        return 0

    if quiet and advance:
        finish_selected_change_action(quiet=True, auto_advance=auto_advance)
    if quiet:
        return hunks_staged

    # Print summary message
    if renames_staged == hunks_staged:
        msg = ngettext(
            "✓ Staged {count} rename from {file}",
            "✓ Staged {count} renames from {file}",
            hunks_staged,
        ).format(count=hunks_staged, file=target_file)
    elif submodule_pointers_staged == hunks_staged:
        msg = ngettext(
            "✓ Staged {count} submodule pointer from {file}",
            "✓ Staged {count} submodule pointers from {file}",
            hunks_staged,
        ).format(count=hunks_staged, file=target_file)
    else:
        msg = ngettext(
            "✓ Staged {count} hunk from {file}",
            "✓ Staged {count} hunks from {file}",
            hunks_staged
        ).format(count=hunks_staged, file=target_file)
    print(msg, file=sys.stderr)

    if advance:
        finish_selected_change_action(quiet=False, auto_advance=auto_advance)
    return hunks_staged


def command_include_file_as(
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage full-file replacement text for a live file-scoped selection."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    if file is None or file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.INCLUDE):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.INCLUDE):
            return
        refuse_bare_action_after_file_list("include --file --as")
        refuse_bare_action_after_auto_advance_disabled("include --file --as")

    _file_scope_include_file_replacement.include_file_as_replacement(
        replacement_text,
        file,
        auto_advance=auto_advance,
    )


def command_include_line(
    line_id_specification: str,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
        file: Optional file path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.INCLUDE,
        action_command=f"include --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    operation_parts = ["include", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)), ExitStack() as selected_state_stack:
        selection_context = _include_line_selection.load_include_line_selection_context(
            file,
            selected_state_stack,
        )
        line_changes = selection_context.line_changes

        requested_ids = parse_line_selection(line_id_specification)
        require_line_selection_in_view(
            line_changes,
            set(requested_ids),
            line_id_specification=line_id_specification,
        )
        if selection_context.reset_processed_include_ids:
            already_included_ids = set()
        else:
            already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
        combined_include_ids = already_included_ids | set(requested_ids)

        current_index_buffer = load_git_object_as_buffer(f":{line_changes.path}")
        if current_index_buffer is None:
            current_index_buffer = LineBuffer.from_bytes(b"")

        with (
            LineBuffer.from_path(get_index_snapshot_file_path()) as hunk_base_lines,
            LineBuffer.from_path(get_working_tree_snapshot_file_path()) as hunk_source_lines,
            current_index_buffer as current_index_lines,
        ):
            selected_change_kind = read_selected_change_kind()
            if selected_change_kind == SelectedChangeKind.FILE:
                leading_replacement_addition_error = (
                    replacement_selection.build_leading_replacement_addition_selection_error(
                        line_changes,
                        combined_include_ids,
                    )
                )
                if leading_replacement_addition_error is not None:
                    exit_with_error(leading_replacement_addition_error)

                partial_structural_run_error = (
                    replacement_selection.build_partial_structural_run_selection_error(
                        line_changes,
                        combined_include_ids,
                        hunk_base_lines=hunk_base_lines,
                        hunk_source_lines=hunk_source_lines,
                    )
                )
                if partial_structural_run_error is not None:
                    exit_with_error(partial_structural_run_error)

            transient_result = (
                _include_line_selection.try_build_index_content_via_transient_batch(
                    line_changes=line_changes,
                    selected_display_ids=set(combined_include_ids),
                    current_index_lines=current_index_lines,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                )
            )
            if (
                transient_result.buffer is None
                and transient_result.failure_reason
                == _include_line_selection.TransientIncludeFailureReason.INDEX_MERGE_FAILED
                and buffer_matches(current_index_lines, hunk_base_lines)
            ):
                transient_result = _include_line_selection.TransientIncludeResult.success(
                    build_target_index_buffer_from_lines(
                        line_changes,
                        set(combined_include_ids),
                        hunk_base_lines,
                        base_has_trailing_newline=(
                            _include_line_selection.line_sequence_ends_with_lf(
                                hunk_base_lines
                            )
                        ),
                    )
                )
        if transient_result.buffer is not None:
            log_journal(
                "include_line_transient_batch_staging_used",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
            )
            target_index_buffer_context = transient_result.buffer
        else:
            failure_reason = (
                transient_result.failure_reason
                or _include_line_selection.TransientIncludeFailureReason.PREPARATION_FAILED
            )
            log_journal(
                "include_line_transient_batch_staging_declined",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
                reason=failure_reason.value,
                detail=transient_result.failure_detail,
            )
            exit_with_error(
                _include_line_selection.transient_include_failure_message(
                    reason=failure_reason,
                    line_id_specification=line_id_specification,
                    file_path=line_changes.path,
                )
            )

        with target_index_buffer_context as target_index_buffer:
            _include_line_selection.stage_live_line_target_buffer(
                line_changes.path,
                target_index_buffer,
            )

        if selection_context.preserve_selected_state:
            assert selection_context.saved_selected_state is not None
            restore_selected_change_state(selection_context.saved_selected_state)
        else:
            # Update processed include IDs only when the selected display remains
            # current for incremental line inclusion.
            write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)
            print(
                _("✓ Included line(s): {lines} from {file}").format(
                    lines=line_id_specification,
                    file=line_changes.path,
                ),
                file=sys.stderr,
            )
            refresh_selected_hunk_after_line_action(
                line_changes.path,
                auto_advance=auto_advance,
            )
        finish_review_scoped_line_action(review_state, file_path=line_changes.path)
    if selection_context.preserve_selected_state:
        print(
            _("✓ Included line(s): {lines} from {file}").format(
                lines=line_id_specification,
                file=line_changes.path,
            ),
            file=sys.stderr,
        )


def command_include_line_as(
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    no_edge_overlap: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Stage a replacement for one contiguous selected line span and mask it."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.INCLUDE,
        action_command=f"include --line {line_id_specification} --as",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = [
        "include",
        "--line",
        line_id_specification,
        "--as",
        replacement_payload.display_text or "<stdin>",
    ]
    if no_edge_overlap:
        operation_parts.append("--no-edge-overlap")
    if file is not None:
        operation_parts.extend(["--file", file])

    replacement_file_context = None
    with undo_checkpoint(" ".join(operation_parts)), ExitStack() as selected_state_stack:
        if file is None:
            replacement_context = (
                _include_line_replacement.prepare_pathless_include_line_replacement(
                    line_id_specification
                )
            )
            line_changes = replacement_context.display_line_changes
            with (
                replacement_context.base_buffer as hunk_base_lines,
                replacement_context.source_buffer as hunk_source_lines,
            ):
                _include_line_replacement.apply_include_line_replacement(
                    replacement_context.replacement_line_changes,
                    line_id_specification=replacement_context.line_id_specification,
                    replacement_text=replacement_text,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )

            write_line_ids_file(get_processed_include_ids_file_path(), set())
            print(
                _("✓ Included line(s) as replacement: {lines} from {file}").format(
                    lines=line_id_specification,
                    file=line_changes.path,
                ),
                file=sys.stderr,
            )
            refresh_selected_hunk_after_line_action(
                line_changes.path,
                auto_advance=auto_advance,
            )
            finish_review_scoped_line_action(review_state, file_path=line_changes.path)
        else:
            replacement_file_context = (
                _include_line_replacement.prepare_file_include_line_replacement(
                    file,
                    selected_state_stack,
                )
            )
            with (
                replacement_file_context.base_buffer as hunk_base_lines,
                replacement_file_context.source_buffer as hunk_source_lines,
            ):
                _include_line_replacement.apply_include_line_replacement(
                    replacement_file_context.line_changes,
                    line_id_specification=line_id_specification,
                    replacement_text=replacement_text,
                    hunk_base_lines=hunk_base_lines,
                    hunk_source_lines=hunk_source_lines,
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )

            if replacement_file_context.preserve_selected_state:
                assert replacement_file_context.saved_selected_state is not None
                restore_selected_change_state(
                    replacement_file_context.saved_selected_state
                )
            else:
                write_line_ids_file(get_processed_include_ids_file_path(), set())
                print(
                    _("✓ Included line(s) as replacement: {lines} from {file}").format(
                        lines=line_id_specification,
                        file=replacement_file_context.target_file,
                    ),
                    file=sys.stderr,
                )
                refresh_selected_hunk_after_line_action(
                    replacement_file_context.target_file,
                    auto_advance=auto_advance,
                )
            finish_review_scoped_line_action(
                review_state,
                file_path=replacement_file_context.target_file,
            )

    if (
        replacement_file_context is not None
        and replacement_file_context.preserve_selected_state
    ):
        print(
            _("✓ Included line(s) as replacement: {lines} from {file}").format(
                lines=line_id_specification,
                file=replacement_file_context.target_file,
            ),
            file=sys.stderr,
        )


def command_include_to_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save selected changes to batch instead of staging.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to include
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
        auto_advance: Whether to select the next hunk after this action.
    """
    require_git_repository()
    ensure_state_directory_exists()
    original_file_scope = file
    scope_resolution = resolve_live_to_batch_action_scope(
        FileReviewAction.INCLUDE_TO_BATCH,
        command_name="include",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
    )
    if scope_resolution.should_stop:
        return
    file = scope_resolution.file
    review_state = scope_resolution.review_state
    operation_parts = ["include", "--to", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.RENAME
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, RenameChange):
                exit_with_error(
                    _(
                        "Cannot include rename '{old} -> {new}' to a batch yet. "
                        "Stage, skip, or discard the rename first."
                    ).format(
                        old=selected_change.old_path,
                        new=selected_change.new_path,
                    )
                )
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.GITLINK
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, GitlinkChange):
                _whole_file_batch_staging.include_gitlink_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.DELETION
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, TextFileDeletionChange):
                _whole_file_batch_staging.include_text_deletion_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                _whole_file_batch_staging.include_binary_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            else:
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
        elif file is not None:
            # File-scoped operation

            # Determine target file
            if file == "":
                # --file with no arg: use selected hunk's file
                target_file = get_selected_change_file_path()
                if target_file is None:
                    exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
            else:
                target_file = file

            if line_ids is None:
                # --file without --line: include entire file
                _file_scope_include_file_to_batch.include_file_to_batch(
                    batch_name,
                    target_file,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                # --file with --line: include specific lines from file
                _include_line_batching.include_file_lines_to_batch(
                    batch_name,
                    target_file,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        else:
            # Hunk-scoped operation (selected behavior)
            if line_ids is not None:
                _include_line_batching.include_selected_lines_to_batch(
                    batch_name,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                # Include entire selected hunk
                _selected_change_batch_staging.include_selected_change_to_batch(
                    batch_name,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
