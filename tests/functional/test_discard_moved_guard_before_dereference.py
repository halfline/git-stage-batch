"""Regression coverage for a moved guard after layered same-file peels."""

from pathlib import Path
import subprocess

from .conftest import git_stage_batch


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _display_ids_for_text(view: str, text: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if text in line and "[#" in line
    ]


def _one_display_id(view: str, text: str) -> int:
    matches = _display_ids_for_text(view, text)
    assert len(matches) == 1, matches
    return matches[0]


def test_discard_layered_castkms_queue_move_preserves_source(functional_repo):
    """Peeling the queue move must not restore its guard at the old site."""
    path = functional_repo / "castkms_capture_uapi.c"
    baseline = (FIXTURE_ROOT / "castkms_capture_uapi_baseline.c").read_text()
    source = (
        FIXTURE_ROOT / "castkms_capture_uapi_session_source.c"
    ).read_text()
    queue_target = (FIXTURE_ROOT / "castkms_queue_move_target.c").read_text()
    queue_baseline = (
        FIXTURE_ROOT / "castkms_queue_move_baseline.c"
    ).read_text()

    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add layered capture adapter fixture"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    path.write_text(source)

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "cursor")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    git_stage_batch(
        "discard",
        "--to",
        "cursor",
        "--line",
        f"{_one_display_id(view, 'void *bitmap = NULL')}-"
        f"{_one_display_id(view, 'kfree(bitmap)')}",
        "--no-auto-advance",
    )

    git_stage_batch("new", "route")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    route_ids = []
    for text in (
        '#include "castkms_connector.h"',
        "struct castkms_output *routed_output",
        "ret = castkms_capture_stream_status(stream)",
        "if (ret == -EACCES)",
        "ret = castkms_connector_get_routed_output",
        "castkms_capture_authority_connector(authority)",
        "&routed_output)",
        "if (!ret && routed_output != output)",
        "ret = -ESTALE",
    ):
        route_ids.extend(_display_ids_for_text(view, text))
    assert sorted(route_ids) == [2, *range(25, 32)]
    git_stage_batch(
        "discard",
        "--to",
        "route",
        "--line",
        "2,25-31",
        "--no-auto-advance",
    )

    git_stage_batch("new", "queue-preparation")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    first = min(_display_ids_for_text(view, "uapi_request = kzalloc_obj"))
    last = max(_display_ids_for_text(view, "drm_event_cancel_free"))
    assert (first, last) == (33, 94)
    result = git_stage_batch(
        "discard",
        "--to",
        "queue-preparation",
        "--line",
        f"{first}-{last}",
        "--no-auto-advance",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    batch = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/batches/queue-preparation:castkms_capture_uapi.c",
        ],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert batch == baseline.replace(queue_baseline, queue_target)
