"""Resolve gitignore-style file patterns against candidate paths."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .file_io import write_text_file_contents
from .git_command import run_git_command
from ..git_paths import decode_path, encode_path, nul_records


def _normalize_path(path: str) -> str:
    """Normalize repository-relative paths to POSIX separators."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _validate_patterns(patterns: Iterable[str]) -> list[str]:
    """Normalize and validate gitignore-style patterns."""
    normalized_patterns = [_normalize_path(pattern) for pattern in patterns]
    for pattern in normalized_patterns:
        if not pattern:
            raise ValueError("Pattern cannot be empty")
    return normalized_patterns


def resolve_gitignore_style_patterns(
    candidates: Iterable[str],
    patterns: Iterable[str],
) -> list[str]:
    """Resolve gitignore-style patterns to matching candidate paths.

    This delegates matching to Git itself using `git check-ignore`, which gives
    parity with Git's wildmatch implementation, including escaping, negation,
    character classes, and directory semantics.
    """
    normalized_candidates = [_normalize_path(candidate) for candidate in candidates]
    normalized_patterns = _validate_patterns(patterns)
    if not normalized_candidates:
        return []

    with tempfile.TemporaryDirectory(prefix="git-stage-batch-patterns-") as temp_dir:
        temp_root = Path(temp_dir)
        run_git_command(
            ["init", "-q"],
            cwd=str(temp_root),
            text_output=False,
            requires_index_lock=False,
        )
        write_text_file_contents(temp_root / ".gitignore", "".join(f"{pattern}\n" for pattern in normalized_patterns))

        payload = b"".join(encode_path(candidate) + b"\0" for candidate in normalized_candidates)
        result = run_git_command(
            ["check-ignore", "--no-index", "--stdin", "-z", "-v", "-n"],
            stdin_chunks=[payload],
            cwd=str(temp_root),
            check=False,
            text_output=False,
            requires_index_lock=False,
        )
        if result.returncode not in (0, 1):
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )

    fields = nul_records(result.stdout)
    resolved_status: dict[str, bool] = {}
    for index in range(0, len(fields) - 1, 4):
        _source, _line_number, pattern, candidate = fields[index:index + 4]
        if not candidate:
            continue
        candidate_text = decode_path(candidate)
        pattern_text = decode_path(pattern)
        resolved_status[candidate_text] = bool(pattern_text) and not pattern_text.startswith("!")

    return [candidate for candidate in normalized_candidates if resolved_status.get(candidate, False)]


def _paths_from_name_status_z(output: bytes) -> list[str]:
    fields = nul_records(output)
    changed_paths: list[str] = []
    index = 0
    while index < len(fields):
        status_bytes = fields[index]
        index += 1
        if not status_bytes:
            continue

        status = status_bytes.decode("ascii", errors="replace")
        path_count = 2 if status.startswith(("R", "C")) else 1
        for _ in range(path_count):
            if index >= len(fields):
                break
            path = fields[index]
            index += 1
            if path:
                changed_paths.append(_normalize_path(decode_path(path)))

    return list(dict.fromkeys(changed_paths))


def list_changed_files() -> list[str]:
    """List repository-relative paths participating in the working-tree diff."""
    result = run_git_command(
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=none",
            "--find-renames",
            "--name-status",
            "-z",
        ],
        text_output=False,
        requires_index_lock=False,
    )

    return _paths_from_name_status_z(result.stdout)


def list_staged_files() -> list[str]:
    """List repository-relative paths participating in the index diff."""
    result = run_git_command(
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--cached",
            "--ignore-submodules=none",
            "--find-renames",
            "--name-status",
            "-z",
        ],
        text_output=False,
        requires_index_lock=False,
    )

    return _paths_from_name_status_z(result.stdout)
