"""Regression coverage for a replacement with a renamed fallback body."""

import subprocess

from .conftest import git_stage_batch


def _display_ids(view: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _display_id_containing(view: str, needle: str) -> int:
    matches = [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line and needle in line
    ]
    assert len(matches) == 1, (needle, matches, view)
    return matches[0]


def test_discard_replacement_retains_renamed_fallback(functional_repo):
    """The supplied predecessor must retain fallback lines renamed elsewhere."""
    path = functional_repo / "connector.c"
    baseline = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\t/* Use the default modes list. */\n"
        "\tcount = add_modes(OLD_MAX, OLD_HEIGHT);\n"
        "\tprefer_mode(OLD_DEFAULT, OLD_DEFAULT_HEIGHT);\n"
        "\n"
        "\treturn count;\n"
        "}\n"
    )
    target = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\tcount = add_edid_modes();\n"
        "\tif (!count) {\n"
        "\t\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t\t  NEW_HEIGHT);\n"
        "\t\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t\t    NEW_DEFAULT_HEIGHT);\n"
        "\t}\n"
        "\n"
        "\treturn count;\n"
        "}\n"
    )
    predecessor_fragment = (
        "\t/* Use the default modes list. */\n"
        "\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t  NEW_HEIGHT);\n"
        "\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t    NEW_DEFAULT_HEIGHT);\n"
    )
    predecessor = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        f"{predecessor_fragment}"
        "\n"
        "\treturn count;\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add connector baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    ids = _display_ids(view)
    result = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        f"{min(ids)}-{max(ids)}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=predecessor_fragment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor
    saved = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/connector-edid:connector.c",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert saved == target
