"""Exact commit-object metadata parsing for history snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from .models import HistoryIdentity, HistorySignature


_IDENTITY_DATE = re.compile(rb"^-?[0-9]+$")
_IDENTITY_TIMEZONE = re.compile(rb"^[+-][0-9]{4}$")
_SIGNATURE_HEADERS = frozenset({b"gpgsig", b"gpgsig-sha256"})
_SUPPORTED_HEADERS = frozenset(
    {
        b"tree",
        b"parent",
        b"author",
        b"committer",
        b"encoding",
        *_SIGNATURE_HEADERS,
    }
)


@dataclass(frozen=True, slots=True)
class ParsedCommitObject:
    """Raw object facts needed by a history snapshot."""

    tree: str
    parents: tuple[str, ...]
    author: HistoryIdentity
    committer: HistoryIdentity
    encoding: str | None
    message: str
    message_bytes: bytes
    message_sha256: str
    signatures: tuple[HistorySignature, ...]
    unsupported_headers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Header:
    name: bytes
    value: bytes
    raw: bytes


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _decode_message(value: bytes, encoding: str | None) -> str:
    if encoding is not None:
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return _decode(value)


def _malformed(commit: str, field: str) -> CommandError:
    return CommandError(
        _("Commit {commit} has malformed {field} metadata.").format(
            commit=commit,
            field=field,
        )
    )


def _headers(payload: bytes, commit: str) -> tuple[tuple[_Header, ...], bytes]:
    try:
        header_bytes, message = payload.split(b"\n\n", 1)
    except ValueError as error:
        raise _malformed(commit, "object") from error

    parsed: list[_Header] = []
    current_name: bytes | None = None
    current_value = b""
    current_raw: list[bytes] = []

    def finish_current() -> None:
        nonlocal current_name, current_value, current_raw
        if current_name is None:
            return
        parsed.append(
            _Header(
                name=current_name,
                value=current_value,
                raw=b"\n".join(current_raw),
            )
        )
        current_name = None
        current_value = b""
        current_raw = []

    for line in header_bytes.split(b"\n"):
        if line.startswith(b" "):
            if current_name is None:
                raise _malformed(commit, "continued header")
            current_value += b"\n" + line[1:]
            current_raw.append(line)
            continue
        finish_current()
        name, separator, value = line.partition(b" ")
        if not separator or not name:
            raise _malformed(commit, "header")
        current_name = name
        current_value = value
        current_raw = [line]
    finish_current()
    return tuple(parsed), message


def _one_header(
    headers: tuple[_Header, ...],
    name: bytes,
    commit: str,
) -> bytes:
    values = [header.value for header in headers if header.name == name]
    if len(values) != 1:
        raise _malformed(commit, name.decode("ascii"))
    return values[0]


def _identity(value: bytes, commit: str, field: str) -> HistoryIdentity:
    if b"\0" in value or b"\n" in value or b"\r" in value:
        raise _malformed(commit, field)
    parts = value.rsplit(b" ", 2)
    if (
        len(parts) != 3
        or not _IDENTITY_DATE.fullmatch(parts[1])
        or not _IDENTITY_TIMEZONE.fullmatch(parts[2])
    ):
        raise _malformed(commit, field)
    identity = parts[0]
    name, separator, email_suffix = identity.rpartition(b" <")
    if not separator or not email_suffix.endswith(b">"):
        raise _malformed(commit, field)
    return HistoryIdentity(
        raw=_decode(value),
        name=_decode(name),
        email=_decode(email_suffix[:-1]),
        timestamp=int(parts[1]),
        timezone=parts[2].decode("ascii"),
    )


def parse_commit_object(commit: str) -> ParsedCommitObject:
    """Read and parse one commit object without Git display formatting."""
    payload = run_git_command(
        ["cat-file", "commit", commit],
        text_output=False,
        requires_index_lock=False,
    ).stdout
    headers, message_bytes = _headers(payload, commit)
    tree = _one_header(headers, b"tree", commit).decode("ascii")
    parents = tuple(
        header.value.decode("ascii")
        for header in headers
        if header.name == b"parent"
    )
    encoding_headers = [
        header.value for header in headers if header.name == b"encoding"
    ]
    if len(encoding_headers) > 1:
        raise _malformed(commit, "encoding")
    if encoding_headers and any(
        delimiter in encoding_headers[0] for delimiter in (b"\0", b"\n", b"\r")
    ):
        raise _malformed(commit, "encoding")
    encoding = _decode(encoding_headers[0]) if encoding_headers else None
    signatures = tuple(
        HistorySignature(
            header=header.name.decode("ascii"),
            sha256=hashlib.sha256(header.raw).hexdigest(),
        )
        for header in headers
        if header.name in _SIGNATURE_HEADERS
    )
    return ParsedCommitObject(
        tree=tree,
        parents=parents,
        author=_identity(_one_header(headers, b"author", commit), commit, "author"),
        committer=_identity(
            _one_header(headers, b"committer", commit),
            commit,
            "committer",
        ),
        encoding=encoding,
        message=_decode_message(message_bytes, encoding),
        message_bytes=message_bytes,
        message_sha256=hashlib.sha256(message_bytes).hexdigest(),
        signatures=signatures,
        unsupported_headers=tuple(
            _decode(header.name)
            for header in headers
            if header.name not in _SUPPORTED_HEADERS
        ),
    )
