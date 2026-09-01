"""Regression coverage for extending a transformed batch after another peel."""

import json
import subprocess

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def _claimed_lines(metadata, path):
    claimed = set()
    for presence in metadata["files"][path]["presence_claims"]:
        for item in presence["source_lines"]:
            for part in item.split(","):
                if "-" in part:
                    first, last = map(int, part.split("-", 1))
                    claimed.update(range(first, last + 1))
                else:
                    claimed.add(int(part))
    return claimed


def test_replacement_extends_whole_file_transform_after_independent_peel(
    functional_repo,
):
    """A lower replacement may augment a transformed batch after an upper peel."""
    path = functional_repo / "tool.c"
    target = (
        "caps = ASYNC_TX |\n"
        "       RX_INJECT |\n"
        "       TRANSPORT_STATE |\n"
        "       EDID;\n"
        "request path\n"
        "tail\n"
    )
    predecessor = target.replace("request path\n", "")
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    first = _display_id_for_text(view, "caps = ASYNC_TX")
    last = _display_id_for_text(view, "tail")
    transformed = git_stage_batch(
        "discard",
        "--to",
        "transmit-request",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert transformed.returncode == 0, transformed.stderr
    assert path.read_text() == predecessor

    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    receive = _display_id_for_text(view, "RX_INJECT")
    independent = git_stage_batch(
        "discard",
        "--to",
        "receive-injection",
        "--line",
        str(receive),
        "--no-auto-advance",
        check=False,
    )
    assert independent.returncode == 0, independent.stderr

    view = git_stage_batch("show", "--file", "tool.c", "--page", "all").stdout
    asynchronous = _display_id_for_text(view, "caps = ASYNC_TX")
    transport = _display_id_for_text(view, "TRANSPORT_STATE")
    extension = git_stage_batch(
        "discard",
        "--to",
        "transmit-request",
        "--line",
        f"{asynchronous}-{transport}",
        "--as-stdin",
        "--no-auto-advance",
        input_text="caps = TRANSPORT_STATE |\n",
        check=False,
    )
    assert extension.returncode == 0, extension.stderr
    advanced_predecessor = "caps = TRANSPORT_STATE |\n       EDID;\ntail\n"
    assert path.read_text() == advanced_predecessor

    replay = git_stage_batch(
        "apply",
        "--from",
        "transmit-request",
        "--file",
        "tool.c",
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target

    inverse = git_stage_batch(
        "discard",
        "--from",
        "transmit-request",
        "--file",
        "tool.c",
        check=False,
    )
    assert inverse.returncode == 0, inverse.stderr
    assert path.read_text() == advanced_predecessor


def test_replacement_extension_preserves_partial_batch_ownership(functional_repo):
    """A replacement must not turn a narrow batch into a whole-file claim."""
    path = functional_repo / "api.c"
    path.write_text(
        "base one\n"
        "caps = ASYNC_TX |\n"
        "       RX_INJECT |\n"
        "       TRANSPORT_STATE |\n"
        "       EDID;\n"
        "request path\n"
        "tail\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
    request = _display_id_for_text(view, "request path")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "transmit-request",
        "--line",
        str(request),
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
    receive = _display_id_for_text(view, "RX_INJECT")
    independent = git_stage_batch(
        "discard",
        "--to",
        "receive-injection",
        "--line",
        str(receive),
        "--no-auto-advance",
        check=False,
    )
    assert independent.returncode == 0, independent.stderr

    view = git_stage_batch("show", "--file", "api.c", "--page", "all").stdout
    asynchronous = _display_id_for_text(view, "caps = ASYNC_TX")
    transport = _display_id_for_text(view, "TRANSPORT_STATE")
    extension = git_stage_batch(
        "discard",
        "--to",
        "transmit-request",
        "--line",
        f"{asynchronous}-{transport}",
        "--as-stdin",
        "--no-auto-advance",
        input_text="caps = TRANSPORT_STATE |\n",
        check=False,
    )
    assert extension.returncode == 0, extension.stderr

    raw = subprocess.check_output(
        [
            "git",
            "show",
            "refs/git-stage-batch/state/transmit-request:batch.json",
        ],
        text=True,
    )
    claimed = _claimed_lines(json.loads(raw), "api.c")
    assert len(claimed) <= 3, claimed
