"""Small strict-JSON validation primitives for reusable command plans."""

from __future__ import annotations

import json
from typing import NoReturn, cast


class StrictJsonError(ValueError):
    """A JSON document violated syntax or an exact structural contract."""


def fail(detail: str) -> NoReturn:
    """Raise one structural validation failure."""
    raise StrictJsonError(detail)


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate field {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    fail(f"non-standard numeric value {value!r}")


def loads(payload: str) -> object:
    """Decode strict JSON, rejecting duplicate keys and numeric extensions."""
    try:
        return json.loads(
            payload,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_constant,
        )
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise StrictJsonError(
            f"document is not strict JSON ({error})"
        ) from error


def require_object(value: object, location: str) -> dict[str, object]:
    """Require one JSON object."""
    if not isinstance(value, dict):
        fail(f"{location} must be an object")
    return cast(dict[str, object], value)


def require_list(value: object, location: str) -> list[object]:
    """Require one JSON array."""
    if not isinstance(value, list):
        fail(f"{location} must be an array")
    return cast(list[object], value)


def require_exact_keys(
    value: dict[str, object],
    expected: frozenset[str],
    location: str,
) -> None:
    """Reject both missing and unknown object fields."""
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        fail(f"{location} is missing field(s): {', '.join(missing)}")
    if unknown:
        fail(f"{location} has unknown field(s): {', '.join(unknown)}")


def require_string(
    value: dict[str, object],
    field: str,
    location: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Require one string field."""
    if field not in value:
        fail(f"{location} is missing field {field!r}")
    result = value[field]
    if not isinstance(result, str) or (not allow_empty and not result):
        qualifier = "a string" if allow_empty else "a non-empty string"
        fail(f"{location}.{field} must be {qualifier}")
    return result


def require_integer(
    value: dict[str, object],
    field: str,
    location: str,
) -> int:
    """Require one true JSON integer rather than a boolean."""
    if field not in value or type(value[field]) is not int:
        fail(f"{location}.{field} must be an integer")
    return cast(int, value[field])
