from .conftest import git_stage_batch
def test_discard_added_file_inner_span_accepts_empty_stdin(functional_repo):
    """An empty replacement can remove part of an added file."""
    path = functional_repo / "grant.h"
    target = "keep first\nremove one\nremove two\nkeep last\n"
    predecessor = "keep first\nkeep last\n"
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    preview = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--line",
        "1-4",
        "--no-advance",
        check=False,
    )
    assert preview.returncode == 0, preview.stderr

    result = git_stage_batch(
        "discard",
        "--to",
        "remove-added-lines",
        "--file",
        path.name,
        "--line",
        "2-3",
        "--as-stdin",
        "--no-auto-advance",
        input_text="",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "remove-added-lines",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    inverse = git_stage_batch(
        "discard",
        "--from",
        "remove-added-lines",
        "--file",
        path.name,
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == predecessor
