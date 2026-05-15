"""Persisted safety state for page-aware file reviews."""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..core.actionable_changes import ActionableSelectionReason
from ..core.line_selection import LineRanges, LineSelection, parse_line_selection_ranges
from ..core.models import ReviewActionGroup
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..editor import EditorBuffer
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import (
    get_index_snapshot_file_path,
    get_last_file_review_state_file_path,
    get_working_tree_snapshot_file_path,
)
from .hunk_tracking import SelectedChangeKind, get_selected_change_file_path, read_selected_change_kind


class ReviewSource(str, Enum):
    """Source of the selected file review."""

    FILE_VS_HEAD = "file-vs-head"
    UNSTAGED = "unstaged"
    BATCH = "batch"


class FileReviewAction(str, Enum):
    """Commands that may act on a file-review selection."""

    INCLUDE = "include"
    SKIP = "skip"
    DISCARD = "discard"
    INCLUDE_TO_BATCH = "include-to-batch"
    DISCARD_TO_BATCH = "discard-to-batch"
    INCLUDE_FROM_BATCH = "include-from-batch"
    DISCARD_FROM_BATCH = "discard-from-batch"
    APPLY_FROM_BATCH = "apply-from-batch"
    RESET_FROM_BATCH = "reset-from-batch"


@dataclass(frozen=True)
class FileReviewSelectionState:
    """One actionable selection shown by a file review."""

    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    change_index: int
    first_page: int
    last_page: int
    reason: ActionableSelectionReason
    actions: tuple[FileReviewAction, ...]
    is_splittable: bool = False


@dataclass(frozen=True)
class FileReviewState:
    """Persisted identity and safety state for the last file review."""

    source: ReviewSource
    batch_name: str | None
    file_path: str
    page_spec: str
    shown_pages: tuple[int, ...]
    page_count: int
    entire_file_shown: bool
    selections: tuple[FileReviewSelectionState, ...]
    selected_change_kind: SelectedChangeKind
    selected_file_fingerprint: str
    diff_fingerprint: str


@dataclass(frozen=True)
class ImplicitLiveToBatchFileActionResult:
    """Validated target for `--to --file` with no path."""

    reviewed_file: str | None = None
    review_state: FileReviewState | None = None
    should_stop: bool = False


@dataclass(frozen=True)
class ActionScopeResolution:
    """Resolved file-review scope for a command prologue."""

    file: str | None
    review_state: FileReviewState | None = None
    should_stop: bool = False


class ReviewScopedSelectionError(CommandError):
    """Raised when a pathless line action is not valid for the current review."""


def _coerce_review_source(source: ReviewSource | str) -> ReviewSource:
    return source if isinstance(source, ReviewSource) else ReviewSource(source)


def _coerce_review_action(action: FileReviewAction | str) -> FileReviewAction:
    return action if isinstance(action, FileReviewAction) else FileReviewAction(action)


def _json_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def _hash_file(path: Path) -> str | None:
    if not path.exists():
        return None

    digest = sha256()
    with EditorBuffer.from_path(path) as buffer:
        for chunk in buffer.byte_chunks():
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_selected_file_view(
    *,
    source: ReviewSource,
    batch_name: str | None,
    file_path: str,
    selected_change_kind: SelectedChangeKind,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
    line_changes=None,
) -> str:
    """Fingerprint the selected file view and its current line ID space."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    snapshots = {}
    for name, path in (
        ("index", get_index_snapshot_file_path()),
        ("working_tree", get_working_tree_snapshot_file_path()),
    ):
        snapshots[name] = _hash_file(path)
    return _json_hash(
        {
            "source": source,
            "batch_name": batch_name,
            "file_path": file_path,
            "selected_change_kind": selected_change_kind.value,
            "snapshots": snapshots,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
            "gutter_to_selection_id": gutter_to_selection_id,
            "actionable_selection_groups": actionable_selection_groups,
            "review_action_groups": [
                {
                    "display_ids": group.display_ids,
                    "selection_ids": group.selection_ids,
                    "actions": group.actions,
                    "reason": group.reason,
                }
                for group in (review_action_groups or ())
            ],
        }
    )


def compute_current_file_review_diff_fingerprint(file_path: str, line_changes=None) -> str:
    """Fingerprint the cached selected file diff for freshness checks."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    return _json_hash(
        {
            "file_path": file_path,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
        }
    )


