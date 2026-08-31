"""Regression coverage for peeling capture lines from a tracked refactor."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_discard_restores_count_inside_retained_dispatch_replacement(functional_repo):
    """A restored baseline line must stay inside the retained replacement block."""
    path = functional_repo / "dispatch.c"
    baseline = (
        "static void dispatch(void)\n"
        "{\n"
        "    struct state *state;\n"
        "\n"
        "    state = output->composer_state;\n"
        "    if (state && composer_is_active()) {\n"
        "        unsigned long frame = accurate_count();\n"
        "\n"
        "        old_body(frame);\n"
        "    }\n"
        "}\n"
    )
    target = (
        "static void dispatch(void)\n"
        "{\n"
        "    struct state *state;\n"
        "    bool queue_dispatch = false;\n"
        "\n"
        "    state = output->dispatch_state;\n"
        "    if (state && demand_is_active(\n"
        "                         &output->dispatch_demand)) {\n"
        "        ktime_t frame_time;\n"
        "        unsigned long frame = count_and_time(&frame_time);\n"
        "\n"
        "        if (crc_enabled()) {\n"
        "            consume(frame);\n"
        "            queue_dispatch = true;\n"
        "        }\n"
        "\n"
        "        if (capture_prepare(state, frame,\n"
        "                            frame_time))\n"
        "            queue_dispatch = true;\n"
        "\n"
        "        if (queue_dispatch)\n"
        "            schedule();\n"
        "    }\n"
        "}\n"
    )
    predecessor = (
        "static void dispatch(void)\n"
        "{\n"
        "    struct state *state;\n"
        "    bool queue_dispatch = false;\n"
        "\n"
        "    state = output->dispatch_state;\n"
        "    if (state && demand_is_active(\n"
        "                         &output->dispatch_demand)) {\n"
        "        unsigned long frame = accurate_count();\n"
        "\n"
        "        if (crc_enabled()) {\n"
        "            consume(frame);\n"
        "            queue_dispatch = true;\n"
        "        }\n"
        "\n"
        "        if (queue_dispatch)\n"
        "            schedule();\n"
        "    }\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add dispatch fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    old_count = _display_id_for_text(view, "accurate_count")
    frame_time = _display_id_for_text(view, "ktime_t frame_time")
    new_count = _display_id_for_text(view, "count_and_time")
    capture = _display_id_for_text(view, "if (capture_prepare")

    result = git_stage_batch(
        "discard",
        "--to",
        "capture-adopter",
        "--line",
        f"{old_count},{frame_time}-{new_count},{capture}-{capture + 2}",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "record exact predecessor"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    replay = git_stage_batch(
        "apply",
        "--from",
        "capture-adopter",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    inverse = git_stage_batch(
        "discard",
        "--from",
        "capture-adopter",
        "--file",
        path.name,
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == predecessor
