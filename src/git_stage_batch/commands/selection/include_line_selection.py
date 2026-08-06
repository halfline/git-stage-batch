"""Line-selection support for include commands."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from enum import Enum
import sys
import uuid

from ...batch.ownership.model import BatchOwnership
from ...batch.ownership.hunk_translation import (
    translate_hunk_selection_to_batch_ownership,
)
from ...batch.ownership import insertion_references as _insertion_references
from ...batch.merge.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.state.lifecycle import create_batch, delete_batch
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.state.query import read_batch_metadata
from ...batch.selection import line_selection_not_valid_message
from ...batch.text_file_storage import add_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer, buffer_matches
from ...batch.source.snapshots import create_batch_source_commit
from ...batch.source.line_coordinates import translate_display_source_coordinates
from ...batch.realized_file_content import build_realized_buffer_from_lines
from ...core.models import LineEntry, LineLevelChange
from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.file_modes import detect_file_mode
from ...data.file_tracking import auto_add_untracked_files
from ...data.line_state import require_line_changes_from_state
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    SelectedChangeStateSnapshot,
    read_selected_change_kind,
    snapshot_selected_change_state,
)
from ...exceptions import MergeError, exit_with_error
from ...git_paths import display_path, terminal_safe_shell_quote
from ...i18n import _
from ...staging.index_update import update_index_with_blob_buffer
from ...staging.content_buffers import build_target_index_buffer_from_lines
from ...utils.file_io import write_file_bytes
from ...utils.git_command import run_git_command
from ...utils.git_index import git_write_tree
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
    content_is_worktree: bool = False

    @classmethod
    def success(
        cls,
        buffer: LineBuffer,
        *,
        content_is_worktree: bool = False,
    ) -> TransientIncludeResult:
        return cls(buffer=buffer, content_is_worktree=content_is_worktree)

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

    line_changes: LineLevelChange
    preserve_selected_state: bool = False
    saved_selected_state: SelectedChangeStateSnapshot | None = None
    reset_processed_include_ids: bool = False


def _snapshot_session_batch_sources_file() -> tuple[bool, bytes | None]:
    path = get_session_batch_sources_file_path()
    if not path.exists():
        return False, None
    return True, path.read_bytes()


def _restore_session_batch_sources_file(existed: bool, content: bytes | None) -> None:
    path = get_session_batch_sources_file_path()
    if existed:
        assert content is not None
        write_file_bytes(path, content)
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
    """Return whether the selected file view can be reused for a path.

    A matching view represents the line IDs the user actually saw.  If its
    snapshots are stale, fail closed instead of rebuilding a different view
    and applying those old IDs to it.
    """
    if not selected_file_view_targets(target_file):
        return False
    require_selected_hunk()
    return True


def load_include_line_selection_context(
    file: str | None,
    selected_state_stack: ExitStack,
) -> IncludeLineSelectionContext:
    """Resolve the selected line view for include --line."""
    if file is None:
        require_selected_hunk()
        return IncludeLineSelectionContext(
            line_changes=annotate_line_changes_with_working_tree_source(
                require_line_changes_from_state()
            )
        )

    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            exit_with_error(
                _("No selected hunk. Run 'show' first or specify file path.")
            )
    else:
        target_file = file

    auto_add_untracked_files([target_file])
    selected_file_view_targets_file = selected_file_view_targets(target_file)
    reuse_selected_file_view = selected_file_view_is_fresh_for(target_file)
    preserve_selected_state = False
    saved_selected_state = None

    if reuse_selected_file_view:
        line_changes = require_line_changes_from_state()
    else:
        if file != "" and not selected_file_view_targets_file:
            preserve_selected_state = True
            saved_selected_state = selected_state_stack.enter_context(
                snapshot_selected_change_state()
            )

        cached_line_changes = cache_unstaged_file_as_single_hunk(target_file)
        if cached_line_changes is None:
            exit_with_error(
                _("No changes in file '{file}'.").format(
                    file=display_path(target_file)
                )
            )
        line_changes = cached_line_changes

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


def _path_has_line_transforming_attributes(file_path: str) -> bool:
    """Return whether Git may change line bytes before storing one path."""
    result = run_git_command(
        [
            "check-attr",
            "-z",
            "text",
            "eol",
            "ident",
            "filter",
            "working-tree-encoding",
            "crlf",
            "--",
            file_path,
        ],
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    fields = result.stdout.split(b"\0")
    values = fields[2::3]
    if any(value not in (b"", b"unspecified", b"unset") for value in values):
        return True

    autocrlf = run_git_command(
        ["config", "--get", "core.autocrlf"],
        check=False,
        requires_index_lock=False,
    )
    return autocrlf.returncode == 0 and autocrlf.stdout.strip().lower() in {
        "1",
        "on",
        "yes",
        "true",
        "input",
    }


def annotate_line_changes_with_working_tree_source(
    line_changes: LineLevelChange,
) -> LineLevelChange:
    """Attach working-tree source line positions to line changes."""
    new_lines: list[LineEntry] = []
    for line, source_line in translate_display_source_coordinates(
        line_changes.lines,
        lambda line_number: line_number,
    ):
        new_lines.append(replace(line, source_line=source_line))

    return replace(line_changes, lines=new_lines)


def build_transient_index_buffer(
    *,
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    current_index_lines: Sequence[bytes],
    hunk_base_lines: Sequence[bytes],
) -> LineBuffer:
    """Merge structurally, then use unchanged reviewed index coordinates."""
    try:
        return merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            current_index_lines,
        )
    except MergeError:
        # The ownership references use the reviewed index snapshot's
        # coordinates. They are a safe fallback only while the live index
        # still matches that exact baseline.
        if not buffer_matches(current_index_lines, hunk_base_lines):
            raise
        return build_realized_buffer_from_lines(
            current_index_lines,
            source_lines,
            ownership,
            preferred_line_ending_lines=current_index_lines,
        )


def try_build_index_content_via_transient_batch(
    *,
    line_changes: LineLevelChange,
    selected_display_ids: set[int],
    current_index_lines: Sequence[bytes],
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: Sequence[bytes],
) -> TransientIncludeResult:
    """Try staging live lines through transient batch ownership."""
    def line_is_selected(line: LineEntry) -> bool:
        return line.id in selected_display_ids

    if not any(line_is_selected(line) for line in line_changes.lines):
        return TransientIncludeResult.failure(
            TransientIncludeFailureReason.NO_SELECTED_LINES
        )

    if _path_has_line_transforming_attributes(line_changes.path):
        with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
            if not buffer_matches(working_lines, hunk_source_lines):
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.WORKING_TREE_WOULD_CHANGE,
                    detail="working tree changed after transformed-file snapshot",
                )
        try:
            return TransientIncludeResult.success(
                build_target_index_buffer_from_lines(
                    line_changes,
                    selected_display_ids,
                    current_index_lines,
                    base_has_trailing_newline=line_sequence_ends_with_lf(
                        current_index_lines
                    ),
                )
            )
        except ValueError as error:
            return TransientIncludeResult.failure(
                TransientIncludeFailureReason.INDEX_MERGE_FAILED,
                detail=str(error),
            )

    if not current_index_lines and all(
        line.kind == "+" for line in line_changes.lines
    ):
        if any(
            line.source_line is None
            for line in line_changes.lines
            if line_is_selected(line)
        ):
            return TransientIncludeResult.failure(
                TransientIncludeFailureReason.PREPARATION_FAILED,
                detail="missing source line for new-file selection",
            )
        with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
            if not buffer_matches(working_lines, hunk_source_lines):
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.WORKING_TREE_WOULD_CHANGE,
                    detail="working tree changed after new-file snapshot",
                )
        def selected_source_chunks() -> Iterator[bytes]:
            for line in line_changes.lines:
                if not line_is_selected(line):
                    continue
                assert line.source_line is not None
                yield hunk_source_lines[line.source_line - 1]

        return TransientIncludeResult.success(
            LineBuffer.from_chunks(selected_source_chunks()),
            content_is_worktree=True,
        )

    batch_name = f"include-line-{uuid.uuid4().hex}"
    session_sources_existed, session_sources_content = (
        _snapshot_session_batch_sources_file()
    )
    created_batch = False
    target_index_buffer: LineBuffer | None = None

    try:
        create_batch(
            batch_name,
            "Transient include-line selection",
            baseline_commit=git_write_tree(),
        )
        created_batch = True

        _insertion_references.record_baseline_references_for_additions(
            line_changes,
            baseline_lines=hunk_base_lines,
            source_lines=hunk_source_lines,
        )
        ownership = translate_hunk_selection_to_batch_ownership(
            line_changes.lines,
            selected_display_ids,
            baseline_lines=hunk_base_lines,
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
            try:
                add_file_to_batch(
                    batch_name,
                    line_changes.path,
                    ownership,
                    detect_file_mode(line_changes.path),
                    batch_source_commit=batch_source_commit,
                )
            except MergeError as error:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.PREPARATION_FAILED,
                    detail=str(error),
                )

            metadata = read_batch_metadata(batch_name)
            file_metadata = metadata.get("files", {}).get(line_changes.path)
            if file_metadata is None:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_METADATA
                )

            stored_batch_source_commit = file_metadata.get("batch_source_commit")
            if not stored_batch_source_commit:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_METADATA
                )

            source_buffer = read_git_object_buffer_or_none(
                f"{stored_batch_source_commit}:{line_changes.path}"
            )
            if source_buffer is None:
                return TransientIncludeResult.failure(
                    TransientIncludeFailureReason.MISSING_BATCH_SOURCE
                )

            with (
                acquire_ownership_for_metadata_dict(file_metadata) as ownership,
                source_buffer as source_lines,
            ):
                try:
                    target_index_buffer = build_transient_index_buffer(
                        source_lines=source_lines,
                        ownership=ownership,
                        current_index_lines=current_index_lines,
                        hunk_base_lines=hunk_base_lines,
                    )
                except MergeError as error:
                    return TransientIncludeResult.failure(
                        TransientIncludeFailureReason.INDEX_MERGE_FAILED,
                        detail=str(error),
                    )

                try:
                    target_working_buffer = merge_batch_from_line_sequences_as_buffer(
                        source_lines,
                        ownership,
                        working_lines,
                    )
                except MergeError as error:
                    target_index_buffer.close()
                    target_index_buffer = None
                    return TransientIncludeResult.failure(
                        TransientIncludeFailureReason.WORKING_TREE_MERGE_FAILED,
                        detail=str(error),
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
    finally:
        if sys.exc_info()[0] is not None and target_index_buffer is not None:
            target_index_buffer.close()
        if created_batch and batch_exists(batch_name):
            delete_batch(batch_name)
        _restore_session_batch_sources_file(
            session_sources_existed,
            session_sources_content,
        )


def stage_live_line_target_buffer(
    file_path: str,
    target_buffer: LineBuffer,
    *,
    content_is_worktree: bool,
) -> None:
    """Stage the result of live line-level include."""
    update_index_with_blob_buffer(
        file_path,
        target_buffer,
        apply_worktree_conversion=content_is_worktree,
    )


def _format_transient_include_failure(
    message: str,
    *,
    line_id_specification: str,
    file_path: str,
) -> str:
    """Insert a readable path and a terminal-safe recovery command."""
    displayed_file = display_path(file_path)
    rendered = message.format(
        lines=line_id_specification,
        file=displayed_file,
    )
    return rendered.replace(
        f"git-stage-batch show --file {displayed_file}",
        "git-stage-batch show --file "
        f"{terminal_safe_shell_quote(file_path)}",
    )


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
        return _format_transient_include_failure(
            _(
                "Cannot safely include selection {lines} from {file} because applying "
                "that selection would also change the working tree.\n"
                "Run 'git-stage-batch show --file {file}' and choose line IDs from "
                "the current file view."
            ),
            line_id_specification=line_id_specification,
            file_path=file_path,
        )

    if reason == TransientIncludeFailureReason.INDEX_MERGE_FAILED:
        return _format_transient_include_failure(
            _(
                "Cannot safely include selection {lines} from {file} because the "
                "selection no longer fits the current staged content.\n"
                "Run 'git-stage-batch show --file {file}' and choose line IDs from "
                "the current file view."
            ),
            line_id_specification=line_id_specification,
            file_path=file_path,
        )

    return _format_transient_include_failure(
        _(
            "Cannot safely include selection {lines} from {file}.\n"
            "Run 'git-stage-batch show --file {file}' and choose line IDs from "
            "the current file view."
        ),
        line_id_specification=line_id_specification,
        file_path=file_path,
    )