def write_last_file_review_state(review_state: FileReviewState) -> None:
    """Persist the last file review state."""
    write_text_file_contents(
        get_last_file_review_state_file_path(),
        json.dumps(asdict(review_state), ensure_ascii=False, indent=0),
    )


def read_last_file_review_state() -> FileReviewState | None:
    """Read the last file review state, if present and valid."""
    path = get_last_file_review_state_file_path()
    if not path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(path))
        selections = tuple(
            FileReviewSelectionState(
                display_ids=tuple(selection["display_ids"]),
                selection_ids=tuple(selection["selection_ids"]),
                change_index=selection["change_index"],
                first_page=selection["first_page"],
                last_page=selection["last_page"],
                reason=ActionableSelectionReason(selection["reason"]),
                actions=tuple(FileReviewAction(action) for action in selection["actions"]),
                is_splittable=bool(selection["is_splittable"]),
            )
            for selection in data.get("selections", [])
        )
        return FileReviewState(
            source=ReviewSource(data["source"]),
            batch_name=data.get("batch_name"),
            file_path=data["file_path"],
            page_spec=data["page_spec"],
            shown_pages=tuple(data["shown_pages"]),
            page_count=data["page_count"],
            entire_file_shown=data["entire_file_shown"],
            selections=selections,
            selected_change_kind=SelectedChangeKind(data["selected_change_kind"]),
            selected_file_fingerprint=data["selected_file_fingerprint"],
            diff_fingerprint=data["diff_fingerprint"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        clear_last_file_review_state()
        return None


def clear_last_file_review_state() -> None:
    """Remove the last file review state."""
    get_last_file_review_state_file_path().unlink(missing_ok=True)


def clear_last_file_review_state_if_file_matches(file_path: str) -> None:
    """Remove the last file review state if it belongs to the given file."""
    review_state = read_last_file_review_state()
    if review_state is not None and review_state.file_path == file_path:
        clear_last_file_review_state()


def line_action_came_from_partial_review(review_state: FileReviewState | None) -> bool:
    """Return whether a line action was validated by a partial file review."""
    return review_state is not None and not review_state.entire_file_shown


def finish_review_scoped_line_action(
    review_state: FileReviewState | None,
    *,
    file_path: str | None = None,
) -> None:
    """Clear review state after a line action unless a partial review must guard follow-ups."""
    if line_action_came_from_partial_review(review_state):
        return
    if file_path is None:
        clear_last_file_review_state()
    else:
        clear_last_file_review_state_if_file_matches(file_path)


def _batch_from_action_command(
    command_name: str,
    batch_name: str,
    *,
    file_scope: bool,
    line_ids: str | None,
    extra_action_parts: tuple[str, ...] = (),
) -> str:
    parts = [command_name, "--from", shlex.quote(batch_name)]
    if file_scope:
        parts.append("--file")
    parts.extend(extra_action_parts)
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    return " ".join(parts)


def resolve_batch_source_action_scope(
    action: FileReviewAction | str,
    *,
    command_name: str,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
    extra_action_parts: tuple[str, ...] = (),
) -> ActionScopeResolution:
    """Resolve pathless and implicit-file batch actions against the last batch review."""
    from .hunk_tracking import (
        refuse_bare_action_after_file_list,
        refuse_bare_action_after_stale_batch_selection,
    )

    review_action = _coerce_review_action(action)
    if patterns is not None:
        return ActionScopeResolution(file=file)

    if file is None:
        action_command = _batch_from_action_command(
            command_name,
            batch_name,
            file_scope=False,
            line_ids=line_ids,
            extra_action_parts=extra_action_parts,
        )
        refuse_bare_action_after_file_list(
            action_command,
            open_command=f"git-stage-batch show --from {shlex.quote(batch_name)} --file PATH",
            source=ReviewSource.BATCH.value,
            batch_name=batch_name,
        )
        refuse_bare_action_after_stale_batch_selection(action_command, batch_name=batch_name)

        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=ReviewSource.BATCH,
                batch_name=batch_name,
            )
            return ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)

        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=ReviewSource.BATCH,
            batch_name=batch_name,
        )
        return ActionScopeResolution(
            file=review_state.file_path if review_state is not None else file,
            review_state=review_state,
        )

    if file == "":
        action_command = _batch_from_action_command(
            command_name,
            batch_name,
            file_scope=True,
            line_ids=line_ids,
            extra_action_parts=extra_action_parts,
        )
        refuse_bare_action_after_file_list(
            action_command,
            open_command=f"git-stage-batch show --from {shlex.quote(batch_name)} --file PATH",
            source=ReviewSource.BATCH.value,
            batch_name=batch_name,
        )
        refuse_bare_action_after_stale_batch_selection(action_command, batch_name=batch_name)

        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=ReviewSource.BATCH,
                batch_name=batch_name,
            )
            return ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)

        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=ReviewSource.BATCH,
            batch_name=batch_name,
        )
        return ActionScopeResolution(
            file=review_state.file_path if review_state is not None else file,
            review_state=review_state,
        )

    return ActionScopeResolution(file=file)


