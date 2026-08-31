"""Regression coverage for rejoining a sifted added-file replacement."""

from .conftest import git_stage_batch


def test_reset_sifted_added_file_replacement_back_to_existing_batch(
    functional_repo,
):
    """A narrowed replacement can rejoin its original multi-file batch."""
    guide = functional_repo / "guide.md"
    support = functional_repo / "support.c"
    predecessor = (
        "# Capture grants\n"
        "\n"
        "Create a normal grant for the current compositor.\n"
        "\n"
        "| Flag | Caller |\n"
        "|---|---|\n"
        "| none | Current master |\n"
        "\n"
        "Grant files remain bound to that master.\n"
    )
    target = predecessor.replace(
        "Create a normal grant for the current compositor.\n",
        "Create a normal grant for the current compositor.\n"
        "Create an administrative grant for diagnostic tools.\n",
    ).replace(
        "| none | Current master |\n",
        "| none | Current master |\n"
        "| admin | Host administrator |\n",
    ).replace(
        "Grant files remain bound to that master.\n",
        "Normal grant files remain bound to that master.\n"
        "Administrative grants follow the current safe owner.\n",
    )

    guide.write_text(target)
    support.write_text("int grant_support;\n")

    git_stage_batch("start", "--no-auto-advance")
    for path in (support, guide):
        captured = git_stage_batch(
            "discard",
            "--to",
            "administrative-grant",
            "--file",
            path.name,
            "--no-auto-advance",
            check=False,
        )
        assert captured.returncode == 0, captured.stderr

    separated = git_stage_batch(
        "reset",
        "--from",
        "administrative-grant",
        "--to",
        "guide-rework",
        "--file",
        guide.name,
        check=False,
    )
    assert separated.returncode == 0, separated.stderr

    guide.write_text(predecessor)
    sifted = git_stage_batch(
        "sift",
        "--from",
        "guide-rework",
        "--to",
        "guide-rework",
        check=False,
    )
    assert sifted.returncode == 0, sifted.stderr

    rejoined = git_stage_batch(
        "reset",
        "--from",
        "guide-rework",
        "--to",
        "administrative-grant",
        "--file",
        guide.name,
        check=False,
    )
    assert rejoined.returncode == 0, rejoined.stderr

    replay = git_stage_batch(
        "apply",
        "--from",
        "administrative-grant",
        "--file",
        guide.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert guide.read_text() == target
