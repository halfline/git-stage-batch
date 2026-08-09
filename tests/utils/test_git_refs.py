"""Tests for transactional Git reference updates."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import subprocess

import pytest

from git_stage_batch.utils import git_refs


def _capture_ref_update(
    monkeypatch,
    *,
    durable: bool,
    no_deref: bool = False,
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
        no_deref=no_deref,
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


def test_exact_ref_update_disables_symbolic_ref_dereferencing(monkeypatch) -> None:
    arguments, payload, kwargs = _capture_ref_update(
        monkeypatch,
        durable=True,
        no_deref=True,
    )

    assert arguments == [
        "-c",
        "core.fsync=reference",
        "-c",
        "core.fsyncMethod=fsync",
        "update-ref",
        "--no-deref",
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


def test_exact_ref_update_preflights_symbolic_expected_absence(
    monkeypatch,
) -> None:
    commands: list[tuple[list[str], dict[str, object]]] = []

    def symbolic_ref(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            ["git", *arguments],
            0,
            stdout="refs/heads/unrelated\n",
            stderr="",
        )

    def reject_transaction(*_args: object, **_kwargs: object) -> Iterator[bytes]:
        pytest.fail("the ref transaction must not run")

    monkeypatch.setattr(git_refs, "run_git_command", symbolic_ref)
    monkeypatch.setattr(git_refs, "stream_git_command", reject_transaction)

    with pytest.raises(subprocess.CalledProcessError) as caught:
        git_refs.update_git_refs(
            updates=(("refs/heads/topic", "a" * 40),),
            expected_old_values={"refs/heads/topic": None},
            no_deref=True,
        )

    assert caught.value.returncode == 128
    assert caught.value.stderr == (
        "fatal: cannot update ref 'refs/heads/topic': "
        "the expected absent ref is symbolic\n"
    )
    assert commands == [
        (
            ["symbolic-ref", "--quiet", "refs/heads/topic"],
            {"check": False, "requires_index_lock": False},
        )
    ]


def test_exact_ref_update_rejects_a_dangling_symbolic_ref(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(
        ["git", "config", "user.name", "Ref Test"],
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ref-test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "base"],
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    owned_ref = "refs/git-stage-batch/test/owned"
    victim_ref = "refs/heads/unrelated"
    subprocess.run(
        ["git", "symbolic-ref", owned_ref, victim_ref],
        check=True,
    )

    with pytest.raises(subprocess.CalledProcessError):
        git_refs.update_git_refs(
            updates=((owned_ref, commit),),
            expected_old_values={owned_ref: None},
            no_deref=True,
        )

    assert (
        subprocess.run(
            ["git", "symbolic-ref", owned_ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == victim_ref
    )
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", victim_ref],
            check=False,
        ).returncode
        == 1
    )

    git_refs.update_git_refs(
        deletes=(owned_ref,),
        no_deref=True,
    )

    assert (
        subprocess.run(
            ["git", "symbolic-ref", "--quiet", owned_ref],
            check=False,
        ).returncode
        == 1
    )
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", victim_ref],
            check=False,
        ).returncode
        == 1
    )
