"""Regression coverage for sifting around an earlier added-file peel."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_sift_removed_outer_function_after_inner_added_file_peel(functional_repo):
    """An inner later peel must not make removal of its outer function foreign."""
    path = functional_repo / "capture-tool.c"
    target = (
        "static_assert(event_size == 112);\n"
        "\n"
        "static int set_connector_crtc(void)\n"
        "{\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "static int run_cursor_test(void)\n"
        "{\n"
        "    validate_cursor_metadata();\n"
        "    read_cursor_bitmap();\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "static void usage(void)\n"
        "{\n"
        '    print("--deliver-one|--cursor");\n'
        "}\n"
    )
    event_target = target.replace("    read_cursor_bitmap();\n", "")
    predecessor = (
        "static_assert(event_size == 80);\n"
        "\n"
        "static void usage(void)\n"
        "{\n"
        '    print("--deliver-one");\n'
        "}\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    bitmap_id = _display_id_for_text(view, "read_cursor_bitmap")
    inner = git_stage_batch(
        "discard",
        "--to",
        "bitmap-read",
        "--line",
        str(bitmap_id),
        "--no-auto-advance",
        check=False,
    )
    assert inner.returncode == 0, inner.stderr
    assert path.read_text() == event_target

    saved = git_stage_batch(
        "include",
        "--to",
        "cursor-event",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert saved.returncode == 0, saved.stderr
    path.write_text(predecessor)

    sifted = git_stage_batch(
        "sift", "--from", "cursor-event", "--to", "cursor-event", check=False
    )
    assert sifted.returncode == 0, sifted.stderr
    assert path.read_text() == predecessor

    replay_event = git_stage_batch(
        "apply", "--from", "cursor-event", "--file", path.name, check=False
    )
    assert replay_event.returncode == 0, replay_event.stderr
    assert path.read_text() == event_target
    replay_inner = git_stage_batch(
        "apply", "--from", "bitmap-read", "--file", path.name, check=False
    )
    assert replay_inner.returncode == 0, replay_inner.stderr
    assert path.read_text() == target
