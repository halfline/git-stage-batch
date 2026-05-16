"""Parsing helpers for batch source selectors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .validation import validate_batch_name
from ..exceptions import CommandError
from ..i18n import _


CandidateOperation = Literal["apply", "include"]

_SELECTOR_RE = re.compile(
    r"^(?P<batch>[^:]+):(?P<operation>apply|include)(?::(?P<ordinal>[1-9][0-9]*))?$"
)


@dataclass(frozen=True)
class BatchSourceSelector:
    """A parsed batch selector with optional candidate preview information."""

    batch_name: str
    candidate_operation: CandidateOperation | None = None
    candidate_ordinal: int | None = None

    @property
    def is_candidate_selector(self) -> bool:
        return self.candidate_operation is not None


def _invalid_selector(value: str) -> CommandError:
    return CommandError(
        _(
            "Invalid batch selector '{selector}'. Candidate selectors use "
            "'BATCH:apply', 'BATCH:apply:N', 'BATCH:include', or 'BATCH:include:N'."
        ).format(selector=value)
    )


def parse_batch_source_selector(value: str) -> BatchSourceSelector:
    """Parse a batch name or operation candidate selector."""
    if ":" not in value:
        validate_batch_name(value)
        return BatchSourceSelector(batch_name=value)

    match = _SELECTOR_RE.fullmatch(value)
    if match is None:
        raise _invalid_selector(value)

    batch_name = match.group("batch")
    validate_batch_name(batch_name)
    ordinal_text = match.group("ordinal")
    return BatchSourceSelector(
        batch_name=batch_name,
        candidate_operation=match.group("operation"),  # type: ignore[arg-type]
        candidate_ordinal=int(ordinal_text) if ordinal_text is not None else None,
    )


def format_batch_source_selector(selector: BatchSourceSelector) -> str:
    """Format a parsed selector back into user-facing syntax."""
    if selector.candidate_operation is None:
        return selector.batch_name
    if selector.candidate_ordinal is None:
        return f"{selector.batch_name}:{selector.candidate_operation}"
    return (
        f"{selector.batch_name}:{selector.candidate_operation}:"
        f"{selector.candidate_ordinal}"
    )


def batch_name_for_source_lookup(value: str) -> str:
    """Return the underlying batch name for early metadata/scope lookup."""
    return parse_batch_source_selector(value).batch_name


def require_plain_batch_name(value: str, command_name: str) -> str:
    """Reject candidate selectors where a command expects a batch object name."""
    selector = parse_batch_source_selector(value)
    if selector.candidate_operation is not None:
        raise CommandError(
            _(
                "'{selector}' is a candidate selector, not a batch name.\n"
                "Use '{batch}' for batch management commands."
            ).format(selector=value, batch=selector.batch_name)
        )
    return selector.batch_name


def require_candidate_operation(
    selector: BatchSourceSelector,
    expected: CandidateOperation,
    *,
    raw_value: str | None = None,
    file: str | None = None,
) -> None:
    """Reject a selector for the opposite operation."""
    actual = selector.candidate_operation
    if actual is None or actual == expected:
        return

    display = raw_value or format_batch_source_selector(selector)
    message = _(
        "'{selector}' is an {actual} candidate, not an {expected} candidate.\n"
        "No changes applied."
    ).format(selector=display, actual=actual, expected=expected)
    if file:
        message += _(
            "\n\nPreview {expected} candidates with:\n"
            "  git-stage-batch show --from {batch}:{expected} --file {file}"
        ).format(expected=expected, batch=selector.batch_name, file=file)
    raise CommandError(message)
