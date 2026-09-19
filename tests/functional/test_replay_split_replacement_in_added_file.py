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


def test_split_replacement_and_disjoint_additions_replay_together(functional_repo):
    """An added-file replacement must replay alongside separate owned lines."""
    path = functional_repo / "supervisor.rs"
    target = (
        "use std::collections::HashMap;\n"
        "struct Running;\n"
        "impl Running {\n"
        "    fn stop(&self) {}\n"
        "    fn healthy(&self) -> bool { true }\n"
        "}\n"
        "fn main() {\n"
        "    let mut running = HashMap::<u32, Running>::new();\n"
        "    running.insert(1, Running);\n"
        "}\n"
    )
    predecessor = (
        "struct Running;\n"
        "impl Running {\n"
        "    fn stop(&self) {}\n"
        "}\n"
        "fn main() {\n"
        "    let running = Running;\n"
        "    running.stop();\n"
        "}\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "multi-output")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    body_start = _display_id_for_text(view, "let mut running = HashMap")
    body_end = _display_id_for_text(view, "running.insert")
    replacement = git_stage_batch(
        "discard", "--to", "multi-output", "--line",
        f"{body_start}-{body_end}", "--as-stdin", "--no-auto-advance",
        input_text="    let running = Running;\n    running.stop();\n",
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    import_line = _display_id_for_text(view, "use std::collections::HashMap")
    git_stage_batch("discard", "--to", "multi-output", "--line", str(import_line), "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    helper_line = _display_id_for_text(view, "fn healthy")
    git_stage_batch("discard", "--to", "multi-output", "--line", str(helper_line), "--no-auto-advance")
    assert path.read_text() == predecessor

    replay = git_stage_batch("apply", "--from", "multi-output", "--file", path.name, check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
