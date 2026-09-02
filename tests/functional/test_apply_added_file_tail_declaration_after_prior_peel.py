"""Regression coverage for replaying an added-file tail after a prior peel."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_tail_declaration_replays_after_preceding_declaration_peel(
    functional_repo,
):
    """A removed predecessor must not remain the tail insertion's only anchor."""
    path = functional_repo / "capture_uapi.h"
    target = (
        "#ifndef CAPTURE_UAPI_H\n"
        "#define CAPTURE_UAPI_H\n"
        "\n"
        "struct file;\n"
        "\n"
        "int queue_buffer(unsigned int id,\n"
        "                 struct file *file);\n"
        "int read_cursor_bitmap(unsigned int id,\n"
        "                       struct file *file);\n"
        "\n"
        "void file_fini(struct file *file);\n"
        "\n"
        "#endif\n"
    )
    after_prior_peel = target.replace(
        "int read_cursor_bitmap(unsigned int id,\n"
        "                       struct file *file);\n",
        "",
    )
    predecessor = after_prior_peel.replace(
        "void file_fini(struct file *file);\n\n",
        "",
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    cursor = _display_id_for_text(view, "int read_cursor_bitmap")
    cursor_file = _display_id_for_text(
        view, "                       struct file *file);"
    )
    assert cursor_file > cursor
    peeled = git_stage_batch(
        "discard",
        "--to",
        "cursor-bitmap",
        "--line",
        f"{cursor}-{cursor_file}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_prior_peel

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    file_fini = _display_id_for_text(view, "void file_fini")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "file-close",
        "--line",
        f"{file_fini}-{file_fini + 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == predecessor

    batch_view = git_stage_batch(
        "show",
        "--from",
        "file-close",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout
    assert "read_cursor_bitmap" in batch_view

    replay = git_stage_batch(
        "apply",
        "--from",
        "file-close",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == after_prior_peel
