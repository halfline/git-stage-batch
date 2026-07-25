"""Projection coverage for batch capture command paths."""

import subprocess

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


def _batch_file_content(functional_repo):
    return subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/saved:sections.txt",
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
