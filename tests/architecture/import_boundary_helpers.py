"""Helpers for architecture import-boundary tests."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "git_stage_batch"


def module_name_for_path(path: Path) -> str:
    relative_path = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(("git_stage_batch", *relative_path.parts))


def resolve_import_from_module(
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


def import_from_nodes(path: Path) -> list[tuple[str | None, ast.ImportFrom]]:
    current_module = module_name_for_path(path)
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            nodes.append((
                resolve_import_from_module(
                    current_module=current_module,
                    level=node.level,
                    module=node.module,
                ),
                node,
            ))
    return nodes


def package_path_for_module(module: str) -> Path | None:
    if not module.startswith("git_stage_batch."):
        return None

    package_path = SRC_ROOT.joinpath(*module.split(".")[1:])
    if not (package_path / "__init__.py").exists():
        return None

    return package_path


def external_package_child_module_import_violations(
    disallowed_children: dict[str, set[str]],
) -> list[str]:
    violations = []

    for path in SRC_ROOT.rglob("*.py"):
        for imported_module, node in import_from_nodes(path):
            if imported_module not in disallowed_children:
                continue

            package_path = package_path_for_module(imported_module)
            if package_path is not None and package_path in path.parents:
                continue

            imported_names = {alias.name for alias in node.names}
            disallowed_names = (
                imported_names & disallowed_children[imported_module]
            )
            if disallowed_names:
                relative_path = path.relative_to(REPO_ROOT)
                names = ", ".join(sorted(disallowed_names))
                violations.append(f"{relative_path}:{node.lineno} imports {names}")

    return violations
