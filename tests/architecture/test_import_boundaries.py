"""Import-boundary checks for architecture-sensitive package seams."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "git_stage_batch"


def _module_name_for_path(path: Path) -> str:
    relative_path = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(("git_stage_batch", *relative_path.parts))


def _resolve_import_from_module(
    *,
    current_module: str,
    level: int,
    module: str | None,
) -> str | None:
    if level == 0:
        return module

    current_package = current_module.split(".")[:-1]
    if level - 1 > len(current_package):
        return None

    base_package = current_package[: len(current_package) - (level - 1)]
    if module:
        return ".".join((*base_package, *module.split(".")))
    return ".".join(base_package)


def _import_from_nodes(path: Path) -> list[tuple[str | None, ast.ImportFrom]]:
    current_module = _module_name_for_path(path)
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            nodes.append((
                _resolve_import_from_module(
                    current_module=current_module,
                    level=node.level,
                    module=node.module,
                ),
                node,
            ))
    return nodes


def test_replacement_payload_imports_use_core_boundary():
    """Non-batch code should not depend on batch replacement for neutral payloads."""
    neutral_names = {
        "ReplacementPayload",
        "ReplacementText",
        "coerce_replacement_payload",
        "replacement_line_bodies",
        "replacement_line_chunks",
    }
    violations = []

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in _import_from_nodes(path):
            if imported_module != "git_stage_batch.batch.replacement":
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = imported_names & neutral_names
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    assert violations == []
