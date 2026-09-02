"""Regression coverage for an EOF function exposed by an earlier peel."""

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


def test_eof_function_keeps_its_closing_brace_after_later_function_peel(
    functional_repo,
):
    """The exposed EOF brace must map to its function, not the peeled successor."""
    path = functional_repo / "connector.c"
    target = (
        "int attach_monitor(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "int detach_monitor(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "bool authority_is_attached(void)\n"
        "{\n"
        "\treturn true;\n"
        "}\n"
        "\n"
        "bool detach_authority(void)\n"
        "{\n"
        "\treturn true;\n"
        "}\n"
        "\n"
        "void set_capture_active(void)\n"
        "{\n"
        "\tnotify();\n"
        "}\n"
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = _view(git_stage_batch, path)
    later = _display_id_for_text(view, "void set_capture_active")
    result = git_stage_batch(
        "discard",
        "--to",
        "capture-active",
        "--file",
        path.name,
        "--line",
        f"{later - 1}-{later + 3}",
        "--no-auto-advance",
        check=False,
    )
    assert result.returncode == 0, result.stderr

    view = _view(git_stage_batch, path)
    first = _display_id_for_text(view, "int detach_monitor")
    result = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        f"{first}-{first + 4}",
        "--no-auto-advance",
        check=False,
    )
    assert result.returncode == 0, result.stderr

    view = _view(git_stage_batch, path)
    authority = _display_id_for_text(view, "bool detach_authority")
    result = git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        f"{authority - 1}-{authority + 3}",
        "--no-auto-advance",
        check=False,
    )
    assert result.returncode == 0, result.stderr

    batch_view = git_stage_batch(
        "show",
        "--from",
        "monitor-detach",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    return_position = batch_view.index("return true;")
    later_position = batch_view.index("void set_capture_active")
    selected_closing_positions = [
        position
        for position, line in enumerate(batch_view.splitlines())
        if "[#" in line and "+ }" in line
    ]
    lines_before_return = len(batch_view[:return_position].splitlines())
    lines_before_later = len(batch_view[:later_position].splitlines())
    authority_closing = next(
        position
        for position in selected_closing_positions
        if position > lines_before_return
    )
    assert authority_closing < lines_before_later, batch_view
