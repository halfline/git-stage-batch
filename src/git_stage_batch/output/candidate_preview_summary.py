"""Candidate preview summary models."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from ..batch.operation_candidate_types import OperationCandidatePreview
from ..core.buffer import LineBuffer
from ..i18n import _
from . import candidate_preview_snippets

_CANDIDATE_OVERVIEW_CONTEXT_LINES = 2
_CANDIDATE_OVERVIEW_MAX_LINES = 9


@dataclass(frozen=True)
class CandidateTargetSummary:
    label: str
    title: str
    lines: tuple[candidate_preview_snippets.CandidateSnippetLine, ...]
    ambiguity_line_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class AmbiguityBlockContext:
    relation: str
    line_range: tuple[int, int]
    description: str


def candidate_overview_subject(
    previews: tuple[OperationCandidatePreview, ...],
) -> tuple[str, str]:
    ambiguous_targets: list[str] = []
    for target_name in ("worktree", "index"):
        for preview in previews:
            target = next(
                (target for target in preview.targets if target.target == target_name),
                None,
            )
            if target is not None and target.resolution_count > 1:
                ambiguous_targets.append(target_name)
                break

    if not ambiguous_targets:
        ambiguous_targets = [
            target_name
            for target_name in ("worktree", "index")
            if any(
                target.target == target_name
                for preview in previews
                for target in preview.targets
            )
        ]

    labels = [
        candidate_target_subject_label(target_name)
        for target_name in ambiguous_targets
    ]
    if len(labels) == 1:
        return labels[0], _("has")
    if len(labels) == 2:
        return _("{first} and {second}").format(
            first=labels[0],
            second=labels[1],
        ), _("have")
    return _("target files"), _("have")


def candidate_target_label(target_name: str) -> str:
    if target_name == "index":
        return _("Index")
    return _("Working tree")


def candidate_target_subject_label(target_name: str) -> str:
    if target_name == "index":
        return _("index")
    return _("working tree")


def summarize_ambiguity_block(lines: list[str]) -> str:
    if not lines:
        return _("ambiguous block")
    if len(lines) == 1:
        text = candidate_preview_snippets.shorten_candidate_overview_text(
            lines[0],
            36,
        )
        if text:
            return f'"{text}"'
        return _("an empty line")

    first = candidate_preview_snippets.shorten_candidate_overview_text(lines[0], 24)
    last = candidate_preview_snippets.shorten_candidate_overview_text(lines[-1], 24)
    if first and last:
        return f'"{first} … {last}"'
    return _("{count} lines").format(count=len(lines))


def candidate_target_summary(target) -> CandidateTargetSummary:
    before_lines = _decode_overview_lines(target.before_buffer)
    after_lines = _decode_overview_lines(target.after_buffer)
    opcode = _first_changed_opcode(before_lines, after_lines)
    label = candidate_target_label(target.target)
    if opcode is None:
        return CandidateTargetSummary(label=label, title=_("No text changes"), lines=())

    tag, before_start, before_end, after_start, after_end = opcode
    ambiguity_context = (
        _delete_ambiguity_block_context(
            before_lines,
            before_start,
            before_end,
            target.ambiguity_target_line_range,
        )
        if tag == "delete"
        else None
    )
    ambiguity_line_range = (
        None if ambiguity_context is None else ambiguity_context.line_range
    )
    return CandidateTargetSummary(
        label=label,
        title=_overview_action_title(
            tag,
            before_lines,
            after_lines,
            before_start,
            before_end,
            after_start,
            after_end,
            ambiguity_context,
        ),
        lines=_overview_snippet_lines(
            before_lines,
            after_lines,
            before_start,
            before_end,
            after_start,
            after_end,
            ambiguity_line_range,
        ),
        ambiguity_line_range=ambiguity_line_range,
    )


def candidate_summary_key(
    summary: CandidateTargetSummary,
) -> tuple[str, tuple[str, ...]]:
    return summary.title, candidate_preview_snippets.plain_candidate_snippet_lines(
        summary.lines,
    )


def common_candidate_target_indexes(
    previews: tuple[OperationCandidatePreview, ...],
    candidate_summaries: list[list[CandidateTargetSummary]],
) -> tuple[int, ...]:
    if not previews or not previews[0].targets:
        return ()

    common_indexes: list[int] = []
    for target_index, first_target in enumerate(previews[0].targets):
        if first_target.resolution_count > 1:
            continue
        first_summary = candidate_summaries[0][target_index]
        first_key = candidate_summary_key(first_summary)
        is_common = True
        for preview, summaries in zip(previews[1:], candidate_summaries[1:]):
            if target_index >= len(preview.targets):
                is_common = False
                break
            target = preview.targets[target_index]
            if target.target != first_target.target or target.resolution_count > 1:
                is_common = False
                break
            if candidate_summary_key(summaries[target_index]) != first_key:
                is_common = False
                break
        if is_common:
            common_indexes.append(target_index)
    return tuple(common_indexes)


def _decode_overview_lines(buffer: LineBuffer) -> list[str]:
    text = buffer.to_bytes().decode("utf-8", errors="surrogateescape")
    return text.splitlines()


def _summarize_overview_lines(lines: list[str]) -> str:
    if not lines:
        return _("nothing")
    if len(lines) == 1:
        text = candidate_preview_snippets.shorten_candidate_overview_text(
            lines[0],
            36,
        )
        if text:
            return f'"{text}"'
        return _("an empty line")
    return _("{count} lines").format(count=len(lines))


def _delete_ambiguity_block_context(
    before_lines: list[str],
    before_start: int,
    before_end: int,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> AmbiguityBlockContext | None:
    if ambiguity_target_line_range is None:
        return None

    removed_lines = before_lines[before_start:before_end]
    removed_len = len(removed_lines)
    if removed_len == 0:
        return None

    span_start, span_end = ambiguity_target_line_range
    current_start = before_start + 1
    current_end = before_end
    if current_start < span_start or current_end > span_end:
        return None

    if current_start == span_start and current_end < span_end:
        block_start = current_end + 1
        block_end = span_end
        alternate_start = span_end - removed_len + 1
        if (
            alternate_start >= block_start
            and before_lines[alternate_start - 1:span_end] == removed_lines
        ):
            block_end = alternate_start - 1
        relation = "before"
    elif current_end == span_end and current_start > span_start:
        block_start = span_start
        block_end = current_start - 1
        alternate_end = span_start + removed_len - 1
        if (
            alternate_end <= block_end
            and before_lines[span_start - 1:alternate_end] == removed_lines
        ):
            block_start = alternate_end + 1
        relation = "after"
    else:
        return None

    if block_start > block_end:
        return None

    block_lines = before_lines[block_start - 1:block_end]
    return AmbiguityBlockContext(
        relation=relation,
        line_range=(block_start, block_end),
        description=summarize_ambiguity_block(block_lines),
    )


def _first_changed_opcode(
    before_lines: list[str],
    after_lines: list[str],
) -> tuple[str, int, int, int, int] | None:
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag != "equal":
            return tag, before_start, before_end, after_start, after_end
    return None


def _nearby_context_summary(
    before_lines: list[str],
    before_start: int,
    before_end: int,
    changed_lines: list[str],
) -> str:
    changed_text = {line.strip() for line in changed_lines if line.strip()}
    candidates = []
    if before_start > 0:
        candidates.append(before_lines[before_start - 1])
    if before_end < len(before_lines):
        candidates.append(before_lines[before_end])

    for line in candidates:
        text = candidate_preview_snippets.shorten_candidate_overview_text(line, 36)
        if text and text not in changed_text:
            return _(' near "{context}"').format(context=text)

    for line in candidates:
        text = candidate_preview_snippets.shorten_candidate_overview_text(line, 36)
        if text:
            return _(' near "{context}"').format(context=text)
    return ""


def _overview_action_title(
    tag: str,
    before_lines: list[str],
    after_lines: list[str],
    before_start: int,
    before_end: int,
    after_start: int,
    after_end: int,
    ambiguity_context: AmbiguityBlockContext | None,
) -> str:
    removed = before_lines[before_start:before_end]
    added = after_lines[after_start:after_end]
    changed = removed or added
    placement = ""
    if ambiguity_context is not None:
        if ambiguity_context.relation == "before":
            placement = _(" before {block}").format(
                block=ambiguity_context.description,
            )
        elif ambiguity_context.relation == "after":
            placement = _(" after {block}").format(
                block=ambiguity_context.description,
            )
    if not placement:
        placement = _nearby_context_summary(
            before_lines,
            before_start,
            before_end,
            changed,
        )

    if tag == "delete":
        return _("Remove {text}{placement}").format(
            text=_summarize_overview_lines(removed),
            placement=placement,
        )
    if tag == "insert":
        return _("Add {text}{placement}").format(
            text=_summarize_overview_lines(added),
            placement=placement,
        )
    return _("Replace {old} with {new}{placement}").format(
        old=_summarize_overview_lines(removed),
        new=_summarize_overview_lines(added),
        placement=placement,
    )


def _append_overview_line(
    lines: list[candidate_preview_snippets.CandidateSnippetLine],
    *,
    line_number: int,
    marker: str,
    text: str,
    highlight: bool = False,
) -> None:
    lines.append(
        candidate_preview_snippets.CandidateSnippetLine(
            line_number,
            marker,
            text,
            highlight,
        )
    )


def _overview_snippet_lines(
    before_lines: list[str],
    after_lines: list[str],
    before_start: int,
    before_end: int,
    after_start: int,
    after_end: int,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> tuple[candidate_preview_snippets.CandidateSnippetLine, ...]:
    lines: list[candidate_preview_snippets.CandidateSnippetLine] = []

    context_start = max(0, before_start - _CANDIDATE_OVERVIEW_CONTEXT_LINES)
    context_end = min(len(before_lines), before_end + _CANDIDATE_OVERVIEW_CONTEXT_LINES)

    for index in range(context_start, before_start):
        _append_overview_line(
            lines,
            line_number=index + 1,
            marker=" ",
            text=before_lines[index],
            highlight=candidate_preview_snippets.candidate_line_in_range(
                index + 1,
                ambiguity_target_line_range,
            ),
        )

    for index in range(before_start, before_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            marker="-",
            text=before_lines[index],
        )

    for index in range(after_start, after_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            marker="+",
            text=after_lines[index],
        )

    for index in range(before_end, context_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            marker=" ",
            text=before_lines[index],
            highlight=candidate_preview_snippets.candidate_line_in_range(
                index + 1,
                ambiguity_target_line_range,
            ),
        )

    if len(lines) > _CANDIDATE_OVERVIEW_MAX_LINES:
        return tuple(
            lines[:_CANDIDATE_OVERVIEW_MAX_LINES]
            + [candidate_preview_snippets.CandidateSnippetLine(None, " ", "...")]
        )
    return tuple(lines)
