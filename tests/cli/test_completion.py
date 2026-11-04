"""Tests for CLI completion helpers."""

from pathlib import Path
import subprocess

import pytest

from git_stage_batch.cli.completion import list_file_completion_candidates


def test_list_file_completion_candidates_uses_changed_files(monkeypatch):
    """Live completion should use changed files and expose directories."""
    monkeypatch.setattr(
        "git_stage_batch.cli.completion.list_changed_files",
        lambda: ["src/auth.py", "src/config/settings.py", "docs/guide.md"],
    )

    candidates = list_file_completion_candidates("s")

    assert "src/" in candidates
    assert "src/auth.py" in candidates
    assert "src/config/" in candidates


def test_list_file_completion_candidates_uses_batch_files(monkeypatch):
    """Batch completion should resolve against batch contents."""
    monkeypatch.setattr(
        "git_stage_batch.cli.completion.list_batch_files",
        lambda _name: ["src/auth.py", "tests/test_auth.py"],
    )

    candidates = list_file_completion_candidates("src/a", from_batch="feature")

    assert candidates == ["src/auth.py"]


def test_list_file_completion_candidates_preserves_negation(monkeypatch):
    """Negated file-pattern completion should preserve the leading !."""
    monkeypatch.setattr(
        "git_stage_batch.cli.completion.list_changed_files",
        lambda: ["dir/keep.py", "dir/exception.py"],
    )

    candidates = list_file_completion_candidates("!dir/e")

    assert candidates == ["!dir/exception.py"]


def test_list_file_completion_candidates_treats_wildmatch_as_directory_prefix(monkeypatch):
    """Wildcard-bearing tokens should complete from their literal directory prefix."""
    monkeypatch.setattr(
        "git_stage_batch.cli.completion.list_changed_files",
        lambda: ["src/auth.py", "src/core/models.py", "tests/test_auth.py"],
    )

    candidates = list_file_completion_candidates("src/**/")

    assert "src/auth.py" in candidates
    assert "src/core/" in candidates


@pytest.mark.parametrize(
    ("command", "prefix", "expected"),
    [
        ("", "jour", ["journal"]),
        ("journal", "--", ["--path", "--purge", "--all", "--porcelain"]),
        ("", "install-", ["install-assets"]),
        ("install-assets", "--g", ["--global"]),
        ("install-assets", "cod", ["codex-skills"]),
    ],
)
def test_bash_completion_for_assets_and_journal(command, prefix, expected):
    """Exercise command and option completion through the shipped Bash code."""
    completion = (
        Path(__file__).resolve().parents[2] / "completions" / "git-stage-batch"
    )
    script = '''
source "$1"
if [[ -n "$2" ]]; then
    COMP_WORDS=(git-stage-batch "$2" "$3")
    COMP_CWORD=2
else
    COMP_WORDS=(git-stage-batch "$3")
    COMP_CWORD=1
fi
_init_completion() {
    words=("${COMP_WORDS[@]}")
    cword=$COMP_CWORD
    cur=${words[cword]}
    prev=${words[cword-1]}
}
_git_stage_batch
printf '%s\\n' "${COMPREPLY[@]}"
'''
    result = subprocess.run(
        ["bash", "-c", script, "completion-test", str(completion), command, prefix],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == expected


def test_bash_completion_covers_fixup_actions_and_options():
    """Installed Bash completion should expose both fixup workflows."""
    completion = (
        Path(__file__).resolve().parents[2] / "completions" / "git-stage-batch"
    ).read_text()

    assert "fixup suggest-fixup rewrite new" in completion
    assert 'compgen -W "suggest create"' in completion
    assert 'compgen -W "--plan --dry-run --partial --porcelain"' in completion
    assert "--plan)" in completion


def test_bash_completion_covers_rewrite_actions_and_options():
    """Installed Bash completion should expose refinement inspection commands."""
    completion = (
        Path(__file__).resolve().parents[2] / "completions" / "git-stage-batch"
    ).read_text()

    assert "fixup suggest-fixup rewrite new" in completion
    assert (
        'compgen -W "scan validate resolve apply status continue abort verify"'
        in completion
    )
    assert 'compgen -W "--output --porcelain"' in completion
    assert "--output)" in completion
    assert 'compgen -W "--workspace --porcelain"' in completion
    assert "--workspace)" in completion
    assert 'compgen -W "--workspace --accept --porcelain"' in completion
    assert "--workspace)" in completion
    assert (
        'compgen -W "--workspace --allow-published-ref --porcelain"'
        in completion
    )
