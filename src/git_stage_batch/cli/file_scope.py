"""CLI file-scope resolution helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..batch.state.query import read_batch_metadata
from ..batch.state.metadata_types import BatchFileMetadataDict
from ..batch.source.selector import batch_name_for_source_lookup
from ..batch.state.batch_names import batch_exists
from ..data.batch_file_scope import (
    resolve_batch_file_scope as resolve_stored_batch_file_scope,
    resolve_current_batch_atomic_file_scope,
)
from ..data.file_tracking import list_untracked_files
from ..data.file_review.action_refusals import (
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
)
from ..data.file_review.action_scope import resolve_batch_source_action_scope
from ..data.file_review.records import FileReviewAction
from ..data.selected_change.paths import get_selected_change_file_path
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
    """Resolved command file scope with known paths and marker provenance."""

    kind: FileScopeKind
    files: tuple[str, ...] = ()
    includes_selected_file_marker: bool = False

    @classmethod
    def implicit(cls) -> "FileScope":
        return cls(FileScopeKind.IMPLICIT)

    @classmethod
    def explicit(cls, file_path: str) -> "FileScope":
        return cls(FileScopeKind.EXPLICIT, (file_path,))

    @classmethod
    def pattern(
        cls,
        files: list[str],
        *,
        includes_selected_file_marker: bool = False,
    ) -> "FileScope":
        return cls(
            FileScopeKind.PATTERN,
            tuple(files),
            includes_selected_file_marker=includes_selected_file_marker,
        )

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
        if not self.files:
            raise ValueError("file scope does not have a concrete path")
        return self.files[0]

    def optional_line_file(self) -> str | None:
        """Return a single line-action file while preserving a selected marker."""
        if self.is_multiple:
            raise ValueError("multiple file scope cannot be represented by one path")
        if self.includes_selected_file_marker:
            return ""
        return self.optional_file()

    def require_single_line_file(self, error_message: str) -> str | None:
        """Return a line-action file, or raise for a multi-file scope."""
        if self.is_multiple:
            raise CommandError(error_message)
        return self.optional_line_file()


def _resolve_file_patterns(
    file_arg: FileArgument,
    file_patterns: list[str] | None,
) -> list[str] | None:
    """Return combined pattern arguments, preserving pathless --file."""
    if file_arg == "" or (
        isinstance(file_arg, list)
        and "" in file_arg
    ):
        if file_patterns is not None:
            raise CommandError(_("Cannot use --file together with --files."))
    if file_arg == "":
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
    *,
    selected_file: str | None = None,
    defer_selected_marker_validation: bool = False,
) -> tuple[list[str], list[str]]:
    """Resolve file arguments while optionally deferring marker validation.

    Line actions must pass a selected-file marker to their command-level
    review validator even when its cached path is absent or unavailable.
    Known marker paths are still retained here for duplicate and multi-file
    detection.
    """
    file_values = _file_arg_values(file_arg)
    candidate_by_path = {
        _normalize_file_argument_path(candidate): candidate
        for candidate in candidates
    }

    exact_files: list[str] = []
    pattern_values: list[str] = []
    display_patterns: list[str] = []
    for raw_value in file_values:
        value = raw_value
        is_selected_marker = value == ""
        if is_selected_marker:
            if selected_file is None:
                if defer_selected_marker_validation:
                    continue
                raise CommandError(
                    _("No selected hunk. Run 'show' first or specify file path.")
                )
            value = selected_file
            selected_candidate = candidate_by_path.get(
                _normalize_file_argument_path(value)
            )
            if selected_candidate is None:
                if defer_selected_marker_validation:
                    exact_files.append(value)
                    display_patterns.append(value)
                    continue
                raise CommandError(
                    _("Selected file is not available in this file scope: {file}").format(
                        file=value,
                    )
                )
        display_patterns.append(value)
        exact_file = candidate_by_path.get(_normalize_file_argument_path(value))
        if exact_file is not None:
            exact_files.append(exact_file)
        else:
            pattern_values.append(value)

    display_patterns.extend(file_patterns or [])
    pattern_values.extend(file_patterns or [])
    resolved_patterns = (
        resolve_gitignore_style_patterns(candidates, pattern_values)
        if pattern_values else
        []
    )
    resolved_files = list(dict.fromkeys([*exact_files, *resolved_patterns]))
    return resolved_files, display_patterns


def _has_pathless_file_marker(file_arg: FileArgument) -> bool:
    """Return whether a multi-value file argument requests the selected file."""
    return isinstance(file_arg, list) and "" in file_arg


def _resolve_live_selected_file(
    action: FileReviewAction | None,
    line_ids: str | None,
) -> str | None:
    """Resolve a selected live file with guards appropriate to the action scope."""
    if action is not None and line_ids is None:
        refuse_live_action_for_batch_selection(action)
        refuse_ambiguous_bare_action_after_partial_file_review(action)
    return get_selected_change_file_path()


def _resolve_batch_selected_file(
    batch_name: str,
    all_files: dict[str, BatchFileMetadataDict],
    action: FileReviewAction | None,
    command_name: str | None,
    line_ids: str | None,
) -> str | None:
    """Resolve a selected batch file with guards appropriate to the action scope."""
    if line_ids is not None:
        return get_selected_change_file_path()

    selected_file: str | None = ""
    if action is not None:
        if command_name is None:
            raise ValueError(
                "command_name is required for a mutating batch file scope"
            )
        scope_resolution = resolve_batch_source_action_scope(
            action,
            command_name=command_name,
            batch_name=batch_name,
            line_ids=None,
            file="",
            patterns=None,
        )
        selected_file = scope_resolution.file
    selected_file = resolve_current_batch_atomic_file_scope(
        batch_name,
        all_files,
        selected_file,
        None,
        line_ids,
    )
    resolved_files = resolve_stored_batch_file_scope(
        batch_name,
        all_files,
        selected_file,
    )
    return next(iter(resolved_files), None)


def resolve_live_file_scope(
    file_arg: FileArgument,
    file_patterns: list[str] | None,
    *,
    include_staged: bool = False,
    selected_action: FileReviewAction | None = None,
    line_ids: str | None = None,
) -> FileScope:
    """Resolve single-file or pattern-based live file scope."""
    resolved_patterns = _resolve_file_patterns(file_arg, file_patterns)
    if resolved_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit("")

    includes_selected_file_marker = _has_pathless_file_marker(file_arg)
    defer_selected_marker_validation = (
        includes_selected_file_marker
        and line_ids is not None
    )
    candidate_files = [*list_changed_files(), *list_untracked_files()]
    if include_staged:
        candidate_files.extend(list_staged_files())
    candidate_files = list(dict.fromkeys(candidate_files))
    selected_file = (
        _resolve_live_selected_file(selected_action, line_ids)
        if includes_selected_file_marker
        else None
    )
    resolved_files, display_patterns = _resolve_file_argument_patterns(
        candidate_files,
        file_arg,
        file_patterns,
        selected_file=selected_file,
        defer_selected_marker_validation=defer_selected_marker_validation,
    )
    if not resolved_files and not defer_selected_marker_validation:
        raise CommandError(
            _("No changed files matched: {patterns}").format(
                patterns=", ".join(display_patterns),
            )
        )
    return FileScope.pattern(
        resolved_files,
        includes_selected_file_marker=includes_selected_file_marker,
    )


def resolve_batch_file_scope(
    batch_name: str,
    file_arg: FileArgument,
    file_patterns: list[str] | None,
    *,
    selected_action: FileReviewAction | None = None,
    command_name: str | None = None,
    line_ids: str | None = None,
) -> FileScope:
    """Resolve single-file or pattern-based batch file scope."""
    lookup_batch_name = batch_name_for_source_lookup(batch_name)
    resolved_patterns = _resolve_file_patterns(file_arg, file_patterns)
    if resolved_patterns is None:
        return FileScope.implicit() if file_arg is None else FileScope.explicit("")
    if not batch_exists(lookup_batch_name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=lookup_batch_name))

    includes_selected_file_marker = _has_pathless_file_marker(file_arg)
    defer_selected_marker_validation = (
        includes_selected_file_marker
        and line_ids is not None
    )
    metadata = read_batch_metadata(lookup_batch_name)
    all_files = metadata.get("files", {})
    selected_file = (
        _resolve_batch_selected_file(
            lookup_batch_name,
            all_files,
            selected_action,
            command_name,
            line_ids,
        )
        if includes_selected_file_marker
        else None
    )
    resolved_files, display_patterns = _resolve_file_argument_patterns(
        list(all_files.keys()),
        file_arg,
        file_patterns,
        selected_file=selected_file,
        defer_selected_marker_validation=defer_selected_marker_validation,
    )
    if not resolved_files and not defer_selected_marker_validation:
        raise CommandError(
            _("No files in batch '{name}' matched: {patterns}").format(
                name=lookup_batch_name,
                patterns=", ".join(display_patterns),
            )
        )
    return FileScope.pattern(
        resolved_files,
        includes_selected_file_marker=includes_selected_file_marker,
    )
