"""Semantic line-selection helpers for partial staging."""

from __future__ import annotations

from dataclasses import dataclass

from .merge import merge_batch
from .ownership import BatchOwnership, DeletionClaim
from ..core.line_selection import format_line_ids
from ..core.models import LineEntry, LineLevelChange
from ..exceptions import MergeError
from ..i18n import _


class SemanticSelectionError(Exception):
    """Raised when a selected hunk cannot be staged semantically."""


class SemanticSelectionAmbiguousError(SemanticSelectionError):
    """Raised when selected lines do not have an unambiguous semantic shape."""


class SemanticSelectionAtomicError(SemanticSelectionError):
    """Raised when selection splits an atomic replacement row."""


class SemanticSelectionUnsupportedError(SemanticSelectionError):
    """Raised when legacy raw line staging should handle the selection."""


@dataclass
class SemanticSelectionRow:
    """A semantic row derived from a live diff hunk."""

    old_display_ids: set[int]
    new_display_ids: set[int]
    deletion_claims: list[DeletionClaim]
    claimed_source_lines: set[int]
    is_atomic: bool


@dataclass
class TemporarySelectionRealization:
    """In-memory semantic realization for selected hunk lines."""

    ownership: BatchOwnership
    hunk_base_content: bytes
    source_content: bytes
    realized_content: bytes
    selected_rows: list[SemanticSelectionRow]
    pairing_mode: str


@dataclass
class SemanticSelectionAttempt:
    """Result of opportunistic semantic partial staging."""

    used_semantic_staging: bool
    realized_content: bytes | None = None
    reason: str | None = None
    realization: TemporarySelectionRealization | None = None


def _split_content_lines(content: bytes) -> list[bytes]:
    return content.splitlines(keepends=True)


def _line_content_from_snapshot(
    snapshot_lines: list[bytes],
    line_number: int | None,
    fallback: LineEntry,
) -> bytes:
    if line_number is not None and 1 <= line_number <= len(snapshot_lines):
        return snapshot_lines[line_number - 1]
    return fallback.text_bytes + b"\n"


def _append_deletion_claim(
    deletion_claims: list[DeletionClaim],
    *,
    anchor_line: int | None,
    line: LineEntry,
    line_content: bytes,
) -> DeletionClaim:
    if deletion_claims and deletion_claims[-1].anchor_line == anchor_line:
        deletion_claims[-1].content_lines.append(line_content)
        return deletion_claims[-1]

    claim = DeletionClaim(anchor_line=anchor_line, content_lines=[line_content])
    deletion_claims.append(claim)
    return claim


def _is_supported_replacement_run(run: list[LineEntry]) -> bool:
    seen_addition = False
    for line in run:
        if line.kind == "+":
            seen_addition = True
        elif line.kind == "-" and seen_addition:
            return False
    return True


def _ensure_unambiguous_replacement(deletions: list[LineEntry], additions: list[LineEntry]) -> None:
    old_texts = [line.text_bytes for line in deletions]
    new_texts = [line.text_bytes for line in additions]
    if len(set(old_texts)) != len(old_texts) or len(set(new_texts)) != len(new_texts):
        raise SemanticSelectionAmbiguousError(
            _("Selected lines do not form an unambiguous replacement.")
        )


def _replacement_pair_key(line: LineEntry) -> bytes:
    return line.text_bytes.strip().lower()


def _replacement_rows_look_reordered(deletions: list[LineEntry], additions: list[LineEntry]) -> bool:
    """Return True for same-content replacement blocks whose order changed.

    Simple same-cardinality replacement blocks pair positionally by default.
    Normalized keys are only a suspicion check: when both sides contain the same
    unique keys but in a different order, this looks like a reorder rather than
    row-wise edits, so semantic staging should decline and let legacy staging
    handle the selection.
    """
    old_keys = [_replacement_pair_key(line) for line in deletions]
    new_keys = [_replacement_pair_key(line) for line in additions]
    if any(not key for key in old_keys + new_keys):
        return False
    if len(set(old_keys)) != len(old_keys) or len(set(new_keys)) != len(new_keys):
        return False
    return set(old_keys) == set(new_keys) and old_keys != new_keys


def _build_claimed_ranges(claimed_source_lines: set[int]) -> list[str]:
    if not claimed_source_lines:
        return []
    return [format_line_ids(sorted(claimed_source_lines))]


def _has_synthetic_gap(line_changes: LineLevelChange) -> bool:
    return any(
        line.kind == " "
        and line.old_line_number is None
        and line.new_line_number is None
        for line in line_changes.lines
    )


