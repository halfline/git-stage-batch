"""Regression coverage for wide replacements with retained renamed lines."""

import subprocess

from .conftest import git_stage_batch


def _display_ids(view):
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def test_wide_replacement_keeps_requested_names_and_nested_loop(functional_repo):
    """A replacement must not restore old names or drop a requested loop header."""
    path = functional_repo / "topology.c"
    baseline = (
        "void build(void)\n"
        "{\n"
        "\tcreate(OLD_NAME);\n"
        "\n"
        "\tif (enabled) {\n"
        "\t\tfor (n = 0; n < OLD_LIMIT; n++) {\n"
        "\t\t\temit();\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )
    target = (
        "void build_many(unsigned int count)\n"
        "{\n"
        "\tunsigned int output;\n"
        "\n"
        "\tfor (output = 0; output < count; output++)\n"
        "\t\tcreate(NEW_NAME);\n"
        "\n"
        "\tif (enabled) {\n"
        "\t\tfor (n = 0; n < NEW_LIMIT; n++) {\n"
        "\t\t\temit();\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
        "\n"
        "void build(void)\n"
        "{\n"
        "\tbuild_many(1);\n"
        "}\n"
    )
    predecessor = (
        "void build(void)\n"
        "{\n"
        "\tcreate(NEW_NAME);\n"
        "\n"
        "\tif (enabled) {\n"
        "\t\tfor (n = 0; n < NEW_LIMIT; n++) {\n"
        "\t\t\temit();\n"
        "\t\t}\n"
        "\t}\n"
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
        ["git", "commit", "-m", "Add topology baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    ids = _display_ids(view)
    replacement = git_stage_batch(
        "discard",
        "--to",
        "multi-output",
        "--line",
        f"{min(ids)}-{max(ids)}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor

    saved = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/multi-output:topology.c",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert saved == target

    git_stage_batch("stop")
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add single-output predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    replay = git_stage_batch(
        "apply",
        "--from",
        "multi-output",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
