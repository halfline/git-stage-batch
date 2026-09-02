"""Regression coverage for inverse replay above a whole-file transform."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_discard_replacements_restores_advanced_whole_file_source(functional_repo):
    """Upper replacements must retain a subsequently peeled whole-file source."""
    path = functional_repo / "tool.c"
    base = "base one\nold authority one\nbase two\nold authority two\ntail\n"
    predecessor = base.replace("base two\n", "query state\nbase two\n")
    target = predecessor.replace(
        "old authority one\n",
        "new authority one\n",
    ).replace(
        "old authority two\n",
        "new authority two\n",
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    for target_line, predecessor_line in (
        ("new authority one", "old authority one\n"),
        ("new authority two", "old authority two\n"),
    ):
        view = git_stage_batch(
            "show",
            "--file",
            "tool.c",
            "--page",
            "all",
        ).stdout
        replacement = git_stage_batch(
            "discard",
            "--to",
            "authority-cleanup",
            "--line",
            str(_display_id_for_text(view, target_line)),
            "--as-stdin",
            "--no-auto-advance",
            input_text=predecessor_line,
            check=False,
        )
        assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor

    view = git_stage_batch(
        "show",
        "--file",
        "tool.c",
        "--page",
        "all",
    ).stdout
    first = _display_id_for_text(view, "base one")
    last = _display_id_for_text(view, "tail")
    lower = git_stage_batch(
        "discard",
        "--to",
        "state-query",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=base,
        check=False,
    )
    assert lower.returncode == 0, lower.stderr
    assert path.read_text() == base

    replay_lower = git_stage_batch(
        "apply",
        "--from",
        "state-query",
        "--file",
        "tool.c",
        check=False,
    )
    assert replay_lower.returncode == 0, replay_lower.stderr
    assert path.read_text() == predecessor

    replay_upper = git_stage_batch(
        "apply",
        "--from",
        "authority-cleanup",
        "--file",
        "tool.c",
        check=False,
    )
    assert replay_upper.returncode == 0, replay_upper.stderr
    assert path.read_text() == target

    inverse = git_stage_batch(
        "discard",
        "--from",
        "authority-cleanup",
        "--file",
        "tool.c",
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == predecessor
