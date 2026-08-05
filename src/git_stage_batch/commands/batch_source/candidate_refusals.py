"""Candidate refusal helpers for batch-source commands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...batch.operation_candidate_types import (
    CandidateOperation,
    CandidatePreviewCount,
)
from ...batch.operation_candidate_labels import candidate_operation_label
from ...exceptions import exit_with_error
from ...i18n import _, ngettext


def refuse_candidate_conflicts(
    *,
    batch_name: str,
    operation: CandidateOperation,
    failed_files: Sequence[str],
    candidate_counts: Mapping[str, CandidatePreviewCount],
) -> None:
    """Exit when merge failures have candidate preview refusal details."""
    operation_label = candidate_operation_label(operation)
    candidate_limit_files = [
        file_path
        for file_path in failed_files
        if candidate_counts.get(file_path, CandidatePreviewCount()).too_many
    ]
    if len(candidate_limit_files) == 1:
        file_path = candidate_limit_files[0]
        exit_with_error(
            _(
                "Cannot {operation_label} batch '{batch}': {file} has too many "
                "{operation_label} candidates to preview safely.\n"
                "No changes applied.\n\n"
                "Use --line with a narrower selection or split the batch "
                "before previewing candidates."
            ).format(
                operation_label=operation_label,
                batch=batch_name,
                file=file_path,
            )
        )
    if len(candidate_limit_files) > 1:
        exit_with_error(
            _(
                "Cannot {operation_label} batch '{batch}': multiple files have too "
                "many {operation_label} candidates to preview safely.\n"
                "No changes applied.\n\n"
                "Use --line with narrower selections or split the batch "
                "before previewing candidates."
            ).format(operation_label=operation_label, batch=batch_name)
        )

    candidate_error_files = [
        file_path
        for file_path in failed_files
        if (
            candidate_counts.get(file_path, CandidatePreviewCount()).error
            and not candidate_counts.get(file_path, CandidatePreviewCount()).too_many
        )
    ]
    if len(candidate_error_files) == 1:
        file_path = candidate_error_files[0]
        error = candidate_counts[file_path].error
        exit_with_error(
            _(
                "Cannot enumerate {operation_label} candidates for {file}: {error}\n"
                "No changes applied."
            ).format(operation_label=operation_label, file=file_path, error=error)
        )
    if len(candidate_error_files) > 1:
        examples = "\n".join(
            f"  {file_path}: {candidate_counts[file_path].error}"
            for file_path in candidate_error_files[:3]
        )
        exit_with_error(
            _(
                "Cannot enumerate {operation_label} candidates for multiple files.\n"
                "No changes applied.\n\n"
                "{examples}"
            ).format(operation_label=operation_label, examples=examples)
        )

    ambiguous_files = [
        file_path
        for file_path in failed_files
        if candidate_counts.get(file_path, CandidatePreviewCount()).count
    ]
    if len(ambiguous_files) == 1:
        file_path = ambiguous_files[0]
        reviewed_action = _reviewed_action_label(operation)
        count = candidate_counts[file_path].count
        exit_with_error(
            ngettext(
                "Cannot {operation_label} batch '{batch}': {file} has {count} "
                "{operation_label} candidate.\n"
                "No changes applied.\n\n"
                "Preview the candidate:\n"
                "  git-stage-batch show --from {batch}:{operation} --file {file}\n\n"
                "{reviewed_action} the reviewed candidate:\n"
                "  git-stage-batch {operation} --from {batch}:{operation}:N --file {file}",
                "Cannot {operation_label} batch '{batch}': {file} has {count} "
                "{operation_label} candidates.\n"
                "No changes applied.\n\n"
                "Preview candidates:\n"
                "  git-stage-batch show --from {batch}:{operation} --file {file}\n\n"
                "{reviewed_action} a reviewed candidate:\n"
                "  git-stage-batch {operation} --from {batch}:{operation}:N --file {file}",
                count,
            ).format(
                operation_label=operation_label,
                operation=operation,
                batch=batch_name,
                file=file_path,
                count=count,
                reviewed_action=reviewed_action,
            )
        )
    if len(ambiguous_files) > 1:
        examples = "\n".join(
            (
                f"  git-stage-batch show --from {batch_name}:{operation} "
                f"--file {file_path}"
            )
            for file_path in ambiguous_files[:3]
        )
        exit_with_error(
            _(
                "Cannot {operation_label} batch '{batch}': multiple files need "
                "{operation_label} decisions.\n"
                "No changes applied.\n\n"
                "Resolve one file at a time:\n{examples}"
            ).format(
                operation_label=operation_label,
                batch=batch_name,
                examples=examples,
            )
        )


def _reviewed_action_label(operation: CandidateOperation) -> str:
    if operation == "apply":
        return _("Apply")
    if operation == "include":
        return _("Include")
    raise ValueError(f"unknown candidate operation: {operation}")
