"""Live safety facts for rewrite-plan operations."""

from __future__ import annotations

import stat
from pathlib import Path

from ..batch.state.query import list_batch_names
from ..data.session_marker import session_is_active
from ..utils.git_command import (
    git_diff_reports_changes,
    run_git_command,
    stream_git_command_bytes,
)
from ..utils.git_repository import get_git_directory_path
from ..utils.paths import get_rewrite_state_directory_path
from .models import HistoryRemoteContainment, HistorySafetyFacts


_GIT_OPERATION_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-apply",
    "rebase-merge",
    "sequencer",
    "BISECT_START",
)


def _active_git_operations(git_directory: Path) -> tuple[str, ...]:
    return tuple(
        marker
        for marker in _GIT_OPERATION_MARKERS
        if (git_directory / marker).exists()
    )


def _active_history_operation() -> str | None:
    history_directory = get_rewrite_state_directory_path()
    try:
        directory_metadata = history_directory.lstat()
        if not stat.S_ISDIR(directory_metadata.st_mode):
            return "invalid"
    except FileNotFoundError:
        return None
    except OSError:
        return "invalid"
    active_path = history_directory / "active"
    try:
        metadata = active_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return "invalid"
        value = active_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return "invalid"
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        return "invalid"
    return value


def _tracked_worktree_is_clean() -> bool:
    result = run_git_command(
        ["diff", "--quiet", "--ignore-submodules=none"],
        check=False,
        capture_stdout=False,
        requires_index_lock=False,
    )
    return not git_diff_reports_changes(result)


def _index_matches_tree(tree: str) -> bool:
    result = run_git_command(
        [
            "diff-index",
            "--cached",
            "--quiet",
            "--ignore-submodules=none",
            tree,
            "--",
        ],
        check=False,
        capture_stdout=False,
        requires_index_lock=False,
    )
    return not git_diff_reports_changes(result)


def _untracked_path_count() -> int:
    return sum(
        chunk.count(b"\0")
        for chunk in stream_git_command_bytes(
            ["ls-files", "--others", "--exclude-standard", "-z"],
            requires_index_lock=False,
        )
    )


def _symbolic_upstream() -> tuple[str | None, str | None]:
    upstream_ref = run_git_command(
        ["rev-parse", "--symbolic-full-name", "@{upstream}"],
        check=False,
        requires_index_lock=False,
    )
    if upstream_ref.returncode != 0 or not upstream_ref.stdout.strip():
        return None, None
    name = upstream_ref.stdout.strip()
    upstream_tip = run_git_command(
        ["rev-parse", "--verify", f"{name}^{{commit}}"],
        check=False,
        requires_index_lock=False,
    )
    if upstream_tip.returncode != 0 or not upstream_tip.stdout.strip():
        return name, None
    return name, upstream_tip.stdout.strip()


def _ahead_behind(tip: str, upstream_tip: str | None) -> tuple[int | None, int | None]:
    if upstream_tip is None:
        return None, None
    result = run_git_command(
        ["rev-list", "--left-right", "--count", f"{tip}...{upstream_tip}"],
        requires_index_lock=False,
    )
    fields = result.stdout.split()
    if len(fields) != 2:
        return None, None
    return int(fields[0]), int(fields[1])


def _remote_ref_tips() -> tuple[tuple[str, str], ...]:
    result = run_git_command(
        [
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/remotes",
        ],
        requires_index_lock=False,
    )
    refs: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        refname, separator, object_id = line.partition(" ")
        if separator and refname and object_id:
            refs.append((refname, object_id))
    return tuple(sorted(refs))


def _commit_is_ancestor(
    commit: str,
    descendant: str,
    cache: dict[tuple[str, str], bool],
) -> bool:
    cache_key = (commit, descendant)
    if cache_key in cache:
        return cache[cache_key]
    result = run_git_command(
        ["merge-base", "--is-ancestor", commit, descendant],
        check=False,
        capture_stdout=False,
        requires_index_lock=False,
    )
    if result.returncode not in {0, 1}:
        result.check_returncode()
    is_ancestor = result.returncode == 0
    cache[cache_key] = is_ancestor
    return is_ancestor


def _remote_containment(
    source_commits: tuple[str, ...],
) -> tuple[HistoryRemoteContainment, ...]:
    ancestry_cache: dict[tuple[str, str], bool] = {}
    refs_by_commit: list[list[str]] = [[] for _commit in source_commits]
    for refname, ref_tip in _remote_ref_tips():
        low = 0
        high = len(source_commits) - 1
        newest_contained = -1
        while low <= high:
            middle = (low + high) // 2
            if _commit_is_ancestor(
                source_commits[middle], ref_tip, ancestry_cache
            ):
                newest_contained = middle
                low = middle + 1
            else:
                high = middle - 1
        for index in range(newest_contained + 1):
            refs_by_commit[index].append(refname)
    return tuple(
        HistoryRemoteContainment(commit_id=commit, remote_refs=tuple(refs))
        for commit, refs in zip(source_commits, refs_by_commit, strict=True)
    )


def collect_history_safety_facts(
    *,
    tip: str,
    final_tree: str,
    branch_ref: str | None,
    source_commits: tuple[str, ...] = (),
    allowed_remote_refs: tuple[str, ...] = (),
) -> HistorySafetyFacts:
    """Capture dynamic preconditions without mutating history state."""
    git_directory = get_git_directory_path()
    index_clean = _index_matches_tree(final_tree)
    index_tree = final_tree if index_clean else None
    staging_active = session_is_active(git_directory)
    batches = tuple(list_batch_names())
    active_git_operations = _active_git_operations(git_directory)
    active_history = _active_history_operation()
    upstream_ref, upstream_tip = _symbolic_upstream()
    ahead, behind = _ahead_behind(tip, upstream_tip)
    commits = source_commits or (tip,)
    remote_containment = _remote_containment(commits)
    allowed_remote_ref_set = frozenset(allowed_remote_refs)
    disallowed_remote_refs = {
        remote_ref
        for containment in remote_containment
        for remote_ref in containment.remote_refs
        if remote_ref not in allowed_remote_ref_set
    }

    blockers: list[str] = []
    if branch_ref is None:
        blockers.append("detached-head")
    if not index_clean:
        blockers.append("staged-index")
    worktree_clean = _tracked_worktree_is_clean()
    if not worktree_clean:
        blockers.append("tracked-worktree")
    if staging_active:
        blockers.append("staging-session")
    if batches:
        blockers.append("saved-batches")
    if active_git_operations:
        blockers.append("active-git-operation")
    if active_history is not None:
        blockers.append("active-rewrite-operation")
    if disallowed_remote_refs:
        blockers.append("published-range")

    return HistorySafetyFacts(
        index_tree=index_tree,
        index_clean=index_clean,
        worktree_clean=worktree_clean,
        untracked_path_count=_untracked_path_count(),
        staging_session_active=staging_active,
        saved_batches=batches,
        active_git_operations=active_git_operations,
        active_history_operation=active_history,
        upstream_ref=upstream_ref,
        upstream_tip=upstream_tip,
        ahead_count=ahead,
        behind_count=behind,
        remote_refs_containing_tip=next(
            (
                containment.remote_refs
                for containment in remote_containment
                if containment.commit_id == tip
            ),
            (),
        ),
        remote_containment=remote_containment,
        blockers=tuple(blockers),
    )