def selected_change_kind_matches_review_source(
    selected_kind: SelectedChangeKind | None,
    review_state: FileReviewState,
) -> bool:
    """Return whether the selected kind is compatible with the review source."""
    if review_state.source in (ReviewSource.FILE_VS_HEAD, ReviewSource.UNSTAGED):
        return selected_kind == SelectedChangeKind.FILE
    if review_state.source == ReviewSource.BATCH:
        return selected_kind in (
            SelectedChangeKind.BATCH_FILE,
            SelectedChangeKind.BATCH_BINARY,
            SelectedChangeKind.BATCH_GITLINK,
        )
    return False


def selected_change_matches_review_state(review_state: FileReviewState) -> bool:
    """Return whether selected state still matches the persisted review state."""
    selected_kind = read_selected_change_kind()
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        return False
    if get_selected_change_file_path() != review_state.file_path:
        return False
    if selected_kind is None:
        return False
    gutter_to_selection_id = None
    line_changes = None
    if review_state.source == ReviewSource.BATCH and review_state.batch_name is not None:
        from .hunk_tracking import render_batch_file_display

        rendered = render_batch_file_display(review_state.batch_name, review_state.file_path)
        if rendered is None:
            return False
        gutter_to_selection_id = (
            rendered.review_gutter_to_selection_id
            or rendered.gutter_to_selection_id
        )
        actionable_selection_groups = rendered.actionable_selection_groups
        review_action_groups = rendered.review_action_groups or None
        line_changes = rendered.line_changes
    else:
        from .hunk_tracking import snapshots_are_stale

        if snapshots_are_stale(review_state.file_path):
            return False
        actionable_selection_groups = None
        review_action_groups = None

    current_selected_fingerprint = fingerprint_selected_file_view(
        source=review_state.source,
        batch_name=review_state.batch_name,
        file_path=review_state.file_path,
        selected_change_kind=selected_kind,
        gutter_to_selection_id=gutter_to_selection_id,
        actionable_selection_groups=actionable_selection_groups,
        review_action_groups=review_action_groups,
        line_changes=line_changes,
    )
    if current_selected_fingerprint != review_state.selected_file_fingerprint:
        return False
    return (
        compute_current_file_review_diff_fingerprint(review_state.file_path, line_changes=line_changes)
        == review_state.diff_fingerprint
    )


def selected_batch_review_matches_reset_state(review_state: FileReviewState) -> bool:
    """Return whether a batch review still has stable reset IDs."""
    selected_kind = read_selected_change_kind()
    if review_state.source != ReviewSource.BATCH or review_state.batch_name is None:
        return False
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        return False
    if get_selected_change_file_path() != review_state.file_path:
        return False

    from .hunk_tracking import render_batch_file_display

    rendered = render_batch_file_display(review_state.batch_name, review_state.file_path)
    if rendered is None:
        return False
    if (
        compute_current_file_review_diff_fingerprint(
            review_state.file_path,
            line_changes=rendered.line_changes,
        )
        != review_state.diff_fingerprint
    ):
        return False

    current_reset_groups = [
        (group.display_ids, group.selection_ids)
        for group in rendered.review_action_groups
        if FileReviewAction.RESET_FROM_BATCH.value in group.actions
    ]
    persisted_reset_groups = {
        (selection.display_ids, selection.selection_ids)
        for selection in review_state.selections
        if FileReviewAction.RESET_FROM_BATCH in selection.actions
    }

    def can_cover(
        remaining_pairs: frozenset[tuple[int, int]],
    ) -> bool:
        if not remaining_pairs:
            return True
        first_pair = min(remaining_pairs)
        for display_ids, selection_ids in current_reset_groups:
            group_pairs = frozenset(zip(display_ids, selection_ids))
            if first_pair not in group_pairs:
                continue
            if not group_pairs.issubset(remaining_pairs):
                continue
            if can_cover(remaining_pairs - group_pairs):
                return True
        return False

    return all(
        can_cover(frozenset(zip(display_ids, selection_ids)))
        for display_ids, selection_ids in persisted_reset_groups
    )


