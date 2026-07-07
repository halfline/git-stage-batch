"""CLI file-scope resolution helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..batch.query import read_batch_metadata
from ..batch.source_selector import batch_name_for_source_lookup
from ..batch.validation import batch_exists
from ..data.file_tracking import list_untracked_files
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_patterns import (
    list_changed_files,
    list_staged_files,
    resolve_gitignore_style_patterns,
)

FileArgument = str | list[str] | None


class FileScopeKind(str, Enum):
    """How a command's optional file scope was requested."""

    IMPLICIT = "implicit"
    EXPLICIT = "explicit"
    PATTERN = "pattern"


@dataclass(frozen=True)
class FileScope:
    """Resolved command file scope with explicit origin and concrete files."""

    kind: FileScopeKind
    files: tuple[str, ...] = ()

    @classmethod
    def implicit(cls) -> "FileScope":
        return cls(FileScopeKind.IMPLICIT)

    @classmethod
    def explicit(cls, file_path: str) -> "FileScope":
        return cls(FileScopeKind.EXPLICIT, (file_path,))

    @classmethod
    def pattern(cls, files: list[str]) -> "FileScope":
        return cls(FileScopeKind.PATTERN, tuple(files))

    @property
    def is_implicit(self) -> bool:
        return self.kind == FileScopeKind.IMPLICIT

    @property
    def is_multiple(self) -> bool:
        return len(self.files) > 1

    def optional_file(self) -> str | None:
        """Return the single file path for this scope, or None for implicit scope."""
        if self.is_implicit:
            return None
        if self.is_multiple:
            raise ValueError("multiple file scope cannot be represented by one path")
        return self.files[0]

    def require_single_file(self, error_message: str) -> str | None:
        """Return an optional single file path, or raise for a multi-file scope."""
        if self.is_multiple:
            raise CommandError(error_message)
        return self.optional_file()


def _resolve_file_patterns(
    file_arg: FileArgument,
    file_patterns: list[str] | None,
) -> list[str] | None:
    """Return combined pattern arguments, preserving pathless --file."""
    if file_arg == "":
        if file_patterns is not None:
            raise CommandError(_("Cannot use --file together with --files."))
        return None

    patterns: list[str] = []
    if isinstance(file_arg, str):
        patterns.append(file_arg)
    elif file_arg is not None:
        patterns.extend(file_arg)

    if file_patterns is not None:
        patterns.extend(file_patterns)

    return patterns or None


def _file_arg_values(file_arg: FileArgument) -> list[str]:
    """Return argument-bearing --file values."""
    if file_arg is None or file_arg == "":
        return []
    if isinstance(file_arg, str):
        return [file_arg]
    return list(file_arg)


def _normalize_file_argument_path(path: str) -> str:
    """Normalize a user-provided file path for exact candidate lookup."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _resolve_file_argument_patterns(
    candidates: Sequence[str],
    file_arg: FileArgument,
    file_patterns: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Resolve --file/--files values against candidates with exact --file fallback."""
    file_values = _file_arg_values(file_arg)
    display_patterns = [*file_values, *(file_patterns or [])]
    candidate_by_path = {
        _normalize_file_argument_path(candidate): candidate
        for candidate in candidates
    }

    exact_files: list[str] = []
    pattern_values: list[str] = []
    for value in file_values:
        exact_file = candidate_by_path.get(_normalize_file_argument_path(value))
        if exact_file is not None:
            exact_files.append(exact_file)
        else:
            pattern_values.append(value)

    pattern_values.extend(file_patterns or [])
    resolved_patterns = (
        resolve_gitignore_style_patterns(candidates, pattern_values)
        if pattern_values else
        []
    )
    resolved_files = list(dict.fromkeys([*exact_files, *resolved_patterns]))
    return resolved_files, display_patterns


def resolve_live_file_scope(
    file_arg: FileArgument,
    file_patterns: list[str] | None,
    *,
    include_staged: bool = False,
) -> FileScope:
    """Resolve single-file or pattern-based live file scope."""
    resolved_patterns = _resolve_file_patterns(file_arg, file_patterns)
    if resolved_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit("")

    candidate_files = [*list_changed_files(), *list_untracked_files()]
    if include_staged:
        candidate_files.extend(list_staged_files())
    candidate_files = list(dict.fromkeys(candidate_files))
    resolved_files, display_patterns = _resolve_file_argument_patterns(
        candidate_files,
        file_arg,
        file_patterns,
    )
    if not resolved_files:
        raise CommandError(
            _("No changed files matched: {patterns}").format(
                patterns=", ".join(display_patterns),
            )
        )
    return FileScope.pattern(resolved_files)


def resolve_batch_file_scope(
    batch_name: str,
    file_arg: FileArgument,
    file_patterns: list[str] | None,
) -> FileScope:
    """Resolve single-file or pattern-based batch file scope."""
    lookup_batch_name = batch_name_for_source_lookup(batch_name)
    resolved_patterns = _resolve_file_patterns(file_arg, file_patterns)
    if resolved_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit("")
    if not batch_exists(lookup_batch_name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=lookup_batch_name))

    metadata = read_batch_metadata(lookup_batch_name)
    resolved_files, display_patterns = _resolve_file_argument_patterns(
        list(metadata.get("files", {}).keys()),
        file_arg,
        file_patterns,
    )
    if not resolved_files:
        raise CommandError(
            _("No files in batch '{name}' matched: {patterns}").format(
                name=lookup_batch_name,
                patterns=", ".join(display_patterns),
            )
        )
    return FileScope.pattern(resolved_files)
