"""Regression coverage for a contraction after an earlier added-file peel."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _view(git_stage_batch, path):
    return git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout


def test_contraction_after_prior_added_file_peel_advances_batch_source(
    functional_repo,
):
    """A two-to-one replacement may follow a prior peel from an added file."""
    path = functional_repo / "capture.h"
    target = (
        "An earlier feature is already peeled.\n"
        "The first section remains.\n"
        "The calling grant owns the attachment until DETACH_MONITOR or final\n"
        "holder close.\n"
        "struct detach_monitor {\n"
        "    int connector_id;\n"
        "};\n"
        "The next paragraph remains.\n"
    )
    predecessor = (
        "The first section remains.\n"
        "The calling grant owns the attachment.\n"
        "The next paragraph remains.\n"
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = _view(git_stage_batch, path)
    prior_id = _display_id_for_text(view, "An earlier feature is already peeled")
    prior = git_stage_batch(
        "discard",
        "--to",
        "prior-feature",
        "--file",
        path.name,
        "--line",
        str(prior_id),
        "--no-auto-advance",
        check=False,
    )
    assert prior.returncode == 0, prior.stderr

    view = _view(git_stage_batch, path)
    command_start = _display_id_for_text(view, "struct detach_monitor")
    command_end = _display_id_for_text(view, "};")
    command = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        f"{command_start}-{command_end}",
        "--no-auto-advance",
        check=False,
    )
    assert command.returncode == 0, command.stderr

    view = _view(git_stage_batch, path)
    attach_start = _display_id_for_text(view, "The calling grant owns")
    attach_end = _display_id_for_text(view, "holder close")
    attachment = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        f"{attach_start}-{attach_end}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="The calling grant owns the attachment.\n",
        check=False,
    )
    assert attachment.returncode == 0, attachment.stderr
    assert path.read_text() == predecessor
