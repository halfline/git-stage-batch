"""Selection setup for reset-from-batch."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from ...batch.query import read_batch_metadata
from ...batch.selection import (
    resolve_batch_file_scope,
    resolve_current_batch_binary_file_scope,
)
from ...batch.source_selector import require_plain_batch_name
from ...batch.validation import batch_exists, validate_batch_name
from ...core.line_selection import LineRanges
from ...data.batch_file_review_selection import (
    translate_reset_batch_file_gutter_ids_to_selection_ranges,
)
from ...data.file_review.records import FileReviewAction
from ...data.file_review.action_scope import resolve_batch_source_action_scope
from ...exceptions import exit_with_error
from ...i18n import _


@dataclass(frozen=True)
class ResetClaimSelection:
    """Resolved reset claim scope and command text."""

    batch_name: str
    file: str | None
    all_files: dict
    effective_line_ids: LineRanges | None
    affected_files: set[str]
    operation_parts: tuple[str, ...]


def resolve_reset_claim_selection(
    batch_name: str,
    *,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
    to_batch: str | None,
) -> ResetClaimSelection:
    """Resolve reset-from-batch scope without rejecting empty batches."""
    batch_name = require_plain_batch_name(batch_name, "reset")
    validate_batch_name(batch_name)
    extra_action_parts = ()
    if to_batch is not None:
        extra_action_parts = ("--to", shlex.quote(to_batch))
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.RESET_FROM_BATCH,
        command_name="reset",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
        extra_action_parts=extra_action_parts,
    )
    file = scope_resolution.file

    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    metadata = read_batch_metadata(batch_name)
    all_files = metadata.get("files", {})
    file = resolve_current_batch_binary_file_scope(
        batch_name,
        all_files,
        file,
        patterns,
        line_ids,
    )

    if to_batch is not None:
        validate_batch_name(to_batch)
        if to_batch == batch_name:
            exit_with_error(_("--to must name a different batch"))

    effective_line_ids = (
        translate_reset_batch_file_gutter_ids_to_selection_ranges(
            batch_name,
            all_files,
            file,
            patterns,
            line_ids,
        )
        if line_ids is not None
        else None
    )
    affected_files = set(
        resolve_batch_file_scope(batch_name, all_files, file, patterns).keys()
    )
    return ResetClaimSelection(
        batch_name=batch_name,
        file=file,
        all_files=all_files,
        effective_line_ids=effective_line_ids,
        affected_files=affected_files,
        operation_parts=_operation_parts(
            batch_name,
            to_batch=to_batch,
            line_ids=line_ids,
            file=file,
        ),
    )


def _operation_parts(
    batch_name: str,
    *,
    to_batch: str | None,
    line_ids: str | None,
    file: str | None,
) -> tuple[str, ...]:
    parts = ["reset", "--from", batch_name]
    if to_batch is not None:
        parts.extend(["--to", to_batch])
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    if file is not None:
        parts.extend(["--file", file])
    return tuple(parts)
