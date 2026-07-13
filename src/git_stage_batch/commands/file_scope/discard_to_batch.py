"""Multi-file discard-to-batch command support."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
import os

from ...batch.source.annotation import annotate_with_batch_source
from ...batch.state.lifecycle import create_batch
from ...batch.ownership_update import acquire_batch_ownership_update_for_selection
from ...batch.state.query import read_batch_metadata
from ...batch.text_file_storage import BatchFileUpdate, add_files_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer
from ...core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ...core.hashing import compute_stable_hunk_hash_from_lines
from ...core.models import BinaryFileChange, FileModeChange, GitlinkChange, RenameChange, TextFileDeletionChange
from ...data.file_modes import detect_file_mode_from_root
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunks_discarded
from ...data.session import snapshot_files_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import read_text_file_line_set
from ...utils.git_worktree import (
    git_apply_to_worktree,
    git_remove_paths,
)
from ...utils.git_repository import (
    get_git_repository_root_path,
    require_git_repository,
)
from ...utils.journal import log_journal
from ...utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
)
from ...utils.session_start_point import session_comparison_base
from ..selection.action_completion import finish_selected_change_action
from .discard_file_to_batch import discard_file_to_batch


@dataclass(frozen=True)
class _PreparedPatchDiscard:
    """One patch scheduled for reverse application."""

    patch_lines: Sequence[bytes]
    patch_hash: str


@dataclass
class _TextFileDiscardInput:
    """Collected text hunks for one file-scope discard."""

    file_path: str
    file_mode: str
    all_lines_to_batch: list
    patches_to_discard: list[_PreparedPatchDiscard]


@dataclass(frozen=True)
class _CollectedTextFileDiscards:
    """Collected text-file discard inputs from one live diff."""

    inputs_by_file: dict[str, _TextFileDiscardInput]
    files_with_text_patches: set[str]


@dataclass(frozen=True)
class _PreparedTextFileDiscardToBatch:
    """Prepared text-file discard that can be published atomically."""

    file_path: str
    file_mode: str
    ownership: object
    batch_source_commit: str | None
    patches_to_discard: list[_PreparedPatchDiscard]


@dataclass(frozen=True)
class DiscardFilesToBatchResult:
    """Aggregate result for multi-file discard-to-batch actions."""

    discarded_hunks: int
    discarded_files: list[str]


class _DiscardFilesToBatchSession:
    """Mutable publication state for one multi-file discard-to-batch action."""

    def __init__(self, batch_name: str, metadata: dict) -> None:
        self._batch_name = batch_name
        self._metadata = metadata
        self._ownership_stack = ExitStack()
        self._prepared_discards: list[_PreparedTextFileDiscardToBatch] = []
        self._discarded_hunks = 0
        self._discarded_files: list[str] = []

    @property
    def discarded_hunks(self) -> int:
        """Return the total discarded hunk count."""
        return self._discarded_hunks

    @property
    def discarded_files(self) -> list[str]:
        """Return files that produced discarded hunks."""
        return self._discarded_files

    def close(self) -> None:
        """Release any pending batch ownership update contexts."""
        self._ownership_stack.close()

    def prepare_text_file(self, discard_input: _TextFileDiscardInput) -> bool:
        """Stage one text file for a later batch publication flush."""
        prepared = _prepare_text_file_discard_to_batch(
            self._batch_name,
            discard_input,
            metadata=self._metadata,
            ownership_stack=self._ownership_stack,
        )
        if prepared is None:
            return False
        self._prepared_discards.append(prepared)
        return True

    def flush(self) -> None:
        """Publish any pending prepared text file discards."""
        ownership_stack = self._ownership_stack
        prepared_discards = self._prepared_discards
        self._ownership_stack = ExitStack()
        self._prepared_discards = []

        with ownership_stack:
            result = _discard_prepared_text_files_to_batch(
                self._batch_name,
                prepared_discards,
            )
        self._record_result(result)

    def record_single_file_discard(self, file_path: str, discarded_hunks: int) -> bool:
        """Record fallback single-file command output."""
        if discarded_hunks <= 0:
            return False
        self._record_result(
            DiscardFilesToBatchResult(
                discarded_hunks=discarded_hunks,
                discarded_files=[file_path],
            )
        )
        return True

    def _record_result(self, result: DiscardFilesToBatchResult) -> None:
        if result.discarded_hunks <= 0:
            return
        self._discarded_hunks += result.discarded_hunks
        self._discarded_files.extend(result.discarded_files)
        self._metadata = read_batch_metadata(self._batch_name)


def _prepare_text_file_discard_to_batch(
    batch_name: str,
    discard_input: _TextFileDiscardInput,
    *,
    metadata: dict,
    ownership_stack: ExitStack,
) -> _PreparedTextFileDiscardToBatch | None:
    """Prepare one normal text file discard without publishing batch state."""
    if not discard_input.all_lines_to_batch:
        return None

    file_path = discard_input.file_path
    file_metadata = metadata.get("files", {}).get(file_path)

    try:
        update = ownership_stack.enter_context(
            acquire_batch_ownership_update_for_selection(
                batch_name=batch_name,
                file_path=file_path,
                file_metadata=file_metadata,
                selected_lines=discard_input.all_lines_to_batch,
            )
        )
    except ValueError as e:
        exit_with_error(
            _(
                "Cannot discard file to batch: batch source is stale and remapping failed.\n"
                "File: {file}\n"
                "Batch: {batch}\n"
                "Error: {error}"
            ).format(file=file_path, batch=batch_name, error=str(e))
        )

    return _PreparedTextFileDiscardToBatch(
        file_path=file_path,
        file_mode=discard_input.file_mode,
        ownership=update.ownership_after,
        batch_source_commit=update.batch_source_commit,
        patches_to_discard=discard_input.patches_to_discard,
    )


def _collect_text_file_discard_inputs(
    files: list[str],
    *,
    blocked_hashes: set[str],
    patch_stack: ExitStack,
) -> _CollectedTextFileDiscards:
    """Collect normal text file discard inputs from one Git diff."""
    if not files:
        return _CollectedTextFileDiscards(
            inputs_by_file={},
            files_with_text_patches=set(),
        )

    repo_root = get_git_repository_root_path()
    inputs_by_file: dict[str, _TextFileDiscardInput] = {}
    files_with_text_patches: set[str] = set()

    with acquire_unified_diff(
        stream_live_git_diff(
            base=session_comparison_base(),
            context_lines=get_context_lines(),
            paths=files,
        )
    ) as patches:
        for patch in patches:
            if isinstance(patch, FileModeChange):
                continue
            if isinstance(patch, RenameChange):
                continue

            if isinstance(patch, TextFileDeletionChange):
                continue

            if isinstance(patch, GitlinkChange):
                exit_with_error(
                    _("Discarding submodule pointer changes to a batch is not supported yet.")
                )

            if isinstance(patch, BinaryFileChange):
                continue

            file_path = patch.path()
            files_with_text_patches.add(file_path)

            patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)
            if patch_hash in blocked_hashes:
                continue

            hunk_lines = build_line_changes_from_patch_lines(
                patch.lines,
                annotator=annotate_with_batch_source,
            )
            discard_input = inputs_by_file.get(file_path)
            if discard_input is None:
                discard_input = _TextFileDiscardInput(
                    file_path=file_path,
                    file_mode=detect_file_mode_from_root(repo_root, file_path),
                    all_lines_to_batch=[],
                    patches_to_discard=[],
                )
                inputs_by_file[file_path] = discard_input
            discard_input.all_lines_to_batch.extend(hunk_lines.lines)
            discard_input.patches_to_discard.append(
                _PreparedPatchDiscard(
                    patch_lines=patch_stack.enter_context(
                        LineBuffer.from_chunks(patch.lines)
                    ),
                    patch_hash=patch_hash,
                )
            )
            blocked_hashes.add(patch_hash)

    return _CollectedTextFileDiscards(
        inputs_by_file=inputs_by_file,
        files_with_text_patches=files_with_text_patches,
    )


def _run_reverse_apply_for_prepared_discards(
    prepared_discards: list[_PreparedTextFileDiscardToBatch],
    *,
    check_only: bool = False,
) -> None:
    def patch_chunks():
        for prepared in prepared_discards:
            for patch in prepared.patches_to_discard:
                yield from patch.patch_lines

    apply_result = git_apply_to_worktree(
        patch_chunks(),
        reverse=True,
        unidiff_zero=True,
        check_only=check_only,
        check=False,
    )

    if apply_result.returncode != 0:
        exit_with_error(
            _("Failed to discard changes from file: {err}").format(
                err=apply_result.stderr
            )
        )


def _discard_prepared_text_files_to_batch(
    batch_name: str,
    prepared_discards: list[_PreparedTextFileDiscardToBatch],
) -> DiscardFilesToBatchResult:
    """Publish prepared text file discards once, then update the worktree."""
    if not prepared_discards:
        return DiscardFilesToBatchResult(discarded_hunks=0, discarded_files=[])

    snapshot_files_if_untracked([prepared.file_path for prepared in prepared_discards])

    _run_reverse_apply_for_prepared_discards(prepared_discards, check_only=True)
    add_files_to_batch(
        batch_name,
        [
            BatchFileUpdate(
                file_path=prepared.file_path,
                ownership=prepared.ownership,
                file_mode=prepared.file_mode,
                batch_source_commit=prepared.batch_source_commit,
            )
            for prepared in prepared_discards
        ],
    )
    _run_reverse_apply_for_prepared_discards(prepared_discards)

    repo_root = get_git_repository_root_path()
    for prepared in prepared_discards:
        full_path = repo_root / prepared.file_path
        if not os.path.lexists(full_path):
            git_remove_paths([prepared.file_path], cached=True, quiet=True, check=False)

    hunk_hashes = [
        patch.patch_hash
        for prepared in prepared_discards
        for patch in prepared.patches_to_discard
    ]
    record_hunks_discarded(hunk_hashes)

    return DiscardFilesToBatchResult(
        discarded_hunks=len(hunk_hashes),
        discarded_files=[
            prepared.file_path
            for prepared in prepared_discards
            if prepared.patches_to_discard
        ],
    )


def discard_files_to_batch(
    batch_name: str,
    files: list[str],
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> DiscardFilesToBatchResult:
    """Save resolved text files to a batch with one batch publication."""
    require_git_repository()
    ensure_state_directory_exists()

    if not files:
        return DiscardFilesToBatchResult(discarded_hunks=0, discarded_files=[])
    auto_add_untracked_files(files)
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    blocklist_path = get_block_list_file_path()
    blocked_hashes = read_text_file_line_set(blocklist_path)
    session = _DiscardFilesToBatchSession(
        batch_name=batch_name,
        metadata=read_batch_metadata(batch_name),
    )
    patch_stack = ExitStack()

    try:
        collected_discards = _collect_text_file_discard_inputs(
            files,
            blocked_hashes=blocked_hashes,
            patch_stack=patch_stack,
        )

        for file_path in files:
            log_journal(
                "discard_file_to_batch_start",
                batch_name=batch_name,
                file_path=file_path,
                quiet=quiet,
            )
            discard_input = collected_discards.inputs_by_file.get(file_path)
            if (
                discard_input is None
                and file_path in collected_discards.files_with_text_patches
            ):
                continue

            if discard_input is None or not session.prepare_text_file(discard_input):
                session.flush()
                discarded_hunks = discard_file_to_batch(
                    batch_name,
                    file_path,
                    quiet=True,
                    advance=False,
                    auto_advance=auto_advance,
                )
                if session.record_single_file_discard(file_path, discarded_hunks):
                    blocked_hashes = read_text_file_line_set(blocklist_path)
                continue

            log_journal(
                "discard_file_to_batch_end",
                batch_name=batch_name,
                file_path=file_path,
            )

        session.flush()
    finally:
        session.close()
        patch_stack.close()

    if advance:
        finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)

    return DiscardFilesToBatchResult(
        discarded_hunks=session.discarded_hunks,
        discarded_files=session.discarded_files,
    )
