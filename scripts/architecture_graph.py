#!/usr/bin/env python3
"""Print the observed internal package import graph."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "git_stage_batch"


def module_name(path: Path) -> str:
    return ".".join(("git_stage_batch", *path.relative_to(SOURCE).with_suffix("").parts))


def resolve_relative(source: str, level: int, target: str | None) -> str | None:
    if level == 0:
        return target
    package = source.split(".")[:-1]
    if level - 1 > len(package):
        return None
    base = package[: len(package) - (level - 1)]
    return ".".join((*base, *(target.split(".") if target else ())))


def main() -> None:
    edges = set()
    for path in SOURCE.rglob("*.py"):
        source = module_name(path)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = resolve_relative(source, node.level, node.module)
            if target and target.startswith("git_stage_batch"):
                edges.add((source, target))
    for source, target in sorted(edges):
        print(f"{source} -> {target}")


if __name__ == "__main__":
    main()
