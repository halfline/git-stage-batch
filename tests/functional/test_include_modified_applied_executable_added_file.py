"""Regression coverage for staging an edited applied executable addition."""

import os
import subprocess

from .conftest import git_stage_batch


def _index_mode(repo, path):
    return subprocess.run(
        ["git", "ls-files", "-s", "--", path],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.split()[0]


def test_include_modified_applied_executable_added_file_preserves_mode(
    functional_repo,
):
    path = functional_repo / "check-architecture.sh"
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "reject() { return 1; }\n"
        "reject future-rule source.c\n"
        "printf 'pass\\n'\n"
    )
    path.chmod(0o755)

    git_stage_batch("new", "checker")
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch(
        "discard",
        "--to",
        "checker",
        "--file",
        path.name,
        "--no-auto-advance",
    )

    git_stage_batch("apply", "--from", "checker", "--file", path.name)
    assert os.access(path, os.X_OK)

    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "reject() { return 1; }\n"
        "printf 'pass\\n'\n"
    )
    assert os.access(path, os.X_OK)

    git_stage_batch("show", "--file", path.name, "--no-advance")
    git_stage_batch("include", "--file", path.name, "--no-auto-advance")

    assert _index_mode(functional_repo, path.name) == "100755"
