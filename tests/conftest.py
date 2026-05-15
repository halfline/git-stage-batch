"""Shared pytest helpers."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import overload

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Refresh the editable Meson build before xdist workers collect tests."""
    if getattr(config, "workerinput", None) is not None:
        return

    numprocesses = getattr(config.option, "numprocesses", None)
    if numprocesses in (None, 0, "0"):
        return

    _sync_editable_meson_build()


def _sync_editable_meson_build() -> None:
    """Run the current interpreter's editable Meson build if it exists."""
    project_root = Path(__file__).resolve().parent.parent
    build_dir = project_root / "build" / f"cp{sys.version_info.major}{sys.version_info.minor}"
    if not (build_dir / "build.ninja").exists():
        return

    result = subprocess.run(
        ["ninja", "-C", str(build_dir)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise pytest.UsageError(
            "failed to refresh editable Meson build before xdist collection:\n"
            f"{result.stderr}"
        )


class _LineSequence(Sequence[bytes]):
    """Minimal non-list byte-line sequence for API contract tests."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = tuple(lines)

    def __len__(self) -> int:
        return len(self._lines)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[bytes, ...]: ...

    def __getitem__(self, index: int | slice) -> bytes | tuple[bytes, ...]:
        return self._lines[index]


@pytest.fixture
def line_sequence():
    """Return a minimal byte-line sequence type."""
    return _LineSequence
