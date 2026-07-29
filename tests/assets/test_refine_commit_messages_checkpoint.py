"""Tests for the bundled commit-message refinement helper."""

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
    / "refine-commit-messages"
    / "scripts"
    / "refine-commit-messages-checkpoint.py"
)
CLAUDE_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "claude-skills"
    / "refine-commit-messages"
    / "scripts"
    / "refine-commit-messages-checkpoint.py"
)
CODEX_SKILL = CODEX_HELPER.parents[1] / "SKILL.md"
CLAUDE_SKILL = CLAUDE_HELPER.parents[1] / "SKILL.md"
CODEX_GUIDELINES = CODEX_HELPER.parents[1] / "references" / "message-guidelines.md"
CLAUDE_GUIDELINES = CLAUDE_HELPER.parents[1] / "references" / "message-guidelines.md"
CODEX_DRAFTER = (
    PROJECT_ROOT / "assets" / "codex-skills" / "internal" / "commit-message-drafter.md"
)
CLAUDE_DRAFTER = (
    PROJECT_ROOT / "assets" / "claude-agents" / "commit-message-drafter.md"
)


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git in a test repository."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
    )
    if check:
        result.check_returncode()
    return result


def _helper(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the bundled message-refinement helper."""
    return _helper_at(repo, CODEX_HELPER, *args, check=check)


def _helper_at(
    repo: Path,
    helper: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one installed copy of the message-refinement helper."""
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


def _message(subject: str, *paragraphs: str) -> str:
    """Build one complete commit message."""
    return "\n\n".join((subject, *paragraphs)) + "\n"


def _commit(repo: Path, message: str, *, amend: bool = False) -> str:
    """Create or amend an empty commit with a complete message."""
    args = ["commit", "--allow-empty", "-q", "-F", "-"]
    if amend:
        args.insert(1, "--amend")
    _git(repo, *args, input_text=message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _opening_message() -> str:
    return _message(
        "core: establish the message interface",
        "The project provides a stable base for message processing.",
        "Callers cannot yet express the new message workflow.",
        "This commit begins that workflow by defining its interface.",
        "The final commit will connect the message implementation.",
    )


def _final_message() -> str:
    return _message(
        "core: connect the message implementation",
        "The project provides an interface for message processing.",
        "Callers still cannot execute the workflow through that interface.",
        "This commit completes the workflow by connecting its implementation.",
        "Together, the series now provides the complete message workflow.",
    )


def _start_single_reword(
    repo: Path,
    base: str,
    replacement: str,
    *,
    helper: Path = CODEX_HELPER,
) -> Path:
    """Start a one-commit checkpoint with one validated replacement."""
    return _start_reword_plan(
        repo,
        base,
        {1: replacement},
        helper=helper,
    )


def _start_reword_plan(
    repo: Path,
    base: str,
    replacements: dict[int, str],
    *,
    helper: Path = CODEX_HELPER,
) -> Path:
    """Start a checkpoint with validated replacements by series position."""
    _helper_at(repo, helper, "start", "--base", base)
    _helper_at(repo, helper, "scan", "--base", base)
    state_dir = repo / ".git-stage-batch" / "refine-commit-messages"
    scan = json.loads((state_dir / "scan.json").read_text(encoding="utf-8"))
    entries = []
    for position, entry in enumerate(scan["commits"], start=1):
        reword = position in replacements
        audit_entry = {
            "sha": entry["sha"],
            "subject": entry["subject"],
            "signals": entry["signals"],
            "verdict": "REWORD" if reword else "KEEP",
            "reason": "The message is checked against its patch and position.",
            "patch_fidelity": "The proposal describes the complete test patch.",
        }
        if len(scan["commits"]) > 1:
            audit_entry["series_transition"] = (
                "The message matches its position in the test series."
            )
        if reword:
            audit_entry["proposed_message"] = replacements[position]
        entries.append(audit_entry)
    audit = {
        "schema": 1,
        "mode": "refine",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["fallback message guidelines"],
            "summary": "Position-aware body paragraphs for the test series.",
        },
        "commits": entries,
    }
    (state_dir / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return state_dir


def _enable_fake_signing(repo: Path) -> None:
    """Configure a deterministic signer that emits a syntactically valid block."""
    git_dir = Path(
        _git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    signer = git_dir / "fake-gpg"
    signer.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        "printf '\\n%s\\n' "
        "'[GNUPG:] SIG_CREATED D 1 10 00 0 0 0 0 0' >&2\n"
        "printf '%s\\n' "
        "'-----BEGIN PGP SIGNATURE-----' "
        "'dGVzdC1zaWduYXR1cmU=' "
        "'-----END PGP SIGNATURE-----'\n",
        encoding="utf-8",
    )
    signer.chmod(0o755)
    _git(repo, "config", "gpg.program", str(signer))
    _git(repo, "config", "user.signingkey", "test-key")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a repository with one base commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "Base\n")
    return repo


def test_codex_and_claude_helpers_match() -> None:
    """Assistant variants must enforce identical rewrite invariants."""
    assert CODEX_HELPER.read_bytes() == CLAUDE_HELPER.read_bytes()


def test_public_audit_mode_uses_a_positional_keyword() -> None:
    """The skill interface should use the same bare-mode style as peer skills."""
    codex_skill = CODEX_SKILL.read_text(encoding="utf-8")
    claude_skill = CLAUDE_SKILL.read_text(encoding="utf-8")

    assert "$refine-commit-messages audit BASE_SHA" in codex_skill
    assert "$refine-commit-messages --audit-only BASE_SHA" not in codex_skill
    assert "/refine-commit-messages audit BASE_SHA" in claude_skill
    assert "/refine-commit-messages --audit-only BASE_SHA" not in claude_skill
    assert (
        'argument-hint: "<base-sha> | audit <base-sha> | resume"' in claude_skill
    )


def test_skill_applies_one_indexed_audit_in_a_single_rebase() -> None:
    """Message refinement should not restart a whole-series audit per reword."""
    codex_skill = CODEX_SKILL.read_text(encoding="utf-8")
    claude_skill = CLAUDE_SKILL.read_text(encoding="utf-8")

    for skill in (codex_skill, claude_skill):
        prose = " ".join(skill.split())
        assert "apply-audit --base" in skill
        assert "one initial semantic audit" in prose
        assert "never repeat semantic drafting once per reworded commit" in prose
        assert "Do not resend the full index" in prose
        assert "Do not copy the whole prefix of earlier entries" in prose
        assert "Persist the index as `series-index.json`" in prose
        assert "--format='%H%n%B%n---'" not in skill
        assert "validate-audit --allow-reword" not in skill
        assert "Do not run a separate validation pass first" in prose
        assert "rebuild the complete audit from the beginning" not in skill


def test_message_guidance_requires_low_context_prose() -> None:
    """Drafters should explain repository terms instead of inventing shorthand."""
    for path in (CODEX_GUIDELINES, CLAUDE_GUIDELINES):
        guidance = path.read_text(encoding="utf-8")
        assert "## Low-context prose" in guidance
        assert "Do not invent a one- or two-word name" in guidance
        assert "Define a codebase-specific or ambiguous term at first use" in guidance
        assert "Make each message independently understandable" in guidance
        assert "Apply a read-once test" in guidance

    for path in (CODEX_DRAFTER, CLAUDE_DRAFTER):
        drafter = " ".join(path.read_text(encoding="utf-8").split())
        assert "Do not reread the complete raw series" in drafter
        assert "Write for a reader who has never seen the repository" in drafter


def test_start_freezes_base_and_binds_resume_to_the_branch(git_repo: Path) -> None:
    """A checkpoint should retain its canonical range and branch ownership."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(
        git_repo,
        _message(
            "core: expose message inspection",
            "The project provides a stable message representation.",
            "Reviewers cannot inspect that representation as a report.",
            "This commit exposes inspection through a report command.",
        ),
    )

    checked = _helper(git_repo, "check-range", "--base", "HEAD~1")
    _helper(git_repo, "start", "--base", "HEAD~1")
    state_dir = git_repo / ".git-stage-batch" / "refine-commit-messages"
    checkpoint = json.loads((state_dir / "checkpoint.json").read_text(encoding="utf-8"))

    assert checked.stdout.strip() == base == checkpoint["base"]
    assert checkpoint["branch_ref"].startswith("refs/heads/")
    assert (
        _git(
            git_repo,
            "show-ref",
            "--verify",
            checkpoint["recovery_ref"],
        ).returncode
        == 0
    )

    _git(git_repo, "switch", "-q", "-c", "unrelated", base)
    _commit(git_repo, "Other draft\n")
    resumed = _helper(git_repo, "check-resume", check=False)

    assert resumed.returncode != 0
    assert "refusing to resume on a different branch" in resumed.stderr


def test_start_rejects_symlinked_state_without_touching_target(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """Fresh-state cleanup must not follow a workspace symlink."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Draft\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    state_parent = git_repo / ".git-stage-batch"
    state_parent.mkdir()
    (state_parent / "refine-commit-messages").symlink_to(
        outside,
        target_is_directory=True,
    )

    result = _helper(git_repo, "start", "--base", base, check=False)

    assert result.returncode != 0
    assert "symlinked state path" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_audit_only_accepts_shared_history_without_writing_state(
    git_repo: Path,
) -> None:
    """Audit-only mode should inspect published history without mutating Git."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    head = _commit(
        git_repo,
        _message(
            "core: expose message inspection",
            "The project provides a stable message representation.",
            "Reviewers cannot inspect that representation as a report.",
            "This commit exposes inspection through a report command.",
        ),
    )
    _git(git_repo, "update-ref", "refs/remotes/origin/draft", head)

    mutating = _helper(
        git_repo,
        "check-range",
        "--base",
        base,
        check=False,
    )
    audited = _helper(
        git_repo,
        "check-range",
        "--audit-only",
        "--base",
        base,
    )
    scan = _helper(git_repo, "inspect", "--base", base)

    assert mutating.returncode != 0
    assert audited.stdout.strip() == base
    assert json.loads(scan.stdout)["mode"] == "audit-only"
    assert not (git_repo / ".git-stage-batch" / "refine-commit-messages").exists()
    assert (
        _git(
            git_repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/refine-commit-messages",
        ).stdout
        == ""
    )


def test_range_rejects_merge_commits(git_repo: Path) -> None:
    """Message-series ordering should not flatten merge topology."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    original_branch = _git(git_repo, "branch", "--show-current").stdout.strip()
    _git(git_repo, "switch", "-q", "-c", "side")
    _commit(git_repo, "Side\n")
    _git(git_repo, "switch", "-q", original_branch)
    _commit(git_repo, "Main\n")
    _git(git_repo, "merge", "-q", "--no-ff", "side", "-m", "Merge side")

    result = _helper(
        git_repo,
        "check-range",
        "--audit-only",
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert "supports only linear ranges" in result.stderr


def test_scan_flags_incorrect_fourth_paragraph_transitions(
    git_repo: Path,
) -> None:
    """Series-position signals should distinguish early, penultimate, and final."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(
        git_repo,
        _message(
            "core: establish message parsing",
            "The project provides raw commit text.",
            "Reviewers cannot distinguish its narrative sections.",
            "This commit begins parsing by identifying body paragraphs.",
            "More work will follow.",
        ),
    )
    _commit(
        git_repo,
        _message(
            "core: classify message paragraphs",
            "The project provides parsed message paragraphs.",
            "Reviewers cannot yet interpret each paragraph's role.",
            "This commit continues the workflow by classifying each role.",
            "Subsequent commits will produce a final report.",
        ),
    )
    _commit(
        git_repo,
        _message(
            "core: report message findings",
            "The project provides classified message paragraphs.",
            "Reviewers still cannot consume those classifications.",
            "This commit completes the workflow by reporting each finding.",
            "A future commit will expose more report formats.",
        ),
    )

    scan = json.loads(_helper(git_repo, "inspect", "--base", base).stdout)

    assert (
        "opening fourth paragraph describes future work only vaguely"
        in (scan["commits"][0]["signals"])
    )
    assert (
        "penultimate fourth paragraph does not name the final commit"
        in scan["commits"][1]["signals"]
    )
    assert (
        "penultimate fourth paragraph refers to subsequent commits"
        in scan["commits"][1]["signals"]
    )
    assert (
        "final fourth paragraph promises future work" in scan["commits"][2]["signals"]
    )


def test_two_commit_opener_is_also_penultimate(git_repo: Path) -> None:
    """A two-commit opener should begin the series and point to the final commit."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, _opening_message())
    _commit(git_repo, _final_message())

    scan = json.loads(_helper(git_repo, "inspect", "--base", base).stdout)

    assert scan["commits"][0]["series_role"] == "opening-penultimate"
    assert scan["commits"][0]["signals"] == []
    assert scan["commits"][1]["signals"] == []


def test_default_refinement_completes_after_message_only_rewrite(
    git_repo: Path,
) -> None:
    """Completion should accept changed messages with all snapshots preserved."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    first = _commit(git_repo, _opening_message())
    old_head = _commit(git_repo, "Bad final message\n")
    _helper(git_repo, "start", "--base", base)

    new_head = _commit(git_repo, _final_message(), amend=True)
    _helper(git_repo, "verify", "--base", base)
    _helper(git_repo, "scan", "--base", base)
    state_dir = git_repo / ".git-stage-batch" / "refine-commit-messages"
    scan = json.loads((state_dir / "scan.json").read_text(encoding="utf-8"))
    audit = {
        "schema": 1,
        "mode": "refine",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["fallback message guidelines"],
            "summary": "Four body paragraphs with position-aware transitions.",
        },
        "commits": [
            {
                "sha": entry["sha"],
                "subject": entry["subject"],
                "signals": entry["signals"],
                "verdict": "KEEP",
                "reason": "The message describes one patch outcome.",
                "patch_fidelity": "Every empty test patch is described exactly.",
                "series_transition": (
                    "The message matches its position in the two-commit series."
                ),
            }
            for entry in scan["commits"]
        ],
    }
    (state_dir / "audit.json").write_text(json.dumps(audit), encoding="utf-8")

    _helper(git_repo, "validate-audit", "--base", base)
    _helper(git_repo, "complete", "--base", base)
    completed_checkpoint = (state_dir / "checkpoint.json").read_bytes()
    resumed = _helper(git_repo, "check-resume", check=False)
    repeated = _helper(git_repo, "complete", "--base", base, check=False)
    checkpoint = json.loads((state_dir / "checkpoint.json").read_text(encoding="utf-8"))
    pre = json.loads((state_dir / "pre-commits.json").read_text(encoding="utf-8"))
    post = json.loads((state_dir / "post-commits.json").read_text(encoding="utf-8"))

    assert first == post["commits"][0]["sha"]
    assert old_head != new_head == post["commits"][1]["sha"]
    assert [entry["tree"] for entry in pre["commits"]] == [
        entry["tree"] for entry in post["commits"]
    ]
    assert [entry["author"] for entry in pre["commits"]] == [
        entry["author"] for entry in post["commits"]
    ]
    assert checkpoint["phase"] == "complete"
    assert resumed.returncode != 0
    assert "already complete" in resumed.stderr
    assert repeated.returncode != 0
    assert "already complete" in repeated.stderr
    assert (state_dir / "checkpoint.json").read_bytes() == completed_checkpoint


def test_begin_reword_neutralizes_history_changing_rebase_config(
    git_repo: Path,
) -> None:
    """The controlled stop must preserve boundaries and out-of-scope refs."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    first = _commit(
        git_repo,
        _message(
            "core: establish the message interface",
            "The project provides a stable base for message processing.",
            "Callers cannot yet express the new message workflow.",
            "This commit begins that workflow by defining its interface.",
            "Later commits will connect and report the implementation.",
        ),
    )
    second = _commit(
        git_repo,
        _message(
            "fixup! core: establish the message interface",
            "The project provides the initial message interface.",
            "Callers cannot yet classify messages through the interface.",
            "This commit continues the workflow by adding classification.",
            "The final commit will report the resulting classifications.",
        ),
    )
    _commit(git_repo, _final_message())
    _git(git_repo, "branch", "side-draft", second)
    _git(git_repo, "config", "rebase.abbreviateCommands", "true")
    _git(git_repo, "config", "rebase.autoSquash", "true")
    _git(git_repo, "config", "rebase.updateRefs", "true")
    _git(git_repo, "config", "rebase.rebaseMerges", "true")
    _git(git_repo, "config", "rebase.autoStash", "true")
    _helper(git_repo, "start", "--base", base)

    _helper(
        git_repo,
        "begin-reword",
        "--base",
        base,
        "--position",
        "1",
        "--target",
        first,
    )
    git_dir = Path(_git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    assert (git_dir / "rebase-merge").is_dir()

    _commit(
        git_repo,
        _message(
            "core: establish the refined message interface",
            "The project provides a stable base for message processing.",
            "Callers cannot yet express the refined message workflow.",
            "This commit begins that workflow by defining its interface.",
            "Later commits will connect and report the implementation.",
        ),
        amend=True,
    )
    _helper(git_repo, "verify-head", "--position", "1")
    _git(git_repo, "rebase", "--continue")
    _helper(git_repo, "verify", "--base", base)

    subjects = _git(
        git_repo,
        "log",
        "--reverse",
        "--format=%s",
        f"{base}..HEAD",
    ).stdout.splitlines()
    assert len(subjects) == 3
    assert subjects[1].startswith("fixup!")
    assert _git(git_repo, "rev-parse", "side-draft").stdout.strip() == second


def test_apply_audit_migrates_an_active_legacy_reword_checkpoint(
    git_repo: Path,
) -> None:
    """Aborting an older per-commit stop should preserve its audit and recovery."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    source = _commit(git_repo, "Bad message\n")
    replacement = _message(
        "core: Expose message inspection",
        "The project provides a stable message representation.",
        "Reviewers cannot inspect that representation as a report.",
        "This commit exposes inspection through a report command.",
    )
    state_dir = _start_single_reword(git_repo, base, replacement)
    checkpoint_before = json.loads(
        (state_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    _helper(
        git_repo,
        "begin-reword",
        "--base",
        base,
        "--position",
        "1",
        "--target",
        source,
    )

    _git(git_repo, "rebase", "--abort")
    _helper(git_repo, "validate-audit", "--allow-reword", "--base", base)
    _helper(git_repo, "apply-audit", "--base", base)

    checkpoint_after = json.loads(
        (state_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint_after["recovery_ref"] == checkpoint_before["recovery_ref"]
    assert [event["event"] for event in checkpoint_after["events"]].count("start") == 1
    assert (
        _git(git_repo, "log", "-1", "--format=%B").stdout.rstrip("\n")
        == replacement.rstrip("\n")
    )
    _helper(git_repo, "complete", "--base", base)


def test_apply_audit_rewords_the_series_in_one_validated_pass(
    git_repo: Path,
) -> None:
    """One rewrite plan should replace every REWORD without repeated audits."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    (git_repo / "opening.txt").write_text("opening\n", encoding="utf-8")
    _git(git_repo, "add", "opening.txt")
    _commit(git_repo, "Bad opening message\n")
    middle_message = _message(
        "core: classify message requests",
        "The project provides an interface for message processing.",
        "Callers still cannot classify requests before executing them.",
        "This commit continues the workflow by classifying each request.",
        "The final commit will connect the classified requests.",
    )
    (git_repo / "middle.txt").write_text("middle\n", encoding="utf-8")
    _git(git_repo, "add", "middle.txt")
    middle = _commit(git_repo, middle_message)
    (git_repo / "final.txt").write_text("final\n", encoding="utf-8")
    _git(git_repo, "add", "final.txt")
    _commit(git_repo, "Bad final message\n")
    _git(git_repo, "branch", "side-draft", middle)
    _git(git_repo, "config", "rebase.autoSquash", "true")
    _git(git_repo, "config", "rebase.updateRefs", "true")
    _helper(git_repo, "start", "--base", base)
    _helper(git_repo, "scan", "--base", base)

    state_dir = git_repo / ".git-stage-batch" / "refine-commit-messages"
    scan = json.loads((state_dir / "scan.json").read_text(encoding="utf-8"))
    replacements = {1: _opening_message(), 3: _final_message()}
    audit_entries = []
    for position, entry in enumerate(scan["commits"], start=1):
        audit_entry = {
            "sha": entry["sha"],
            "subject": entry["subject"],
            "signals": entry["signals"],
            "verdict": "REWORD" if position in replacements else "KEEP",
            "reason": "The message is checked against its exact patch and position.",
            "patch_fidelity": "The message describes the complete test patch.",
            "series_transition": "The message matches its role in this three-step series.",
        }
        if position in replacements:
            audit_entry["proposed_message"] = replacements[position]
        audit_entries.append(audit_entry)
    audit = {
        "schema": 1,
        "mode": "refine",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["fallback message guidelines"],
            "summary": "Four body paragraphs with position-aware transitions.",
        },
        "commits": audit_entries,
    }
    (state_dir / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    pre_trees = [entry["tree"] for entry in scan["commits"]]
    pre_authors = [entry["author"] for entry in scan["commits"]]

    _helper(git_repo, "validate-audit", "--allow-reword", "--base", base)
    result = _helper(git_repo, "apply-audit", "--base", base)

    current = json.loads((state_dir / "scan.json").read_text(encoding="utf-8"))
    final_audit = json.loads((state_dir / "audit.json").read_text(encoding="utf-8"))
    checkpoint = json.loads(
        (state_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    messages = _git(
        git_repo,
        "log",
        "--reverse",
        "--format=%B%x00",
        f"{base}..HEAD",
    ).stdout.rstrip("\x00\n").split("\x00\n")

    assert "2 replacement messages" in result.stdout
    assert [message.rstrip("\n") for message in messages] == [
        _opening_message().rstrip("\n"),
        middle_message.rstrip("\n"),
        _final_message().rstrip("\n"),
    ]
    assert [entry["tree"] for entry in current["commits"]] == pre_trees
    assert len(set(pre_trees)) == 3
    assert [entry["author"] for entry in current["commits"]] == pre_authors
    assert [entry["verdict"] for entry in final_audit["commits"]] == [
        "KEEP",
        "KEEP",
        "KEEP",
    ]
    assert all("proposed_message" not in entry for entry in final_audit["commits"])
    assert checkpoint["phase"] == "rewritten"
    assert checkpoint["rewrite_source_head"] == scan["head"]
    assert checkpoint["rewrite_positions"] == [1, 3]
    assert Path(checkpoint["rewrite_helper"]).is_file()
    assert [event["event"] for event in checkpoint["events"]].count(
        "apply-audit"
    ) == 1
    assert (state_dir / "rewrite-entry-1.json").is_file()
    assert (state_dir / "rewrite-entry-2.json").is_file()
    assert (state_dir / "rewrite-entry-3.json").is_file()
    assert _git(git_repo, "rev-parse", "side-draft").stdout.strip() == middle
    _helper(git_repo, "complete", "--base", base)


def test_apply_audit_preserves_mixed_signature_presence(
    git_repo: Path,
) -> None:
    """Each replayed position should restore its own signed or unsigned state."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _enable_fake_signing(git_repo)
    _git(
        git_repo,
        "commit",
        "--allow-empty",
        "-q",
        "--gpg-sign",
        "-F",
        "-",
        input_text=_opening_message(),
    )
    _commit(git_repo, "Bad unsigned message\n")
    state_dir = _start_reword_plan(
        git_repo,
        base,
        {2: _final_message()},
    )
    initial_scan = json.loads(
        (state_dir / "scan.json").read_text(encoding="utf-8")
    )
    assert [entry["signed"] for entry in initial_scan["commits"]] == [True, False]
    _git(git_repo, "config", "commit.gpgSign", "true")

    _helper(git_repo, "apply-audit", "--base", base)

    final_scan = json.loads(
        (state_dir / "scan.json").read_text(encoding="utf-8")
    )
    assert [entry["signed"] for entry in final_scan["commits"]] == [True, False]
    assert [entry["message"] for entry in final_scan["commits"]] == [
        _opening_message().rstrip("\n"),
        _final_message().rstrip("\n"),
    ]
    _helper(git_repo, "complete", "--base", base)


def test_apply_audit_uses_a_helper_outside_the_rewritten_tree(
    git_repo: Path,
) -> None:
    """Historical checkouts must not replace internal rebase callback code."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    installed_helper = (
        git_repo
        / ".agents"
        / "skills"
        / "refine-commit-messages"
        / "scripts"
        / "refine-commit-messages-checkpoint.py"
    )
    installed_helper.parent.mkdir(parents=True)
    installed_helper.write_text(
        "raise SystemExit('historical helper executed')\n",
        encoding="utf-8",
    )
    _git(git_repo, "add", str(installed_helper.relative_to(git_repo)))
    _commit(git_repo, "Bad historical-helper message\n")
    installed_helper.write_bytes(CODEX_HELPER.read_bytes())
    _git(git_repo, "add", str(installed_helper.relative_to(git_repo)))
    current_message = _final_message()
    _commit(git_repo, current_message)
    replacement = _opening_message()
    state_dir = _start_reword_plan(
        git_repo,
        base,
        {1: replacement},
        helper=installed_helper,
    )

    _helper_at(git_repo, installed_helper, "apply-audit", "--base", base)

    checkpoint = json.loads(
        (state_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    git_dir = Path(
        _git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    rewrite_helper = Path(checkpoint["rewrite_helper"])
    assert rewrite_helper.is_file()
    assert rewrite_helper.is_relative_to(git_dir)
    assert rewrite_helper != installed_helper
    final_scan = json.loads(
        (state_dir / "scan.json").read_text(encoding="utf-8")
    )
    assert [entry["message"] for entry in final_scan["commits"]] == [
        replacement.rstrip("\n"),
        current_message.rstrip("\n"),
    ]
    _helper_at(git_repo, installed_helper, "complete", "--base", base)


def test_apply_audit_handles_a_source_subject_larger_than_one_read_block(
    git_repo: Path,
) -> None:
    """Finding the active pick should not depend on a bounded log-file tail."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "x" * 9000 + "\n")
    replacement = _message(
        "core: Expose message inspection",
        "The project provides a stable message representation.",
        "Reviewers cannot inspect that representation as a report.",
        "This commit exposes inspection through a report command.",
    )
    _start_single_reword(git_repo, base, replacement)

    _helper(git_repo, "apply-audit", "--base", base)

    assert (
        _git(git_repo, "log", "-1", "--format=%B").stdout.rstrip("\n")
        == replacement.rstrip("\n")
    )
    _helper(git_repo, "complete", "--base", base)


def test_apply_audit_can_retry_when_a_pre_rebase_hook_blocks_start(
    git_repo: Path,
) -> None:
    """A failed rebase preflight should remain retryable from the checkpoint."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    source_head = _commit(git_repo, "Bad message\n")
    replacement = _message(
        "core: Expose message inspection",
        "The project provides a stable message representation.",
        "Reviewers cannot inspect that representation as a report.",
        "This commit exposes inspection through a report command.",
    )
    _start_single_reword(git_repo, base, replacement)
    git_dir = Path(
        _git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    hook = git_dir / "hooks" / "pre-rebase"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    stopped = _helper(git_repo, "apply-audit", "--base", base, check=False)

    assert stopped.returncode != 0
    assert "one-pass message rewrite did not start" in stopped.stderr
    assert not (git_dir / "rebase-merge").exists()
    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == source_head
    hook.unlink()

    _helper(git_repo, "apply-audit", "--base", base)

    assert (
        _git(git_repo, "log", "-1", "--format=%B").stdout.rstrip("\n")
        == replacement.rstrip("\n")
    )
    _helper(git_repo, "complete", "--base", base)


def test_apply_message_rejects_an_entry_from_another_rewrite_attempt(
    git_repo: Path,
) -> None:
    """Each per-position callback record must stay bound to its checkpoint."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Bad message\n")
    replacement = _message(
        "core: Expose message inspection",
        "The project provides a stable message representation.",
        "Reviewers cannot inspect that representation as a report.",
        "This commit exposes inspection through a report command.",
    )
    state_dir = _start_single_reword(git_repo, base, replacement)
    git_dir = Path(
        _git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    marker = git_dir / "fail-commit-message-once"
    marker.write_text("fail\n", encoding="utf-8")
    hook = git_dir / "hooks" / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        'git_dir=$(git rev-parse --absolute-git-dir)\n'
        'if test -f "$git_dir/fail-commit-message-once"; then\n'
        '  rm "$git_dir/fail-commit-message-once"\n'
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    stopped = _helper(git_repo, "apply-audit", "--base", base, check=False)
    assert stopped.returncode != 0

    entry_path = state_dir / "rewrite-entry-1.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["source_head"] = "0" * 40
    entry_path.write_text(json.dumps(entry), encoding="utf-8")

    continued = _git(git_repo, "rebase", "--continue", check=False)

    assert continued.returncode != 0
    assert (
        "rewrite entry does not match the active checkpoint attempt"
        in continued.stderr
    )
    _git(git_repo, "rebase", "--abort")


def test_apply_audit_resumes_after_a_transient_hook_failure(
    git_repo: Path,
) -> None:
    """A rescheduled amendment should finish from the frozen position record."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Bad message\n")
    _helper(git_repo, "start", "--base", base)
    _helper(git_repo, "scan", "--base", base)
    state_dir = git_repo / ".git-stage-batch" / "refine-commit-messages"
    scan = json.loads((state_dir / "scan.json").read_text(encoding="utf-8"))
    entry = scan["commits"][0]
    replacement = _message(
        "core: expose message inspection",
        "The project provides a stable message representation.",
        "Reviewers cannot inspect that representation as a report.",
        "This commit exposes inspection through a report command.",
    )
    audit = {
        "schema": 1,
        "mode": "refine",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["fallback message guidelines"],
            "summary": "Three body paragraphs for a standalone commit.",
        },
        "commits": [
            {
                "sha": entry["sha"],
                "subject": entry["subject"],
                "signals": entry["signals"],
                "verdict": "REWORD",
                "reason": "The existing message lacks the required narrative.",
                "patch_fidelity": "The proposal describes the complete test patch.",
                "proposed_message": replacement,
            }
        ],
    }
    (state_dir / "audit.json").write_text(json.dumps(audit), encoding="utf-8")

    git_dir = Path(
        _git(git_repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    marker = git_dir / "fail-commit-message-once"
    marker.write_text("fail\n", encoding="utf-8")
    hook = git_dir / "hooks" / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        'git_dir=$(git rev-parse --absolute-git-dir)\n'
        'if test -f "$git_dir/fail-commit-message-once"; then\n'
        '  rm "$git_dir/fail-commit-message-once"\n'
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    stopped = _helper(git_repo, "apply-audit", "--base", base, check=False)

    assert stopped.returncode != 0
    assert "one-pass message rewrite stopped" in stopped.stderr
    assert (git_dir / "rebase-merge").is_dir()
    _git(git_repo, "rebase", "--continue")
    finalized = _helper(git_repo, "finalize-apply", "--base", base)
    assert "1 replacement messages" in finalized.stdout
    assert (
        _git(git_repo, "log", "-1", "--format=%B").stdout.rstrip("\n")
        == replacement.rstrip("\n")
    )
    _helper(git_repo, "complete", "--base", base)


def test_checkpoint_rejects_changes_to_out_of_scope_local_branches(
    git_repo: Path,
) -> None:
    """A side branch into the range must remain at its original object."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    first = _commit(git_repo, _opening_message())
    second = _commit(git_repo, _final_message())
    _git(git_repo, "branch", "side-draft", first)
    _helper(git_repo, "start", "--base", base)

    _git(git_repo, "update-ref", "refs/heads/side-draft", second)
    result = _helper(git_repo, "check-resume", check=False)

    assert result.returncode != 0
    assert "out-of-scope local branch changed" in result.stderr


def test_verify_rejects_a_tree_change(git_repo: Path) -> None:
    """A same-count amend must fail when it changes committed content."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(
        git_repo,
        _message(
            "core: expose message inspection",
            "The project provides a stable message representation.",
            "Reviewers cannot inspect that representation as a report.",
            "This commit exposes inspection through a report command.",
        ),
    )
    _helper(git_repo, "start", "--base", base)
    changed = git_repo / "changed.txt"
    changed.write_text("changed\n", encoding="utf-8")
    _git(git_repo, "add", "changed.txt")
    _git(
        git_repo,
        "commit",
        "--amend",
        "--allow-empty",
        "-q",
        "--no-edit",
    )

    result = _helper(git_repo, "verify", "--base", base, check=False)

    assert result.returncode != 0
    assert "tree changed during message refinement" in result.stderr


def test_verify_rejects_an_author_change(git_repo: Path) -> None:
    """Message-only refinement must preserve the original author header."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(
        git_repo,
        _message(
            "core: expose message inspection",
            "The project provides a stable message representation.",
            "Reviewers cannot inspect that representation as a report.",
            "This commit exposes inspection through a report command.",
        ),
    )
    _helper(git_repo, "start", "--base", base)
    _git(git_repo, "config", "user.name", "Different Author")
    _git(
        git_repo,
        "commit",
        "--amend",
        "--allow-empty",
        "--reset-author",
        "-q",
        "--no-edit",
    )

    result = _helper(git_repo, "verify", "--base", base, check=False)

    assert result.returncode != 0
    assert "author changed during message refinement" in result.stderr


def test_audit_only_validator_accepts_a_complete_reword_proposal(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """Audit-only findings should support validated replacement messages."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Bad message\n")
    scan = json.loads(_helper(git_repo, "inspect", "--base", base).stdout)
    entry = scan["commits"][0]
    audit = {
        "schema": 1,
        "mode": "audit-only",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["fallback message guidelines"],
            "summary": "Three body paragraphs for a standalone commit.",
        },
        "commits": [
            {
                "sha": entry["sha"],
                "subject": entry["subject"],
                "signals": entry["signals"],
                "verdict": "REWORD",
                "reason": "The existing message lacks the required narrative.",
                "patch_fidelity": "The proposal describes the complete patch.",
                "proposed_message": _message(
                    "core: expose message inspection",
                    "The project provides a stable message representation.",
                    "Reviewers cannot inspect that representation as a report.",
                    "This commit exposes inspection through a report command.",
                ),
            }
        ],
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    result = _helper(
        git_repo,
        "validate-audit",
        "--audit-only",
        "--audit-file",
        str(audit_path),
        "--base",
        base,
    )

    assert "audit valid for 1 commits" in result.stdout
    assert not (git_repo / ".git-stage-batch" / "refine-commit-messages").exists()


def test_keep_false_positives_must_cite_a_convention_source(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """A KEEP verdict should not dismiss scanner pressure without provenance."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(git_repo, "Bad message\n")
    scan = json.loads(_helper(git_repo, "inspect", "--base", base).stdout)
    entry = scan["commits"][0]
    audit = {
        "schema": 1,
        "mode": "audit-only",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": {
            "sources": ["CONTRIBUTING.md"],
            "summary": "The repository explicitly permits terse messages.",
        },
        "commits": [
            {
                "sha": entry["sha"],
                "subject": entry["subject"],
                "signals": entry["signals"],
                "verdict": "KEEP",
                "reason": "The local convention permits this shape.",
                "patch_fidelity": "The subject covers the complete empty patch.",
                "signal_false_positives": [
                    {
                        "signal": signal,
                        "reason": "The local terse-message rule overrides this.",
                    }
                    for signal in entry["signals"]
                ],
            }
        ],
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    result = _helper(
        git_repo,
        "validate-audit",
        "--audit-only",
        "--audit-file",
        str(audit_path),
        "--base",
        base,
        check=False,
    )

    assert result.returncode != 0
    assert "requires signal, source, and reason" in result.stderr


def test_completion_cannot_be_marked_directly(git_repo: Path) -> None:
    """The generic progress marker must not bypass the completion gate."""
    base = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit(
        git_repo,
        _message(
            "core: expose message inspection",
            "The project provides a stable message representation.",
            "Reviewers cannot inspect that representation as a report.",
            "This commit exposes inspection through a report command.",
        ),
    )
    _helper(git_repo, "start", "--base", base)

    result = _helper(
        git_repo,
        "mark",
        "--phase",
        "complete",
        check=False,
    )

    assert result.returncode != 0
    assert "use the complete command" in result.stderr
