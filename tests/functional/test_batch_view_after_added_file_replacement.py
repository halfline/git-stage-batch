"""Regression coverage for narrow batch views after added-file replacements."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_added_file_replacement_does_not_render_as_whole_file_change(
    functional_repo,
):
    """A narrow replacement after an earlier peel stays narrow in batch review."""
    path = functional_repo / "adapter.c"
    target = (
        "static void base(void)\n"
        "{\n"
        "}\n"
        "\n"
        "#define CURSOR_META 1\n"
        "const int params[3] = { 1, 2, 3 };\n"
        "int damage_meta;\n"
        "publish_damage(damage_meta);\n"
        "return_frame();\n"
    )
    predecessor = (
        "static void base(void)\n"
        "{\n"
        "}\n"
        "\n"
        "const int params[2] = { 1, 2 };\n"
        "return_frame();\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    cursor = _display_id_for_text(view, "#define CURSOR_META")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        str(cursor),
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    params = _display_id_for_text(view, "const int params[3]")
    replaced = git_stage_batch(
        "discard",
        "--to",
        "damage-metadata",
        "--line",
        str(params),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="const int params[2] = { 1, 2 };\n",
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    declaration = _display_id_for_text(view, "int damage_meta")
    publication = _display_id_for_text(view, "publish_damage")
    discarded = git_stage_batch(
        "discard",
        "--to",
        "damage-metadata",
        "--line",
        f"{declaration},{publication}",
        "--no-auto-advance",
        check=False,
    )
    assert discarded.returncode == 0, discarded.stderr
    assert path.read_text() == predecessor

    batch_view = git_stage_batch(
        "show",
        "--from",
        "damage-metadata",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    selected_base = [
        line
        for line in batch_view.splitlines()
        if "[#" in line and "static void base" in line
    ]
    assert not selected_base, batch_view

    replay = git_stage_batch(
        "apply",
        "--from",
        "damage-metadata",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target.replace("#define CURSOR_META 1\n", "")
