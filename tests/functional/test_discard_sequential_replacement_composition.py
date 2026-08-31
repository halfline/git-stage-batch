"""Regression coverage for sequential replacements in one batch."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_later_replacement_composes_with_earlier_multiline_replacement(
    functional_repo,
):
    """Changing a retained replacement line must keep the batch replayable."""
    path = functional_repo / "new_test.c"
    target = (
        "before\n"
        "struct holder {\n"
        "    struct nested item;\n"
        "};\n"
        "static void init(struct holder *holder)\n"
        "{\n"
        "    holder->item.value = 1;\n"
        "}\n"
        "\n"
        "struct fence {\n"
        "    int fence_value;\n"
        "};\n"
        "\n"
        "outer.inner(&outer.inner,\n"
        "            value);\n"
        "after\n"
    )
    intermediate = (
        "before\n"
        "struct holder {\n"
        "    int item;\n"
        "    int value;\n"
        "};\n"
        "static void init(struct holder *holder)\n"
        "{\n"
        "    holder->item = 1;\n"
        "    holder->value = 2;\n"
        "}\n"
        "\n"
        "outer(&outer,\n"
        "            value);\n"
        "after\n"
    )
    predecessor = (
        "before\n"
        "struct holder {\n"
        "    int item;\n"
        "    int value;\n"
        "};\n"
        "static void init(struct holder *holder)\n"
        "{\n"
        "    holder->item = 1;\n"
        "    holder->value = 2;\n"
        "}\n"
        "\n"
        "outer(&outer,\n"
        "       value);\n"
        "after\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    holder = _display_id_for_text(view, "struct holder {")
    old_assignment = _display_id_for_text(view, "holder->item.value = 1;")
    fixture = git_stage_batch(
        "discard",
        "--to",
        "rewrite",
        "--line",
        f"{holder}-{old_assignment + 1}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=(
            "struct holder {\n"
            "    int item;\n"
            "    int value;\n"
            "};\n"
            "static void init(struct holder *holder)\n"
            "{\n"
            "    holder->item = 1;\n"
            "    holder->value = 2;\n"
            "}\n"
        ),
        check=False,
    )
    assert fixture.returncode == 0, fixture.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    fence = _display_id_for_text(view, "struct fence {")
    fence_value = _display_id_for_text(view, "int fence_value;")
    removed = git_stage_batch(
        "discard",
        "--to",
        "rewrite",
        "--line",
        f"{fence}-{fence_value + 2}",
        "--no-auto-advance",
        check=False,
    )
    assert removed.returncode == 0, removed.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    call = _display_id_for_text(view, "outer.inner(&outer.inner")
    argument = _display_id_for_text(view, "            value);")
    first = git_stage_batch(
        "discard",
        "--to",
        "rewrite",
        "--line",
        f"{call}-{argument}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="outer(&outer,\n            value);\n",
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert path.read_text() == intermediate

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    argument = _display_id_for_text(view, "            value);")
    second = git_stage_batch(
        "discard",
        "--to",
        "rewrite",
        "--line",
        str(argument),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text="       value);\n",
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "rewrite",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
