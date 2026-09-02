"""Regression coverage for an earlier replacement in a layered added-file batch."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _view(git_stage_batch, path):
    return git_stage_batch(
        "show",
        "--file",
        path.name,
        "--page",
        "all",
    ).stdout


def _replace(git_stage_batch, path, old_text, replacement, last_text=None):
    view = _view(git_stage_batch, path)
    first = _display_id_for_text(view, old_text)
    last = first if last_text is None else _display_id_for_text(view, last_text)
    return git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        str(first) if first == last else f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=replacement,
        check=False,
    )


def _discard(git_stage_batch, path, first_text, last_text=None):
    view = _view(git_stage_batch, path)
    first = _display_id_for_text(view, first_text)
    last = first if last_text is None else _display_id_for_text(view, last_text)
    return git_stage_batch(
        "discard",
        "--to",
        "monitor-detach",
        "--file",
        path.name,
        "--line",
        str(first) if first == last else f"{first}-{last}",
        "--no-auto-advance",
        check=False,
    )


def test_earlier_replacement_survives_later_replacements_and_deletions(
    functional_repo,
):
    """A batch may replace an earlier line after peeling several later regions."""
    path = functional_repo / "capture-grants.md"
    target = (
        "Errors:\n"
        "- connector is detached;\n"
        "- device teardown.\n"
        "\n"
        "Lifetime:\n"
        "Detaching stops streams.\n"
        "The holder may attach again.\n"
        "\n"
        "Revocation:\n"
        "1. Clean registered resources.\n"
        "2. Detach the monitor.\n"
        "3. Send the event.\n"
        "\n"
        "Cleanup finishes before monitor detachment or\n"
        "event delivery begins.\n"
        "\n"
        "Kernel details:\n"
        "Checks repeat so revocation racing startup cannot disclose a frame.\n"
        "Complete attachment and EDID transitions are serialized.\n"
        "Attachment ioctls acquire the transition lock before the holder lock,\n"
        "so detaching cannot deadlock against an EDID operation.\n"
        "End.\n"
    )
    predecessor = (
        target.replace(
            "Attachment ioctls acquire the transition lock before the holder lock,\n"
            "so detaching cannot deadlock against an EDID operation.\n",
            "Attachment operations acquire the transition lock before the holder "
            "lock,\n"
            "so attachment cannot deadlock against an EDID operation.\n",
        )
        .replace(
            "Checks repeat so revocation racing startup cannot disclose a frame.\n",
            "Checks repeat so revocation racing stream startup cannot disclose a "
            "frame.\n",
        )
        .replace(
            "Cleanup finishes before monitor detachment or\nevent delivery begins.\n",
            "Cleanup finishes before event delivery begins.\n",
        )
        .replace("3. Send the event.\n", "2. Send the event.\n")
        .replace("2. Detach the monitor.\n", "")
        .replace("Detaching stops streams.\nThe holder may attach again.\n\n", "")
        .replace("- connector is detached;\n", "- connector is not attached;\n")
    )
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")

    result = _replace(
        git_stage_batch,
        path,
        "Attachment ioctls acquire",
        "Attachment operations acquire the transition lock before the holder "
        "lock,\n"
        "so attachment cannot deadlock against an EDID operation.\n",
        "so detaching cannot deadlock",
    )
    assert result.returncode == 0, result.stderr
    result = _replace(
        git_stage_batch,
        path,
        "Checks repeat so revocation racing startup",
        "Checks repeat so revocation racing stream startup cannot disclose a frame.\n",
    )
    assert result.returncode == 0, result.stderr
    result = _replace(
        git_stage_batch,
        path,
        "Cleanup finishes before monitor detachment",
        "Cleanup finishes before event delivery begins.\n",
        "event delivery begins.",
    )
    assert result.returncode == 0, result.stderr
    result = _replace(
        git_stage_batch, path, "3. Send the event.", "2. Send the event.\n"
    )
    assert result.returncode == 0, result.stderr
    result = _discard(git_stage_batch, path, "2. Detach the monitor.")
    assert result.returncode == 0, result.stderr
    result = _discard(
        git_stage_batch,
        path,
        "Detaching stops streams.",
        "The holder may attach again.",
    )
    assert result.returncode == 0, result.stderr

    result = _replace(
        git_stage_batch,
        path,
        "- connector is detached;",
        "- connector is not attached;\n",
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "monitor-detach",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target
