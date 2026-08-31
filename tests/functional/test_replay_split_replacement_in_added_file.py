"""Regression coverage for replaying a split replacement in an added file."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_split_wrapped_call_replays_to_exact_added_file(functional_repo):
    """A one-line transform plus adjacent deletions must replay as one concern."""
    baseline = functional_repo / "README"
    baseline.write_text("baseline\n")
    subprocess.run(
        ["git", "add", baseline.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    path = functional_repo / "topology.c"
    target = (
        "int setup(void)\n"
        "{\n"
        "\tunsigned int output_index = 0;\n"
        "\n"
        "\tconnector = connector_init(device,\n"
        "\t\t\t\t   output_index++);\n"
        "\treturn 0;\n"
        "}\n"
    )
    predecessor = (
        "int setup(void)\n{\n\tconnector = connector_init(device);\n\treturn 0;\n}\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "stable-output")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    call = _display_id_for_text(view, "connector = connector_init(device,")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "stable-output",
        "--line",
        str(call),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\tconnector = connector_init(device);\n",
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    continuation = _display_id_for_text(view, "output_index++);")
    discard_continuation = git_stage_batch(
        "discard",
        "--to",
        "stable-output",
        "--line",
        str(continuation),
        "--no-auto-advance",
        check=False,
    )
    assert discard_continuation.returncode == 0, discard_continuation.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    declaration = _display_id_for_text(view, "unsigned int output_index = 0;")
    discard_declaration = git_stage_batch(
        "discard",
        "--to",
        "stable-output",
        "--line",
        f"{declaration}-{declaration + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert discard_declaration.returncode == 0, discard_declaration.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch("apply", "--from", "stable-output", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