def _build_temporary_ownership_for_selected_hunk(
    *,
    line_changes: LineLevelChange,
    selected_display_ids: set[int],
    selected_hunk_base_content: bytes,
    selected_hunk_source_content: bytes,
    current_index_content: bytes,
) -> TemporarySelectionRealization:
    """Build and merge temporary ownership for semantic partial staging.

    The temporary source is the selected semantic result over the selected
    hunk's base/index snapshot. That gives the batch merge engine enough
    unchanged context to apply a selected replacement row into the current index
    without treating the full worktree rewrite as the source.
    """
    if not selected_display_ids:
        raise SemanticSelectionAmbiguousError(_("No lines selected."))

    known_ids = {line.id for line in line_changes.lines if line.id is not None}
    unknown_ids = selected_display_ids - known_ids
    if unknown_ids:
        first_unknown = min(unknown_ids)
        raise SemanticSelectionAmbiguousError(
            _("Line ID {id} not found in selected hunk.").format(id=first_unknown)
        )

    if _has_synthetic_gap(line_changes):
        raise SemanticSelectionUnsupportedError(
            _("Selected lines contain omitted file-review context.")
        )

    hunk_base_lines = _split_content_lines(selected_hunk_base_content)
    hunk_source_lines = _split_content_lines(selected_hunk_source_content)
    source_lines: list[bytes] = []
    claimed_source_lines: set[int] = set()
    deletion_claims: list[DeletionClaim] = []
    selected_rows: list[SemanticSelectionRow] = []
    pairing_modes: set[str] = set()

    old_pointer = max(line_changes.header.old_start - 1, 0)
    for index in range(0, min(old_pointer, len(hunk_base_lines))):
        source_lines.append(hunk_base_lines[index])

    def previous_source_line() -> int | None:
        return len(source_lines) if source_lines else None

    def copy_remaining_baseline_until(new_pointer: int) -> None:
        nonlocal old_pointer
        while old_pointer < min(new_pointer, len(hunk_base_lines)):
            source_lines.append(hunk_base_lines[old_pointer])
            old_pointer += 1

    def flush_run(run: list[LineEntry]) -> None:
        nonlocal old_pointer
        if not run:
            return

        deletions = [line for line in run if line.kind == "-"]
        additions = [line for line in run if line.kind == "+"]

        if deletions and additions:
            if not _is_supported_replacement_run(run):
                raise SemanticSelectionAmbiguousError(
                    _("Selected lines do not form an unambiguous replacement.")
                )

            if len(deletions) != len(additions):
                selected_deletions = {
                    line.id for line in deletions
                    if line.id is not None and line.id in selected_display_ids
                }
                selected_additions = {
                    line.id for line in additions
                    if line.id is not None and line.id in selected_display_ids
                }
                if selected_deletions and selected_additions:
                    raise SemanticSelectionAmbiguousError(
                        _("Selected lines do not form an unambiguous replacement.")
                    )
                raise SemanticSelectionUnsupportedError(
                    _("Selected lines do not form an unambiguous replacement.")
                )

            _ensure_unambiguous_replacement(deletions, additions)
            if _replacement_rows_look_reordered(deletions, additions):
                raise SemanticSelectionUnsupportedError(
                    _("Selected lines do not form an unambiguous replacement.")
                )
            pairing_modes.add("replacement")

            for old_line, new_line in zip(deletions, additions):
                old_selected = old_line.id in selected_display_ids
                new_selected = new_line.id in selected_display_ids

                if old_selected != new_selected:
                    raise SemanticSelectionAtomicError(
                        _("Select the whole replacement row or broaden the selection.")
                    )

                if old_selected and new_selected:
                    anchor = previous_source_line()
                    claim = _append_deletion_claim(
                        deletion_claims,
                        anchor_line=anchor,
                        line=old_line,
                        line_content=_line_content_from_snapshot(
                            hunk_base_lines,
                            old_line.old_line_number,
                            old_line,
                        ),
                    )
                    source_lines.append(
                        _line_content_from_snapshot(
                            hunk_source_lines,
                            new_line.new_line_number,
                            new_line,
                        )
                    )
                    source_line = len(source_lines)
                    claimed_source_lines.add(source_line)
                    selected_rows.append(
                        SemanticSelectionRow(
                            old_display_ids={old_line.id} if old_line.id is not None else set(),
                            new_display_ids={new_line.id} if new_line.id is not None else set(),
                            deletion_claims=[claim],
                            claimed_source_lines={source_line},
                            is_atomic=True,
                        )
                    )
                else:
                    source_lines.append(
                        _line_content_from_snapshot(
                            hunk_base_lines,
                            old_line.old_line_number,
                            old_line,
                        )
                    )
                old_pointer += 1
            return

        if additions:
            pairing_modes.add("addition")
            for line in additions:
                if line.id in selected_display_ids:
                    anchor = previous_source_line()
                    if anchor is not None:
                        claimed_source_lines.add(anchor)
                    source_lines.append(
                        _line_content_from_snapshot(
                            hunk_source_lines,
                            line.new_line_number,
                            line,
                        )
                    )
                    source_line = len(source_lines)
                    claimed_source_lines.add(source_line)
                    selected_rows.append(
                        SemanticSelectionRow(
                            old_display_ids=set(),
                            new_display_ids={line.id} if line.id is not None else set(),
                            deletion_claims=[],
                            claimed_source_lines={source_line},
                            is_atomic=False,
                        )
                    )
            return

        if deletions:
            pairing_modes.add("deletion")
            for line in deletions:
                if line.id in selected_display_ids:
                    anchor = previous_source_line()
                    claim = _append_deletion_claim(
                        deletion_claims,
                        anchor_line=anchor,
                        line=line,
                        line_content=_line_content_from_snapshot(
                            hunk_base_lines,
                            line.old_line_number,
                            line,
                        ),
                    )
                    selected_rows.append(
                        SemanticSelectionRow(
                            old_display_ids={line.id} if line.id is not None else set(),
                            new_display_ids=set(),
                            deletion_claims=[claim],
                            claimed_source_lines=set(),
                            is_atomic=False,
                        )
                    )
                else:
                    source_lines.append(
                        _line_content_from_snapshot(
                            hunk_base_lines,
                            line.old_line_number,
                            line,
                        )
                    )
                old_pointer += 1

    run: list[LineEntry] = []
    for line in line_changes.lines:
        if line.kind == " ":
            is_gap_line = (
                line.old_line_number is None
                and line.new_line_number is None
            )
            flush_run(run)
            run = []
            if is_gap_line:
                continue
            copy_remaining_baseline_until(max(old_pointer, (line.old_line_number or 1) - 1))
            if old_pointer < len(hunk_base_lines):
                source_lines.append(hunk_base_lines[old_pointer])
                old_pointer += 1
            else:
                source_lines.append(
                    _line_content_from_snapshot(
                        hunk_source_lines,
                        line.new_line_number,
                        line,
                    )
                )
        else:
            run.append(line)
    flush_run(run)

    while old_pointer < len(hunk_base_lines):
        source_lines.append(hunk_base_lines[old_pointer])
        old_pointer += 1

    ownership = BatchOwnership(
        claimed_lines=_build_claimed_ranges(claimed_source_lines),
        deletions=deletion_claims,
    )
    if ownership.is_empty():
        raise SemanticSelectionAmbiguousError(_("No selected semantic changes to stage."))

    source_content = b"".join(source_lines)

    try:
        realized_content = merge_batch(source_content, ownership, current_index_content)
    except MergeError as e:
        raise SemanticSelectionAmbiguousError(
            _("Selected semantic change cannot be merged into the current index state.")
        ) from e

    pairing_mode = "+".join(sorted(pairing_modes)) if pairing_modes else "unknown"
    return TemporarySelectionRealization(
        ownership=ownership,
        hunk_base_content=selected_hunk_base_content,
        source_content=source_content,
        realized_content=realized_content,
        selected_rows=selected_rows,
        pairing_mode=pairing_mode,
    )


def try_build_semantic_selection_for_selected_hunk(
    *,
    line_changes: LineLevelChange,
    selected_display_ids: set[int],
    selected_hunk_base_content: bytes,
    selected_hunk_source_content: bytes,
    current_index_content: bytes,
) -> SemanticSelectionAttempt:
    """Try semantic partial staging and return a best-effort attempt result."""
    try:
        realization = _build_temporary_ownership_for_selected_hunk(
            line_changes=line_changes,
            selected_display_ids=selected_display_ids,
            selected_hunk_base_content=selected_hunk_base_content,
            selected_hunk_source_content=selected_hunk_source_content,
            current_index_content=current_index_content,
        )
    except SemanticSelectionAtomicError:
        return SemanticSelectionAttempt(False, reason="atomic_row_split")
    except SemanticSelectionAmbiguousError:
        return SemanticSelectionAttempt(False, reason="ambiguous_replacement")
    except SemanticSelectionUnsupportedError:
        return SemanticSelectionAttempt(False, reason="unsupported_shape")

    return SemanticSelectionAttempt(
        True,
        realized_content=realization.realized_content,
        realization=realization,
    )
