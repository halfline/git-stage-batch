"""Projection coverage for batch capture command paths."""

import subprocess

import pytest

from .conftest import git_stage_batch


def _prepare_replacement_against_older_batch(functional_repo):
    file_path = functional_repo / "sections.txt"
    original = (
        "section1\n"
        "x\n"
        "old\n"
        "y\n"
        "section2\n"
        "x\n"
        "old\n"
        "y\n"
        "end\n"
    )
    file_path.write_text(original)
    subprocess.run(
        ["git", "add", "sections.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add repeated sections"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    git_stage_batch("new", "saved")

    file_path.write_text("section2\nx\nold\ny\nend\n")
    subprocess.run(
        ["git", "add", "sections.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Remove first section"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    file_path.write_text("section2\nx\nNEW\ny\nend\n")
    git_stage_batch("start", "--no-auto-advance")
    return file_path, original


def _batch_file_content(functional_repo, file_path="sections.txt"):
    return subprocess.run(
        [
            "git",
            "show",
            f"refs/git-stage-batch/batches/saved:{file_path}",
        ],
        check=True,
        cwd=functional_repo,
        capture_output=True,
        text=True,
    ).stdout


def _expected_replacement(original):
    return original.replace(
        "section2\nx\nold\n",
        "section2\nx\nNEW\n",
    )


def _prepare_insertion_against_older_batch(functional_repo):
    file_path = functional_repo / "insertions.txt"
    original = "top\n\ntop\n\nbottom\n"
    file_path.write_text(original)
    subprocess.run(
        ["git", "add", "insertions.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add repeated insertion boundaries"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    git_stage_batch("new", "saved")

    head_content = "prefix-a\nprefix-b\n" + original
    file_path.write_text(head_content)
    subprocess.run(
        ["git", "add", "insertions.txt"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add later prefix"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    file_path.write_text(
        "prefix-a\n"
        "prefix-b\n"
        "top\n"
        "\n"
        "first\n"
        "second\n"
        "\n"
        "top\n"
        "\n"
        "bottom\n"
    )
    git_stage_batch("start", "--no-auto-advance")
    return original


def _insertion_command_arguments(route):
    if route == "bulk-discard":
        return ("discard", "--to", "saved", "--files", "insertions.txt")
    if route == "file-discard":
        return ("discard", "--to", "saved", "--file", "insertions.txt")
    if route == "selected-discard":
        return ("discard", "--to", "saved")
    if route == "selected-include":
        return ("include", "--to", "saved")
    return ("include", "--to", "saved", "--file", "insertions.txt")


@pytest.mark.parametrize(
    "route",
    [
        "bulk-discard",
        "file-discard",
        "selected-discard",
        "selected-include",
        "file-include",
    ],
)
def test_insertions_project_without_claiming_newer_context(
    functional_repo,
    route,
):
    """Whole-change saves must project only changed lines onto old batches."""
    original = _prepare_insertion_against_older_batch(functional_repo)

    result = git_stage_batch(
        *_insertion_command_arguments(route),
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_file_content(functional_repo, "insertions.txt") == original.replace(
        "\ntop\n",
        "\nfirst\nsecond\n\ntop\n",
        1,
    )


def test_bulk_discard_projects_onto_older_batch_baseline(functional_repo):
    """Bulk discard must project collected live coordinates before storage."""
    _file_path, original = _prepare_replacement_against_older_batch(
        functional_repo
    )

    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--files",
        "sections.txt",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_file_content(functional_repo) == _expected_replacement(original)


def test_selected_discard_projects_cached_comparison_onto_batch(functional_repo):
    """Selected discard must use the comparison snapshot that built its hunk."""
    _file_path, original = _prepare_replacement_against_older_batch(
        functional_repo
    )

    result = git_stage_batch(
        "discard",
        "--to",
        "saved",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_file_content(functional_repo) == _expected_replacement(original)


def test_selected_include_projects_index_onto_older_batch(functional_repo):
    """Selected include must translate index coordinates to the batch baseline."""
    _file_path, original = _prepare_replacement_against_older_batch(
        functional_repo
    )

    result = git_stage_batch(
        "include",
        "--to",
        "saved",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_file_content(functional_repo) == _expected_replacement(original)


def test_explicit_include_projects_comparison_onto_older_batch(functional_repo):
    """Explicit file include must bind its rendered comparison to the batch."""
    _file_path, original = _prepare_replacement_against_older_batch(
        functional_repo
    )

    result = git_stage_batch(
        "include",
        "--to",
        "saved",
        "--file",
        "sections.txt",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _batch_file_content(functional_repo) == _expected_replacement(original)