def _review_state_matches_action(
    review_state: FileReviewState,
    action: FileReviewAction | str,
) -> bool:
    """Return whether a review is fresh for a specific action."""
    review_action = _coerce_review_action(action)
    if (
        review_state.source == ReviewSource.BATCH
        and review_action == FileReviewAction.RESET_FROM_BATCH
    ):
        return selected_batch_review_matches_reset_state(review_state)
    return selected_change_matches_review_state(review_state)


def _format_pages(pages: set[int]) -> str:
    from ..core.line_selection import format_line_ids

    return format_line_ids(sorted(pages))


def _format_line_ranges(selection: LineRanges) -> str:
    return selection.to_line_spec()


def _coerce_line_ranges(selection: LineSelection | Iterable[int]) -> LineRanges:
    if isinstance(selection, LineRanges):
        return selection
    return LineRanges.from_lines(selection)


@dataclass(frozen=True)
class _ReviewValidationGroup:
    display_ids: LineRanges
    is_splittable: bool


def shown_review_selections_for_action(
    review_state: FileReviewState,
    action: FileReviewAction | str,
) -> list[FileReviewSelectionState]:
    """Return actionable selections fully contained by the shown review pages."""
    review_action = _coerce_review_action(action)
    shown_pages = (
        set(range(1, review_state.page_count + 1))
        if review_state.entire_file_shown else
        set(review_state.shown_pages)
    )
    return [
        selection
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(shown_pages)
    ]


