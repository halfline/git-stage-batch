"""Discover exact staged change units for fixup planning."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace

from ..core.buffer import LineBuffer
from ..core.diff_parser import acquire_unified_diff
from ..core.hunk_headers import line_is_hunk_header, parse_hunk_header_line
from ..core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    HunkHeader,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ..core.patch_headers import (
    line_is_new_file_header,
    line_is_old_file_header,
    new_file_path_from_header,
    old_file_path_from_header,
)
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import stream_git_diff
from .models import FixupUnitKind, StagedFixupUnit


def _unit_id(
    kind: FixupUnitKind,
    path: str,
    payload_chunks: Iterable[bytes],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"git-stage-batch-fixup-unit-v1\0")
    digest.update(kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(path.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    for chunk in payload_chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _hunk_metadata(
    patch_buffer: LineBuffer,
) -> tuple[HunkHeader, bool, bool]:
    old_path = ""
    new_path = ""
    for patch_line in patch_buffer:
        line = patch_line.rstrip(b"\n")
        if line_is_old_file_header(line):
            old_path = old_file_path_from_header(line)
            continue
        if line_is_new_file_header(line):
            new_path = new_file_path_from_header(line)
            continue
        if line_is_hunk_header(line):
            return (
                parse_hunk_header_line(line),
                old_path == "/dev/null",
                new_path == "/dev/null",
            )
    raise CommandError(_("Staged text patch has no hunk header."))


def _insertion_anchors(header: HunkHeader) -> tuple[int, ...]:
    before = header.old_start
    after = header.old_start + 1
    if before <= 0:
        return (after,) if after > 0 else ()
    if after == before:
        return (before,)
    return before, after


def _text_unit(
    item: SingleHunkPatch,
    patch_buffer: LineBuffer,
) -> StagedFixupUnit:
    header, is_whole_file_addition, is_whole_file_deletion = _hunk_metadata(
        patch_buffer
    )
    if is_whole_file_addition:
        kind: FixupUnitKind = "text-file-addition"
        unsupported_reason = "whole-file-addition"
    elif is_whole_file_deletion:
        kind = "text-file-deletion"
        unsupported_reason = "whole-file-deletion"
    elif header.old_len and header.new_len:
        kind = "text-replacement"
        unsupported_reason = None
    elif header.new_len:
        kind = "text-addition"
        unsupported_reason = None
    else:
        kind = "text-deletion"
        unsupported_reason = None

    anchor_lines = _insertion_anchors(header) if kind == "text-addition" else ()
    path = item.path()
    return StagedFixupUnit(
        unit_id=_unit_id(kind, path, patch_buffer.byte_chunks()),
        path=path,
        kind=kind,
        patch_buffer=patch_buffer,
        old_start=header.old_start,
        old_len=header.old_len,
        new_start=header.new_start,
        new_len=header.new_len,
        anchor_line_numbers=anchor_lines,
        unsupported_reason=unsupported_reason,
    )


def _atomic_unit(
    *,
    kind: FixupUnitKind,
    path: str,
    payload: str,
    reason: str,
) -> StagedFixupUnit:
    payload_bytes = payload.encode("utf-8", errors="surrogateescape")
    return StagedFixupUnit(
        unit_id=_unit_id(kind, path, (payload_bytes,)),
        path=path,
        kind=kind,
        patch_buffer=None,
        unsupported_reason=reason,
    )


@contextmanager
def acquire_staged_fixup_units() -> Iterator[tuple[StagedFixupUnit, ...]]:
    """Acquire deterministic units and their bounded patch buffers."""
    units: list[StagedFixupUnit] = []
    owned_buffers: list[LineBuffer] = []
    renamed_paths: set[str] = set()
    diff_lines = stream_git_diff(
        base="HEAD",
        cached=True,
        context_lines=0,
        full_index=True,
        find_renames=True,
        ignore_submodules="none",
        submodule_format="short",
    )
    try:
        with acquire_unified_diff(diff_lines) as items:
            for item in items:
                if isinstance(item, SingleHunkPatch):
                    if not isinstance(item.lines, LineBuffer):
                        raise TypeError("parsed text hunk must use LineBuffer storage")
                    patch_buffer = item.lines.clone()
                    owned_buffers.append(patch_buffer)
                    units.append(_text_unit(item, patch_buffer))
                elif isinstance(item, RenameChange):
                    renamed_paths.update((item.old_path, item.new_path))
                    units.append(
                        _atomic_unit(
                            kind="rename",
                            path=item.new_path,
                            payload=f"{item.old_path}\0{item.new_path}",
                            reason="rename",
                        )
                    )
                elif isinstance(item, BinaryFileChange):
                    units.append(
                        _atomic_unit(
                            kind="binary",
                            path=item.path(),
                            payload=(
                                f"{item.old_path}\0{item.new_path}\0"
                                f"{item.change_type}\0"
                                f"{item.content_fingerprint or ''}"
                            ),
                            reason="binary-change",
                        )
                    )
                elif isinstance(item, FileModeChange):
                    units.append(
                        _atomic_unit(
                            kind="mode",
                            path=item.path(),
                            payload=(
                                f"{item.index_path or ''}\0{item.old_mode}\0"
                                f"{item.new_mode}"
                            ),
                            reason="file-mode-change",
                        )
                    )
                elif isinstance(item, GitlinkChange):
                    units.append(
                        _atomic_unit(
                            kind="gitlink",
                            path=item.path(),
                            payload=(
                                f"{item.old_path}\0{item.new_path}\0"
                                f"{item.old_oid or ''}\0{item.new_oid or ''}"
                            ),
                            reason="gitlink-change",
                        )
                    )
                elif isinstance(item, TextFileDeletionChange):
                    units.append(
                        _atomic_unit(
                            kind="text-file-deletion",
                            path=item.path(),
                            payload=item.old_path,
                            reason="whole-file-deletion",
                        )
                    )

        if renamed_paths:
            units = [
                replace(unit, unsupported_reason="rename-with-content")
                if unit.patch_buffer is not None and unit.path in renamed_paths
                else unit
                for unit in units
            ]
        yield tuple(units)
    finally:
        for buffer in owned_buffers:
            buffer.close()
