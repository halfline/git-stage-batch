"""Functional coverage for include --file rename handling."""

import subprocess
from pathlib import Path

from .conftest import get_staged_files, git_stage_batch


def _semantic_version_of_transient_test(content: str) -> str:
    old_content = content.replace(
        "Functional tests for include --line transient batch staging.",
        "Functional tests for semantic line-level staging.",
    )
    old_content = old_content.replace(
        "test_include_line_transient_staging_",
        "test_semantic_partial_staging_",
    )
    old_content = old_content.replace(
        "test_include_line_uses_batch_order_for_",
        "test_semantic_partial_staging_falls_back_for_",
    )
    return old_content.replace(
        "test_include_line_batch_round_trip_without_intervening_tree_change",
        "test_semantic_partial_staging_batch_round_trip_without_intervening_tree_change",
    )


def test_include_files_stages_renamed_new_path_after_earlier_path(functional_repo):
    source_test = Path(__file__).with_name("test_include_line_transient_staging.py")
    new_content = source_test.read_text()
    old_content = _semantic_version_of_transient_test(new_content)

    old_path = functional_repo / "tests" / "functional" / "test_semantic_partial_staging.py"
    new_path = functional_repo / "tests" / "functional" / "test_include_line_transient_staging.py"
    command_test_path = functional_repo / "tests" / "commands" / "test_include.py"
    old_path.parent.mkdir(parents=True)
    command_test_path.parent.mkdir(parents=True)
    old_path.write_text(old_content)
    command_test_path.write_text("base\n")
    subprocess.run(
        [
            "git",
            "add",
            "tests/functional/test_semantic_partial_staging.py",
            "tests/commands/test_include.py",
        ],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add tests"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )

    old_path.rename(new_path)
    new_path.write_text(new_content)
    command_test_path.write_text("changed\n")

    git_stage_batch("start")
    result = git_stage_batch(
        "include",
        "--files",
        "tests/commands/test_include.py",
        "tests/functional/test_semantic_partial_staging.py",
        "tests/functional/test_include_line_transient_staging.py",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Failed to apply hunk" not in result.stderr
    assert "tests/functional/test_include_line_transient_staging.py" in get_staged_files()

    staged_content = subprocess.run(
        [
            "git",
            "show",
            ":tests/functional/test_include_line_transient_staging.py",
        ],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert staged_content == new_content

    staged_deletions = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "--no-renames",
            "--diff-filter=D",
        ],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "tests/functional/test_semantic_partial_staging.py" in staged_deletions
