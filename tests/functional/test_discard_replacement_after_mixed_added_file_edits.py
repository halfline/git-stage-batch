"""Regression coverage for a replacement after mixed edits to an added file."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_replacement_after_mixed_added_file_edits_replays(functional_repo):
    """One batch may replace, delete, then replace regions of an added file."""
    path = functional_repo / "Makefile"
    target = (
        "# SPDX-License-Identifier: GPL-2.0-only\n"
        "\n"
        "CC ?= cc\n"
        "CFLAGS ?= -O2\n"
        "WARN_CFLAGS := -std=gnu11 -Wall -Wextra -Werror\n"
        "CPPFLAGS += -I../include/uapi\n"
        "\n"
        ".PHONY: all check clean\n"
        "\n"
        "all: castkms-capture-test castkms-grant-test castkms-edid-test\n"
        "\t$(MAKE) -C pw-castkms\n"
        "\n"
        "castkms-capture-test: castkms-capture-test.c common.c common.h\n"
        "\t$(CC) -o $@ castkms-capture-test.c common.c\n"
        "\n"
        "castkms-grant-test: castkms-grant-test.c common.c common.h\n"
        "\t$(CC) -o $@ castkms-grant-test.c common.c\n"
        "\n"
        "castkms-edid-test: castkms-edid-test.c virtualscreen-edid.h\n"
        "\t$(CC) -o $@ $<\n"
        "\n"
        "check: all\n"
        "\t./castkms-capture-test -h >/dev/null 2>&1\n"
        "\t./castkms-grant-test -h >/dev/null 2>&1\n"
        "\t./castkms-edid-test\n"
        "\t$(MAKE) -C pw-castkms check\n"
        "\n"
        "clean:\n"
        "\t$(RM) castkms-capture-test castkms-grant-test castkms-edid-test\n"
        "\t$(MAKE) -C pw-castkms clean\n"
    )
    predecessor = (
        target.replace(
            "all: castkms-capture-test castkms-grant-test castkms-edid-test\n",
            "all: castkms-capture-test castkms-grant-test\n",
        )
        .replace(
            "castkms-edid-test: castkms-edid-test.c virtualscreen-edid.h\n"
            "\t$(CC) -o $@ $<\n"
            "\n",
            "",
        )
        .replace(
            "\t./castkms-edid-test\n",
            "",
        )
        .replace(
            "\t$(RM) castkms-capture-test castkms-grant-test castkms-edid-test\n",
            "\t$(RM) castkms-capture-test castkms-grant-test\n",
        )
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    all_target = _display_id_for_text(
        view,
        "all: castkms-capture-test castkms-grant-test castkms-edid-test",
    )
    first = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(all_target),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="all: castkms-capture-test castkms-grant-test\n",
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    target_rule = _display_id_for_text(view, "castkms-edid-test: castkms-edid-test.c")
    target_command = _display_id_for_text(view, "$(CC) -o $@ $<")
    second = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        f"{target_rule}-{target_command + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert second.returncode == 0, second.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    check_command = _display_id_for_text(view, "./castkms-edid-test")
    third = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(check_command),
        "--no-auto-advance",
        check=False,
    )
    assert third.returncode == 0, third.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    clean_command = _display_id_for_text(
        view,
        "$(RM) castkms-capture-test castkms-grant-test castkms-edid-test",
    )
    fourth = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(clean_command),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\t$(RM) castkms-capture-test castkms-grant-test\n",
        check=False,
    )
    assert fourth.returncode == 0, fourth.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch("apply", "--from", "audio-edid", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
    undo = git_stage_batch("undo", check=False)
    assert undo.returncode == 0, undo.stderr
    assert path.read_text() == predecessor


def test_replay_after_replacements_then_deletions_restores_added_file(functional_repo):
    """The safe edit order must still replay to the exact added-file target."""
    path = functional_repo / "Makefile"
    target = (
        "all: capture grant edid\n"
        "\tbuild all\n"
        "\n"
        "edid: edid.c header.h\n"
        "\tbuild edid\n"
        "\n"
        "check:\n"
        "\tcheck edid\n"
        "\n"
        "clean:\n"
        "\trm capture grant edid\n"
    )
    predecessor = (
        "all: capture grant\n\tbuild all\n\ncheck:\n\nclean:\n\trm capture grant\n"
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    all_target = _display_id_for_text(view, "all: capture grant edid")
    first = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(all_target),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="all: capture grant\n",
        check=False,
    )
    assert first.returncode == 0, first.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    clean_target = _display_id_for_text(view, "rm capture grant edid")
    second = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(clean_target),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="\trm capture grant\n",
        check=False,
    )
    assert second.returncode == 0, second.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    target_rule = _display_id_for_text(view, "edid: edid.c header.h")
    target_command = _display_id_for_text(view, "build edid")
    third = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        f"{target_rule}-{target_command + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert third.returncode == 0, third.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    check_command = _display_id_for_text(view, "check edid")
    fourth = git_stage_batch(
        "discard",
        "--to",
        "audio-edid",
        "--line",
        str(check_command),
        "--no-auto-advance",
        check=False,
    )
    assert fourth.returncode == 0, fourth.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch("apply", "--from", "audio-edid", check=False)
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
