"""Shape and size validation for file-job transport values."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import importlib
from pathlib import (
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)


_MAX_TRANSPORT_STRING_CHARACTERS = 16 * 1024
_MAX_TRANSPORT_TUPLE_ITEMS = 256
_MAX_TRANSPORT_INTEGER_BITS = 256
_MAX_TRANSPORT_VALUE_COUNT = 4 * 1024
_MAX_TRANSPORT_NESTING_DEPTH = 64
_MAX_TRANSPORT_TOTAL_STRING_CHARACTERS = 64 * 1024
_STANDARD_PATH_TYPES = frozenset(
    {
        Path,
        PosixPath,
        PurePath,
        PurePosixPath,
        PureWindowsPath,
        WindowsPath,
    }
)


@dataclass(slots=True)
class _TransportValidationState:
    active_ids: set[int]
    value_count: int = 0
    string_characters: int = 0


def assert_file_job_transport_value(
    value: object,
    *,
    label: str = "transport value",
) -> None:
    """Reject content-bearing or non-compact values before Python IPC."""
    _assert_transport_value(
        value,
        label=label,
        state=_TransportValidationState(set()),
        depth=0,
    )


def _assert_transport_value(
    value: object,
    *,
    label: str,
    state: _TransportValidationState,
    depth: int,
) -> None:
    state.value_count += 1
    if state.value_count > _MAX_TRANSPORT_VALUE_COUNT:
        raise TypeError(f"{label} contains too many transport values")
    if depth > _MAX_TRANSPORT_NESTING_DEPTH:
        raise TypeError(f"{label} contains excessive nesting")

    if value is None:
        return
    if isinstance(value, Enum):
        _assert_importable_transport_type(value, label=label)
        _assert_transport_enum_shape(value, label=label)
        _recurse_transport_values(
            value,
            label=label,
            state=state,
            depth=depth,
            values=(value.value,),
        )
        return
    # Exact scalar and collection types are intentional. State-bearing
    # subclasses may customize pickle behavior or hide additional content.
    if type(value) in {bool, float}:
        return
    if type(value) is int:
        if value.bit_length() > _MAX_TRANSPORT_INTEGER_BITS:
            raise TypeError(f"{label} contains an oversized integer")
        return
    if type(value) is str:
        _assert_transport_string(value, label=label, state=state)
        return
    if isinstance(value, (bool, int, float, str)):
        raise TypeError(f"{label} contains a state-bearing scalar subtype")
    if isinstance(value, PurePath):
        _assert_importable_transport_type(value, label=label)
        if type(value) not in _STANDARD_PATH_TYPES:
            raise TypeError(f"{label} contains a custom path subtype")
        _assert_transport_string(str(value), label=label, state=state)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} contains file-content bytes")
    if isinstance(value, (list, dict, set, frozenset)):
        raise TypeError(f"{label} contains a state-bearing collection subtype")
    if type(value) is tuple:
        if len(value) > _MAX_TRANSPORT_TUPLE_ITEMS:
            raise TypeError(f"{label} contains too many tuple items")
        _recurse_transport_values(
            value,
            label=label,
            state=state,
            depth=depth,
            values=value,
        )
        return
    if isinstance(value, tuple):
        raise TypeError(f"{label} contains a state-bearing tuple subtype")
    if is_dataclass(value) and not isinstance(value, type):
        _assert_importable_transport_type(value, label=label)
        _assert_transport_dataclass_shape(value, label=label)
        _recurse_transport_values(
            value,
            label=label,
            state=state,
            depth=depth,
            values=(
                (field.name, getattr(value, field.name)) for field in fields(value)
            ),
            named_values=True,
        )
        return
    raise TypeError(
        f"{label} contains unsupported {type(value).__module__}."
        f"{type(value).__qualname__}"
    )


def _assert_importable_transport_type(value: object, *, label: str) -> None:
    value_type = type(value)
    module_name = getattr(value_type, "__module__", "")
    name = getattr(value_type, "__name__", "")
    qualified_name = getattr(value_type, "__qualname__", "")
    if (
        not module_name
        or module_name == "__main__"
        or not name
        or qualified_name != name
    ):
        raise TypeError(f"{label} has a non-importable value type")
    try:
        imported_type = getattr(importlib.import_module(module_name), name)
    except Exception as error:
        raise TypeError(f"{label} has a non-importable value type") from error
    if imported_type is not value_type:
        raise TypeError(f"{label} has a non-importable value type")


def _assert_transport_enum_shape(value: Enum, *, label: str) -> None:
    for value_class in type(value).__mro__:
        if value_class.__module__ in {"builtins", "enum"}:
            continue
        if _has_custom_pickle_hook(value_class):
            raise TypeError(f"{label} contains custom pickle behavior")


def _assert_transport_dataclass_shape(value: object, *, label: str) -> None:
    value_type = type(value)
    parameters = getattr(value_type, "__dataclass_params__", None)
    if parameters is None or not parameters.frozen or hasattr(value, "__dict__"):
        raise TypeError(f"{label} must use a frozen slots dataclass")

    field_names = {field.name for field in fields(value)}
    for value_class in value_type.__mro__:
        if value_class is object:
            continue
        slots = value_class.__dict__.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        undeclared_slots = (
            set(slots)
            - field_names
            - {
                "__dict__",
                "__weakref__",
            }
        )
        if undeclared_slots:
            raise TypeError(f"{label} contains undeclared dataclass slots")

        for hook_name in (
            "__reduce__",
            "__reduce_ex__",
            "__getnewargs__",
            "__getnewargs_ex__",
            "__getstate__",
            "__setstate__",
        ):
            hook = value_class.__dict__.get(hook_name)
            if hook is None:
                continue
            if (
                hook_name in {"__getstate__", "__setstate__"}
                and getattr(hook, "__module__", None) == "dataclasses"
            ):
                continue
            raise TypeError(f"{label} contains custom pickle behavior")


def _has_custom_pickle_hook(value_class: type[object]) -> bool:
    return any(
        hook_name in value_class.__dict__
        for hook_name in (
            "__reduce__",
            "__reduce_ex__",
            "__getnewargs__",
            "__getnewargs_ex__",
            "__getstate__",
            "__setstate__",
        )
    )


def _assert_transport_string(
    value: str,
    *,
    label: str,
    state: _TransportValidationState,
) -> None:
    if len(value) > _MAX_TRANSPORT_STRING_CHARACTERS:
        raise TypeError(f"{label} contains an oversized string")
    state.string_characters += len(value)
    if state.string_characters > _MAX_TRANSPORT_TOTAL_STRING_CHARACTERS:
        raise TypeError(f"{label} contains too much string data")


def _recurse_transport_values(
    owner: object,
    *,
    label: str,
    state: _TransportValidationState,
    depth: int,
    values: Sequence[object] | Iterator[tuple[str, object]],
    named_values: bool = False,
) -> None:
    value_id = id(owner)
    if value_id in state.active_ids:
        raise TypeError(f"{label} contains a recursive value")
    state.active_ids.add(value_id)
    try:
        for index, value in enumerate(values):
            value_label = f"{label}[{index}]"
            if named_values:
                value_name, value = value
                value_label = f"{label}.{value_name}"
            _assert_transport_value(
                value,
                label=value_label,
                state=state,
                depth=depth + 1,
            )
    finally:
        state.active_ids.remove(value_id)
