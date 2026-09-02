"""Regression coverage for plain apply after reviewing one batch file."""

from .conftest import git_stage_batch


def test_plain_apply_after_file_review_restores_entire_batch(functional_repo):
    readme = functional_repo / "README.md"
    main = functional_repo / "src" / "main.py"
    readme_target = "# Reviewed file\n"
    main_target = "def main():\n    return 'same batch'\n"

    readme.write_text(readme_target)
    main.write_text(main_target)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "two-file-change",
        "--file",
        str(readme.relative_to(functional_repo)),
        "--no-auto-advance",
    )
    git_stage_batch(
        "discard",
        "--to",
        "two-file-change",
        "--file",
        str(main.relative_to(functional_repo)),
        "--no-auto-advance",
    )

    git_stage_batch(
        "show",
        "--from",
        "two-file-change",
        "--file",
        str(main.relative_to(functional_repo)),
        "--page",
        "all",
    )
    applied = git_stage_batch(
        "apply",
        "--from",
        "two-file-change",
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    assert readme.read_text() == readme_target
    assert main.read_text() == main_target


def test_plain_apply_after_batch_file_list_restores_entire_batch(functional_repo):
    readme = functional_repo / "README.md"
    main = functional_repo / "src" / "main.py"
    readme_target = "# Listed batch file\n"
    main_target = "def main():\n    return 'same listed batch'\n"

    readme.write_text(readme_target)
    main.write_text(main_target)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "two-file-change",
        "--file",
        str(readme.relative_to(functional_repo)),
        "--no-auto-advance",
    )
    git_stage_batch(
        "discard",
        "--to",
        "two-file-change",
        "--file",
        str(main.relative_to(functional_repo)),
        "--no-auto-advance",
    )

    git_stage_batch(
        "show",
        "--from",
        "two-file-change",
        "--files",
        "**",
    )
    applied = git_stage_batch(
        "apply",
        "--from",
        "two-file-change",
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    assert readme.read_text() == readme_target
    assert main.read_text() == main_target
