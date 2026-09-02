"""Regression coverage for a first replacement in a tracked insertion."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_first_replacement_after_peeling_tracked_insertions_replays_target(
    functional_repo,
):
    """Earlier insertion peels must not reverse replacement ownership."""
    path = functional_repo / "connector.h"
    baseline = (
        "#ifndef CONNECTOR_H\n"
        "#define CONNECTOR_H\n"
        "\n"
        "struct connector {\n"
        "\tint base;\n"
        "};\n"
        "\n"
        "int other_route(void);\n"
        "\n"
        "#endif\n"
    )
    target = (
        "#ifndef CONNECTOR_H\n"
        "#define CONNECTOR_H\n"
        "\n"
        "struct drm_edid;\n"
        "\n"
        "/**\n"
        " * @monitor_attached: Whether ATTACH_MONITOR plugged in a sink\n"
        " */\n"
        "struct connector {\n"
        "\tint base;\n"
        "};\n"
        "\n"
        "/* Callers hold the attachment transition lock. */\n"
        "int attach_monitor(const struct drm_edid *drm_edid);\n"
        "int is_attached(void);\n"
        "int require_attached(void);\n"
        "int other_route(void);\n"
        "\n"
        "#endif\n"
    )
    predecessor = (
        "#ifndef CONNECTOR_H\n"
        "#define CONNECTOR_H\n"
        "\n"
        "\n"
        "/**\n"
        " * @monitor_attached: Whether a sink is attached\n"
        " */\n"
        "struct connector {\n"
        "\tint base;\n"
        "};\n"
        "\n"
        "int is_attached(void);\n"
        "int other_route(void);\n"
        "\n"
        "#endif\n"
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
    forward = _display_id_for_text(view, "struct drm_edid;")
    git_stage_batch(
        "discard",
        "--to",
        "monitor-attach",
        "--file",
        path.name,
        "--line",
        str(forward),
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    attach_comment = _display_id_for_text(view, "Callers hold")
    attach_declaration = _display_id_for_text(view, "int attach_monitor")
    git_stage_batch(
        "discard",
        "--to",
        "monitor-attach",
        "--file",
        path.name,
        "--line",
        f"{attach_comment}-{attach_declaration}",
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    require = _display_id_for_text(view, "int require_attached")
    git_stage_batch(
        "discard",
        "--to",
        "monitor-attach",
        "--file",
        path.name,
        "--line",
        str(require),
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    monitor_doc = _display_id_for_text(view, "Whether ATTACH_MONITOR")
    replaced = git_stage_batch(
        "discard",
        "--to",
        "monitor-attach",
        "--file",
        path.name,
        "--line",
        str(monitor_doc),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=" * @monitor_attached: Whether a sink is attached\n",
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-attach",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
