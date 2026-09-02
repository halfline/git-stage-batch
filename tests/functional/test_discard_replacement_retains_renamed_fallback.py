"""Regression coverage for a replacement with a renamed fallback body."""

import subprocess

from .conftest import git_stage_batch


def _display_ids(view: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _display_id_containing(view: str, needle: str) -> int:
    matches = [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line and needle in line
    ]
    assert len(matches) == 1, (needle, matches, view)
    return matches[0]


def test_discard_replacement_retains_renamed_fallback(functional_repo):
    """The supplied predecessor must retain fallback lines renamed elsewhere."""
    path = functional_repo / "connector.c"
    baseline = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\t/* Use the default modes list. */\n"
        "\tcount = add_modes(OLD_MAX, OLD_HEIGHT);\n"
        "\tprefer_mode(OLD_DEFAULT, OLD_DEFAULT_HEIGHT);\n"
        "\n"
        "\treturn count;\n"
        "}\n"
    )
    target = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\tcount = add_edid_modes();\n"
        "\tif (!count) {\n"
        "\t\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t\t  NEW_HEIGHT);\n"
        "\t\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t\t    NEW_DEFAULT_HEIGHT);\n"
        "\t}\n"
        "\n"
        "\treturn count;\n"
        "}\n"
    )
    predecessor_fragment = (
        "\t/* Use the default modes list. */\n"
        "\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t  NEW_HEIGHT);\n"
        "\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t    NEW_DEFAULT_HEIGHT);\n"
    )
    predecessor = (
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        f"{predecessor_fragment}"
        "\n"
        "\treturn count;\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add connector baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    ids = _display_ids(view)
    result = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        f"{min(ids)}-{max(ids)}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=predecessor_fragment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor
    saved = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/connector-edid:connector.c",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert saved == target


def test_discard_freshly_applied_wrapper_around_renamed_fallback(functional_repo):
    """A freshly applied wrapper must reverse while its fallback stays shared."""
    path = functional_repo / "connector.c"
    baseline = (
        "static int detect_connector(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\t/* Use the default modes list. */\n"
        "\tcount = add_modes(OLD_MAX, OLD_HEIGHT);\n"
        "\tprefer_mode(OLD_DEFAULT, OLD_DEFAULT_HEIGHT);\n"
        "\n"
        "\treturn count;\n"
        "}\n"
        "\n"
        "static int init_connector(void)\n"
        "{\n"
        "\tint connector = allocate_connector();\n"
        "\n"
        "\treturn connector;\n"
        "}\n"
        "\n"
        "static int existing_query(void)\n"
        "{\n"
        "\treturn query();\n"
        "}\n"
    )
    target = (
        "static int detect_connector(void)\n"
        "{\n"
        "\t/* attached branch begin */\n"
        "\tif (monitor_attached)\n"
        "\t\treturn 1;\n"
        "\t/* attached branch end */\n"
        "\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\tcount = add_edid_modes();\n"
        "\tif (!count) {\n"
        "\t\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t\t  NEW_HEIGHT);\n"
        "\t\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t\t    NEW_DEFAULT_HEIGHT);\n"
        "\t}\n"
        "\n"
        "\treturn count;\n"
        "}\n"
        "\n"
        "static int init_connector(void)\n"
        "{\n"
        "\tint cast_connector = allocate_connector();\n"
        "\tint connector = cast_connector;\n"
        "\tattach_property(connector);\n"
        "\n"
        "\treturn cast_connector;\n"
        "}\n"
        "\n"
        "/* publish helper begin */\n"
        "static int publish_edid(void)\n"
        "{\n"
        "\treturn publish();\n"
        "}\n"
        "/* publish helper end */\n"
        "\n"
        "static int existing_query(void)\n"
        "{\n"
        "\treturn query();\n"
        "}\n"
    )
    predecessor = (
        "static int detect_connector(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int get_modes(void)\n"
        "{\n"
        "\tint count;\n"
        "\n"
        "\t/* Use the default modes list. */\n"
        "\t\tcount = add_modes(NEW_MAX,\n"
        "\t\t\t\t  NEW_HEIGHT);\n"
        "\t\tprefer_mode(NEW_DEFAULT,\n"
        "\t\t\t    NEW_DEFAULT_HEIGHT);\n"
        "\n"
        "\treturn count;\n"
        "}\n"
        "\n"
        "static int init_connector(void)\n"
        "{\n"
        "\tint connector = allocate_connector();\n"
        "\n"
        "\treturn connector;\n"
        "}\n"
        "\n"
        "static int existing_query(void)\n"
        "{\n"
        "\treturn query();\n"
        "}\n"
    )

    path.write_text(baseline)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add connector baseline"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    helper_first = _display_id_containing(view, "publish helper begin")
    helper_last = _display_id_containing(view, "publish helper end")
    peel_helper = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        f"{helper_first}-{helper_last}",
        "--no-auto-advance",
        check=False,
    )
    assert peel_helper.returncode == 0, peel_helper.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    init_selected = [
        _display_id_containing(view, "- \tint connector = allocate_connector"),
        _display_id_containing(view, "+ \tint cast_connector = allocate_connector"),
        _display_id_containing(view, "+ \tint connector = cast_connector"),
        _display_id_containing(view, "attach_property"),
        _display_id_containing(view, "- \treturn connector"),
        _display_id_containing(view, "+ \treturn cast_connector"),
    ]
    peel_init = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        f"{min(init_selected)}-{max(init_selected)}",
        "--no-auto-advance",
        check=False,
    )
    assert peel_init.returncode == 0, peel_init.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    wrapper_selected = [
        _display_id_containing(view, "Use the default modes list."),
        _display_id_containing(view, "add_edid_modes"),
        _display_id_containing(view, "if (!count)"),
        _display_id_containing(view, "+ \t}"),
    ]
    peel_wrapper = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        ",".join(str(line_id) for line_id in wrapper_selected),
        "--no-auto-advance",
        check=False,
    )
    assert peel_wrapper.returncode == 0, peel_wrapper.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    detect_first = _display_id_containing(view, "attached branch begin")
    detect_last = _display_id_containing(view, "attached branch end")
    peel_detect = git_stage_batch(
        "discard",
        "--to",
        "connector-edid",
        "--file",
        path.name,
        "--line",
        f"{detect_first}-{detect_last + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peel_detect.returncode == 0, peel_detect.stderr
    assert path.read_text() == predecessor

    apply = git_stage_batch(
        "apply", "--from", "connector-edid", "--file", path.name, check=False
    )
    assert apply.returncode == 0, apply.stderr
    assert path.read_text() == target

    reverse = git_stage_batch(
        "discard",
        "--from",
        "connector-edid",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert reverse.returncode == 0, reverse.stderr
    assert path.read_text() == predecessor
