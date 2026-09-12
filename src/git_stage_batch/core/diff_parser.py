"""Parse unified diff format into structured models."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Iterator
from types import TracebackType

from . import binary_diff as _binary_diff
from . import diff_headers as _diff_headers
from . import empty_file_diff as _empty_file_diff
from . import gitlink_diff as _gitlink_diff
from . import hunk_headers as _hunk_headers
from . import line_change_body as _line_change_body
from . import patch_headers as _patch_headers
from .buffer import LineBuffer
from .diff_stream import DiffLineCursor, hunk_line_chunks
from .diff_file_metadata import FileDiffMetadata, read_file_diff_metadata
from .models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    LineLevelChange,
    HunkHeader,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ..exceptions import CommandError
from ..i18n import _
from ..git_paths import encode_path, quote_path_token


# Type for annotator hooks that enrich LineLevelChange with additional metadata
LineLevelChangeAnnotator = Callable[[str, LineLevelChange], LineLevelChange]
UnifiedDiffItem = (
    SingleHunkPatch
    | BinaryFileChange
    | FileModeChange
    | GitlinkChange
    | RenameChange
    | TextFileDeletionChange
)


def patch_is_file_deletion(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a deleted file path."""
    return _patch_headers.patch_targets_file_deletion(patch_lines)


