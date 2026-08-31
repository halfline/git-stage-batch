"""Regression coverage for replay after layered edits to an added file."""

from .conftest import git_stage_batch


def _display_ids_for_text(view, text):
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if text in line and "[#" in line
    ]


def _single_display_id(view, text):
    matches = _display_ids_for_text(view, text)
    assert len(matches) == 1, matches
    return matches[0]


def test_multiregion_replay_after_prior_added_file_replacement(functional_repo):
    """Three reconnect regions replay above earlier replacement and deletion peels."""
    path = functional_repo / "capture.c"
    target = (
        "static int drain_stopped(struct bridge *bridge);\n"
        "\n"
        "int start_capture(void)\n"
        "{\n"
        "\tint flags = EXCLUSIVE | CURSOR;\n"
        "\n"
        "\treturn flags;\n"
        "}\n"
        "\n"
        "int stop_capture(struct bridge *bridge, int status)\n"
        "{\n"
        "\tif (!status)\n"
        "\t\tstatus = drain_stopped(bridge);\n"
        "\treturn status;\n"
        "}\n"
        "\n"
        "void complete_frame(struct frame *frame, struct event *event)\n"
        "{\n"
        "\tframe->damage = event->damage;\n"
        "}\n"
        "\n"
        "static int drain_stopped(struct bridge *bridge)\n"
        "{\n"
        "\tfor (;;) {\n"
        "\t\tint status = dispatch_events(bridge);\n"
        "\n"
        "\t\tif (status)\n"
        "\t\t\treturn status;\n"
        "\t}\n"
        "}\n"
        "\n"
        "void on_ready(void)\n"
        "{\n"
        "}\n"
    )
    after_cursor = target.replace("EXCLUSIVE | CURSOR", "EXCLUSIVE")
    after_damage = after_cursor.replace("\tframe->damage = event->damage;\n", "")
    predecessor = (
        after_damage.replace("static int drain_stopped(struct bridge *bridge);\n", "")
        .replace("\tif (!status)\n\t\tstatus = drain_stopped(bridge);\n", "")
        .replace(
            "static int drain_stopped(struct bridge *bridge)\n"
            "{\n"
            "\tfor (;;) {\n"
            "\t\tint status = dispatch_events(bridge);\n"
            "\n"
            "\t\tif (status)\n"
            "\t\t\treturn status;\n"
            "\t}\n"
            "}\n"
            "\n",
            "",
        )
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    flags = _single_display_id(view, "int flags = EXCLUSIVE | CURSOR")
    cursor = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        str(flags),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\tint flags = EXCLUSIVE;\n",
        check=False,
    )
    assert cursor.returncode == 0, cursor.stderr
    assert path.read_text() == after_cursor

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    damage = _single_display_id(view, "frame->damage")
    peeled_damage = git_stage_batch(
        "discard",
        "--to",
        "damage-metadata",
        "--line",
        str(damage),
        "--no-auto-advance",
        check=False,
    )
    assert peeled_damage.returncode == 0, peeled_damage.stderr
    assert path.read_text() == after_damage

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    signatures = _display_ids_for_text(view, "static int drain_stopped")
    assert len(signatures) == 2, signatures
    prototype, definition = signatures
    prototype_peel = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        str(prototype),
        "--no-auto-advance",
        check=False,
    )
    assert prototype_peel.returncode == 0, prototype_peel.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    call_start = _single_display_id(view, "if (!status)")
    call_end = _single_display_id(view, "status = drain_stopped")
    call_peel = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        f"{call_start}-{call_end}",
        "--no-auto-advance",
        check=False,
    )
    assert call_peel.returncode == 0, call_peel.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    definition = _single_display_id(view, "static int drain_stopped")
    next_function = _single_display_id(view, "void on_ready")
    function_peel = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        f"{definition}-{next_function - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert function_peel.returncode == 0, function_peel.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "consumer-reconnect",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_damage