def fresh_batch_review_selections_for_action(
    batch_name: str,
    file_path: str,
    action: FileReviewAction | str,
) -> list[FileReviewSelectionState] | None:
    """Return shown review selections for a fresh matching batch review, if one is active."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if review_state.source != ReviewSource.BATCH:
        return None
    if review_state.batch_name != batch_name or review_state.file_path != file_path:
        return None
    review_action = _coerce_review_action(action)
    try:
        review_is_fresh = _review_state_matches_action(review_state, review_action)
    except Exception:
        review_is_fresh = False
    if not review_is_fresh:
        raise CommandError(
            _(
                "The file review for {file} no longer matches batch '{batch}'.\n"
                "Line IDs may no longer match.\n\n"
                "Run:\n"
                "  git-stage-batch show --from {batch} --file {file}"
            ).format(
                batch=shlex.quote(batch_name),
                file=shlex.quote(file_path),
            )
        )

    return shown_review_selections_for_action(review_state, action)


def _print_stale_or_mismatched_file_review_help(action: str, review_state: FileReviewState) -> None:
    show_command = _show_command_for_review_state(review_state)
    raise ReviewScopedSelectionError(
        _(
            "The file review for {file} no longer matches the selected file view.\n"
            "Line IDs may no longer match.\n\n"
            "Run:\n"
            "  {command}"
        ).format(file=review_state.file_path, command=show_command)
    )


def _quote(value: str) -> str:
    return shlex.quote(value)


def _show_command_for_review_state(review_state: FileReviewState, *, page: str | None = None) -> str:
    command = "git-stage-batch show"
    if review_state.source == ReviewSource.BATCH and review_state.batch_name is not None:
        command += f" --from {_quote(review_state.batch_name)}"
    command += f" --file {_quote(review_state.file_path)}"
    if page is not None:
        command += f" --page {page}"
    return command


def _line_action_command(
    action: FileReviewAction | str,
    review_state: FileReviewState,
    *,
    line_spec: str | None = None,
    whole_file: bool = False,
    pathless_line: bool = False,
) -> str | None:
    review_action = _coerce_review_action(action)
    action_value = review_action.value
    if review_action in (FileReviewAction.INCLUDE_TO_BATCH, FileReviewAction.DISCARD_TO_BATCH):
        return None
    if review_state.source == ReviewSource.BATCH:
        if review_action in (FileReviewAction.INCLUDE, FileReviewAction.INCLUDE_FROM_BATCH):
            action_value = FileReviewAction.INCLUDE.value
        elif review_action in (FileReviewAction.DISCARD, FileReviewAction.DISCARD_FROM_BATCH):
            action_value = FileReviewAction.DISCARD.value
        elif review_action == FileReviewAction.APPLY_FROM_BATCH:
            action_value = "apply"
        elif review_action == FileReviewAction.RESET_FROM_BATCH:
            action_value = "reset"
        else:
            return None
        command = f"git-stage-batch {action_value} --from {_quote(review_state.batch_name or '')}"
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


def refuse_live_action_for_batch_selection(action: FileReviewAction | str) -> bool:
    """Refuse bare live actions when the current selection came from a batch view."""
    review_action = _coerce_review_action(action)
    if read_selected_change_kind() not in (
        SelectedChangeKind.BATCH_FILE,
        SelectedChangeKind.BATCH_BINARY,
        SelectedChangeKind.BATCH_GITLINK,
    ):
        return False

    review_state = read_last_file_review_state()
    if review_state is not None:
        if review_state.source != ReviewSource.BATCH:
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
        if not selected_change_matches_review_state(review_state):
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)

        lines = [
            _("The selected file view for {file} came from batch '{batch}', not the live working tree.").format(
                file=review_state.file_path,
                batch=review_state.batch_name,
            )
        ]
        if not review_state.entire_file_shown:
            lines.extend(
                [
                    "",
                    _("To review all pages from the batch:"),
                    f"  {_show_command_for_review_state(review_state, page='all')}",
                ]
            )

        whole_file_command = _line_action_command(review_action, review_state, whole_file=True)
        if whole_file_command is not None:
            lines.extend(
                [
                    "",
                    _("To act on the batch file:"),
                    f"  {whole_file_command}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    _("Batch reviews do not support this action."),
                    _("If you meant to act on live working-tree changes, open a live file review:"),
                    f"  git-stage-batch show --file {_quote(review_state.file_path)}",
                ]
            )
        raise CommandError("\n".join(lines))

    file_path = get_selected_change_file_path() or _("the selected file")
    raise CommandError(
        _(
            "The selected file view for {file} came from a batch, not the live working tree.\n"
            "Show the batch file again and use `include --from` or `discard --from`,\n"
            "or open a live file review with:\n"
            "  git-stage-batch show --file {file}"
        ).format(file=file_path)
    )


def refuse_ambiguous_bare_action_after_partial_file_review(action: FileReviewAction | str) -> bool:
    """Refuse pathless whole-file actions after a partial file review."""
    review_action = _coerce_review_action(action)
    review_state = read_last_file_review_state()
    if review_state is None:
        return False

    selected_kind = read_selected_change_kind()
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        if selected_kind in (
            SelectedChangeKind.FILE,
            SelectedChangeKind.BATCH_FILE,
            SelectedChangeKind.BATCH_BINARY,
            SelectedChangeKind.BATCH_GITLINK,
        ):
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
        clear_last_file_review_state()
        return False

    if not _review_state_matches_action(review_state, review_action):
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)

    if review_state.entire_file_shown:
        return False

    shown = set(review_state.shown_pages)
    missing = set(range(1, review_state.page_count + 1)) - shown
    complete_selections = [
        selection
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(shown)
    ]
    selection_specs = [
        _format_pages(set(selection.display_ids))
        for selection in complete_selections
    ]

    lines = [
        _("Only pages {shown} of {count} of {file} were shown.").format(
            shown=_format_pages(shown),
            count=review_state.page_count,
            file=review_state.file_path,
        )
    ]
    if missing:
        lines.append(_("Pages {pages} were not shown.").format(pages=_format_pages(missing)))
    if selection_specs:
        line_command = _line_action_command(
            review_action, review_state, line_spec=",".join(selection_specs)
        )
        if line_command is not None:
            lines.extend(
                [
                    "",
                    _("To act on complete changes shown here:"),
                    f"  {line_command}",
                ]
            )
    lines.extend(
        [
            "",
            _("To review all pages:"),
            f"  {_show_command_for_review_state(review_state, page='all')}",
        ]
    )

    whole_file_command = _line_action_command(review_action, review_state, whole_file=True)
    if whole_file_command is not None:
        lines.extend(
            [
                "",
                _("To act on the whole file:"),
                f"  {whole_file_command}",
            ]
        )
    raise CommandError("\n".join(lines))


def resolve_review_file_for_bare_whole_file_action(
    action: FileReviewAction | str,
    *,
    source: ReviewSource | str,
    batch_name: str | None = None,
) -> str | None:
    """Return the reviewed file for a fresh full-file review, or refuse if partial."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return None

    if review_state.source != _coerce_review_source(source):
        return None
    if batch_name is not None and review_state.batch_name != batch_name:
        return None

    if refuse_ambiguous_bare_action_after_partial_file_review(action):
        return None
    if read_last_file_review_state() != review_state:
        return None
    return review_state.file_path


