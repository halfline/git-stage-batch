"""Recovery objects remain available across aggressive Git garbage collection."""

from __future__ import annotations

import subprocess

from .conftest import git_stage_batch


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _prepare_session_with_batch(functional_repo, batch_name: str) -> tuple[str, str]:
    git_stage_batch("new", batch_name)
    content = _git(
        "rev-parse", f"refs/git-stage-batch/batches/{batch_name}"
    ).stdout.strip()
    state = _git(
        "rev-parse", f"refs/git-stage-batch/state/{batch_name}"
    ).stdout.strip()
    (functional_repo / "README.md").write_text("# changed\n")
    git_stage_batch("start")
    return content, state


def _prune_unreachable_objects() -> None:
    _git("reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    _git("gc", "--prune=now")


def _assert_object_exists(object_name: str) -> None:
    assert _git("cat-file", "-e", object_name, check=False).returncode == 0


def test_undo_restores_dropped_batch_after_aggressive_gc(functional_repo):
    """Undo roots old batch commits even after their public refs are deleted."""
    content, state = _prepare_session_with_batch(functional_repo, "undo-gc")

    git_stage_batch("drop", "undo-gc")
    _prune_unreachable_objects()

    _assert_object_exists(content)
    _assert_object_exists(state)
    git_stage_batch("undo")

    assert _git(
        "rev-parse", "refs/git-stage-batch/batches/undo-gc"
    ).stdout.strip() == content
    assert _git(
        "rev-parse", "refs/git-stage-batch/state/undo-gc"
    ).stdout.strip() == state


def test_abort_restores_manually_deleted_batch_after_aggressive_gc(functional_repo):
    """Abort roots its batch snapshot independently of current batch refs."""
    content, state = _prepare_session_with_batch(functional_repo, "abort-gc")

    _git("update-ref", "-d", "refs/git-stage-batch/batches/abort-gc")
    _git("update-ref", "-d", "refs/git-stage-batch/state/abort-gc")
    _prune_unreachable_objects()

    _assert_object_exists(content)
    _assert_object_exists(state)
    git_stage_batch("abort")

    assert _git(
        "rev-parse", "refs/git-stage-batch/batches/abort-gc"
    ).stdout.strip() == content
    assert _git(
        "rev-parse", "refs/git-stage-batch/state/abort-gc"
    ).stdout.strip() == state
    assert not _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/git-stage-batch/session/anchors/",
    ).stdout.strip()


def test_stop_removes_session_recovery_anchors(functional_repo):
    """A completed session leaves no internal reachability roots behind."""
    _prepare_session_with_batch(functional_repo, "cleanup-gc")
    assert _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/git-stage-batch/session/anchors/",
    ).stdout.strip()

    git_stage_batch("stop")

    assert not _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/git-stage-batch/session/anchors/",
    ).stdout.strip()


def test_abort_refuses_before_mutation_when_an_anchor_is_missing(functional_repo):
    """A damaged current-format recovery root fails before abort resets HEAD."""
    content, _state = _prepare_session_with_batch(functional_repo, "damaged-anchor")
    anchor_ref = f"refs/git-stage-batch/session/anchors/{content}"
    changed_bytes = (functional_repo / "README.md").read_bytes()
    _git("update-ref", "-d", anchor_ref)

    result = git_stage_batch("abort", check=False)

    assert result.returncode != 0
    assert "Recovery anchor" in result.stderr
    assert (functional_repo / "README.md").read_bytes() == changed_bytes

    _git("update-ref", anchor_ref, content)
    git_stage_batch("abort")
