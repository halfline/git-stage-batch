"""Regression coverage for adjacent replacements peeled from an added file."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_adjacent_replacements_in_added_file_keep_explicit_alternatives(
    functional_repo,
):
    """Earlier replacements must not split the alternative for the next one."""
    path = functional_repo / "painter.js"
    predecessor = (
        "function paint(view, entity) {\n"
        "  if (view.selected === entity.id) {\n"
        "    view.path(entity);\n"
        "    ctx.strokeStyle = '#fff0b4';\n"
        "    ctx.lineWidth = 2;\n"
        "    ctx.stroke();\n"
        "  }\n"
        "  if (image) {\n"
        "    draw(image);\n"
        "  } else {\n"
        "    drawFallback();\n"
        "  }\n"
        "}\n"
    )
    target = (
        "// earlier feature\n"
        "function paint(view, entity) {\n"
        "  const selected = view.selected === entity.id;\n"
        "  const rally = entity.id === plan.rallyId;\n"
        "  const obstacle = plan.obstacles?.some(({ id }) => id === entity.id);\n"
        "  if (obstacle) {\n"
        "    view.path(entity);\n"
        "    ctx.fillStyle = '#513e37aa';\n"
        "    ctx.fill();\n"
        "    ctx.strokeStyle = '#e6b19788';\n"
        "    ctx.lineWidth = 1;\n"
        "    ctx.stroke();\n"
        "  } else if (selected || rally || entity.locked) {\n"
        "    view.path(entity);\n"
        "    ctx.fillStyle = selected ? '#e7d59728' : '#22372620';\n"
        "    ctx.fill();\n"
        "    ctx.strokeStyle = selected ? '#fff0b4' : '#b1d0ba';\n"
        "    ctx.lineWidth = selected ? 2 : 1.2;\n"
        "    ctx.stroke();\n"
        "  }\n"
        "  if (image) {\n"
        "    draw(image);\n"
        "  } else if (!obstacle) {\n"
        "    drawFallback();\n"
        "  }\n"
        "}\n"
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    prior_id = _display_id_for_text(view, "// earlier feature")
    prior = git_stage_batch(
        "discard",
        "--to",
        "prior-feature",
        "--file",
        path.name,
        "--line",
        str(prior_id),
        "--no-auto-advance",
        check=False,
    )
    assert prior.returncode == 0, prior.stderr
    git_stage_batch("new", "obstacle", "--note", "Highlight obstacles")

    replacements = (
        ("} else if (!obstacle) {", "} else if (!obstacle) {", "  } else {\n"),
        (
            "ctx.fillStyle = '#513e37aa'",
            "ctx.lineWidth = selected ? 2 : 1.2",
            "    ctx.strokeStyle = '#fff0b4';\n    ctx.lineWidth = 2;\n",
        ),
        (
            "const selected = view.selected",
            "if (obstacle) {",
            "  if (view.selected === entity.id) {\n",
        ),
    )
    for first, last, alternative in replacements:
        view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
        start = _display_id_for_text(view, first)
        end = _display_id_for_text(view, last)
        result = git_stage_batch(
            "discard",
            "--to",
            "obstacle",
            "--file",
            path.name,
            "--line",
            f"{start}-{end}",
            "--as-stdin",
            "--no-edge-overlap",
            "--no-auto-advance",
            input_text=alternative,
            check=False,
        )
        assert result.returncode == 0, f"{first}: {result.stderr}"

    assert path.read_text() == predecessor
