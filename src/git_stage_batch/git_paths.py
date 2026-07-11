"""Byte-safe conversion and parsing for pathnames emitted by Git."""

from __future__ import annotations

import os

from .exceptions import CommandError


_C_ESCAPES = {
    ord("a"): b"\a",
    ord("b"): b"\b",
    ord("t"): b"\t",
    ord("n"): b"\n",
    ord("v"): b"\v",
    ord("f"): b"\f",
    ord("r"): b"\r",
    ord('"'): b'"',
    ord("\\"): b"\\",
}


def encode_path(path: str) -> bytes:
    """Encode a filesystem path without losing undecodable bytes."""
    return os.fsencode(path)


def decode_path(path: bytes) -> str:
    """Decode a filesystem path without losing undecodable bytes."""
    return os.fsdecode(path)


def nul_records(output: bytes) -> list[bytes]:
    """Split NUL-delimited Git output, omitting its final empty record."""
    records = output.split(b"\0")
    if records and not records[-1]:
        records.pop()
    return records


def quote_path_token(path: bytes) -> bytes:
    """Encode raw pathname bytes as one Git C-style quoted token."""
    if path and all(
        32 < value < 127 and value not in (ord('"'), ord("\\"))
        for value in path
    ):
        return path

    escaped = bytearray(b'"')
    for value in path:
        if value == ord('"'):
            escaped.extend(b'\\"')
        elif value == ord("\\"):
            escaped.extend(b"\\\\")
        elif value == ord("\t"):
            escaped.extend(b"\\t")
        elif value == ord("\n"):
            escaped.extend(b"\\n")
        elif value == ord("\r"):
            escaped.extend(b"\\r")
        elif 32 <= value < 127:
            escaped.append(value)
        else:
            escaped.extend(f"\\{value:03o}".encode("ascii"))
    escaped.append(ord('"'))
    return bytes(escaped)


def unquote_path_token(token: bytes) -> bytes:
    """Decode one Git C-style quoted pathname token to raw bytes."""
    if not token.startswith(b'"'):
        return token
    if len(token) < 2 or not token.endswith(b'"'):
        raise CommandError("Unterminated quoted Git pathname")

    result = bytearray()
    index = 1
    end = len(token) - 1
    while index < end:
        value = token[index]
        if value != ord("\\"):
            result.append(value)
            index += 1
            continue

        index += 1
        if index >= end:
            raise CommandError("Incomplete escape in Git pathname")
        escaped = token[index]
        if escaped in _C_ESCAPES:
            result.extend(_C_ESCAPES[escaped])
            index += 1
            continue
        if ord("0") <= escaped <= ord("7"):
            octal_end = index
            while (
                octal_end < min(index + 3, end)
                and ord("0") <= token[octal_end] <= ord("7")
            ):
                octal_end += 1
            result.append(int(token[index:octal_end], 8))
            index = octal_end
            continue
        raise CommandError("Invalid escape in Git pathname")

    return bytes(result)


def quoted_token_end(data: bytes, start: int = 0) -> int:
    """Return the exclusive end of a C-quoted token in *data*."""
    if start >= len(data) or data[start] != ord('"'):
        raise CommandError("Expected quoted Git pathname")
    index = start + 1
    while index < len(data):
        if data[index] == ord("\\"):
            index += 2
            continue
        if data[index] == ord('"'):
            return index + 1
        index += 1
    raise CommandError("Unterminated quoted Git pathname")
