"""Candidate preview output rendering."""

from __future__ import annotations

import difflib
import json
import shlex
from dataclasses import dataclass

from ..batch.operation_candidates import (
    OperationCandidatePreview,
    render_candidate_buffer_diff,
)
from ..core.buffer import LineBuffer
from ..core.diff_parser import build_line_changes_from_patch_lines
from ..i18n import _
from ..utils.paths import get_context_lines
from .colors import Colors


_CANDIDATE_OVERVIEW_CONTEXT_LINES = 2
_CANDIDATE_OVERVIEW_MAX_LINES = 9
_CANDIDATE_OVERVIEW_MAX_LINE_WIDTH = 64
_CANDIDATE_OVERVIEW_MAX_CANDIDATES = 10
_CANDIDATE_GUTTER_SEPARATOR = "│"


def _candidate_selector_text(
    batch_name: str,
    operation: str,
    ordinal: int | None = None,
) -> str:
    if ordinal is None:
        return f"{batch_name}:{operation}"
    return f"{batch_name}:{operation}:{ordinal}"


def _show_candidate_command(preview: OperationCandidatePreview, ordinal: int | None = None) -> str:
    return "git-stage-batch show --from {selector} --file {file}".format(
        selector=_candidate_selector_text(
            preview.batch_name,
            preview.operation,
            ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )


def _execute_candidate_command(preview: OperationCandidatePreview) -> str:
    return "git-stage-batch {command} --from {selector} --file {file}".format(
        command=preview.operation,
        selector=_candidate_selector_text(
            preview.batch_name,
            preview.operation,
            preview.ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )


def _candidate_overview_subject(
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
        _("working tree") if target_name == "worktree" else _("index")
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


def _candidate_choice_count(count: int) -> str:
    if count == 1:
        return _("1 choice")
    return _("{count} choices").format(count=count)


def _candidate_operation_past_tense(operation: str) -> str:
    if operation == "include":
        return _("included")
    return _("applied")


def _print_candidate_rule() -> None:
    rule = "─" * 78
    if Colors.enabled():
        print(f"{Colors.GRAY}{rule}{Colors.RESET}")
    else:
        print(rule)


def _print_candidate_header(status: str, *, note: str | None) -> None:
    if Colors.enabled():
        print(f"{Colors.BOLD}{status}{Colors.RESET}")
    else:
        print(status)
    if note:
        if Colors.enabled():
            print(f"{Colors.GRAY}{_('Note: {note}').format(note=note)}{Colors.RESET}")
        else:
            print(_("Note: {note}").format(note=note))
    _print_candidate_rule()


def _print_candidate_overview_header(
    first: OperationCandidatePreview,
    *,
    note: str | None,
) -> None:
    status = "  ·  ".join(
        (
            first.file_path,
            first.batch_name,
            _("{operation} candidates").format(operation=first.operation),
            _candidate_choice_count(first.count),
        )
    )
    _print_candidate_header(status, note=note)


def _print_candidate_detail_header(
    preview: OperationCandidatePreview,
    *,
    note: str | None,
) -> None:
    status = "  ·  ".join(
        (
            preview.file_path,
            preview.batch_name,
            _("{operation} candidate {ordinal}/{count}").format(
                operation=preview.operation,
                ordinal=preview.ordinal,
                count=preview.count,
            ),
        )
    )
    _print_candidate_header(status, note=note)


@dataclass(frozen=True)
class _CandidateTargetSummary:
    label: str
    title: str
    lines: tuple["_CandidateSnippetLine", ...]
    ambiguity_line_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class _AmbiguityBlockContext:
    relation: str
    line_range: tuple[int, int]
    description: str


@dataclass(frozen=True)
class _CandidateSnippetLine:
    line_number: int | None
    marker: str
    text: str
    highlight: bool = False

    def plain(self, *, width: int) -> str:
        line_number = " " * width if self.line_number is None else f"{self.line_number:>{width}}"
        return (
            f"{line_number}{_CANDIDATE_GUTTER_SEPARATOR} "
            f"{self.marker}{_shorten_overview_text(self.text)}"
        )


def _decode_overview_lines(buffer: LineBuffer) -> list[str]:
    text = buffer.to_bytes().decode("utf-8", errors="surrogateescape")
    return text.splitlines()


def _shorten_overview_text(text: str, max_width: int = _CANDIDATE_OVERVIEW_MAX_LINE_WIDTH) -> str:
    compact = text.strip()
    if len(compact) <= max_width:
        return compact
    if max_width <= 3:
        return compact[:max_width]
    return compact[: max_width - 3] + "..."


def _summarize_overview_lines(lines: list[str]) -> str:
    if not lines:
        return _("nothing")
    if len(lines) == 1:
        text = _shorten_overview_text(lines[0], 36)
        if text:
            return f'"{text}"'
        return _("an empty line")
    return _("{count} lines").format(count=len(lines))


def _summarize_ambiguity_block(lines: list[str]) -> str:
    if not lines:
        return _("ambiguous block")
    if len(lines) == 1:
        text = _shorten_overview_text(lines[0], 36)
        if text:
            return f'"{text}"'
        return _("an empty line")

    first = _shorten_overview_text(lines[0], 24)
    last = _shorten_overview_text(lines[-1], 24)
    if first and last:
        return f'"{first} … {last}"'
    return _("{count} lines").format(count=len(lines))


def _delete_ambiguity_block_context(
    before_lines: list[str],
    before_start: int,
    before_end: int,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> _AmbiguityBlockContext | None:
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
    return _AmbiguityBlockContext(
        relation=relation,
        line_range=(block_start, block_end),
        description=_summarize_ambiguity_block(block_lines),
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
        text = _shorten_overview_text(line, 36)
        if text and text not in changed_text:
            return _(' near "{context}"').format(context=text)

    for line in candidates:
        text = _shorten_overview_text(line, 36)
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
    ambiguity_context: _AmbiguityBlockContext | None,
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
        placement = _nearby_context_summary(before_lines, before_start, before_end, changed)

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
    lines: list[_CandidateSnippetLine],
    *,
    line_number: int,
    marker: str,
    text: str,
    highlight: bool = False,
) -> None:
    lines.append(_CandidateSnippetLine(line_number, marker, text, highlight))


def _snippet_line_width(lines: tuple[_CandidateSnippetLine, ...]) -> int:
    numbered_lines = [line for line in lines if line.line_number is not None]
    if not numbered_lines:
        return 1
    return max(len(str(line.line_number)) for line in numbered_lines)


def _style_candidate_snippet_line(line: _CandidateSnippetLine, *, width: int) -> str:
    plain = line.plain(width=width)
    if not Colors.enabled():
        return plain

    line_number = " " * width if line.line_number is None else f"{line.line_number:>{width}}"
    gutter = f"{line_number}{_CANDIDATE_GUTTER_SEPARATOR} "
    body = f"{line.marker}{_shorten_overview_text(line.text)}"
    if line.highlight:
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.REVERSE}{Colors.GRAY}{body}{Colors.RESET}"
    if line.marker == "+":
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.GREEN}{body}{Colors.RESET}"
    if line.marker == "-":
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.RED}{body}{Colors.RESET}"
    return f"{Colors.GRAY}{gutter}{Colors.RESET}{body}"


def _plain_candidate_snippet_lines(
    lines: tuple[_CandidateSnippetLine, ...],
) -> tuple[str, ...]:
    width = _snippet_line_width(lines)
    return tuple(line.plain(width=width) for line in lines)


def _line_in_range(
    line_number: int | None,
    line_range: tuple[int, int] | None,
) -> bool:
    if line_number is None or line_range is None:
        return False
    start, end = line_range
    return start <= line_number <= end


def _candidate_diff_hunks(diff_text: str) -> tuple[tuple[bytes, ...], ...]:
    headers: list[bytes] = []
    current_hunk: list[bytes] = []
    hunks: list[tuple[bytes, ...]] = []
    for line in diff_text.splitlines(keepends=True):
        line_bytes = line.encode("utf-8", errors="surrogateescape")
        if line_bytes.startswith((b"--- ", b"+++ ")):
            headers.append(line_bytes)
            continue
        if line_bytes.startswith(b"@@ "):
            if current_hunk:
                hunks.append(tuple(headers + current_hunk))
            current_hunk = [line_bytes]
            continue
        if current_hunk:
            current_hunk.append(line_bytes)

    if current_hunk:
        hunks.append(tuple(headers + current_hunk))
    return tuple(hunks)


def _candidate_line_number(line) -> int | None:
    if line.kind == "+":
        return line.new_line_number
    return line.old_line_number if line.old_line_number is not None else line.new_line_number


def _print_candidate_line_changes(
    line_changes,
    *,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> None:
    use_color = Colors.enabled()
    header = line_changes.header
    header_part = f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"
    if use_color:
        print(f"{Colors.BOLD}{line_changes.path}{Colors.RESET} :: {Colors.CYAN}{header_part}{Colors.RESET}")
    else:
        print(f"{line_changes.path} :: {header_part}")

    line_numbers = [
        line_number
        for line_number in (_candidate_line_number(line) for line in line_changes.lines)
        if line_number is not None
    ]
    width = max((len(str(line_number)) for line_number in line_numbers), default=1)

    for line in line_changes.lines:
        line_number = _candidate_line_number(line)
        gutter_number = " " * width if line_number is None else f"{line_number:>{width}}"
        gutter = f"{gutter_number}{_CANDIDATE_GUTTER_SEPARATOR} "
        body = f"{line.kind}{line.display_text()}"

        if not use_color:
            print(f"{gutter}{body}")
            continue

        styled_gutter = f"{Colors.GRAY}{gutter}{Colors.RESET}"
        if line.kind == "+":
            print(f"{styled_gutter}{Colors.GREEN}{body}{Colors.RESET}")
        elif line.kind == "-":
            print(f"{styled_gutter}{Colors.RED}{body}{Colors.RESET}")
        elif _line_in_range(line_number, ambiguity_target_line_range):
            print(f"{styled_gutter}{Colors.REVERSE}{Colors.GRAY}{body}{Colors.RESET}")
        else:
            print(f"{styled_gutter}{body}")


def _print_candidate_buffer_diff(
    file_path: str,
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    context_lines: int,
    ambiguity_target_line_range: tuple[int, int] | None,
    leading_blank: bool = False,
) -> bool:
    diff_text = render_candidate_buffer_diff(
        file_path,
        before_buffer,
        after_buffer,
        label_before="a",
        label_after="b",
        context_lines=context_lines,
    )
    if not diff_text:
        return False

    if leading_blank:
        print()

    hunks = _candidate_diff_hunks(diff_text)
    if not hunks:
        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
        return True

    for index, hunk in enumerate(hunks):
        if index:
            print()
        line_changes = build_line_changes_from_patch_lines(hunk)
        _print_candidate_line_changes(
            line_changes,
            ambiguity_target_line_range=ambiguity_target_line_range,
        )
    return True


def _overview_snippet_lines(
    before_lines: list[str],
    after_lines: list[str],
    before_start: int,
    before_end: int,
    after_start: int,
    after_end: int,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> tuple[_CandidateSnippetLine, ...]:
    lines: list[_CandidateSnippetLine] = []

    context_start = max(0, before_start - _CANDIDATE_OVERVIEW_CONTEXT_LINES)
    context_end = min(len(before_lines), before_end + _CANDIDATE_OVERVIEW_CONTEXT_LINES)

    for index in range(context_start, before_start):
        _append_overview_line(
            lines,
            line_number=index + 1,
            marker=" ",
            text=before_lines[index],
            highlight=_line_in_range(index + 1, ambiguity_target_line_range),
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
            highlight=_line_in_range(index + 1, ambiguity_target_line_range),
        )

    if len(lines) > _CANDIDATE_OVERVIEW_MAX_LINES:
        return tuple(
            lines[:_CANDIDATE_OVERVIEW_MAX_LINES]
            + [_CandidateSnippetLine(None, " ", "...")]
        )
    return tuple(lines)


def _summarize_candidate_target(target) -> _CandidateTargetSummary:
    before_lines = _decode_overview_lines(target.before_buffer)
    after_lines = _decode_overview_lines(target.after_buffer)
    opcode = _first_changed_opcode(before_lines, after_lines)
    label = _("Index") if target.target == "index" else _("Working tree")
    if opcode is None:
        return _CandidateTargetSummary(label=label, title=_("No text changes"), lines=())

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
    return _CandidateTargetSummary(
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


def _candidate_summary_key(summary: _CandidateTargetSummary) -> tuple[str, tuple[str, ...]]:
    return summary.title, _plain_candidate_snippet_lines(summary.lines)


def _common_candidate_target_indexes(
    previews: tuple[OperationCandidatePreview, ...],
    candidate_summaries: list[list[_CandidateTargetSummary]],
) -> tuple[int, ...]:
    if not previews or not previews[0].targets:
        return ()

    common_indexes: list[int] = []
    for target_index, first_target in enumerate(previews[0].targets):
        if first_target.resolution_count > 1:
            continue
        first_summary = candidate_summaries[0][target_index]
        first_key = _candidate_summary_key(first_summary)
        is_common = True
        for preview, summaries in zip(previews[1:], candidate_summaries[1:]):
            if target_index >= len(preview.targets):
                is_common = False
                break
            target = preview.targets[target_index]
            if target.target != first_target.target or target.resolution_count > 1:
                is_common = False
                break
            if _candidate_summary_key(summaries[target_index]) != first_key:
                is_common = False
                break
        if is_common:
            common_indexes.append(target_index)
    return tuple(common_indexes)


def _print_candidate_summary_block(
    summary: _CandidateTargetSummary,
    *,
    indent: str,
) -> None:
    width = _snippet_line_width(summary.lines)
    for line in summary.lines:
        print(f"{indent}{_style_candidate_snippet_line(line, width=width)}")


def _print_common_candidate_target_blocks(
    previews: tuple[OperationCandidatePreview, ...],
    candidate_summaries: list[list[_CandidateTargetSummary]],
    common_target_indexes: tuple[int, ...],
) -> None:
    if not common_target_indexes:
        return

    first_summaries = candidate_summaries[0]
    for target_index in common_target_indexes:
        target = previews[0].targets[target_index]
        summary = first_summaries[target_index]
        label = _("Index") if target.target == "index" else _("Working tree")
        print(
            _("{target} update, same for all candidates: {summary}").format(
                target=label,
                summary=summary.title,
            )
        )
        _print_candidate_summary_block(summary, indent="   ")
        print()


def render_operation_candidate_overview(
    previews: tuple[OperationCandidatePreview, ...],
    *,
    porcelain: bool,
    note: str | None = None,
) -> tuple[OperationCandidatePreview, ...]:
    first = previews[0]
    candidate_summaries = [
        [
            _summarize_candidate_target(target)
            for target in preview.targets
        ]
        for preview in previews
    ]

    if porcelain:
        print(json.dumps({
            "status": "candidates",
            "changed": False,
            "batch": first.batch_name,
            "selector": {
                "operation": first.operation,
                "count": len(previews),
            },
            "scope": {
                "file": first.file_path,
            },
            "candidates": [
                {
                    "ordinal": preview.ordinal,
                    "selector": _candidate_selector_text(
                        preview.batch_name,
                        preview.operation,
                        preview.ordinal,
                    ),
                    "commands": {
                        "preview": (
                            "git-stage-batch show --from {selector} --file {file}".format(
                                selector=_candidate_selector_text(
                                    preview.batch_name,
                                    preview.operation,
                                    preview.ordinal,
                                ),
                                file=shlex.quote(preview.file_path),
                            )
                        ),
                        "execute": (
                            _execute_candidate_command(preview)
                        ),
                    },
                    "targets": [
                        {
                            "target": target.target,
                            "summary": summary.title,
                            "context": list(_plain_candidate_snippet_lines(summary.lines)),
                        }
                        for target, summary in zip(preview.targets, summaries)
                    ],
                }
                for preview, summaries in zip(previews, candidate_summaries)
            ],
        }, sort_keys=True))
        return previews

    targets, verb = _candidate_overview_subject(previews)
    _print_candidate_overview_header(
        first,
        note=note,
    )
    print(
        _("The {targets} {verb} changed in an ambiguous way since this batch was created.").format(
            targets=targets,
            verb=verb,
        )
    )
    print(
        _("The batch can be {operation} in more than one way:").format(
            operation=_candidate_operation_past_tense(first.operation),
        )
    )
    print()

    shown_previews = previews[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    shown_summaries = candidate_summaries[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    common_target_indexes = _common_candidate_target_indexes(previews, candidate_summaries)

    for preview, summaries in zip(shown_previews, shown_summaries):
        visible_summaries = [
            (target, summary)
            for target_index, (target, summary) in enumerate(zip(preview.targets, summaries))
            if target_index not in common_target_indexes
        ]
        candidate_header = _("Candidate {ordinal}/{count}").format(
            ordinal=preview.ordinal,
            count=preview.count,
        )
        if len(visible_summaries) == 1:
            _target, summary = visible_summaries[0]
            print(f"{candidate_header}   {summary.title}")
            _print_candidate_summary_block(summary, indent="   ")
        else:
            print(candidate_header)
            for target, summary in visible_summaries:
                label = _("Index") if target.target == "index" else _("Working tree")
                print(f"   {label}: {summary.title}")
                _print_candidate_summary_block(summary, indent="      ")
        action = _("Apply") if preview.operation == "apply" else _("Include")
        print(f"   {_('Preview this candidate:')}")
        print(f"     {_show_candidate_command(preview, preview.ordinal)}")
        print(f"   {_('{action} this candidate:').format(action=action)}")
        print(f"     {_execute_candidate_command(preview)}")
        print()

    if len(previews) > len(shown_previews):
        remaining = len(previews) - len(shown_previews)
        print(
            _("... {count} more candidates. Preview another candidate with {selector}:N.").format(
                count=remaining,
                selector=_candidate_selector_text(first.batch_name, first.operation),
            )
        )
        print()
    _print_common_candidate_target_blocks(
        previews,
        candidate_summaries,
        common_target_indexes,
    )
    return tuple(shown_previews)


def render_operation_candidate(
    preview: OperationCandidatePreview,
    *,
    porcelain: bool,
    note: str | None,
) -> None:
    if porcelain:
        print(json.dumps({
            "status": "candidate",
            "changed": False,
            "batch": preview.batch_name,
            "selector": {
                "operation": preview.operation,
                "ordinal": preview.ordinal,
                "count": preview.count,
                "id": preview.candidate_id,
            },
            "scope": {
                "file": preview.file_path,
            },
            "targets": [
                {
                    "target": target.target,
                    "file": target.file_path,
                    "summary": target.summary,
                    "resolution_ordinal": target.resolution_ordinal,
                    "resolution_count": target.resolution_count,
                }
                for target in preview.targets
            ],
        }, sort_keys=True))
        return

    _print_candidate_detail_header(preview, note=note)

    context_lines = get_context_lines()
    for target_index, target in enumerate(preview.targets):
        summary = _summarize_candidate_target(target)
        has_multiple_targets = len(preview.targets) > 1
        if has_multiple_targets:
            if target_index > 0:
                print()
            target_label = _("Index") if target.target == "index" else _("Working tree")
            print(
                _("{target} update: {summary}").format(
                    target=target_label,
                    summary=summary.title,
                ),
            )
        else:
            print(summary.title)
        _print_candidate_buffer_diff(
            preview.file_path,
            target.before_buffer,
            target.after_buffer,
            context_lines=context_lines,
            ambiguity_target_line_range=summary.ambiguity_line_range,
            leading_blank=not has_multiple_targets,
        )

    print()
    action = _("Apply") if preview.operation == "apply" else _("Include")
    print(
        _("{action} this candidate:\n  {command}").format(
            action=action,
            command=_execute_candidate_command(preview),
        ),
    )
    print()
    print(_("Review candidates:"))
    print(_("  overview: {command}").format(
        command=_show_candidate_command(preview),
    ))
    if preview.ordinal > 1:
        print(_("  previous: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal - 1),
        ))
    if preview.ordinal < preview.count:
        print(_("  next: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal + 1),
        ))
