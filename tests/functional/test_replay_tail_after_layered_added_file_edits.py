"""Regression coverage for replaying a tail after layered added-file edits."""

from .conftest import git_stage_batch


def _display_ids(view, text=None):
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line and (text is None or text in line)
    ]


def _single_display_id(view, text):
    matches = _display_ids(view, text)
    assert len(matches) == 1, matches
    return matches[0]


def test_tail_replays_after_replacement_and_prior_region_peels(functional_repo):
    """A contiguous frame-dispatch tail must replay on its exact predecessor."""
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
        "\treturn dispatch_events(bridge);\n"
        "}\n"
        "\n"
        "static int dispatch_event_batch(struct bridge *bridge)\n"
        "{\n"
        "\treturn handle_frame(bridge);\n"
        "}\n"
        "\n"
        "void on_ready(struct bridge *bridge)\n"
        "{\n"
        "\t(void)dispatch_event_batch(bridge);\n"
        "}\n"
    )
    after_cursor = target.replace("EXCLUSIVE | CURSOR", "EXCLUSIVE")
    after_damage = after_cursor.replace("\tframe->damage = event->damage;\n", "")
    after_reconnect = (
        after_damage.replace("static int drain_stopped(struct bridge *bridge);\n", "")
        .replace("\tif (!status)\n\t\tstatus = drain_stopped(bridge);\n", "")
        .replace(
            "static int drain_stopped(struct bridge *bridge)\n"
            "{\n"
            "\treturn dispatch_events(bridge);\n"
            "}\n"
            "\n",
            "",
        )
    )
    predecessor = (
        after_reconnect[
            : after_reconnect.index("static int dispatch_event_batch")
        ].rstrip("\n")
        + "\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    flags = _single_display_id(view, "int flags = EXCLUSIVE | CURSOR")
    replacement = git_stage_batch(
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
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == after_cursor

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    damage = _single_display_id(view, "frame->damage")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "damage-metadata",
        "--line",
        str(damage),
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_damage

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    definition = _display_ids(view, "static int drain_stopped")[-1]
    dispatch = _single_display_id(view, "static int dispatch_event_batch")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        f"{definition}-{dispatch - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    call_start = _single_display_id(view, "if (!status)")
    call_end = _single_display_id(view, "status = drain_stopped")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        f"{call_start}-{call_end}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    prototype = _single_display_id(view, "static int drain_stopped")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "consumer-reconnect",
        "--line",
        str(prototype),
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_reconnect

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    dispatch = _single_display_id(view, "static int dispatch_event_batch")
    tail = max(_display_ids(view))
    peeled = git_stage_batch(
        "discard",
        "--to",
        "frame-delivery",
        "--line",
        f"{dispatch - 1}-{tail}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "frame-delivery",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_reconnect
