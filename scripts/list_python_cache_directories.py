#!/usr/bin/env python3
"""List prospective Python cache directories below an install source."""

import os
import sys
from pathlib import Path


def python_cache_directories(source_root: Path) -> list[str]:
    """Return every ``__pycache__`` path that could appear below source_root."""
    source_root = source_root.resolve()
    cache_directories = []

    for directory, child_directories, _files in os.walk(source_root):
        child_directories[:] = sorted(
            name for name in child_directories if name != "__pycache__"
        )
        relative_directory = Path(directory).relative_to(source_root)
        cache_directories.append(
            (relative_directory / "__pycache__").as_posix()
        )

    return cache_directories


def main(arguments: list[str]) -> int:
    """Print Meson-compatible relative cache-directory exclusions."""
    if len(arguments) != 1:
        print(
            "usage: list_python_cache_directories.py SOURCE_ROOT",
            file=sys.stderr,
        )
        return 2

    source_root = Path(arguments[0])
    if not source_root.is_dir():
        print(f"not a directory: {source_root}", file=sys.stderr)
        return 2

    print("\n".join(python_cache_directories(source_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
