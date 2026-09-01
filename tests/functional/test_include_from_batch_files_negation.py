"""Regression coverage for negated include-from file scopes."""

from .conftest import get_staged_files, git_stage_batch


def test_include_from_batch_files_excludes_negated_untracked_path(
    functional_repo,
):
    """A negated pattern must keep a matching batch file out of the index."""
    included = functional_repo / "included.txt"
    docs = functional_repo / "docs"
    docs.mkdir()
    excluded = docs / "excluded.md"
    included.write_text("include me\n")
    excluded.write_text("leave me unstaged\n")

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "include",
        "--to",
        "saved",
        "--files",
        "included.txt",
        "docs/excluded.md",
    )

    result = git_stage_batch(
        "include",
        "--from",
        "saved",
        "--files",
        "**",
        "!docs/excluded.md",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert get_staged_files() == ["included.txt"]
