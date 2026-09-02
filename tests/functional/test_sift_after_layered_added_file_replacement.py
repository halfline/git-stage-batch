"""Regression coverage for layered sifts in a newly added file."""

from .conftest import git_stage_batch


def _save_replace_and_sift(path, batch, predecessor):
    saved = git_stage_batch(
        "include",
        "--to",
        batch,
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert saved.returncode == 0, saved.stderr

    replaced = git_stage_batch(
        "discard",
        "--file",
        path.name,
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert replaced.returncode == 0, replaced.stderr

    return git_stage_batch(
        "sift",
        "--from",
        batch,
        "--to",
        batch,
        check=False,
    )


def test_sift_accepts_second_replacement_in_partially_peeled_added_file(
    functional_repo,
):
    """A prior peeled slice must not make the next sift look like a rewrite."""
    path = functional_repo / "capture.c"
    base = (
        "struct stream {\n"
        "    bool active;\n"
        "};\n"
        "\n"
        "static void attach(struct stream *stream)\n"
        "{\n"
        "    stream->active = true;\n"
        "}\n"
        "\n"
        "static void detach(struct stream *stream)\n"
        "{\n"
        "    stream->active = false;\n"
        "}\n"
    )
    with_cursor_exclusion = base.replace(
        "    bool active;\n",
        "    bool active;\n    bool exclude_cursor;\n",
    ).replace(
        "    stream->active = true;\n",
        "    stream->active = true;\n    stream->exclude_cursor = true;\n",
    )
    target = with_cursor_exclusion.replace(
        "    stream->active = true;\n",
        "    stream->active = true;\n    publish_active(stream);\n",
    ).replace(
        "    stream->active = false;\n",
        "    publish_inactive(stream);\n    stream->active = false;\n",
    )
    path.write_text(target)
    header = functional_repo / "capture_internal.h"
    header_target = (
        "struct stream {\n"
        "    bool active;\n"
        "    bool exclude_cursor;\n"
        "};\n"
    )
    header_predecessor = header_target.replace("    bool exclude_cursor;\n", "")
    header.write_text(header_target)

    git_stage_batch("start", "--no-auto-advance")
    first = _save_replace_and_sift(
        path, "active-property", with_cursor_exclusion
    )
    assert first.returncode == 0, first.stderr
    assert path.read_text() == with_cursor_exclusion

    header_result = _save_replace_and_sift(
        header, "cursor-exclusion", header_predecessor
    )
    assert header_result.returncode == 0, header_result.stderr
    assert header.read_text() == header_predecessor

    second = _save_replace_and_sift(path, "cursor-exclusion", base)
    assert second.returncode == 0, second.stderr
    assert path.read_text() == base

    replay = git_stage_batch(
        "apply",
        "--from",
        "cursor-exclusion",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == with_cursor_exclusion
