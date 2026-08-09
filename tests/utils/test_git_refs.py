"""Tests for transactional Git reference updates."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from git_stage_batch.utils import git_refs


def _capture_ref_update(
    monkeypatch,
    *,
    durable: bool,
) -> tuple[list[str], bytes, dict[str, object]]:
    captured: list[tuple[list[str], bytes, dict[str, object]]] = []

    def capture(
        arguments: list[str],
        stdin_chunks: Iterable[bytes],
        **kwargs: object,
    ) -> Iterator[bytes]:
        captured.append((arguments, b"".join(stdin_chunks), kwargs))
        return iter(())

    monkeypatch.setattr(git_refs, "stream_git_command", capture)
    git_refs.update_git_refs(
        updates=(("refs/heads/topic", "a" * 40),),
        expected_old_values={"refs/heads/topic": "b" * 40},
        durable=durable,
    )
    assert len(captured) == 1
    return captured[0]


def test_durable_ref_update_enables_reference_fsync(monkeypatch) -> None:
    arguments, payload, kwargs = _capture_ref_update(
        monkeypatch,
        durable=True,
    )

    assert arguments == [
        "-c",
        "core.fsync=reference",
        "-c",
        "core.fsyncMethod=fsync",
        "update-ref",
        "--stdin",
    ]
    assert payload == (
        b"start\n"
        + b"update refs/heads/topic "
        + b"a" * 40
        + b" "
        + b"b" * 40
        + b"\nprepare\ncommit\n"
    )
    assert kwargs == {"requires_index_lock": False}


def test_ordinary_ref_update_retains_default_fsync_policy(monkeypatch) -> None:
    arguments, _payload, _kwargs = _capture_ref_update(
        monkeypatch,
        durable=False,
    )

    assert arguments == ["update-ref", "--stdin"]
