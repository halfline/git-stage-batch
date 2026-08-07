"""Tests for the bundled history-refinement workflow helpers."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "codex-skills"
    / "refine-history"
    / "scripts"
    / "refine-history-checkpoint.py"
)
CLAUDE_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "claude-skills"
    / "refine-history"
    / "scripts"
    / "refine-history-checkpoint.py"
)
DECOMPOSE_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "codex-skills"
    / "decompose-and-commit-unstaged-changes"
    / "scripts"
    / "decompose-checkpoint.py"
)
MESSAGE_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "codex-skills"
    / "refine-commit-messages"
    / "scripts"
    / "refine-commit-messages-checkpoint.py"
)


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run Git in a test repository."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check:
        result.check_returncode()
    return result


def _helper(
    repo: Path,
    *args: str,
    helper: Path = CODEX_HELPER,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one bundled workflow helper in a test repository."""
    result = subprocess.run(
        [sys.executable, str(helper), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check:
        result.check_returncode()
    return result


def _commit(repo: Path, subject: str) -> str:
    """Create an empty commit and return its full ID."""
    _git(repo, "commit", "--allow-empty", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a repository with one base commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "Base")
    return repo


def test_codex_and_claude_checkpoint_helpers_match() -> None:
    """Assistant variants should enforce the same workflow invariants."""
    assert CODEX_HELPER.read_bytes() == CLAUDE_HELPER.read_bytes()


def test_start_freezes_base_and_creates_unique_recovery_refs(git_repo: Path) -> None:
    """A symbolic base must not drift, and backup creation must be atomic."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Add parser")
    _commit(git_repo, "Route requests")

    checked = _helper(git_repo, "check-range", "--base", "HEAD~2")
    assert checked.stdout.strip() == base

    _helper(git_repo, "start", "--base", "HEAD~2")
    checkpoint_path = (
        git_repo / ".git-stage-batch" / "refine-history" / "checkpoint.json"
    )
    first = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert first["base"] == base
    assert first["branch_ref"].startswith("refs/heads/")
    assert first["original_count"] == 2
    assert (
        _git(
            git_repo,
            "show-ref",
            "--verify",
            first["recovery_ref"],
        ).returncode
        == 0
    )

    _helper(git_repo, "start", "--base", base)
    second = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert second["recovery_ref"] != first["recovery_ref"]
    assert _helper(git_repo, "check-resume").stdout.strip() == base
    bypass = _helper(
        git_repo,
        "mark",
        "--phase",
        "complete",
        check=False,
    )
    assert bypass.returncode != 0
    assert "use the complete command" in bypass.stderr

    pre_count = checkpoint_path.parent / "pre-count.txt"
    pre_count.write_text("999\n", encoding="utf-8")
    corrupted = _helper(git_repo, "check-resume", check=False)
    assert corrupted.returncode != 0
    assert "pre-count.txt does not match" in corrupted.stderr


def test_check_range_infers_merge_base_from_remote_tracking_branch(
    git_repo: Path,
) -> None:
    """An omitted base should use the current branch's tracked remote ref."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _git(git_repo, "update-ref", "refs/remotes/origin/trunk", base)
    _git(git_repo, "config", "remote.origin.url", "https://example.com/repo.git")
    _git(
        git_repo,
        "config",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    _git(git_repo, "branch", "--set-upstream-to", "origin/trunk")
    _commit(git_repo, "Add parser")
    _commit(git_repo, "Route requests")

    checked = _helper(git_repo, "check-range")

    assert checked.stdout.strip() == base


def test_check_range_without_base_requires_remote_tracking_branch(
    git_repo: Path,
) -> None:
    """Base inference should fail with an actionable detached-upstream error."""
    _commit(git_repo, "Draft")

    result = _helper(git_repo, "check-range", check=False)

    assert result.returncode != 0
    assert "no remote-tracking upstream; pass --base" in result.stderr


def test_force_push_ref_allowance_is_narrow_and_persists(git_repo: Path) -> None:
    """A verified review head may be rewritten without allowing other refs."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    shared = _commit(git_repo, "Review draft")
    review_ref = "refs/remotes/origin/review"
    _git(git_repo, "update-ref", review_ref, shared)

    checked = _helper(
        git_repo,
        "check-range",
        "--base",
        base,
        "--allow-remote-ref",
        review_ref,
    )
    _helper(
        git_repo,
        "start",
        "--base",
        base,
        "--allow-remote-ref",
        review_ref,
    )
    checkpoint = json.loads(
        (
            git_repo
            / ".git-stage-batch"
            / "refine-history"
            / "checkpoint.json"
        ).read_text(encoding="utf-8")
    )

    assert checked.stdout.strip() == base
    assert checkpoint["allowed_remote_refs"] == [review_ref]
    assert _helper(git_repo, "check-resume").stdout.strip() == base

    other_ref = "refs/remotes/origin/release"
    _git(git_repo, "update-ref", other_ref, shared)
    rejected = _helper(git_repo, "check-resume", check=False)

    assert rejected.returncode != 0
    assert other_ref in rejected.stderr
    assert review_ref not in rejected.stderr


def test_range_rejects_an_earlier_commit_in_a_remote_ref(git_repo: Path) -> None:
    """Publication checks must cover the whole range, not only HEAD."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    shared = _commit(git_repo, "First draft")
    _commit(git_repo, "Unpublished follow-up")
    _git(git_repo, "update-ref", "refs/remotes/origin/draft", shared)

    result = _helper(
        git_repo,
        "check-range",
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert shared in result.stderr
    assert "refs/remotes/origin/draft" in result.stderr


def test_range_rejects_merge_commits(git_repo: Path) -> None:
    """The linear rewrite procedures must not flatten merge topology."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    original_branch = _git(
        git_repo,
        "branch",
        "--show-current",
    ).stdout.strip()
    _git(git_repo, "switch", "-c", "side")
    _commit(git_repo, "Side")
    _git(git_repo, "switch", original_branch)
    _commit(git_repo, "Main")
    _git(git_repo, "merge", "--no-ff", "side", "-m", "Merge side")

    result = _helper(
        git_repo,
        "check-range",
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert "supports only linear ranges" in result.stderr


def test_start_refuses_symlinked_state_without_touching_target(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """Fresh-state cleanup must never follow a workspace symlink."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    state_parent = git_repo / ".git-stage-batch"
    state_parent.mkdir()
    (state_parent / "refine-history").symlink_to(
        outside,
        target_is_directory=True,
    )

    result = _helper(
        git_repo,
        "start",
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert "symlinked state path" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_resume_rejects_a_different_branch_with_the_same_base(
    git_repo: Path,
) -> None:
    """A checkpoint must remain bound to the branch that created it."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft")
    _helper(git_repo, "start", "--base", base)
    _git(git_repo, "switch", "-c", "unrelated", base)
    _commit(git_repo, "Other draft")

    result = _helper(git_repo, "check-resume", check=False)

    assert result.returncode != 0
    assert "refusing to resume on a different branch" in result.stderr


def test_complete_refuses_a_changed_final_tree(git_repo: Path) -> None:
    """Completion must not record a final tree different from the checkpoint."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft")
    _helper(git_repo, "start", "--base", base)
    changed = git_repo / "changed.txt"
    changed.write_text("changed\n", encoding="utf-8")
    _git(git_repo, "add", "changed.txt")
    _git(git_repo, "commit", "-m", "Change final tree")

    result = _helper(
        git_repo,
        "complete",
        "--base",
        base,
        check=False,
    )
    checkpoint = json.loads(
        (
            git_repo / ".git-stage-batch" / "refine-history" / "checkpoint.json"
        ).read_text(encoding="utf-8")
    )

    assert result.returncode != 0
    assert "current branch tree does not match" in result.stderr
    assert checkpoint["phase"] == "started"


def test_complete_requires_matching_message_refinement(git_repo: Path) -> None:
    """History completion must not bypass its message-only dependency."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft")
    _helper(git_repo, "start", "--base", base)

    result = _helper(
        git_repo,
        "complete",
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert "requires a completed refine-commit-messages run" in result.stderr


def test_audit_validator_requires_current_complete_evidence(git_repo: Path) -> None:
    """The completion gate must cover every commit and every pressure signal."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Repair batch routing")
    _commit(git_repo, "Route requests")
    _helper(git_repo, "start", "--base", base)
    _helper(git_repo, "pressure", "--base", base)
    state_dir = git_repo / ".git-stage-batch" / "refine-history"
    pressure = json.loads((state_dir / "pressure.json").read_text(encoding="utf-8"))

    audit_entries = []
    for commit in pressure["commits"]:
        entry = {
            "sha": commit["sha"],
            "subject": commit["subject"],
            "verdict": "KEEP",
            "reason": "The patch establishes one independently reviewable contract.",
        }
        if commit["reasons"]:
            entry.update(
                {
                    "pressure": commit["reasons"],
                    "smallest_runnable_spine": (
                        "Routing accepts one request and returns one result."
                    ),
                    "later_enrichments_checked": [
                        "Additional request variants and their proofs"
                    ],
                    "split_probes": [
                        {
                            "candidate": "Move request variants to a later commit",
                            "blocking_reason": (
                                "src/router.py imports their dispatch table at "
                                "module load"
                            ),
                        }
                    ],
                }
            )
        audit_entries.append(entry)

    audit = {
        "schema": 1,
        "base": pressure["base"],
        "head": pressure["head"],
        "commits": audit_entries,
    }
    audit_path = state_dir / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    missing_false_positive = _helper(
        git_repo,
        "validate-audit",
        "--base",
        base,
        check=False,
    )
    assert missing_false_positive.returncode != 0
    assert "concrete false positive" in missing_false_positive.stderr

    audit_entries[0]["repair_process_false_positive"] = (
        "Batch routing is the product domain, not rewrite-process cleanup."
    )
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    valid = _helper(git_repo, "validate-audit", "--base", base)
    assert "audit valid for 2 commits" in valid.stdout
    _helper(git_repo, "start", "--base", base, helper=MESSAGE_HELPER)
    message_state = git_repo / ".git-stage-batch" / "refine-commit-messages"
    message_checkpoint = json.loads(
        (message_state / "checkpoint.json").read_text(encoding="utf-8")
    )
    message_checkpoint["phase"] = "complete"
    message_checkpoint["events"].append(
        {
            "event": "complete",
            "head": _git(git_repo, "rev-parse", "HEAD").stdout.strip(),
        }
    )
    (message_state / "checkpoint.json").write_text(
        json.dumps(message_checkpoint),
        encoding="utf-8",
    )
    unverified = _helper(
        git_repo,
        "complete",
        "--base",
        base,
        check=False,
    )
    assert unverified.returncode != 0
    assert "lacks a successful verify-range record" in unverified.stderr
    _helper(
        git_repo,
        "verify-range",
        "--base",
        base,
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(0)",
    )
    _helper(git_repo, "complete", "--base", base)
    checkpoint = json.loads((state_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["phase"] == "complete"
    assert (state_dir / "post-count.txt").read_text(encoding="utf-8") == "2\n"

    audit["commits"] = audit_entries[:-1]
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    incomplete = _helper(
        git_repo,
        "validate-audit",
        "--base",
        base,
        check=False,
    )
    assert incomplete.returncode != 0
    assert "cover every current commit exactly once" in incomplete.stderr

    audit["commits"] = audit_entries
    audit_entries[0]["split_probes"][0]["blocking_reason"] = (
        "There is no immediate breakage."
    )
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    weak_probe = _helper(
        git_repo,
        "validate-audit",
        "--base",
        base,
        check=False,
    )
    assert weak_probe.returncode != 0
    assert "no-breakage probes must be split" in weak_probe.stderr


def test_decompose_resume_prefers_recorded_refinement_phase(
    git_repo: Path,
) -> None:
    """A retained plan must not send an interrupted refinement to Phase 2."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft")
    _helper(
        git_repo,
        "start",
        "--mode",
        "full",
        "--base",
        base,
        helper=DECOMPOSE_HELPER,
    )
    state_dir = git_repo / ".git-stage-batch"
    (state_dir / "decompose-plan.json").write_text("{}\n", encoding="utf-8")
    _helper(
        git_repo,
        "mark",
        "--phase",
        "refine-history-running",
        helper=DECOMPOSE_HELPER,
    )

    status = _helper(
        git_repo,
        "status",
        "--json",
        helper=DECOMPOSE_HELPER,
    )

    assert json.loads(status.stdout)["resume_target"] == "gate3-or-manual-audit"
