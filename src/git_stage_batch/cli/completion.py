"""Shell completion helpers."""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath

from ..batch.query import list_batch_files
from ..exceptions import CommandError
from ..utils.file_patterns import list_changed_files


_WILDMATCH_META = frozenset({"*", "?", "["})


def _extract_completion_prefix(token: str) -> tuple[str, str]:
    """Split a token into its negation prefix and literal completion prefix."""
    negation = "!" if token.startswith("!") else ""
    pattern = token[1:] if negation else token

    if not pattern:
        return negation, ""

    escaped = False
    in_class = False
    literal_chars: list[str] = []
    saw_meta = False

    for char in pattern:
        if escaped:
            literal_chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_class:
            if char == "]":
                in_class = False
            saw_meta = True
            continue
        if char == "[":
            in_class = True
            saw_meta = True
            break
        if char in {"*", "?"}:
            saw_meta = True
            break
        literal_chars.append(char)

    literal_prefix = "".join(literal_chars)
    if saw_meta:
        slash_index = literal_prefix.rfind("/")
        if slash_index != -1:
            literal_prefix = literal_prefix[:slash_index + 1]
        else:
            literal_prefix = ""

    return negation, literal_prefix


def _build_completion_candidates(paths: list[str]) -> list[str]:
    """Build file and directory completion candidates from repo-relative files."""
    candidates = set(paths)
    for path in paths:
        current = PurePosixPath(path).parent
        while str(current) not in (".", ""):
            candidates.add(f"{current.as_posix()}/")
            current = current.parent
    return sorted(candidates)


def _filter_completion_candidates(candidates: list[str], token: str) -> list[str]:
    """Filter candidates based on the partially typed token."""
    negation, literal_prefix = _extract_completion_prefix(token)
    pattern = token[1:] if negation else token

    if "/" in pattern:
        filtered = [candidate for candidate in candidates if candidate.startswith(literal_prefix)]
    elif literal_prefix:
        filtered = [
            candidate for candidate in candidates
            if candidate.startswith(literal_prefix) or PurePosixPath(candidate.rstrip("/")).name.startswith(literal_prefix)
        ]
    else:
        filtered = candidates

    return [negation + candidate for candidate in filtered]


def list_file_completion_candidates(
    current_token: str,
    *,
    from_batch: str | None = None,
) -> list[str]:
    """List repo-aware completion candidates for --file/--files."""
    try:
        paths = list_batch_files(from_batch) if from_batch is not None else list_changed_files()
    except (CommandError, subprocess.CalledProcessError):
        return []

    if not paths:
        return []

    candidates = _build_completion_candidates(paths)
    return _filter_completion_candidates(candidates, current_token)


def command_complete_files(current_token: str, *, from_batch: str | None = None) -> None:
    """Print completion candidates for --file/--files, one per line."""
    for candidate in list_file_completion_candidates(current_token, from_batch=from_batch):
        print(candidate)
