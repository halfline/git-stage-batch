"""Regression coverage for replacing part of an already-peeled added file."""

import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _commit_baseline(repo, supports):
    path = repo / "README"
    path.write_text("baseline\n")
    subprocess.run(
        ["git", "add", path.name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_replacement_in_partially_peeled_added_file_replays(functional_repo):
    """A later concern must replace only its region in an earlier added file."""
    prefix = "".join(
        f"static int helper_{index}(int fd)\n"
        "{\n"
        "\tif (fd < 0)\n"
        "\t\treturn -1;\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        for index in range(17)
    )
    target = prefix + (
        "int main(int argc, char **argv)\n"
        "{\n"
        "\tuint8_t edid[MAX_SIZE];\n"
        "\tuint32_t connector_id;\n"
        "\tchar discard;\n"
        "\tint inherited_fd = -1;\n"
        "\tint edid_size;\n"
        "\tint fd;\n"
        "\tint ret = EXIT_FAILURE;\n"
        "\n"
        '\tif (argc == 2 && !strcmp(argv[1], "--help")) {\n'
        "\t\tusage(argv[0]);\n"
        "\t\treturn EXIT_SUCCESS;\n"
        "\t}\n"
        '\tif (argc == 3 && !strcmp(argv[1], "--grant-fd")) {\n'
        "\t\tif (parse_fd(argv[2], &inherited_fd)) {\n"
        "\t\t\tusage(argv[0]);\n"
        "\t\t\treturn EXIT_FAILURE;\n"
        "\t\t}\n"
        "\t} else if (argc != 1) {\n"
        "\t\tusage(argv[0]);\n"
        "\t\treturn EXIT_FAILURE;\n"
        "\t}\n"
        "\n"
        "\tfd = open_grant(inherited_fd, &connector_id);\n"
        "\tif (fd < 0)\n"
        "\t\treturn EXIT_FAILURE;\n"
        "\n"
        "\tedid_size = fill_edid(edid, sizeof(edid), AUDIO);\n"
        "\tif (edid_size < 0) {\n"
        '\t\tfprintf(stderr, "failed to build attachment EDID\\n");\n'
        "\t\tgoto out_close;\n"
        "\t}\n"
        "\tif (attach_monitor(fd, connector_id, edid, (uint32_t)edid_size)) {\n"
        '\t\tperror("ATTACH_MONITOR");\n'
        "\t\tgoto out_close;\n"
        "\t}\n"
        "\tif (connector_is_connected(fd, connector_id)) {\n"
        '\t\tfprintf(stderr, "attached connector is not connected\\n");\n'
        "\t\tgoto out_detach;\n"
        "\t}\n"
        "\n"
        '\tprintf("connector_id=%u\\n", connector_id);\n'
        '\tprintf("attached=1\\n");\n'
        "\tfflush(stdout);\n"
        "\t(void)read(STDIN_FILENO, &discard, 1);\n"
        "\tret = EXIT_SUCCESS;\n"
        "\n"
        "out_detach:\n"
        "\tif (detach_monitor(fd, connector_id) && ret == EXIT_SUCCESS) {\n"
        '\t\tperror("DETACH_MONITOR");\n'
        "\t\tret = EXIT_FAILURE;\n"
        "\t}\n"
        "out_close:\n"
        "\tclose(fd);\n"
        "\treturn ret;\n"
        "}\n"
    )
    target_after_earlier_peel = target.replace("\tchar discard;\n", "")
    for line in (
        '\tprintf("connector_id=%u\\n", connector_id);\n',
        '\tprintf("attached=1\\n");\n',
        "\tfflush(stdout);\n",
        "\t(void)read(STDIN_FILENO, &discard, 1);\n",
    ):
        target_after_earlier_peel = target_after_earlier_peel.replace(line, "")
    predecessor = prefix + (
        "int main(int argc, char **argv)\n"
        "{\n"
        "\tuint8_t edid[BLOCK_SIZE];\n"
        "\tuint32_t connector_id;\n"
        "\tint inherited_fd = -1;\n"
        "\tint fd;\n"
        "\tint ret = EXIT_FAILURE;\n"
        "\n"
        '\tif (argc == 2 && !strcmp(argv[1], "--help")) {\n'
        "\t\tusage(argv[0]);\n"
        "\t\treturn EXIT_SUCCESS;\n"
        "\t}\n"
        '\tif (argc == 3 && !strcmp(argv[1], "--grant-fd")) {\n'
        "\t\tif (parse_fd(argv[2], &inherited_fd)) {\n"
        "\t\t\tusage(argv[0]);\n"
        "\t\t\treturn EXIT_FAILURE;\n"
        "\t\t}\n"
        "\t} else if (argc != 1) {\n"
        "\t\tusage(argv[0]);\n"
        "\t\treturn EXIT_FAILURE;\n"
        "\t}\n"
        "\n"
        "\tfd = open_grant(inherited_fd, &connector_id);\n"
        "\tif (fd < 0)\n"
        "\t\treturn EXIT_FAILURE;\n"
        "\n"
        "\tif (fill_named_edid(edid) < 0) {\n"
        '\t\tfprintf(stderr, "failed to build attachment EDID\\n");\n'
        "\t\tgoto out_close;\n"
        "\t}\n"
        "\tif (attach_monitor(fd, connector_id, edid, sizeof(edid))) {\n"
        '\t\tperror("ATTACH_MONITOR");\n'
        "\t\tgoto out_close;\n"
        "\t}\n"
        "\tif (connector_is_connected(fd, connector_id)) {\n"
        '\t\tfprintf(stderr, "attached connector is not connected\\n");\n'
        "\t\tgoto out_detach;\n"
        "\t}\n"
        "\n"
        "\tret = EXIT_SUCCESS;\n"
        "\n"
        "out_detach:\n"
        "\tif (detach_monitor(fd, connector_id) && ret == EXIT_SUCCESS) {\n"
        '\t\tperror("DETACH_MONITOR");\n'
        "\t\tret = EXIT_FAILURE;\n"
        "\t}\n"
        "out_close:\n"
        "\tclose(fd);\n"
        "\treturn ret;\n"
        "}\n"
    )

    supports = [functional_repo / f"audio-{index}.c" for index in range(10)]
    _commit_baseline(functional_repo, supports)
    git_stage_batch("stop", check=False)
    path = functional_repo / "tool.c"
    path.write_text(target)
    for index, support in enumerate(supports):
        support.write_text(f"void audio_output_{index}(void) {{}}\n")
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    discard = _display_id_for_text(view, "char discard;")
    connector = _display_id_for_text(view, 'printf("connector_id=')
    attached = _display_id_for_text(view, 'printf("attached=1')
    flush = _display_id_for_text(view, "fflush(stdout)")
    wait = _display_id_for_text(view, "read(STDIN_FILENO")
    earlier = git_stage_batch(
        "discard",
        "--to",
        "attachment-lifecycle",
        "--line",
        f"{discard},{connector},{attached},{flush},{wait}",
        "--no-auto-advance",
        check=False,
    )
    assert earlier.returncode == 0, earlier.stderr
    assert path.read_text() == target_after_earlier_peel

    earlier_replay = git_stage_batch(
        "apply",
        "--from",
        "attachment-lifecycle",
        check=False,
    )
    assert earlier_replay.returncode == 0, earlier_replay.stderr
    assert path.read_text() == target
    earlier_undo = git_stage_batch("undo", check=False)
    assert earlier_undo.returncode == 0, earlier_undo.stderr
    assert path.read_text() == target_after_earlier_peel

    for index, support in enumerate(supports):
        git_stage_batch("show", "--file", support.name, "--page", "all")
        audio = git_stage_batch(
            "discard",
            "--to",
            "audio-output",
            "--file",
            support.name,
            "--no-auto-advance",
            check=False,
        )
        assert audio.returncode == 0, audio.stderr
        assert not support.exists()

    initial_replay = git_stage_batch(
        "apply",
        "--from",
        "audio-output",
        check=False,
    )
    assert initial_replay.returncode == 0, initial_replay.stderr
    for index, support in enumerate(supports):
        assert support.read_text() == f"void audio_output_{index}(void) {{}}\n"
    undo = git_stage_batch("undo", check=False)
    assert undo.returncode == 0, undo.stderr
    for index, support in enumerate(supports):
        assert not support.exists()
    again = git_stage_batch("again", "--no-auto-advance", check=False)
    assert again.returncode == 0, again.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    first = _display_id_for_text(view, "uint8_t edid[MAX_SIZE]")
    last = _display_id_for_text(view, "if (attach_monitor(fd, connector_id, edid")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "audio-output",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            "\tuint8_t edid[BLOCK_SIZE];\n"
            "\tuint32_t connector_id;\n"
            "\tint inherited_fd = -1;\n"
            "\tint fd;\n"
            "\tint ret = EXIT_FAILURE;\n"
            "\n"
            '\tif (argc == 2 && !strcmp(argv[1], "--help")) {\n'
            "\t\tusage(argv[0]);\n"
            "\t\treturn EXIT_SUCCESS;\n"
            "\t}\n"
            '\tif (argc == 3 && !strcmp(argv[1], "--grant-fd")) {\n'
            "\t\tif (parse_fd(argv[2], &inherited_fd)) {\n"
            "\t\t\tusage(argv[0]);\n"
            "\t\t\treturn EXIT_FAILURE;\n"
            "\t\t}\n"
            "\t} else if (argc != 1) {\n"
            "\t\tusage(argv[0]);\n"
            "\t\treturn EXIT_FAILURE;\n"
            "\t}\n"
            "\n"
            "\tfd = open_grant(inherited_fd, &connector_id);\n"
            "\tif (fd < 0)\n"
            "\t\treturn EXIT_FAILURE;\n"
            "\n"
            "\tif (fill_named_edid(edid) < 0) {\n"
            '\t\tfprintf(stderr, "failed to build attachment EDID\\n");\n'
            "\t\tgoto out_close;\n"
            "\t}\n"
            "\tif (attach_monitor(fd, connector_id, edid, sizeof(edid))) {\n"
        ),
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "audio-output",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target_after_earlier_peel
    for index, support in enumerate(supports):
        assert support.read_text() == f"void audio_output_{index}(void) {{}}\n"