def validate_implicit_live_to_batch_file_action(
    action: FileReviewAction | str,
    action_command: str,
    line_id_specification: str | None,
) -> ImplicitLiveToBatchFileActionResult:
    """Validate `--to --file` with no path against the current live review.

    Returns the reviewed file for a full live-file review when the caller should
    use that explicit file path. The boolean is true when the caller should stop
    after a live-action guard handled the request.
    """
    from .hunk_tracking import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    review_action = _coerce_review_action(action)
    refuse_bare_action_after_file_list(action_command)
    refuse_bare_action_after_auto_advance_disabled(action_command)
    if line_id_specification is None:
        return ImplicitLiveToBatchFileActionResult(
            reviewed_file=resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=ReviewSource.FILE_VS_HEAD,
            ),
        )
    if refuse_live_action_for_batch_selection(review_action):
        return ImplicitLiveToBatchFileActionResult(should_stop=True)
    review_state = validate_pathless_review_line_action(
        review_action,
        line_id_specification,
        source=ReviewSource.FILE_VS_HEAD,
    )
    return ImplicitLiveToBatchFileActionResult(review_state=review_state)


def _live_to_batch_action_command(
    command_name: str,
    batch_name: str,
    *,
    file_scope: bool,
    line_ids: str | None,
) -> str:
    parts = [command_name, "--to", batch_name]
    if file_scope:
        parts.append("--file")
    if line_ids is not None:
        parts.extend(["--line", line_ids])
    return " ".join(parts)


def resolve_live_to_batch_action_scope(
    action: FileReviewAction | str,
    *,
    command_name: str,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
) -> ActionScopeResolution:
    """Resolve pathless and implicit-file live-to-batch actions against live reviews."""
    from .hunk_tracking import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    review_action = _coerce_review_action(action)
    if file is None:
        action_command = _live_to_batch_action_command(
            command_name,
            batch_name,
            file_scope=False,
            line_ids=line_ids,
        )
        refuse_bare_action_after_file_list(action_command)
        refuse_bare_action_after_auto_advance_disabled(action_command)
        if refuse_live_action_for_batch_selection(review_action):
            return ActionScopeResolution(file=file, should_stop=True)
        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=ReviewSource.FILE_VS_HEAD,
            )
            return ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)
        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=ReviewSource.FILE_VS_HEAD,
        )
        return ActionScopeResolution(file=file, review_state=review_state)

    if file == "":
        action_command = _live_to_batch_action_command(
            command_name,
            batch_name,
            file_scope=True,
            line_ids=line_ids,
        )
        action_result = validate_implicit_live_to_batch_file_action(
            review_action,
            action_command,
            line_ids,
        )
        if action_result.should_stop:
            return ActionScopeResolution(file=file, should_stop=True)
        return ActionScopeResolution(
            file=action_result.reviewed_file if action_result.reviewed_file is not None else file,
            review_state=action_result.review_state,
        )

    return ActionScopeResolution(file=file)


def resolve_live_line_action_scope(
    action: FileReviewAction | str,
    *,
    action_command: str,
    line_id_specification: str,
    file: str | None,
    source: ReviewSource | str | None = None,
    batch_name: str | None = None,
    validate_pathless_before_live_guard: bool = False,
) -> ActionScopeResolution:
    """Validate a pathless or implicit-file live line action against review state."""
    from .hunk_tracking import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    if file not in (None, ""):
        return ActionScopeResolution(file=file)

    review_action = _coerce_review_action(action)
    refuse_bare_action_after_file_list(action_command)
    refuse_bare_action_after_auto_advance_disabled(action_command)

    if file is None and validate_pathless_before_live_guard:
        review_state = validate_pathless_review_line_action(
            review_action,
            line_id_specification,
            source=source,
            batch_name=batch_name,
        )
        if refuse_live_action_for_batch_selection(review_action):
            return ActionScopeResolution(file=file, review_state=review_state, should_stop=True)
        return ActionScopeResolution(file=file, review_state=review_state)

    if refuse_live_action_for_batch_selection(review_action):
        return ActionScopeResolution(file=file, should_stop=True)

    review_state = validate_pathless_review_line_action(
        review_action,
        line_id_specification,
        source=source,
        batch_name=batch_name,
    )
    return ActionScopeResolution(file=file, review_state=review_state)


