"""Regression coverage for staged replay of a layered added-file rewrite."""

import json
import subprocess

from .conftest import git_stage_batch


def _display_ids(view: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _index_text(repo, path) -> str:
    return subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_index(repo, message: str) -> None:
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _remove_complete_file_pair_marker(repo, batch_name: str) -> None:
    """Downgrade saved metadata to the format emitted before the marker."""
    state_ref = f"refs/git-stage-batch/state/{batch_name}"
    metadata = json.loads(
        subprocess.run(
            ["git", "show", f"{state_ref}:batch.json"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    for deletion in metadata["files"]["dispatch.c"]["deletions"]:
        deletion.pop("complete_file_pair", None)
    for claim in metadata["files"]["dispatch.c"]["presence_claims"]:
        last_line = max(
            int(part.split("-", 1)[-1])
            for part in claim["source_lines"]
        )
        claim["baseline_references"] = {
            str(line): {"after_line": None, "before_line": None}
            for line in range(1, last_line + 1)
        }

    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=json.dumps(metadata),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=f"100644 blob {blob}\tbatch.json\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "Downgrade capture-adopter state"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", state_ref, commit],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_include_complete_added_file_replacement_preserves_prior_repair(
    functional_repo,
):
    """A layered whole-source replacement must not append its predecessor."""
    path = functional_repo / "dispatch.c"
    predecessor = (
        "static void dispatch(void)\n"
        "{\n"
        "    writeback();\n"
        "}\n"
        "\n"
        "static int set_crc(bool enabled)\n"
        "{\n"
        "    lock_irqsave();\n"
        "    if (enabled)\n"
        "        return demand_get();\n"
        "    demand_put();\n"
        "    return 0;\n"
        "}\n"
    )
    target = predecessor.replace(
        "    writeback();\n",
        "    capture();\n    writeback();\n",
    )
    repaired_predecessor = predecessor.replace(
        "    lock_irqsave();\n",
        "    lock_irq();\n",
    )
    expected = target.replace(
        "    lock_irqsave();\n",
        "    lock_irq();\n",
    )
    future_target = target + (
        "\nstatic void later_consumer(void)\n"
        "{\n"
        "    consume_frame();\n"
        "}\n"
    )
    # Model reverse decomposition of a file that did not exist at the base:
    # first peel a complete source replacement, then peel its predecessor.
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    ids = _display_ids(view)
    replace = git_stage_batch(
        "discard",
        "--to",
        "capture-adopter",
        "--line",
        f"{min(ids)}-{max(ids)}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert replace.returncode == 0, replace.stderr
    assert path.read_text() == predecessor

    peel = git_stage_batch(
        "discard",
        "--to",
        "dispatch-base",
        "--file",
        path.name,
        check=False,
    )
    assert peel.returncode == 0, peel.stderr
    assert not path.exists()
    git_stage_batch("stop")
    _remove_complete_file_pair_marker(functional_repo, "capture-adopter")

    # Rebuild the predecessor, commit an independent repair, and leave the
    # later target in the worktree just as a decomposition rebuild does.
    path.write_text(target)
    (functional_repo / "future.txt").write_text("later concern\n")
    git_stage_batch("start", "--no-auto-advance")
    restore = git_stage_batch(
        "include",
        "--from",
        "dispatch-base",
        "--file",
        path.name,
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    assert _index_text(functional_repo, path) == predecessor
    git_stage_batch("stop")
    _commit_index(functional_repo, "Add dispatch base")

    path.write_text(repaired_predecessor)
    subprocess.run(
        ["git", "add", path.name],
        cwd=functional_repo,
        check=True,
        capture_output=True,
    )
    _commit_index(functional_repo, "Repair dispatch locking")
    path.write_text(future_target)

    git_stage_batch("start", "--no-auto-advance")
    replay = git_stage_batch(
        "include",
        "--from",
        "capture-adopter",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert _index_text(functional_repo, path) == expected
