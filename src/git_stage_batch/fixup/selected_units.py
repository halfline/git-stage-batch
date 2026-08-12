"""Adapt selected working-tree lines into exact fixup units."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from ..core.buffer import buffer_ends_with_lf
from ..core.line_selection import LineRanges
from ..core.models import LineEntry, LineLevelChange
from ..exceptions import CommandError
from ..i18n import _
from ..staging.content_buffers import build_target_index_buffer_from_lines
from ..utils.git_index import (
    git_read_tree,
    git_update_index,
    git_write_tree,
    temp_git_index,
)
from ..utils.git_object_io import create_git_blob, list_git_tree_blobs
from ..utils.repository_buffers import read_git_object_buffer_or_none
from .commutation import load_tree_diff_as_buffer
from .models import FixupUnit, FixupUnitKind
from .unit_ids import fixup_unit_id


_REGULAR_FILE_MODES = {"100644", "100755"}


def _line_is_selected(
    line: LineEntry,
    selected_ids: Collection[int] | None,
) -> bool:
    return (
        line.id is not None
        and line.kind in {"+", "-"}
        and (selected_ids is None or line.id in selected_ids)
    )


def _selected_shape(
    line_changes: LineLevelChange,
    selected_ids: Collection[int] | None,
) -> tuple[FixupUnitKind, tuple[tuple[int, int], ...], int, int, int | None]:
    selected_count = 0
    selected_addition_count = 0
    selected_deletion_count = 0
    first_new_line: int | None = None
    old_ranges: list[tuple[int, int]] = []
    current_old_start: int | None = None
    current_old_end: int | None = None

    for line in line_changes.lines:
        if not _line_is_selected(line, selected_ids):
            continue
        selected_count += 1
        if line.kind == "-":
            selected_deletion_count += 1
            if line.old_line_number is not None:
                if (
                    current_old_start is not None
                    and current_old_end is not None
                    and line.old_line_number == current_old_end + 1
                ):
                    current_old_end = line.old_line_number
                else:
                    if current_old_start is not None and current_old_end is not None:
                        old_ranges.append((current_old_start, current_old_end))
                    current_old_start = line.old_line_number
                    current_old_end = line.old_line_number
        else:
            selected_addition_count += 1
            if first_new_line is None and line.new_line_number is not None:
                first_new_line = line.new_line_number

    if selected_ids is not None and selected_count != len(selected_ids):
        raise CommandError(
            _("Selection contains line IDs outside the current hunk.")
        )
    if selected_count == 0:
        raise CommandError(_("No changed lines were selected."))

    if current_old_start is not None and current_old_end is not None:
        old_ranges.append((current_old_start, current_old_end))

    if selected_addition_count and selected_deletion_count:
        kind: FixupUnitKind = "text-replacement"
    elif selected_addition_count:
        kind = "text-addition"
    else:
        kind = "text-deletion"

    return (
        kind,
        tuple(old_ranges),
        selected_deletion_count,
        selected_addition_count,
        first_new_line,
    )


def _selected_addition_anchors(
    line_changes: LineLevelChange,
    selected_ids: Collection[int] | None,
    *,
    old_line_count: int,
) -> tuple[int, ...]:
    insertion_indexes: list[int] = []
    additions_seen = 0
    deletions_seen = 0
    previous_was_selected_addition = False

    for line in line_changes.lines:
        selected_addition = (
            line.kind == "+" and _line_is_selected(line, selected_ids)
        )
        if (
            selected_addition
            and not previous_was_selected_addition
            and line.new_line_number is not None
        ):
            insertion_indexes.append(
                max(
                    0,
                    min(
                        line.new_line_number - 1
                        + deletions_seen
                        - additions_seen,
                        old_line_count,
                    ),
                )
            )

        if line.kind == "+":
            additions_seen += 1
        elif line.kind == "-":
            deletions_seen += 1
        previous_was_selected_addition = selected_addition

    def anchor_lines() -> Iterator[int]:
        for insertion_index in insertion_indexes:
            if insertion_index > 0:
                yield insertion_index
            if insertion_index < old_line_count:
                yield insertion_index + 1

    return tuple(LineRanges.from_lines(anchor_lines()))


def _unsupported_selected_unit(
    *,
    kind: FixupUnitKind,
    line_changes: LineLevelChange,
    reason: str,
) -> FixupUnit:
    payload = (
        f"{line_changes.header.old_start}\0{line_changes.header.old_len}\0"
        f"{line_changes.header.new_start}\0{line_changes.header.new_len}"
    ).encode("ascii")
    return FixupUnit(
        unit_id=fixup_unit_id(kind, line_changes.path, (payload,)),
        path=line_changes.path,
        kind=kind,
        patch_buffer=None,
        unsupported_reason=reason,
    )


@contextmanager
def acquire_selected_fixup_unit(
    line_changes: LineLevelChange,
    selected_ids: Collection[int] | None,
    *,
    source_tree: str,
) -> Iterator[FixupUnit]:
    """Materialize an exact selected-line patch against a frozen source tree."""
    (
        kind,
        old_ranges,
        selected_deletion_count,
        selected_addition_count,
        first_new_line,
    ) = _selected_shape(line_changes, selected_ids)

    source_entry = list_git_tree_blobs(
        source_tree,
        (line_changes.path,),
    ).get(line_changes.path)
    if source_entry is None:
        yield _unsupported_selected_unit(
            kind="text-file-addition",
            line_changes=line_changes,
            reason="whole-file-addition",
        )
        return
    if source_entry.mode not in _REGULAR_FILE_MODES:
        yield _unsupported_selected_unit(
            kind=kind,
            line_changes=line_changes,
            reason="non-regular-text-file",
        )
        return

    base_buffer = read_git_object_buffer_or_none(source_entry.blob_sha)
    if base_buffer is None:
        raise CommandError(_("Could not read the selected file from the index."))

    index_tree = git_write_tree()
    with base_buffer:
        anchor_lines = (
            _selected_addition_anchors(
                line_changes,
                selected_ids,
                old_line_count=len(base_buffer),
            )
            if kind == "text-addition"
            else ()
        )
        lineage_ranges = (
            old_ranges
            if old_ranges
            else tuple((line, line) for line in anchor_lines)
        )
        try:
            target_buffer = build_target_index_buffer_from_lines(
                line_changes,
                selected_ids,
                base_buffer,
                base_has_trailing_newline=buffer_ends_with_lf(base_buffer),
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        with target_buffer:
            target_blob = create_git_blob(target_buffer.byte_chunks())

    with temp_git_index() as env:
        git_read_tree(index_tree, env=env)
        git_update_index(
            file_path=line_changes.path,
            mode=source_entry.mode,
            blob_sha=target_blob,
            env=env,
        )
        target_tree = git_write_tree(env=env)

    with load_tree_diff_as_buffer(index_tree, target_tree) as patch_buffer:
        if patch_buffer.byte_count == 0:
            raise CommandError(_("The selected lines do not change the index."))

        old_start = (
            old_ranges[0][0]
            if old_ranges
            else (anchor_lines[0] if anchor_lines else line_changes.header.old_start)
        )
        yield FixupUnit(
            unit_id=fixup_unit_id(
                kind,
                line_changes.path,
                patch_buffer.byte_chunks(),
            ),
            path=line_changes.path,
            kind=kind,
            patch_buffer=patch_buffer,
            old_start=old_start,
            old_len=selected_deletion_count,
            new_start=first_new_line or line_changes.header.new_start,
            new_len=selected_addition_count,
            lineage_ranges=lineage_ranges,
            anchor_line_numbers=anchor_lines,
        )
