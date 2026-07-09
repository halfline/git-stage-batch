"""Line-selection support for include commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
import uuid

from ...batch.operations import create_batch, delete_batch
from ...batch.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.ownership import BatchOwnership
from ...batch.ownership_translation import (
    translate_hunk_selection_to_batch_ownership,
)
from ...batch.query import read_batch_metadata
from ...batch.selection import line_selection_not_valid_message
from ...batch.text_file_storage import add_file_to_batch
from ...batch.validation import batch_exists
from ...core.buffer import LineBuffer, buffer_matches
from ...data.batch_sources import create_batch_source_commit
from ...data.file_hunk_display import cache_unstaged_file_as_single_hunk
from ...data.file_modes import detect_file_mode
from ...data.file_tracking import auto_add_untracked_files
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
    snapshot_selected_change_state,
)
from ...data.selected_change.snapshots import snapshots_are_stale
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.operations import update_index_with_blob_buffer
from ...utils.paths import get_session_batch_sources_file_path
from . import replacement_selection


class TransientIncludeFailureReason(Enum):
    """Why transient batch staging could not safely realize a line selection."""

    NO_SELECTED_LINES = "no_selected_lines"
    EMPTY_OWNERSHIP = "empty_ownership"
    PREPARATION_FAILED = "preparation_failed"
    MISSING_BATCH_METADATA = "missing_batch_metadata"
    MISSING_BATCH_SOURCE = "missing_batch_source"
    INDEX_MERGE_FAILED = "index_merge_failed"
    WORKING_TREE_MERGE_FAILED = "working_tree_merge_failed"
    WORKING_TREE_WOULD_CHANGE = "working_tree_would_change"


@dataclass(frozen=True)
class TransientIncludeResult:
    """Result of staging a live line selection through transient batch ownership."""

    buffer: LineBuffer | None
    failure_reason: TransientIncludeFailureReason | None = None
    failure_detail: str | None = None

    @classmethod
    def success(cls, buffer: LineBuffer) -> TransientIncludeResult:
        return cls(buffer=buffer)

    @classmethod
    def failure(
        cls,
        reason: TransientIncludeFailureReason,
        *,
        detail: str | None = None,
    ) -> TransientIncludeResult:
        return cls(buffer=None, failure_reason=reason, failure_detail=detail)


@dataclass(frozen=True)
class IncludeLineSelectionContext:
    """Resolved selected-line view for a live include action."""

    line_changes: object
    preserve_selected_state: bool = False
    saved_selected_state: object | None = None
    reset_processed_include_ids: bool = False


def record_baseline_references_for_additions(line_changes) -> None:
    """Attach old-file insertion references to addition lines for batch round trips."""
    last_old_line: int | None = None
    last_old_text_bytes: bytes | None = None
    index = 0

    while index < len(line_changes.lines):
        line = line_changes.lines[index]
        if line.kind == "+":
            next_old_line: int | None = None
            next_old_text_bytes: bytes | None = None
            scan_index = index + 1
            while scan_index < len(line_changes.lines):
                next_line = line_changes.lines[scan_index]
                if next_line.kind in {" ", "-"} and next_line.old_line_number is not None:
                    next_old_line = next_line.old_line_number
                    next_old_text_bytes = next_line.text_bytes
                    break
                scan_index += 1

            while index < len(line_changes.lines) and line_changes.lines[index].kind == "+":
                addition_line = line_changes.lines[index]
                addition_line.baseline_reference_after_line = last_old_line
                addition_line.baseline_reference_after_text_bytes = last_old_text_bytes
                addition_line.has_baseline_reference_after = True
                addition_line.baseline_reference_before_line = next_old_line
                addition_line.baseline_reference_before_text_bytes = next_old_text_bytes
                addition_line.has_baseline_reference_before = next_old_line is not None
                index += 1
            continue

        if line.kind in {" ", "-"} and line.old_line_number is not None:
            last_old_line = line.old_line_number
            last_old_text_bytes = line.text_bytes
        index += 1


def _snapshot_session_batch_sources_file() -> tuple[bool, bytes | None]:
    path = get_session_batch_sources_file_path()
    if not path.exists():
        return False, None
    return True, path.read_bytes()


def _restore_session_batch_sources_file(existed: bool, content: bytes | None) -> None:
    path = get_session_batch_sources_file_path()
    if existed:
        assert content is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def selected_file_view_targets(target_file: str) -> bool:
    """Return whether the selected file view targets a path."""
    return (
        read_selected_change_kind() == SelectedChangeKind.FILE
        and get_selected_change_file_path() == target_file
    )


def selected_file_view_is_fresh_for(target_file: str) -> bool:
    """Return whether the selected file view can be reused for a path."""
    return (
        selected_file_view_targets(target_file)
        and not snapshots_are_stale(target_file)
    )


def load_include_line_selection_context(
    file: str | None,
    selected_state_stack,
) -> IncludeLineSelectionContext:
    """Resolve the selected line view for include --line."""
    if file is None:
        require_selected_hunk()
        return IncludeLineSelectionContext(
            line_changes=annotate_line_changes_with_working_tree_source(
                load_line_changes_from_state()
            )
        )

    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
    else:
        target_file = file

    auto_add_untracked_files([target_file])
    selected_file_view_targets_file = selected_file_view_targets(target_file)
    reuse_selected_file_view = selected_file_view_is_fresh_for(target_file)
    preserve_selected_state = False
    saved_selected_state = None

    if reuse_selected_file_view:
        line_changes = load_line_changes_from_state()
    else:
        if file != "" and not selected_file_view_targets_file:
            preserve_selected_state = True
            saved_selected_state = selected_state_stack.enter_context(
                snapshot_selected_change_state()
            )

        line_changes = cache_unstaged_file_as_single_hunk(target_file)
        if line_changes is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

    return IncludeLineSelectionContext(
        line_changes=annotate_line_changes_with_working_tree_source(line_changes),
        preserve_selected_state=preserve_selected_state,
        saved_selected_state=saved_selected_state,
        reset_processed_include_ids=not reuse_selected_file_view,
    )


def line_sequence_ends_with_lf(lines: Sequence[bytes]) -> bool:
    """Return whether a byte-line sequence has a trailing newline."""
    line_count = len(lines)
    return line_count > 0 and lines[line_count - 1].endswith(b"\n")


def annotate_line_changes_with_working_tree_source(line_changes):
    """Attach working-tree source line positions to line changes."""
    if line_changes is None:
        return None

    last_source_line: int | None = None
    new_lines = []
    for line in line_changes.lines:
        source_line = None
        if line.kind in {" ", "+"}:
            source_line = line.new_line_number
            if source_line is not None:
                last_source_line = source_line
        elif line.kind == "-":
            source_line = last_source_line
            if (
                source_line is None
                and line.old_line_number is not None
                and line.old_line_number > 1
            ):
                source_line = line.old_line_number - 1

        new_lines.append(replace(line, source_line=source_line))

    return replace(line_changes, lines=new_lines)


def try_build_index_content_via_transient_batch(
    *,
    line_changes,
    selected_display_ids: set[int],
    current_index_lines: Sequence[bytes],
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: Sequence[bytes],
) -> TransientIncludeResult:
    """Try staging live lines through transient batch ownership."""
    selected_lines = [
        line
        for line in line_changes.lines
        if line.id in selected_display_ids
    ]
    if not selected_lines:
        return TransientIncludeResult.failure(
            TransientIncludeFailureReason.NO_SELECTED_LINES
        )

    batch_name = f"include-line-{uuid.uuid4().hex}"
    session_sources_existed, session_sources_content = _snapshot_session_batch_sources_file()
    created_batch = False
    target_index_buffer: LineBuffer | None = None

    try:
        create_batch(batch_name, "Transient include-line selection")
        created_batch = True

        record_baseline_references_for_additions(line_changes)
        ownership = translate_hunk_selection_to_batch_ownership(
            line_changes.lines,
            selected_display_ids,
            replacement_line_runs=replacement_selection.derive_replacement_line_runs(
                hunk_base_lines=hunk_base_lines,
                hunk_source_lines=hunk_source_lines,
            ),
        )
        if ownership.is_empty():
            return TransientIncludeResult.failure(
                TransientIncludeFailureReason.EMPTY_OWNERSHIP
            )

        with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
            batch_source_commit = create_batch_source_commit(
                line_changes.path,
                file_buffer_override=working_lines,
            )
            add_file_to_batch(
                batch_name,
                line_changes.path,
                ownership,
                detect_file_mode(line_changes.path),
                batch_source_commit=batch_source_commit,
            )

            metadata = read_batch_metadata(batch_name)
            file_metadata = metadata.get("files", {}).get(line_changes.path)
            if file_metadata is None:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_METADATA
                )

            batch_source_commit = file_metadata.get("batch_source_commit")
            if not batch_source_commit:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_METADATA
                )

            source_buffer = load_git_object_as_buffer(
                f"{batch_source_commit}:{line_changes.path}"
            )
            if source_buffer is None:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_SOURCE
                )

            with (
                BatchOwnership.acquire_for_metadata_dict(file_metadata) as ownership,
                source_buffer as source_lines,
            ):
                try:
                    target_index_buffer = merge_batch_from_line_sequences_as_buffer(
                        source_lines,
                        ownership,
                        current_index_lines,
                    )
                except Exception as error:
                    return TransientIncludeResult.failure(
                        TransientIncludeFailureReason.INDEX_MERGE_FAILED,
                        detail=error.__class__.__name__,
                    )

                try:
                    target_working_buffer = merge_batch_from_line_sequences_as_buffer(
                        source_lines,
                        ownership,
                        working_lines,
                    )
                except Exception as error:
                    target_index_buffer.close()
                    target_index_buffer = None
                    return TransientIncludeResult.failure(
                        TransientIncludeFailureReason.WORKING_TREE_MERGE_FAILED,
                        detail=error.__class__.__name__,
                    )

                with target_working_buffer:
                    if not buffer_matches(working_lines, target_working_buffer):
                        target_index_buffer.close()
                        target_index_buffer = None
                        return TransientIncludeResult.failure(
                            TransientIncludeFailureReason.WORKING_TREE_WOULD_CHANGE
                        )

        assert target_index_buffer is not None
        return TransientIncludeResult.success(target_index_buffer)
    except Exception as error:
        if target_index_buffer is not None:
            target_index_buffer.close()
        return TransientIncludeResult.failure(
            TransientIncludeFailureReason.PREPARATION_FAILED,
            detail=error.__class__.__name__,
        )
    finally:
        if created_batch and batch_exists(batch_name):
            delete_batch(batch_name)
        _restore_session_batch_sources_file(
            session_sources_existed,
            session_sources_content,
        )


def stage_live_line_target_buffer(file_path: str, target_buffer: LineBuffer) -> None:
    """Stage the result of live line-level include."""
    update_index_with_blob_buffer(file_path, target_buffer)


def transient_include_failure_message(
    *,
    reason: TransientIncludeFailureReason,
    line_id_specification: str,
    file_path: str,
) -> str:
    """Return a user-facing message for transient include failures."""
    if reason in (
        TransientIncludeFailureReason.NO_SELECTED_LINES,
        TransientIncludeFailureReason.EMPTY_OWNERSHIP,
    ):
        return line_selection_not_valid_message(
            line_id_specification=line_id_specification,
            file_path=file_path,
        )

    if reason in (
        TransientIncludeFailureReason.WORKING_TREE_MERGE_FAILED,
        TransientIncludeFailureReason.WORKING_TREE_WOULD_CHANGE,
    ):
        return _(
            "Cannot safely include line(s) {lines} from {file} because applying "
            "that selection would also change the working tree.\n"
            "Run 'git-stage-batch show --file {file}' and choose line IDs from "
            "the current file view."
        ).format(lines=line_id_specification, file=file_path)

    if reason == TransientIncludeFailureReason.INDEX_MERGE_FAILED:
        return _(
            "Cannot safely include line(s) {lines} from {file} because the "
            "selection no longer fits the current staged content.\n"
            "Run 'git-stage-batch show --file {file}' and choose line IDs from "
            "the current file view."
        ).format(lines=line_id_specification, file=file_path)

    return _(
        "Cannot safely include line(s) {lines} from {file}.\n"
        "Run 'git-stage-batch show --file {file}' and choose line IDs from "
        "the current file view."
    ).format(lines=line_id_specification, file=file_path)
