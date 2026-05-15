"""Session preference for selecting the next hunk after an action."""

from __future__ import annotations

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_auto_advance_config_file_path


DEFAULT_AUTO_ADVANCE = True


def read_auto_advance_default() -> bool:
    """Return whether actions should select the next hunk by default."""
    value = read_text_file_contents(get_auto_advance_config_file_path()).strip()
    if value == "false":
        return False
    if value == "true":
        return True
    return DEFAULT_AUTO_ADVANCE


def write_auto_advance_default(auto_advance: bool) -> None:
    """Persist whether actions should select the next hunk by default."""
    value = "true\n" if auto_advance else "false\n"
    write_text_file_contents(get_auto_advance_config_file_path(), value)


def resolve_auto_advance(auto_advance: bool | None) -> bool:
    """Resolve a command-specific preference against the session default."""
    if auto_advance is not None:
        return auto_advance
    return read_auto_advance_default()
