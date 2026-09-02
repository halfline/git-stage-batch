"""Regression coverage for replaying a deletion past inserted context."""

import os
import subprocess

from .conftest import git_stage_batch as source_git_stage_batch


def _git_stage_batch(*args, input_text=None, check=True):
    binary = os.environ.get("GIT_STAGE_BATCH_TEST_BINARY")
    if not binary:
        return source_git_stage_batch(
            *args,
            input_text=input_text,
            check=check,
        )
    return subprocess.run(
        [binary, *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        capture_output=True,
        check=check,
    )


def _index_text(repo, path) -> str:
    return subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _display_id_for_text(view: str, text: str) -> int:
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_include_deletion_follows_baseline_past_inserted_source_context(
    functional_repo,
):
    """A source insertion inside a deletion span must not make replay a no-op."""
    path = functional_repo / "README.md"
    baseline = (
        "before\n"
        "\n"
        "obsolete setup\n"
        "obsolete assertion\n"
        "\n"
        "after\n"
    )
    batch_source = (
        "before\n"
        "\n"
        "check tool\n"
        "\n"
        "after\n"
    )
    replay_target = (
        "before\n"
        "\n"
        "check tool\n"
        "updated setup\n"
        "obsolete assertion\n"
        "\n"
        "after\n"
    )

    path.write_text(baseline)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Add obsolete setup"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    path.write_text(batch_source)
    _git_stage_batch("start", "--no-auto-advance")
    view = _git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    deletion_ids = [
        _display_id_for_text(view, "obsolete setup"),
        _display_id_for_text(view, "obsolete assertion"),
    ]
    saved = _git_stage_batch(
        "discard",
        "--to",
        "extract-setup",
        "--line",
        ",".join(str(display_id) for display_id in deletion_ids),
        "--no-auto-advance",
        check=False,
    )
    assert saved.returncode == 0, saved.stderr

    path.write_text(replay_target)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Insert tool check"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )

    replay = _git_stage_batch(
        "include",
        "--from",
        "extract-setup",
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )

    assert replay.returncode == 0, replay.stderr
    assert _index_text(functional_repo, path) == batch_source
