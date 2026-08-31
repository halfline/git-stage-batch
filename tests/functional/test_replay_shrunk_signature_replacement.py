"""Regression coverage for replaying a shrunk multiline replacement."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_shrunk_signature_replay_does_not_duplicate_shared_suffix(functional_repo):
    """Restoring a three-line signature must consume its shared two-line form."""
    path = functional_repo / "adapter.c"
    cursor_macro = "#define MAX_CURSOR_META_SIZE 4096\n\n"
    retained_prefix = "static void keep_frame(void)\n{\n\tconsume_frame();\n}\n\n"
    cursor_helper = (
        "static int fill_cursor(void)\n{\n\tint cursor = 1;\n\treturn cursor;\n}\n\n"
    )
    target_signature = (
        "static int set_metadata(struct bridge *bridge,\n"
        "                        struct buffer *buffer,\n"
        "                        struct meta *meta)\n"
    )
    predecessor_signature = (
        "static int set_metadata(struct buffer *buffer,\n"
        "                        struct meta *meta)\n"
    )
    target_body = "{\n\treturn fill_cursor();\n}\n"
    predecessor_body = "{\n\treturn 0;\n}\n"
    target_callsite = "status = set_metadata(bridge, buffer, meta);\n"
    predecessor_callsite = "status = set_metadata(buffer, meta);\n"
    target = (
        retained_prefix
        + cursor_macro
        + cursor_helper
        + target_signature
        + target_body
        + target_callsite
    )
    outer_target = target + "explicit sync\n"
    predecessor = (
        retained_prefix
        + predecessor_signature
        + predecessor_body
        + predecessor_callsite
    )
    path.write_text(outer_target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    outer_first = _display_id_for_text(view, "static void keep_frame")
    outer_last = _display_id_for_text(view, "explicit sync")
    outer = git_stage_batch(
        "discard",
        "--to",
        "explicit-sync",
        "--line",
        f"{outer_first}-{outer_last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=target,
        check=False,
    )
    assert outer.returncode == 0, outer.stderr
    assert path.read_text() == target

    replay_outer = git_stage_batch(
        "apply",
        "--from",
        "explicit-sync",
        "--file",
        path.name,
        check=False,
    )
    assert replay_outer.returncode == 0, replay_outer.stderr
    assert path.read_text() == outer_target
    undo_outer = git_stage_batch("undo", check=False)
    assert undo_outer.returncode == 0, undo_outer.stderr
    assert path.read_text() == target

    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    macro = _display_id_for_text(view, "#define MAX_CURSOR_META_SIZE")
    macro_discard = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        f"{macro}-{macro + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert macro_discard.returncode == 0, macro_discard.stderr

    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    helper_first = _display_id_for_text(view, "static int fill_cursor")
    helper_last = _display_id_for_text(view, "return cursor;")
    helper = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        f"{helper_first}-{helper_last + 2}",
        "--no-auto-advance",
        check=False,
    )
    assert helper.returncode == 0, helper.stderr

    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    cursor_call = _display_id_for_text(view, "return fill_cursor();")
    call_replacement = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        str(cursor_call),
        "--as-stdin",
        "--no-auto-advance",
        input_text="\treturn 0;\n",
        check=False,
    )
    assert call_replacement.returncode == 0, call_replacement.stderr

    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    first = _display_id_for_text(view, "static int set_metadata")
    last = _display_id_for_text(view, "struct meta *meta)")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_signature,
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr

    view = git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    callsite = _display_id_for_text(view, "status = set_metadata(bridge")
    callsite_replacement = git_stage_batch(
        "discard",
        "--to",
        "cursor-metadata",
        "--line",
        str(callsite),
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_callsite,
        check=False,
    )
    assert callsite_replacement.returncode == 0, callsite_replacement.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "cursor-metadata",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    actual = path.read_text()
    assert actual == target, (
        f"expected replay {target!r}, got {actual!r}; "
        "shared signature suffix may have been duplicated"
    )
