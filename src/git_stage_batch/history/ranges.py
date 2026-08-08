"""Canonical linear range selection for history refinement."""

from __future__ import annotations

import subprocess
from pathlib import Path
from dataclasses import dataclass

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_repository import get_git_object_format


@dataclass(frozen=True, slots=True)
class ResolvedHistoryRange:
    """Full object IDs for one non-empty linear source range."""

    object_format: str
    base_commit: str
    tip_commit: str
    commits_oldest_first: tuple[str, ...]


def _require_unmodified_object_graph() -> None:
    replacement_objects = run_git_command(
        ["replace", "--list"],
        requires_index_lock=False,
    ).stdout.splitlines()
    grafts_path = Path(
        run_git_command(
            ["rev-parse", "--git-path", "info/grafts"],
            requires_index_lock=False,
        ).stdout.strip()
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


def resolve_history_range(boundary: str | None) -> ResolvedHistoryRange:
    """Return the frozen non-empty linear range ending at canonical HEAD."""
    _require_unmodified_object_graph()
    tip = resolve_history_commit("HEAD")
    base = resolve_history_commit(boundary) if boundary is not None else _default_base()
    _require_ancestor(base, tip)
    commits = _linear_commits(base, tip)
    return ResolvedHistoryRange(
        object_format=get_git_object_format(),
        base_commit=base,
        tip_commit=tip,
        commits_oldest_first=commits,
    )
