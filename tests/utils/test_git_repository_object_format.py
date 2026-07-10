"""Repository object-format discovery tests."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.utils.git_repository import (
    get_git_object_format,
    null_object_id,
    object_id_hex_length,
)


@pytest.mark.parametrize(("object_format", "width"), [("sha1", 40), ("sha256", 64)])
def test_repository_object_format_controls_oid_width(
    tmp_path,
    monkeypatch,
    object_format,
    width,
):
    """Object helpers should follow the format selected by git init."""
    repo = tmp_path / object_format
    subprocess.run(
        ["git", "init", f"--object-format={object_format}", str(repo)],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repo)

    assert get_git_object_format() == object_format
    assert object_id_hex_length() == width
    assert null_object_id() == "0" * width
