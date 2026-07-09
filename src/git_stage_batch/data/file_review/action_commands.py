"""Command text helpers for file review actions."""

from __future__ import annotations

import shlex

from . import records as _records


def _quote(value: str) -> str:
    return shlex.quote(value)


def batch_source_action_command(
    command_name: str,
    batch_name: str,
    *,
    file_scope: bool,
    line_ids: str | None,
    extra_action_parts: tuple[str, ...] = (),
) -> str:
    """Return a batch-source command for a reviewed action."""
    parts = [command_name, "--from", shlex.quote(batch_name)]
    if file_scope:
        parts.append("--file")
    parts.extend(extra_action_parts)
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    return " ".join(parts)


def show_command_for_review_state(
    review_state: _records.FileReviewState,
    *,
    page: str | None = None,
) -> str:
    """Return the show command that recreates a file review."""
    command = "git-stage-batch show"
    if (
        review_state.source == _records.ReviewSource.BATCH
        and review_state.batch_name is not None
    ):
        command += f" --from {_quote(review_state.batch_name)}"
    command += f" --file {_quote(review_state.file_path)}"
    if page is not None:
        command += f" --page {page}"
    return command


def line_action_command(
    action: _records.FileReviewAction | str,
    review_state: _records.FileReviewState,
    *,
    line_spec: str | None = None,
    whole_file: bool = False,
    pathless_line: bool = False,
) -> str | None:
    """Return an action command for a reviewed file or line selection."""
    review_action = _records.coerce_review_action(action)
    action_value = review_action.value
    if review_action in (
        _records.FileReviewAction.INCLUDE_TO_BATCH,
        _records.FileReviewAction.DISCARD_TO_BATCH,
    ):
        return None
    if review_state.source == _records.ReviewSource.BATCH:
        if review_action in (
            _records.FileReviewAction.INCLUDE,
            _records.FileReviewAction.INCLUDE_FROM_BATCH,
        ):
            action_value = _records.FileReviewAction.INCLUDE.value
        elif review_action in (
            _records.FileReviewAction.DISCARD,
            _records.FileReviewAction.DISCARD_FROM_BATCH,
        ):
            action_value = _records.FileReviewAction.DISCARD.value
        elif review_action == _records.FileReviewAction.APPLY_FROM_BATCH:
            action_value = "apply"
        elif review_action == _records.FileReviewAction.RESET_FROM_BATCH:
            action_value = "reset"
        else:
            return None
        command = (
            f"git-stage-batch {action_value} "
            f"--from {_quote(review_state.batch_name or '')}"
        )
        file_args = f" --file {_quote(review_state.file_path)}"
    else:
        command = f"git-stage-batch {action_value}"
        file_args = f" --file {_quote(review_state.file_path)}"

    if line_spec is not None:
        if pathless_line:
            return f"{command} --line {line_spec}"
        return f"{command}{file_args} --line {line_spec}"
    if whole_file:
        return f"{command}{file_args}"
    return command


def live_to_batch_action_command(
    command_name: str,
    batch_name: str,
    *,
    file_scope: bool,
    line_ids: str | None,
) -> str:
    """Return a live-to-batch command for a reviewed action."""
    parts = [command_name, "--to", batch_name]
    if file_scope:
        parts.append("--file")
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    return " ".join(parts)