def patch_is_new_file(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a newly added file path."""
    return _patch_headers.patch_targets_new_file(patch_lines)


def patch_is_empty_file_change(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines describe a synthetic empty-file change."""
    return any(
        line.rstrip(b"\n") == _empty_file_diff.SYNTHETIC_EMPTY_HUNK_HEADER
        for line in patch_lines
    )


def patch_requires_unidiff_zero(patch_lines: Iterable[bytes]) -> bool:
    """Return whether any unified-diff hunk has no unchanged context.

    Git's ``--unidiff-zero`` switch applies to the complete input patch and
    also relaxes beginning/end placement for hunks that do have context.  Keep
    that relaxation scoped to inputs that actually need it.
    """
    in_hunk = False
    hunk_has_context = False
    for line in patch_lines:
        if _hunk_headers.line_is_hunk_header(line):
            if in_hunk and not hunk_has_context:
                return True
            in_hunk = True
            hunk_has_context = False
            continue
        if in_hunk and (line.startswith(b" ") or line in (b"\n", b"\r\n")):
            hunk_has_context = True

    return in_hunk and not hunk_has_context


class _UnifiedDiffParserBuildContext:
    """Own parser-created hunk buffers for a scoped unified diff parse."""

    def __init__(
        self,
        lines: Iterable[bytes],
        *,
        allow_file_type_changes: bool = False,
    ) -> None:
        self._lines = lines
        self._allow_file_type_changes = allow_file_type_changes
        self._buffers: list[LineBuffer] = []
        self._parser: Generator[UnifiedDiffItem, None, None] | None = None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._parser is not None:
            self._parser.close()
            self._parser = None

        for buffer in self._buffers:
            buffer.close()
        self._buffers.clear()

    def __enter__(self) -> Iterator[UnifiedDiffItem]:
        if self._closed:
            raise ValueError("parser context is closed")
        if self._parser is not None:
            raise RuntimeError("parser context can only be entered once")
        self._parser = self._iter_owned()
        return self._parser

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _own_buffer(self, buffer: LineBuffer) -> LineBuffer:
        self._buffers.append(buffer)
        return buffer

    def _release_buffer(self, buffer: LineBuffer) -> None:
        try:
            self._buffers.remove(buffer)
        except ValueError:
            return
        buffer.close()

    def _release_item(self, item: UnifiedDiffItem | None) -> None:
        if isinstance(item, SingleHunkPatch) and isinstance(item.lines, LineBuffer):
            self._release_buffer(item.lines)

    def _iter_owned(self) -> Generator[UnifiedDiffItem, None, None]:
        current_item: UnifiedDiffItem | None = None
        parser = self._parse()

        try:
            while True:
                self._release_item(current_item)
                current_item = None
                current_item = next(parser)
                yield current_item
        except StopIteration:
            return
        finally:
            self._release_item(current_item)
            parser.close()

    def _build_single_hunk_patch(
        self,
        *,
        old_path: str,
        new_path: str,
        lines: Iterable[bytes],
    ) -> SingleHunkPatch:
        return SingleHunkPatch(
            old_path=old_path,
            new_path=new_path,
            lines=self._own_buffer(LineBuffer.from_chunks(lines)),
        )

    def _parse(self) -> Generator[UnifiedDiffItem, None, None]:
        cursor = DiffLineCursor(self._lines)
        deleted_modes_by_path: dict[str, str] = {}
        try:
            while True:
                line = cursor.next_line()
                if line is None:
                    return
                line = line.rstrip(b"\n")
                if not _diff_headers.line_is_diff_git_header(line):
                    continue
                metadata = read_file_diff_metadata(
                    line,
                    cursor,
                    deleted_modes_by_path,
                    allow_file_type_changes=self._allow_file_type_changes,
                )
                yield from self._file_changes(metadata, cursor)
        finally:
            cursor.close()

    def _changes_without_hunks(
        self, metadata: FileDiffMetadata
    ) -> Iterator[UnifiedDiffItem]:
        """Emit atomic metadata changes or a synthetic empty-file hunk."""
        if metadata.is_rename:
            yield RenameChange(old_path=metadata.old_path, new_path=metadata.new_path)

        if metadata.is_gitlink:
            yield GitlinkChange(
                old_path=_gitlink_diff.gitlink_old_path(
                    metadata.old_path,
                    metadata.index_old_oid,
                ),
                new_path=_gitlink_diff.gitlink_new_path(
                    metadata.new_path,
                    metadata.index_new_oid,
                ),
                old_oid=_gitlink_diff.non_null_git_oid(metadata.index_old_oid),
                new_oid=_gitlink_diff.non_null_git_oid(metadata.index_new_oid),
                change_type=_gitlink_diff.gitlink_change_type(
                    metadata.metadata_lines,
                    metadata.index_old_oid,
                    metadata.index_new_oid,
                ),
            )
            return

        if _binary_diff.metadata_indicates_binary_file(metadata.metadata_lines):
            yield BinaryFileChange(
                old_path=metadata.old_path,
                new_path=metadata.new_path,
                change_type=_binary_diff.binary_change_type(metadata.metadata_lines),
            )
            if metadata.mode_change is not None:
                yield metadata.mode_change
            if metadata.type_change is not None:
                yield metadata.type_change
            return

        if metadata.is_rename:
            if metadata.mode_change is not None:
                yield metadata.mode_change
            if metadata.type_change is not None:
                yield metadata.type_change
            return

        if metadata.is_deleted_file:
            yield TextFileDeletionChange(old_path=metadata.old_path)
            return

        if _empty_file_diff.metadata_indicates_new_empty_file(metadata.metadata_lines):
            yield self._build_single_hunk_patch(
                old_path="/dev/null",
                new_path=metadata.new_path,
                lines=_empty_file_diff.synthetic_empty_file_patch_lines(
                    b"--- /dev/null",
                    b"+++ " + quote_path_token(b"b/" + encode_path(metadata.new_path)),
                ),
            )
        if metadata.mode_change is not None:
            yield metadata.mode_change
        if metadata.type_change is not None:
            yield metadata.type_change
        # Skip other files without hunks (mode-only, rename-only, etc.)
        return

    def _file_changes(
        self, metadata: FileDiffMetadata, cursor: DiffLineCursor
    ) -> Iterator[UnifiedDiffItem]:
        """Resolve patch paths and dispatch atomic items or text hunks."""
        if metadata.old_file_line is None:
            yield from self._changes_without_hunks(metadata)
            return
        old_file_line = metadata.old_file_line
        old_path, new_path = metadata.old_path, metadata.new_path
        # Get +++ line
        plus_line = cursor.next_line()
        if plus_line is None:
            raise CommandError(_("Malformed unified diff: missing +++ file header."))
        plus_line_stripped = plus_line.rstrip(b"\n")
        if not _patch_headers.line_is_new_file_header(plus_line_stripped):
            raise CommandError(_("Malformed unified diff: expected +++ file header."))
        new_file_line = plus_line_stripped

        patch_old_path = _patch_headers.old_file_path_from_header(old_file_line)
        patch_new_path = _patch_headers.new_file_path_from_header(new_file_line)
        if _patch_headers.path_names_repository_file(patch_old_path):
            old_path = patch_old_path
        if _patch_headers.path_names_repository_file(patch_new_path):
            new_path = patch_new_path

        if metadata.is_rename:
            yield RenameChange(old_path=old_path, new_path=new_path)

        if metadata.is_gitlink:
            hunk_old_oid, hunk_new_oid = _gitlink_diff.consume_gitlink_hunks(
                cursor.next_line,
                cursor.peek_line,
            )
            old_oid = hunk_old_oid or _gitlink_diff.non_null_git_oid(
                metadata.index_old_oid
            )
            new_oid = hunk_new_oid or _gitlink_diff.non_null_git_oid(
                metadata.index_new_oid
            )
            if old_oid is not None and old_oid == new_oid:
                return
            yield GitlinkChange(
                old_path=_gitlink_diff.gitlink_old_path(
                    old_path,
                    old_oid or metadata.index_old_oid,
                ),
                new_path=_gitlink_diff.gitlink_new_path(
                    new_path,
                    new_oid or metadata.index_new_oid,
                ),
                old_oid=old_oid,
                new_oid=new_oid,
                change_type=_gitlink_diff.gitlink_change_type(
                    metadata.metadata_lines,
                    old_oid or metadata.index_old_oid,
                    new_oid or metadata.index_new_oid,
                ),
            )
            return

        has_hunks = yield from self._file_hunks(
            metadata, cursor, old_path, new_path, old_file_line, new_file_line
        )
        if not has_hunks:
            if _empty_file_diff.metadata_indicates_new_empty_file(
                metadata.metadata_lines
            ):
                yield self._build_single_hunk_patch(
                    old_path=old_path,
                    new_path=new_path,
                    lines=_empty_file_diff.synthetic_empty_file_patch_lines(
                        old_file_line,
                        new_file_line,
                    ),
                )
            elif metadata.is_deleted_file:
                yield TextFileDeletionChange(old_path=old_path)
        if metadata.mode_change is not None:
            yield metadata.mode_change
        if metadata.type_change is not None:
            yield metadata.type_change

    def _file_hunks(
        self,
        metadata: FileDiffMetadata,
        cursor: DiffLineCursor,
        old_path: str,
        new_path: str,
        old_file_line: bytes,
        new_file_line: bytes,
    ) -> Generator[UnifiedDiffItem, None, bool]:
        """Emit one scoped hunk buffer at a time, including gitlink fallback."""
        has_hunks = False
        while True:
            # Check if next line is a hunk header
            hunk_header_line = cursor.peek_line()
            if hunk_header_line is None:
                break
            hunk_header_stripped = hunk_header_line.rstrip(b"\n")
            if not _hunk_headers.line_is_hunk_header(hunk_header_stripped):
                # No more hunks for this file
                break

            has_hunks = True

            # Consume the hunk header
            cursor.next_line()

            patch_lines: Iterable[bytes] = hunk_line_chunks(
                cursor,
                old_file_line,
                new_file_line,
                hunk_header_stripped,
            )
            patch = self._build_single_hunk_patch(
                old_path=old_path,
                new_path=new_path,
                lines=patch_lines,
            )
            subproject_oids = (
                _gitlink_diff.gitlink_oids_from_subproject_commit_patch(patch.lines)
                if not metadata.metadata_lines
                else None
            )
            if subproject_oids is not None:
                self._release_item(patch)
                old_oid, new_oid = subproject_oids
                if old_oid is not None and old_oid == new_oid:
                    continue
                yield GitlinkChange(
                    old_path=_gitlink_diff.gitlink_old_path(
                        old_path,
                        old_oid,
                    ),
                    new_path=_gitlink_diff.gitlink_new_path(
                        new_path,
                        new_oid,
                    ),
                    old_oid=old_oid,
                    new_oid=new_oid,
                    change_type=_gitlink_diff.gitlink_change_type(
                        metadata.metadata_lines,
                        old_oid,
                        new_oid,
                    ),
                )
                continue

            yield patch
        return has_hunks


def acquire_unified_diff(
    lines: Iterable[bytes],
    *,
    allow_file_type_changes: bool = False,
) -> _UnifiedDiffParserBuildContext:
    """Acquire a scoped unified diff parser with parser-owned hunk buffers."""
    return _UnifiedDiffParserBuildContext(
        lines,
        allow_file_type_changes=allow_file_type_changes,
    )


def build_line_changes_from_patch_lines(
    patch_lines: Iterable[bytes],
    *,
    annotator: LineLevelChangeAnnotator | None = None,
) -> LineLevelChange:
    """Parse single-hunk patch lines into a LineLevelChange structure.

    Args:
        patch_lines: Unified diff patch lines for a single hunk
        annotator: Optional function to enrich LineLevelChange with provenance metadata
                   (e.g., batch source alignment, 3-way merge base). If None, source_line
                   fields remain None (no provenance).

    Returns:
        LineLevelChange object with parsed line entries and IDs
    """
    path_value = ""
    old_path_value = ""
    new_path_value = ""
    hunk_header: HunkHeader | None = None
    body_builder = _line_change_body.LineChangeBodyBuilder()

    # Preserve line endings so a parsed hunk can be emitted unchanged.
    for line_with_ending in patch_lines:
        # Strip only \n for comparison (preserve \r in content)
        line = line_with_ending.rstrip(b"\n")

        if hunk_header is not None:
            body_builder.append_patch_line(line)
        elif _patch_headers.line_is_old_file_header(line):
            old_path_value = _patch_headers.old_file_path_from_header(line)
        elif _patch_headers.line_is_new_file_header(line):
            new_path_value = _patch_headers.new_file_path_from_header(line)
        elif _hunk_headers.line_is_hunk_header(line):
            hunk_header = _hunk_headers.parse_hunk_header_line(line)
            body_builder.reset_for_hunk_header(hunk_header)

    path_value = _patch_headers.line_change_path(old_path_value, new_path_value)

    if hunk_header is None:
        raise CommandError(_("Failed to parse hunk header."))

    line_changes = LineLevelChange(
        path=path_value,
        header=hunk_header,
        lines=body_builder.line_entries,
    )

    # Apply annotator hook if provided
    if annotator is not None:
        line_changes = annotator(path_value, line_changes)

    return line_changes
