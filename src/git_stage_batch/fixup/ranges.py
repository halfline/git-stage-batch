"""Canonical range selection for fixup analysis."""

from __future__ import annotations

import subprocess

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from .models import FixupRange


def _resolve_commit(revision: str) -> str:
    result = run_git_command(
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        requires_index_lock=False,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not resolved:
        raise CommandError(
            _("Invalid fixup boundary: {boundary}").format(boundary=revision)
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
                "No upstream branch is configured. Pass the base commit "
                "for fixup analysis explicitly."
            )
        )

    fork_point = run_git_command(
        ["merge-base", "--fork-point", "@{upstream}", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    if fork_point.returncode == 0 and fork_point.stdout.strip():
        return _resolve_commit(fork_point.stdout.strip())

    merge_base = run_git_command(
        ["merge-base", "HEAD", "@{upstream}"],
        check=False,
        requires_index_lock=False,
    )
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise CommandError(
            _("Could not determine a merge base between HEAD and its upstream.")
        )
    return _resolve_commit(merge_base.stdout.strip())


def _require_ancestor(base: str, head_commit: str) -> None:
    result = run_git_command(
        ["merge-base", "--is-ancestor", base, head_commit],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise CommandError(
            _("Fixup base {base} is not an ancestor of HEAD.").format(base=base)
        )
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _require_linear_range(base: str, head_commit: str) -> tuple[str, ...]:
    result = run_git_command(
        ["rev-list", "--parents", f"{base}..{head_commit}"],
        requires_index_lock=False,
    )
    commits: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 2:
            raise CommandError(
                _(
                    "Fixup analysis currently requires a linear range; "
                    "commit {commit} does not have exactly one parent."
                ).format(commit=fields[0])
            )
        commits.append(fields[0])

    if not commits:
        raise CommandError(_("No commits found between the fixup base and HEAD."))
    return tuple(commits)


def resolve_fixup_range(boundary: str | None) -> FixupRange:
    """Return the frozen, non-empty linear range used for fixup analysis."""
    head_commit = _resolve_commit("HEAD")
    base = _resolve_commit(boundary) if boundary is not None else _default_base()
    _require_ancestor(base, head_commit)
    commits = _require_linear_range(base, head_commit)
    return FixupRange(
        base_commit=base,
        head_commit=head_commit,
        commits_newest_first=commits,
    )
