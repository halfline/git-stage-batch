"""Regression coverage for replay between replaced added-file neighbors."""

from .conftest import git_stage_batch


def _display_ids(view):
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _view(git_stage_batch, path, batch=None):
    args = ["show"]
    if batch is not None:
        args.extend(["--from", batch])
    args.extend(["--file", path.name, "--page", "all"])
    return git_stage_batch(*args).stdout


def test_insertion_replays_between_neighbors_replaced_by_another_batch(
    functional_repo,
):
    """An owned call remains actionable when both neighboring lines change."""
    path = functional_repo / "grant-test.c"
    target_before_successor_replacement = (
        "static int attachment_lifecycle(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "int main(void)\n"
        "{\n"
        "\tputs(\"old grant\");\n"
        "\tif (attachment_lifecycle())\n"
        "\t\treturn 1;\n"
        "\tputs(\"old setup\");\n"
        "\treturn 0;\n"
        "}\n"
    )
    predecessor = (
        "int main(void)\n"
        "{\n"
        "\tputs(\"old grant\");\n"
        "\tputs(\"old setup\");\n"
        "\treturn 0;\n"
        "}\n"
    )
    successor = predecessor.replace("old grant", "new grant").replace(
        "old setup", "new setup"
    )
    target = target_before_successor_replacement.replace(
        "old grant", "new grant"
    ).replace("old setup", "new setup")
    path.write_text(target_before_successor_replacement)

    git_stage_batch("start", "--no-auto-advance")

    view = _view(git_stage_batch, path)
    helper = _display_id_for_text(view, "static int attachment_lifecycle")
    main = _display_id_for_text(view, "int main(void)")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--line",
        f"{helper}-{main - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = _view(git_stage_batch, path)
    call = _display_id_for_text(view, "if (attachment_lifecycle())")
    setup = _display_id_for_text(view, 'puts("old setup")')
    peeled = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--line",
        f"{call}-{setup - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == predecessor

    path.write_text(successor)
    view = _view(git_stage_batch, path)
    new_grant = _display_id_for_text(view, 'puts("new grant")')
    peeled = git_stage_batch(
        "discard",
        "--to",
        "context-update",
        "--line",
        str(new_grant),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text='\tputs("old grant");\n',
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = _view(git_stage_batch, path)
    new_setup = _display_id_for_text(view, 'puts("new setup")')
    peeled = git_stage_batch(
        "discard",
        "--to",
        "context-update",
        "--line",
        str(new_setup),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text='\tputs("old setup");\n',
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "context-update",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == successor

    view = _view(git_stage_batch, path, "monitor-detach")
    call = _display_id_for_text(view, "if (attachment_lifecycle())")
    ids = _display_ids(view)
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-detach",
        "--line",
        f"{min(ids)}-{call - 1}",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr

    view = _view(git_stage_batch, path, "monitor-detach")
    call = _display_id_for_text(view, "if (attachment_lifecycle())")
    ids = _display_ids(view)
    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-detach",
        "--line",
        f"{call}-{max(ids)}",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
