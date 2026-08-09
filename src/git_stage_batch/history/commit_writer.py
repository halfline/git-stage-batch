"""Deterministic unsigned commit-object construction for history replay."""

from __future__ import annotations

from collections.abc import Iterator

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from .commit_objects import ParsedCommitObject, parse_commit_object
from .models import HistoryCommitSnapshot, HistoryPlannedCommit


def _encode_header(value: str) -> bytes:
    return value.encode("utf-8", errors="surrogateescape")


def _planned_message_bytes(
    output: HistoryPlannedCommit,
    target: HistoryCommitSnapshot,
    *,
    env: dict[str, str] | None,
) -> bytes:
    if output.operation in {"KEEP", "REORDER"}:
        return parse_commit_object(target.commit_id, env=env).message_bytes
    try:
        return output.message.encode(
            output.encoding or "utf-8",
            errors="surrogateescape",
        )
    except (LookupError, UnicodeEncodeError) as error:
        raise CommandError(
            _(
                "The message for history source {commit} cannot be encoded "
                "as {encoding}."
            ).format(
                commit=target.commit_id,
                encoding=output.encoding or "UTF-8",
            )
        ) from error


def history_commit_payload_chunks(
    *,
    tree: str,
    parent: str,
    output: HistoryPlannedCommit,
    target: HistoryCommitSnapshot,
    env: dict[str, str] | None = None,
) -> Iterator[bytes]:
    """Yield one normalized unsigned commit object in bounded field chunks."""
    yield f"tree {tree}\n".encode("ascii")
    yield f"parent {parent}\n".encode("ascii")
    yield b"author "
    yield _encode_header(output.author.raw)
    yield b"\ncommitter "
    yield _encode_header(target.committer.raw)
    yield b"\n"
    if output.encoding is not None:
        yield b"encoding "
        yield _encode_header(output.encoding)
        yield b"\n"
    yield b"\n"
    yield _planned_message_bytes(output, target, env=env)


def create_history_commit(
    *,
    tree: str,
    parent: str,
    output: HistoryPlannedCommit,
    target: HistoryCommitSnapshot,
    write: bool,
    env: dict[str, str] | None = None,
) -> str:
    """Hash or store one deterministic unsigned replacement commit."""
    arguments = ["hash-object", "-t", "commit"]
    if write:
        arguments.append("-w")
    arguments.append("--stdin")
    return run_git_command(
        arguments,
        stdin_chunks=history_commit_payload_chunks(
            tree=tree,
            parent=parent,
            output=output,
            target=target,
            env=env,
        ),
        env=env,
        requires_index_lock=False,
    ).stdout.strip()


def require_history_commit_matches(
    commit: str,
    *,
    tree: str,
    parent: str,
    output: HistoryPlannedCommit,
    target: HistoryCommitSnapshot,
    env: dict[str, str] | None = None,
) -> ParsedCommitObject:
    """Require one built commit to match every planned object-level field."""
    parsed = parse_commit_object(commit, env=env)
    if parsed.tree != tree or parsed.parents != (parent,):
        raise CommandError(
            _("Rewrite output commit {commit} has unexpected topology.").format(
                commit=commit
            )
        )
    if parsed.author != output.author or parsed.committer != target.committer:
        raise CommandError(
            _(
                "Rewrite output commit {commit} has unexpected identity metadata."
            ).format(commit=commit)
        )
    if parsed.encoding != output.encoding or parsed.message != output.message:
        raise CommandError(
            _("Rewrite output commit {commit} has unexpected message metadata.").format(
                commit=commit
            )
        )
    if parsed.signatures or parsed.unsupported_headers:
        raise CommandError(
            _("Rewrite output commit {commit} has unexpected extra headers.").format(
                commit=commit
            )
        )
    return parsed
