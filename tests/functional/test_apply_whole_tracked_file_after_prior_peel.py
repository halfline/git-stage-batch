"""Regression coverage for replaying a whole tracked-file batch after a peel."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _display_ids_for_text(view: str, text: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if text in line and "[#" in line
    ]


def test_whole_tracked_file_replays_after_prior_same_file_peel(functional_repo):
    """A whole-file capture must replay the surviving session-start changes."""
    path = functional_repo / "dispatch.c"
    baseline = (
        "ret = handle(crtc);\n"
        "\tif (!ret)\n"
        "\t\twarn();\n"
        "\n"
        "\tstate = output->composer_state;\n"
        "\tif (state && composer_is_active(&output->composer_demand)) {\n"
        "\t\tunsigned long frame = count(crtc);\n"
        "\n"
        "\t\tlock(&output->composer_lock);\n"
        "\t\tif (!state->pending)\n"
        "\t\t\tstate->start = frame;\n"
        "\t\telse\n"
        "\t\t\twarn_behind(state->start, frame);\n"
        "\t\tstate->end = frame;\n"
        "\t\tstate->pending = true;\n"
        "\t\tunlock(&output->composer_lock);\n"
        "\n"
        "\t\tret = queue(output->composer_queue, &state->composer_work);\n"
        "\t\tif (!ret)\n"
        "\t\t\twarn_queued();\n"
        "\t}\n"
        "\tunlock(&output->lock);\n"
        "\n"
        "\tend(cookie);\n"
        "\n"
        "\treturn true;\n"
        "}\n"
        "\n"
        "static int init(void)\n"
        "{\n"
        "    return baseline_init();\n"
        "}\n"
    )
    target = (
        "ret = handle(crtc);\n"
        "\tif (!ret)\n"
        "\t\twarn();\n"
        "\n"
        "\tstate = output->dispatch_state;\n"
        "\tif (state && demand_is_active(\n"
        "\t\t\t     &output->dispatch_demand)) {\n"
        "\t\tunsigned long frame = count(crtc);\n"
        "\n"
        "\t\tif (output->dispatch_demand.enabled && content_safe(output)) {\n"
        "\t\t\tlock(&output->dispatch_lock);\n"
        "\t\t\tif (!state->pending)\n"
        "\t\t\t\tstate->start = frame;\n"
        "\t\t\telse\n"
        "\t\t\t\twarn_behind(state->start, frame);\n"
        "\t\t\tstate->end = frame;\n"
        "\t\t\tstate->pending = true;\n"
        "\t\t\tunlock(&output->dispatch_lock);\n"
        "\t\t\tqueue_dispatch = true;\n"
        "\t\t}\n"
        "\n"
        "\t\tif (queue_dispatch &&\n"
        "\t\t    !queue(output->dispatch_queue, &state->dispatch_work))\n"
        "\t\t\twarn_queued();\n"
        "\t}\n"
        "\tunlock(&output->lock);\n"
        "\n"
        "\tend(cookie);\n"
        "\n"
        "\treturn true;\n"
        "}\n"
        "\n"
        "static int init(void)\n"
        "{\n"
        "    int err;\n"
        "\n"
        "    setup();\n"
        "    err = add_action();\n"
        "    if (err)\n"
        "        return err;\n"
        "    err = old_init();\n"
        "    if (err)\n"
        "        return err;\n"
        "    new_init();\n"
        "\n"
        "    return 0;\n"
        "}\n"
    )
    after_prior_peel = target.replace(
        "    if (err)\n"
        "        return err;\n"
        "    err = old_init();\n",
        "",
        1,
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
    error_checks = _display_ids_for_text(view, "if (err)")
    assert len(error_checks) == 2, error_checks
    old_init = _display_id_for_text(view, "err = old_init")
    prior = git_stage_batch(
        "discard",
        "--to",
        "capture-adopter",
        "--file",
        path.name,
        "--line",
        f"{error_checks[0]}-{old_init}",
        "--no-auto-advance",
        check=False,
    )
    assert prior.returncode == 0, prior.stderr
    assert path.read_text() == after_prior_peel

    whole = git_stage_batch(
        "discard",
        "--to",
        "dispatch-refactor",
        "--file",
        path.name,
        check=False,
    )
    assert whole.returncode == 0, whole.stderr
    assert path.read_text() == baseline

    replay = git_stage_batch(
        "apply",
        "--from",
        "dispatch-refactor",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_prior_peel

    inverse = git_stage_batch(
        "discard",
        "--from",
        "dispatch-refactor",
        "--file",
        path.name,
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == baseline


def test_whole_tracked_file_replay_keeps_surviving_baseline_deletions(
    functional_repo,
):
    """Replay must not resurrect baseline lines removed outside the prior peel."""
    path = functional_repo / "output.c"
    baseline = (
        "static void output_init(void)\n"
        "{\n"
        "    old_lock_init();\n"
        "    old_composer_lock_init();\n"
        "    old_queue = alloc_old_queue();\n"
        "}\n"
    )
    target = (
        "static void output_init(void)\n"
        "{\n"
        "    old_lock_init();\n"
        "    dispatch_lock_init();\n"
        "    capture_output_init();\n"
        "    dispatch_queue = alloc_dispatch_queue();\n"
        "}\n"
    )
    after_prior_peel = (
        "static void output_init(void)\n"
        "{\n"
        "    capture_output_init();\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add output fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")

    peeled = git_stage_batch(
        "discard",
        "--to",
        "dispatch-demand",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    replay = git_stage_batch(
        "apply",
        "--from",
        "dispatch-demand",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    path.write_text(after_prior_peel)
    sifted = git_stage_batch(
        "sift",
        "--from",
        "dispatch-demand",
        "--to",
        "dispatch-demand",
        check=False,
    )
    assert sifted.returncode == 0, sifted.stderr
    assert path.read_text() == after_prior_peel

    whole = git_stage_batch(
        "discard",
        "--to",
        "capture-init",
        "--file",
        path.name,
        check=False,
    )
    assert whole.returncode == 0, whole.stderr
    assert path.read_text() == baseline

    replay = git_stage_batch(
        "apply",
        "--from",
        "capture-init",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_prior_peel
