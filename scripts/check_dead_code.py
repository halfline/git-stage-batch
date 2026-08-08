#!/usr/bin/env python3
"""Report unreviewed Vulture findings and stale finding exceptions."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import sys

from vulture import Vulture


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCAN_PATHS = (
    REPOSITORY_ROOT / "src" / "git_stage_batch",
)
MINIMUM_CONFIDENCE = 60


@dataclass(frozen=True)
class FindingIdentity:
    """Stable identity for one kind of Vulture finding."""

    path: str
    kind: str
    name: str


@dataclass(frozen=True)
class AllowedFinding:
    """One reviewed indirect use that Vulture cannot follow."""

    identity: FindingIdentity
    expected_count: int
    reason: str


@dataclass(frozen=True)
class Finding:
    """One Vulture finding with its source location and diagnostic."""

    identity: FindingIdentity
    line: int
    message: str
    confidence: int


def _allowed(
    path: str,
    kind: str,
    name: str,
    reason: str,
    *,
    expected_count: int = 1,
) -> AllowedFinding:
    return AllowedFinding(
        FindingIdentity(path, kind, name),
        expected_count,
        reason,
    )


# Vulture is intentionally conservative and cannot follow some uses that do
# not spell an attribute or function name in ordinary Python code. Keep each
# exception tied to one path, finding kind, and name. The expected occurrence
# count makes a second, genuinely unused definition fail instead of inheriting
# an existing exception.
ALLOWED_FINDINGS = (
    # Persisted and dynamically indexed mapping keys.
    _allowed(
        "src/git_stage_batch/batch/operation_candidate_state.py",
        "variable",
        "algorithm_version",
        "TypedDict key read from decoded JSON by string name.",
    ),
    _allowed(
        "src/git_stage_batch/batch/ownership/metadata_types.py",
        "variable",
        "original_unit",
        "TypedDict key read from persisted metadata by string name.",
    ),
    _allowed(
        "src/git_stage_batch/batch/state/metadata_types.py",
        "variable",
        "added_lines",
        "TypedDict key read from persisted metadata by string name.",
    ),
    _allowed(
        "src/git_stage_batch/batch/state/metadata_types.py",
        "variable",
        "deleted_lines",
        "TypedDict key read from persisted metadata by string name.",
    ),
    _allowed(
        "src/git_stage_batch/commands/batch_source/text_plan_jobs.py",
        "variable",
        "replacement_display_text",
        "TypedDict key read from a worker manifest by string name.",
    ),
    _allowed(
        "src/git_stage_batch/commands/batch_source/text_plan_jobs.py",
        "variable",
        "replacement_exact",
        "TypedDict key read from a worker manifest by string name.",
    ),
    _allowed(
        "src/git_stage_batch/commands/validate.py",
        "variable",
        "migration_required",
        "TypedDict key read from a validation report by string name.",
    ),
    _allowed(
        "src/git_stage_batch/commands/validate.py",
        "variable",
        "residue",
        "TypedDict key read from a validation report by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/line_state.py",
        "variable",
        "old_lineno",
        "TypedDict key read from persisted line state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/line_state.py",
        "variable",
        "new_lineno",
        "TypedDict key read from persisted line state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/line_state.py",
        "variable",
        "text_bytes_b64",
        "TypedDict key read from persisted line state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/suggest_fixup_state.py",
        "variable",
        "range_fingerprint",
        "TypedDict key read from persisted suggestion state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/suggest_fixup_state.py",
        "variable",
        "last_shown_commit",
        "TypedDict key read from persisted suggestion state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/live_change_jobs.py",
        "variable",
        "patch_artifact_path",
        "TypedDict key read from a worker manifest by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "head",
        "TypedDict key validated through a field-name collection.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "storage_mode",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "tracked_session_paths",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "tracked_batches_paths",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "session_files",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "batches_files",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/recovery_types.py",
        "variable",
        "repository_files",
        "TypedDict key read from persisted recovery state by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/status_types.py",
        "variable",
        "in_progress",
        "TypedDict key read from a status response by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/status_types.py",
        "variable",
        "skipped",
        "TypedDict key read from a status response by string name.",
    ),
    _allowed(
        "src/git_stage_batch/data/status_types.py",
        "variable",
        "discarded",
        "TypedDict key read from a status response by string name.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "skipped",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "discarded",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "file_review_batch",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "file_review_fresh",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "file_review_source",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/output/status_prompt.py",
        "variable",
        "in_progress",
        "TypedDict key consumed through str.format_map.",
    ),
    _allowed(
        "src/git_stage_batch/utils/journal.py",
        "variable",
        "oldest_timestamp",
        "TypedDict key returned through the journal's public summary mapping.",
    ),
    _allowed(
        "src/git_stage_batch/utils/journal.py",
        "variable",
        "newest_timestamp",
        "TypedDict key returned through the journal's public summary mapping.",
    ),
    # Dataclass fields used through whole-record behavior or serialization.
    _allowed(
        "src/git_stage_batch/batch/ownership/merging.py",
        "variable",
        "content_digest",
        "Field contributes to dataclass equality and hashing used by a dictionary.",
    ),
    _allowed(
        "src/git_stage_batch/data/file_review/records.py",
        "variable",
        "change_index",
        "Field is persisted through dataclasses.asdict.",
    ),
    _allowed(
        "src/git_stage_batch/data/live_change_jobs.py",
        "variable",
        "mtime_ns",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/data/live_change_jobs.py",
        "variable",
        "ctime_ns",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/data/live_change_jobs.py",
        "variable",
        "device",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/data/live_change_jobs.py",
        "variable",
        "inode",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_files.py",
        "variable",
        "device",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_files.py",
        "variable",
        "inode",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_files.py",
        "variable",
        "links",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_files.py",
        "variable",
        "modified_ns",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_files.py",
        "variable",
        "changed_ns",
        "Field contributes to whole-dataclass equality.",
    ),
    _allowed(
        "src/git_stage_batch/history/resolution_workspace.py",
        "function",
        "materialize_completed_history_resolution",
        (
            "Public seam is isolated and directly tested before the next "
            "history-validation command slice consumes it."
        ),
    ),
    # Protocol surface used indirectly by the standard library.
    _allowed(
        "src/git_stage_batch/cli/pager.py",
        "method",
        "writable",
        "Method implements the TextIO-compatible stream protocol.",
    ),
    _allowed(
        "src/git_stage_batch/utils/file_io.py",
        "method",
        "readable",
        "Method implements the RawIOBase protocol queried by io.BufferedReader.",
    ),
)


def _allowed_findings_by_identity() -> dict[FindingIdentity, AllowedFinding]:
    allowed = {finding.identity: finding for finding in ALLOWED_FINDINGS}
    if len(allowed) != len(ALLOWED_FINDINGS):
        raise RuntimeError("dead-code allowlist contains duplicate identities")
    return allowed


def _relative_path(filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()


def find_unused_code() -> list[Finding]:
    """Return Vulture findings from production code alone."""
    checker = Vulture()
    checker.scavenge([str(path) for path in SCAN_PATHS])
    return [
        Finding(
            identity=FindingIdentity(
                path=_relative_path(item.filename),
                kind=item.typ,
                name=item.name,
            ),
            line=item.first_lineno,
            message=item.message,
            confidence=item.confidence,
        )
        for item in checker.get_unused_code(
            min_confidence=MINIMUM_CONFIDENCE,
            sort_by_size=True,
        )
    ]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "emit GitHub warning annotations and succeed when dead-code "
            "policy findings are present"
        ),
    )
    return parser.parse_args(argv)


def _workflow_command_data(value: str) -> str:
    """Escape message data used in a GitHub workflow command."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _workflow_command_property(value: str) -> str:
    """Escape a property used in a GitHub workflow command."""
    return (
        _workflow_command_data(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _emit_warning(
    *,
    path: str,
    line: int | None,
    title: str,
    message: str,
) -> None:
    properties = [
        f"file={_workflow_command_property(path)}",
        f"title={_workflow_command_property(title)}",
    ]
    if line is not None:
        properties.append(f"line={line}")
    print(
        f"::warning {','.join(properties)}::"
        f"{_workflow_command_data(message)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    findings = find_unused_code()
    allowed = _allowed_findings_by_identity()
    counts = Counter(finding.identity for finding in findings)
    unexpected = [
        finding for finding in findings if finding.identity not in allowed
    ]
    count_mismatches = [
        (finding, counts.get(identity, 0))
        for identity, finding in allowed.items()
        if counts.get(identity, 0) != finding.expected_count
    ]

    if not unexpected and not count_mismatches:
        print(
            "Dead-code check passed: "
            f"{len(findings)} indirect uses have reviewed exceptions."
        )
        return 0

    for finding in unexpected:
        message = f"{finding.message} ({finding.confidence}% confidence)"
        print(
            f"{finding.identity.path}:{finding.line}: {message}",
            file=sys.stderr,
        )
        if args.advisory:
            _emit_warning(
                path=finding.identity.path,
                line=finding.line,
                title="Dead code in intermediate stack layer",
                message=message,
            )
    for allowed_finding, actual_count in count_mismatches:
        identity = allowed_finding.identity
        message = (
            "stale or changed dead-code exception for "
            f"{identity.kind} {identity.name!r}: expected "
            f"{allowed_finding.expected_count}, found {actual_count}; "
            f"{allowed_finding.reason}"
        )
        print(
            f"{identity.path}: {message}",
            file=sys.stderr,
        )
        if args.advisory:
            _emit_warning(
                path=identity.path,
                line=None,
                title="Dead-code exception in intermediate stack layer",
                message=message,
            )
    return 0 if args.advisory else 1


if __name__ == "__main__":
    raise SystemExit(main())
