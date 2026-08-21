"""Canonical linear range selection for history refinement."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_repository import (
    get_git_common_directory_path,
    get_git_object_format,
)


@dataclass(frozen=True, slots=True)
class ResolvedHistoryRange:
    """Full object IDs for one non-empty linear source range."""

    object_format: str
    base_commit: str
    tip_commit: str
    movable_base: str
    commits_oldest_first: tuple[str, ...]


def _require_unmodified_object_graph() -> None:
    replacement_objects = run_git_command(
        ["replace", "--list"],
        requires_index_lock=False,
    ).stdout.splitlines()
    # The scan scope redirects GIT_GRAFT_FILE, so inspect the pre-scope
    # location directly to preserve the upfront rejection.
    grafts_override = os.environ.get("GIT_GRAFT_FILE")
    grafts_path = (
        Path(grafts_override)
        if grafts_override is not None
        else get_git_common_directory_path() / "info" / "grafts"
    )
    try:
        grafts_active = grafts_path.stat().st_size > 0
    except FileNotFoundError:
        grafts_active = False
    except OSError as error:
        raise CommandError(
            _("Could not inspect legacy grafts: {error}").format(
                error=terminal_safe_text(str(error))
            )
        ) from error
    if replacement_objects or grafts_active:
        raise CommandError(
            _(
                "Rewrite scanning refuses replace objects or legacy grafts because they "
                "change commit identity semantics."
            )
        )


def resolve_history_commit(revision: str) -> str:
    """Resolve one revision to a full commit object ID."""
    result = run_git_command(
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        requires_index_lock=False,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not resolved:
        raise CommandError(
            _("Invalid history revision: {revision}").format(
                revision=terminal_safe_text(revision)
            )
        )
    return resolved


def _default_base() -> str:
    upstream = run_git_command(
        ["rev-parse", "--verify", "@{upstream}^{commit}"],
        check=False,
        requires_index_lock=False,
    )
    if upstream.returncode != 0:
        raise CommandError(
            _(
                "No upstream branch is configured. Pass the excluded history "
                "base explicitly."
            )
        )

    fork_point = run_git_command(
        ["merge-base", "--fork-point", "@{upstream}", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    if fork_point.returncode == 0 and fork_point.stdout.strip():
        return resolve_history_commit(fork_point.stdout.strip())

    merge_base = run_git_command(
        ["merge-base", "HEAD", "@{upstream}"],
        check=False,
        requires_index_lock=False,
    )
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise CommandError(
            _("Could not determine a merge base between HEAD and its upstream.")
        )
    return resolve_history_commit(merge_base.stdout.strip())


def _require_ancestor(base: str, tip: str) -> None:
    result = run_git_command(
        ["merge-base", "--is-ancestor", base, tip],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise CommandError(
            _("History base {base} is not an ancestor of HEAD.").format(base=base)
        )
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _linear_commits(base: str, tip: str) -> tuple[str, ...]:
    result = run_git_command(
        ["rev-list", "--parents", "--reverse", f"{base}..{tip}"],
        requires_index_lock=False,
    )
    commits: list[str] = []
    expected_parent = base
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 2 or fields[1] != expected_parent:
            raise CommandError(
                _(
                    "History refinement currently requires a linear range; "
                    "commit {commit} does not continue the expected parent chain."
                ).format(commit=fields[0])
            )
        commits.append(fields[0])
        expected_parent = fields[0]

    if not commits or commits[-1] != tip:
        raise CommandError(_("No commits found between the history base and HEAD."))
    return tuple(commits)


def resolve_history_range(
    onto_boundary: str | None,
    movable_boundary: str | None = None,
) -> ResolvedHistoryRange:
    """Return the frozen non-empty linear range ending at canonical HEAD.

    ``movable_boundary`` is the exclusive base of the commits that may move;
    ``onto_boundary`` is the older frozen base that movable units may be
    commuted back toward. When ``onto_boundary`` is omitted the frozen base
    equals the movable base and the whole range is movable.
    """
    _require_unmodified_object_graph()
    tip = resolve_history_commit("HEAD")
    movable_base = (
        resolve_history_commit(movable_boundary)
        if movable_boundary is not None
        else _default_base()
    )
    base = (
        resolve_history_commit(onto_boundary)
        if onto_boundary is not None
        else movable_base
    )
    return _resolved_range(base, tip, movable_base)


def _require_movable_base(base: str, movable_base: str, commits: tuple[str, ...]) -> None:
    if movable_base == base:
        return
    if movable_base not in commits:
        raise CommandError(
            _(
                "Movable base {movable_base} is not the frozen base or a commit "
                "in the frozen range."
            ).format(movable_base=movable_base)
        )


def _resolved_range(
    base: str,
    tip: str,
    movable_base: str,
) -> ResolvedHistoryRange:
    _require_ancestor(base, tip)
    _require_ancestor(base, movable_base)
    _require_ancestor(movable_base, tip)
    commits = _linear_commits(base, tip)
    _require_movable_base(base, movable_base, commits)
    return ResolvedHistoryRange(
        object_format=get_git_object_format(),
        base_commit=base,
        tip_commit=tip,
        movable_base=movable_base,
        commits_oldest_first=commits,
    )


def resolve_exact_history_range(
    boundary: str,
    tip_revision: str,
    movable_boundary: str | None = None,
) -> ResolvedHistoryRange:
    """Return one frozen linear range without consulting the current HEAD."""
    _require_unmodified_object_graph()
    base = resolve_history_commit(boundary)
    tip = resolve_history_commit(tip_revision)
    movable_base = (
        resolve_history_commit(movable_boundary)
        if movable_boundary is not None
        else base
    )
    return _resolved_range(base, tip, movable_base)