def validate_review_scoped_line_selection(
    requested_ids: LineSelection | Iterable[int],
    valid_selections: Iterable[FileReviewSelectionState],
) -> None:
    """Validate a union of complete actionable review selections."""
    requested_ranges = _coerce_line_ranges(requested_ids)
    groups: list[_ReviewValidationGroup] = []
    for selection in valid_selections:
        display_ids = LineRanges.from_lines(selection.display_ids)
        if display_ids:
            groups.append(
                _ReviewValidationGroup(
                    display_ids=display_ids,
                    is_splittable=selection.is_splittable,
                )
            )

    def can_cover(remaining_ids: LineRanges) -> bool:
        if not remaining_ids:
            return True
        first_id = remaining_ids.first()
        if first_id is None:
            return True
        for group in groups:
            if first_id not in group.display_ids:
                continue
            if group.is_splittable:
                selected_from_group = remaining_ids.intersection(group.display_ids)
                if can_cover(remaining_ids.difference(selected_from_group)):
                    return True
                continue
            if group.display_ids.difference(remaining_ids):
                continue
            if can_cover(remaining_ids.difference(group.display_ids)):
                return True
        return False

    if can_cover(requested_ranges):
        return

    matched_ids = LineRanges.empty()
    for group in groups:
        if group.is_splittable:
            matched_ids = matched_ids.union(requested_ranges.intersection(group.display_ids))
        elif not group.display_ids.difference(requested_ranges):
            matched_ids = matched_ids.union(group.display_ids)

    outside_ids = requested_ranges.difference(matched_ids)
    for group in groups:
        if group.is_splittable:
            continue
        overlap = outside_ids.intersection(group.display_ids)
        if overlap and overlap != group.display_ids:
            raise CommandError(
                _("Line selection #{requested} only partly selects a reviewed change.\nUse: --line {required}").format(
                    requested=_format_line_ranges(requested_ranges),
                    required=_format_line_ranges(group.display_ids),
                )
            )

    if outside_ids:
        raise CommandError(
            _("Line selection #{ids} is not valid from the current file review.").format(
                ids=_format_line_ranges(outside_ids),
            )
        )


def validate_pathless_review_line_action(
    action: FileReviewAction | str,
    line_id_specification: str,
    *,
    source: ReviewSource | str | None = None,
    batch_name: str | None = None,
) -> FileReviewState | None:
    """Validate pathless --line against the last file review."""
    review_action = _coerce_review_action(action)
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if source is not None and review_state.source != _coerce_review_source(source):
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
    if batch_name is not None and review_state.batch_name != batch_name:
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
    if not _review_state_matches_action(review_state, review_action):
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
    if (
        review_state.source == ReviewSource.BATCH
        and review_action in (
            FileReviewAction.INCLUDE,
            FileReviewAction.SKIP,
            FileReviewAction.DISCARD,
            FileReviewAction.INCLUDE_TO_BATCH,
            FileReviewAction.DISCARD_TO_BATCH,
        )
        and not any(review_action in selection.actions for selection in review_state.selections)
    ):
        lines = [
            _("The selected file view for {file} came from batch '{batch}', not the live working tree.").format(
                file=review_state.file_path,
                batch=review_state.batch_name,
            )
        ]
        line_command = _line_action_command(review_action, review_state, line_spec=line_id_specification)
        if line_command is not None:
            lines.extend(["", _("To act on the batch file:"), f"  {line_command}"])
        else:
            lines.extend(
                [
                    "",
                    _("Batch reviews do not support this action."),
                    _("If you meant to act on live working-tree changes, open a live file review:"),
                    f"  git-stage-batch show --file {_quote(review_state.file_path)}",
                ]
            )
        raise CommandError("\n".join(lines))

    try:
        requested_ids = parse_line_selection_ranges(line_id_specification)
    except ValueError as error:
        raise CommandError(str(error)) from error

    valid_selections = shown_review_selections_for_action(review_state, review_action)
    validate_review_scoped_line_selection(requested_ids, valid_selections)
    return review_state
