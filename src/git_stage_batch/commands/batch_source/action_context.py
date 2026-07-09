"""Shared command context for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass

from ...batch.metadata_validation import read_validated_batch_metadata
from ...batch.source_selector import (
    BatchSourceSelector,
    CandidateOperation,
    require_plain_batch_name,
)
from ...batch.validation import batch_exists
from ...data.file_review.records import ActionScopeResolution, FileReviewAction
from ...data.file_review.state import resolve_batch_source_action_scope
from ...exceptions import BatchMetadataError, exit_with_error
from ...i18n import _
from . import candidate_selectors


@dataclass(frozen=True)
class BatchSourceActionContext:
    """Resolved selector, review scope, and metadata for an action command."""

    raw_selector: str
    selector: BatchSourceSelector
    batch_name: str
    file: str | None
    scope_resolution: ActionScopeResolution
    metadata: dict
    all_files: dict


def resolve_batch_source_action_context(
    raw_selector: str,
    *,
    operation: CandidateOperation,
    review_action: FileReviewAction,
    command_name: str,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
) -> BatchSourceActionContext:
    """Resolve shared batch-source command inputs before action-specific work."""
    selector = candidate_selectors.resolve_batch_source_action_selector(
        raw_selector,
        operation,
        file=file,
    )
    return _resolve_batch_source_action_context(
        raw_selector=raw_selector,
        selector=selector,
        review_action=review_action,
        command_name=command_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )


def resolve_plain_batch_source_action_context(
    raw_selector: str,
    *,
    review_action: FileReviewAction,
    command_name: str,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
) -> BatchSourceActionContext:
    """Resolve shared action inputs for commands that require a plain batch."""
    batch_name = require_plain_batch_name(raw_selector, command_name)
    selector = BatchSourceSelector(batch_name=batch_name)
    return _resolve_batch_source_action_context(
        raw_selector=raw_selector,
        selector=selector,
        review_action=review_action,
        command_name=command_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )


def _resolve_batch_source_action_context(
    *,
    raw_selector: str,
    selector: BatchSourceSelector,
    review_action: FileReviewAction,
    command_name: str,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
) -> BatchSourceActionContext:
    batch_name = selector.batch_name
    scope_resolution = resolve_batch_source_action_scope(
        review_action,
        command_name=command_name,
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    file = scope_resolution.file

    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})
    if not all_files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    return BatchSourceActionContext(
        raw_selector=raw_selector,
        selector=selector,
        batch_name=batch_name,
        file=file,
        scope_resolution=scope_resolution,
        metadata=metadata,
        all_files=all_files,
    )
