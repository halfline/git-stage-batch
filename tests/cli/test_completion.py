"""Tests for CLI completion helpers."""

from pathlib import Path

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


def test_bash_completion_covers_journal_command_and_options():
    """Installed Bash completion should expose diagnostic journal controls."""
    completion = (
        Path(__file__).resolve().parents[2] / "completions" / "git-stage-batch"
    ).read_text()

    assert 'validate journal"' in completion
    assert "--path --purge --all --porcelain" in completion
