"""Tests for product-backed history-refinement skill assets."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_ROOT = PROJECT_ROOT / "assets" / "codex-skills"
CLAUDE_ROOT = PROJECT_ROOT / "assets" / "claude-skills"
CODEX_HISTORY = CODEX_ROOT / "refine-history"
CLAUDE_HISTORY = CLAUDE_ROOT / "refine-history"
CODEX_MESSAGES = CODEX_ROOT / "refine-commit-messages"
CLAUDE_MESSAGES = CLAUDE_ROOT / "refine-commit-messages"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_refinement_assets_remove_skill_owned_rewrite_engines() -> None:
    """Assistant variants should not retain checkpoint or rebase engines."""
    roots = (CODEX_HISTORY, CLAUDE_HISTORY, CODEX_MESSAGES, CLAUDE_MESSAGES)
    forbidden = (
        "refine-history-checkpoint.py",
        "refine-commit-messages-checkpoint.py",
        "GIT_SEQUENCE_EDITOR=",
        "git rebase ",
        "git reset ",
        "git commit --amend",
    )

    for root in roots:
        skill = _read(root / "SKILL.md")
        for value in forbidden:
            assert value not in skill
        assert not any(root.glob("scripts/*checkpoint.py"))

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        reference = _read(root / "references" / "rewrite-procedures.md")
        for value in forbidden:
            assert value not in reference


def test_refine_history_delegates_every_mechanical_transition() -> None:
    """Boundary skills should use plans and the complete product lifecycle."""
    required = (
        "rewrite scan",
        "rewrite validate",
        "rewrite apply",
        "rewrite status",
        "rewrite continue",
        "rewrite abort",
        "rewrite verify",
        "Edit only `plan.outputs`",
        "SPLIT",
        "INTEGRATE",
        "REORDER",
        "BLOCKED",
        "UNKNOWN",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = _read(root / "SKILL.md")
        prose = " ".join(skill.split())
        for value in required:
            assert value in skill
        assert "stop after validation" in prose
        assert "Do not create refs, checkpoints, commits" in prose

    assert _read(
        CODEX_HISTORY / "references" / "rewrite-procedures.md"
    ) == _read(CLAUDE_HISTORY / "references" / "rewrite-procedures.md")


def test_refine_history_binds_narrow_publication_scope() -> None:
    """Publication audits should consider provider-default and protected refs."""
    skill_contracts = (
        "bind an explicit run-local publication scope",
        "provider's freshly queried default branch",
        "fresh provider protected-branch query",
        "A configured upstream participates only when",
        "exactly match that provider-default binding",
        "An arbitrary feature, WIP, or review upstream remains excluded",
        "Never infer the default branch or protection from a branch name or configured upstream",
        "Require zero range overlap only against those in-scope tips",
        "Report excluded categories and observed refs separately",
        "unprotected WIP branches, tags, and archived or closed review refs",
        "Do not silently broaden the scope",
        "user or repository policy explicitly says to",
        "stop without mutation and report the executor limitation",
    )
    reference_contracts = (
        "## Bind publication scope",
        "repository's current default branch",
        "In the same fresh evidence window",
        "Resolve a configured upstream, when one exists, only as a consistency fact",
        "Do not add an arbitrary feature, WIP, or review upstream",
        "provider cannot resolve its current default branch",
        "Never infer the default branch or protection from names",
        "including any configured upstream that does not match the provider-default binding",
        "compute reachability from every commit in `BASE_SHA..HEAD` only to those bound tips",
        "unpublished only when that in-scope overlap set is empty",
        "Report each category, its observed exact refs, and why it is excluded",
        "Only an explicit user instruction or repository policy may expand",
        "Never narrow the default set",
        "fresh `safety.remote_containment`",
        "excluded containment and non-mutating result",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in skill_contracts:
            assert contract in skill
        for contract in reference_contracts:
            assert contract in reference
        assert "canonical configured upstream/default mainline" not in skill
        assert "A range commit contained by any unrelated remote ref" not in skill
        assert skill.index("bind an explicit run-local publication scope") < skill.index(
            "## Targeted exact rewrite"
        )
        assert reference.index("## Bind publication scope") < reference.index(
            "## Immutable and editable fields"
        )


def test_message_refinement_is_a_message_only_history_plan() -> None:
    """Message skills should validate their allowed subset before resume."""
    for root in (CODEX_MESSAGES, CLAUDE_MESSAGES):
        skill = _read(root / "SKILL.md")
        prose = " ".join(skill.split())
        assert "only `KEEP` and `REWORD`" in skill
        assert "`plan.operation_counts`" in skill
        assert "rewrite scan" in skill
        assert "rewrite validate" in skill
        assert "rewrite apply" in skill
        assert "rewrite continue" in skill
        assert "rewrite abort" in skill
        assert "rewrite verify" in skill
        assert "installed `rewrite --help` wins" in prose
        assert "history --help" not in skill
        assert "constructs deterministic unsigned commits" in skill
        assert "signature header by audited digest" in skill
        assert "Audit mode must not call `rewrite apply`" in skill

    codex = _read(CODEX_MESSAGES / "SKILL.md")
    claude = _read(CLAUDE_MESSAGES / "SKILL.md")
    assert "$refine-commit-messages audit BASE_SHA" in codex
    assert "/refine-commit-messages audit BASE_SHA" in claude
    assert '--audit-only' not in codex
    assert '--audit-only' not in claude


def test_message_guidance_requires_low_context_prose() -> None:
    """Drafters should explain repository terms instead of inventing shorthand."""
    for root in (CODEX_MESSAGES, CLAUDE_MESSAGES):
        guidance = _read(root / "references" / "message-guidelines.md")
        assert "## Low-context prose" in guidance
        assert "Do not invent a one- or two-word name" in guidance
        assert "Define a codebase-specific or ambiguous term at first use" in guidance
        assert "Make each message independently understandable" in guidance
        assert "Apply a read-once test" in guidance

    drafters = (
        CODEX_ROOT / "internal" / "commit-message-drafter.md",
        PROJECT_ROOT / "assets" / "claude-agents" / "commit-message-drafter.md",
    )
    for path in drafters:
        drafter = " ".join(_read(path).split())
        assert "Do not reread the complete raw series" in drafter
        assert "Write for a reader who has never seen the repository" in drafter


def test_snapshot_helpers_match_and_leave_no_worktree(
    tmp_path: Path,
) -> None:
    """The retained semantic checker should clean up its detached worktree."""
    codex_helper = CODEX_HISTORY / "scripts" / "verify-head-snapshot.py"
    claude_helper = CLAUDE_HISTORY / "scripts" / "verify-head-snapshot.py"
    assert codex_helper.read_bytes() == claude_helper.read_bytes()

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "value.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "value.txt")
    _git(repo, "commit", "-m", "Base")

    result = subprocess.run(
        [
            sys.executable,
            str(codex_helper),
            "--repo",
            str(repo),
            "--ref",
            "HEAD",
            "--",
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert Path('value.txt').read_text() == 'committed\\n'"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    worktrees = _git(repo, "worktree", "list", "--porcelain").stdout
    assert worktrees.count("worktree ") == 1
    assert _git(repo, "status", "--short").stdout == ""
