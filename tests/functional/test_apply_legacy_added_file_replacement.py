"""Regression coverage for applying a legacy added-file replacement."""

import json
import subprocess

from .conftest import git_stage_batch


def _display_ids(view: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    ]


def _remove_complete_file_pair_marker(repo, batch_name: str) -> None:
    """Model replacement metadata saved before complete-file pair markers."""
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
    file_metadata = metadata["files"]["grant.c"]
    for deletion in file_metadata["deletions"]:
        deletion.pop("complete_file_pair", None)
    for claim in file_metadata["presence_claims"]:
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
        ["git", "commit-tree", tree, "-m", "Downgrade grant state"],
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


def test_apply_legacy_added_file_replacement_consumes_live_source(
    functional_repo,
):
    """Replay must replace the predecessor instead of prepending the target."""
    path = functional_repo / "grant.c"
    predecessor = (
        "// license\n"
        "#include \"core.h\"\n"
        "\n"
        "static int open_card(void)\n"
        "{\n"
        "    return legacy_open();\n"
        "}\n"
    )
    independent = (
        "static int output_index(int fd)\n"
        "{\n"
        "    return get_property(fd, \"output_index\");\n"
        "}\n"
        "\n"
    )
    target = (
        "// license\n"
        "#include \"core.h\"\n"
        "\n"
        + independent
        + "static int query_grant(int fd)\n"
        "{\n"
        "    return ioctl(fd, GET_GRANT);\n"
        "}\n"
        "\n"
        "static int open_grant(int inherited_fd)\n"
        "{\n"
        "    int fd = dup(inherited_fd);\n"
        "\n"
        "    if (fd < 0 || query_grant(fd))\n"
        "        return -1;\n"
        "    return fd;\n"
        "}\n"
    )
    advanced_predecessor = predecessor.replace(
        "#include \"core.h\"\n\n",
        "#include \"core.h\"\n\n" + independent,
        1,
    )

    # The file is absent at the session baseline. Peel a complete replacement,
    # then peel the predecessor that introduced the file.
    path.write_text(target)
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    ids = _display_ids(view)
    replace = git_stage_batch(
        "discard",
        "--to",
        "grant-adopter",
        "--line",
        f"{min(ids)}-{max(ids)}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert replace.returncode == 0, replace.stderr
    assert path.read_text() == predecessor

    base = git_stage_batch(
        "discard",
        "--to",
        "grant-base",
        "--file",
        path.name,
        check=False,
    )
    assert base.returncode == 0, base.stderr
    assert not path.exists()
    git_stage_batch("stop")
    _remove_complete_file_pair_marker(functional_repo, "grant-adopter")

    # Rebuild and commit the predecessor, then replay the legacy replacement
    # through the worktree-facing apply path.
    restore = git_stage_batch(
        "apply",
        "--from",
        "grant-base",
        "--file",
        path.name,
        check=False,
    )
    assert restore.returncode == 0, restore.stderr
    assert path.read_text() == predecessor
    path.write_text(advanced_predecessor)
    subprocess.run(["git", "add", path.name], cwd=functional_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add grant base"],
        cwd=functional_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    replay = git_stage_batch(
        "apply",
        "--from",
        "grant-adopter",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert path.read_text() == target, (
        "replacement target was prepended without consuming its live source:\n"
        f"{path.read_text()}"
    )
