"""Functional coverage for staging populated gitlinks."""

import resource
import subprocess

from .conftest import _git_stage_batch_command, git_stage_batch


def _git(repository, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _limit_output_file_size():
    limit = 512 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def test_include_large_populated_gitlink_does_not_need_a_temporary_archive(
    functional_repo,
):
    """A multi-gitlink checkpoint must not archive populated submodule trees."""
    source = functional_repo.parent / "submodule-source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")
    (source / "tracked.txt").write_text("first\n")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "First")

    _git(
        functional_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        "vendor/submodule",
    )
    _git(functional_repo, "commit", "-am", "Add submodule")

    other_source = functional_repo.parent / "other-submodule-source"
    other_source.mkdir()
    _git(other_source, "init")
    _git(other_source, "config", "user.name", "Test User")
    _git(other_source, "config", "user.email", "test@example.com")
    (other_source / "tracked.txt").write_text("first\n")
    _git(other_source, "add", "tracked.txt")
    _git(other_source, "commit", "-m", "First")
    _git(
        functional_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(other_source),
        "vendor/other",
    )
    _git(functional_repo, "commit", "-am", "Add other submodule")

    (source / "tracked.txt").write_text("second\n")
    _git(source, "commit", "-am", "Second")
    second = _git(source, "rev-parse", "HEAD").stdout.strip()
    submodule = functional_repo / "vendor/submodule"
    _git(submodule, "fetch")
    _git(submodule, "checkout", second)
    (submodule / "build-output.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
    (other_source / "tracked.txt").write_text("second\n")
    _git(other_source, "commit", "-am", "Second")
    other_second = _git(other_source, "rev-parse", "HEAD").stdout.strip()
    other_submodule = functional_repo / "vendor/other"
    _git(other_submodule, "fetch")
    _git(other_submodule, "checkout", other_second)

    git_stage_batch("start", "--no-auto-advance")
    result = subprocess.run(
        _git_stage_batch_command(
            "include",
            "--files",
            "vendor/*",
            "--no-auto-advance",
        ),
        text=True,
        capture_output=True,
        check=False,
        preexec_fn=_limit_output_file_size,
    )

    assert result.returncode == 0, result.stderr
    staged = _git(functional_repo, "rev-parse", ":vendor/submodule").stdout.strip()
    assert staged == second
    other_staged = _git(functional_repo, "rev-parse", ":vendor/other").stdout.strip()
    assert other_staged == other_second

    git_stage_batch("undo")
    restored = _git(functional_repo, "rev-parse", ":vendor/submodule").stdout.strip()
    first = _git(functional_repo, "rev-parse", "HEAD:vendor/submodule").stdout.strip()
    assert restored == first
    other_restored = _git(functional_repo, "rev-parse", ":vendor/other").stdout.strip()
    other_first = _git(functional_repo, "rev-parse", "HEAD:vendor/other").stdout.strip()
    assert other_restored == other_first
    assert _git(submodule, "rev-parse", "HEAD").stdout.strip() == second
    assert _git(other_submodule, "rev-parse", "HEAD").stdout.strip() == other_second
    assert (submodule / "build-output.bin").stat().st_size == 2 * 1024 * 1024
