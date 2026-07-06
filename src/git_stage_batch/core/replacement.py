"""Neutral replacement text payloads and byte-line helpers."""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.text import bytes_to_lines


@dataclass(frozen=True, slots=True)
class ReplacementPayload:
    """Exact replacement bytes plus optional argv display text."""

    data: bytes
    display_text: str | None = None
    exact: bool = True

    @classmethod
    def from_text(cls, text: str, *, exact: bool = True) -> "ReplacementPayload":
        return cls(
            text.encode("utf-8", errors="surrogateescape"),
            display_text=text,
            exact=exact,
        )

    @property
    def has_trailing_lf(self) -> bool:
        return self.data.endswith(b"\n")

    def as_text(self) -> str:
        return self.data.decode("utf-8", errors="surrogateescape")


class ReplacementText(str):
    """String-compatible replacement value carrying exact source bytes."""

    def __new__(
        cls,
        text: str,
        *,
        data: bytes | None = None,
        exact: bool = True,
    ) -> "ReplacementText":
        obj = str.__new__(cls, text)
        obj.data = text.encode("utf-8", errors="surrogateescape") if data is None else data
        obj.exact = exact
        return obj


def coerce_replacement_payload(
    replacement: str | bytes | ReplacementPayload,
) -> ReplacementPayload:
    """Return exact replacement bytes for legacy str callers and new payloads."""
    if isinstance(replacement, ReplacementPayload):
        return replacement
    if isinstance(replacement, ReplacementText):
        return ReplacementPayload(
            replacement.data,
            display_text=str(replacement) if not replacement.exact else None,
            exact=replacement.exact,
        )
    if isinstance(replacement, bytes):
        return ReplacementPayload(replacement)
    # Plain str is the legacy command-internal API. Preserve its historical
    # line-oriented behavior; CLI --as-stdin uses ReplacementText for exact bytes.
    return ReplacementPayload.from_text(replacement, exact=False)


def replacement_line_chunks(payload: ReplacementPayload) -> list[bytes]:
    """Split replacement bytes into exact line chunks."""
    if not payload.exact:
        return [line + b"\n" for line in payload.data.splitlines()]
    return list(bytes_to_lines([payload.data]))


def replacement_line_bodies(payload: ReplacementPayload) -> list[bytes]:
    """Return editor line bodies while preserving CRLF as body CR bytes."""
    if not payload.exact:
        return payload.data.splitlines()
    bodies: list[bytes] = []
    for line in replacement_line_chunks(payload):
        if line.endswith(b"\n"):
            bodies.append(line[:-1])
        else:
            bodies.append(line)
    return bodies
