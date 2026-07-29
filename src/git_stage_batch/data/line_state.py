"""Line-level state management for line operations."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from typing import TypedDict, cast

from ..core.models import LineLevelChange, HunkHeader, LineEntry
from ..exceptions import CommandError
from ..i18n import _
from .line_id_files import read_line_ids_file
from ..utils.file_io import read_text_file_contents
from ..utils.paths import (
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
)


class SerializedHunkHeader(TypedDict):
    """Persisted unified-diff coordinates."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int


class _RequiredSerializedLineEntry(TypedDict):
    id: int | None
    kind: str
    old_lineno: int | None
    new_lineno: int | None


class SerializedLineEntry(_RequiredSerializedLineEntry, total=False):
    """Persisted line entry, including the legacy text representation."""

    text_bytes_b64: str
    text: str
    source_line: int | None
    has_trailing_newline: bool


class SerializedLineChanges(TypedDict):
    """Persisted selected-hunk line state."""

    path: str
    header: SerializedHunkHeader
    lines: list[SerializedLineEntry]


def convert_line_changes_to_serializable_dict(
    line_changes: LineLevelChange,
) -> SerializedLineChanges:
    """Convert LineLevelChange to a JSON-serializable dictionary."""
    return {
        "path": line_changes.path,
        "header": {
            "old_start": line_changes.header.old_start,
            "old_len": line_changes.header.old_len,
            "new_start": line_changes.header.new_start,
            "new_len": line_changes.header.new_len,
        },
        "lines": [
            {
                "id": line_entry.id,
                "kind": line_entry.kind,
                "old_lineno": line_entry.old_line_number,
                "new_lineno": line_entry.new_line_number,
                "text_bytes_b64": b64encode(line_entry.text_bytes).decode("ascii"),
                "source_line": line_entry.source_line,
                "has_trailing_newline": line_entry.has_trailing_newline,
            }
            for line_entry in line_changes.lines
        ],
    }


def _json_object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{description} must be a JSON object")
    return cast(dict[str, object], value)


def _json_array(value: object, description: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{description} must be a JSON array")
    return cast(list[object], value)


def _required_string(
    data: dict[str, object],
    field: str,
    description: str,
) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{description}.{field} must be a string")
    return value


def _required_int(
    data: dict[str, object],
    field: str,
    description: str,
) -> int:
    value = data.get(field)
    if type(value) is not int:
        raise ValueError(f"{description}.{field} must be an integer")
    return value


def _optional_int(
    data: dict[str, object],
    field: str,
    description: str,
) -> int | None:
    value = data.get(field)
    if value is not None and type(value) is not int:
        raise ValueError(f"{description}.{field} must be an integer or null")
    return value


def _line_text_bytes(
    line_data: dict[str, object],
    description: str,
) -> bytes:
    encoded = line_data.get("text_bytes_b64")
    if isinstance(encoded, str):
        return b64decode(encoded.encode("ascii"))
    legacy_text = line_data.get("text")
    if not isinstance(legacy_text, str):
        raise ValueError(
            f"{description} must contain text_bytes_b64 or legacy text"
        )
    return legacy_text.encode("utf-8", errors="surrogateescape")


def load_line_changes_from_state() -> LineLevelChange | None:
    """Load the selected hunk from saved state.

    Returns:
        LineLevelChange if state exists, None otherwise
    """
    if not get_selected_hunk_patch_file_path().exists() or not get_line_changes_json_file_path().exists():
        return None
    raw_data: object = json.loads(
        read_text_file_contents(get_line_changes_json_file_path())
    )
    data = _json_object(raw_data, "line state")
    header_data = _json_object(data.get("header"), "line state header")
    header = HunkHeader(
        old_start=_required_int(header_data, "old_start", "line state header"),
        old_len=_required_int(header_data, "old_len", "line state header"),
        new_start=_required_int(header_data, "new_start", "line state header"),
        new_len=_required_int(header_data, "new_len", "line state header"),
    )

    lines: list[LineEntry] = []
    for index, raw_line in enumerate(
        _json_array(data.get("lines"), "line state lines")
    ):
        description = f"line state lines[{index}]"
        line_data = _json_object(raw_line, description)
        trailing_newline = line_data.get("has_trailing_newline", True)
        if not isinstance(trailing_newline, bool):
            raise ValueError(
                f"{description}.has_trailing_newline must be a boolean"
            )
        lines.append(
            LineEntry(
                id=_optional_int(line_data, "id", description),
                kind=_required_string(line_data, "kind", description),
                old_line_number=_optional_int(
                    line_data,
                    "old_lineno",
                    description,
                ),
                new_line_number=_optional_int(
                    line_data,
                    "new_lineno",
                    description,
                ),
                text_bytes=_line_text_bytes(line_data, description),
                source_line=_optional_int(
                    line_data,
                    "source_line",
                    description,
                ),
                has_trailing_newline=trailing_newline,
            )
        )
    return LineLevelChange(
        path=_required_string(data, "path", "line state"),
        header=header,
        lines=lines,
    )


def require_line_changes_from_state() -> LineLevelChange:
    """Return the selected line changes or raise a user-facing state error."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        raise CommandError(_("No selected hunk. Run 'start' first."))
    return line_changes


def compute_remaining_changed_line_ids() -> list[int]:
    """Compute which changed line IDs haven't been processed yet."""
    line_changes = require_line_changes_from_state()
    all_changed_ids = set(line_changes.changed_line_ids())
    included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    processed_ids = included_ids | skipped_ids
    remaining_ids = all_changed_ids - processed_ids
    return sorted(remaining_ids)
