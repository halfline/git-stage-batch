"""Regression coverage for reversing a sifted slice of an added file."""

import subprocess

from .conftest import git_stage_batch


def test_discard_sifted_added_file_slice_preserves_predecessor(functional_repo):
    """Reversing a narrow sifted batch must not remove the whole added file."""
    path = functional_repo / "frame.h"
    target = (
        "#ifndef FRAME_H\n"
        "#define FRAME_H\n"
        "\n"
        "/**\n"
        " * struct frame - Render state\n"
        " * @base: Base state\n"
        " * @stage: Immutable render stage\n"
        " */\n"
        "struct frame {\n"
        "    int base;\n"
        "    int stage;\n"
        "};\n"
        "\n"
        "#endif\n"
    )
    predecessor = target.replace(
        " * @stage: Immutable render stage\n", ""
    ).replace("    int stage;\n", "")
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    captured = git_stage_batch(
        "discard",
        "--to",
        "frame-stage",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert captured.returncode == 0, captured.stderr
    assert not path.exists()

    subprocess.run(
        [
            "git",
            "restore",
            "--source=refs/git-stage-batch/batches/frame-stage",
            "--worktree",
            "--",
            path.name,
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(predecessor)

    sifted = git_stage_batch(
        "sift",
        "--from",
        "frame-stage",
        "--to",
        "frame-stage",
        check=False,
    )
    assert sifted.returncode == 0, sifted.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "frame-stage",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    reverse = git_stage_batch(
        "discard",
        "--from",
        "frame-stage",
        "--file",
        path.name,
        check=False,
    )
    assert reverse.returncode == 0, reverse.stderr
    assert path.exists()
    assert path.read_text() == predecessor
