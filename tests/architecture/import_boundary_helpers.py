"""Helpers for architecture import-boundary tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "git_stage_batch"


@dataclass(frozen=True)
class ImportEdge:
    """One internal module import with its source location."""

    source: str
    target: str
    line: int
    names: frozenset[str]


@dataclass(frozen=True)
class ForbiddenImportRule:
    """A declarative prohibition on one architectural dependency edge."""

    source_prefix: str
    target_prefix: str
    reason: str
    allowed_sources: frozenset[str] = frozenset()


def internal_import_edges() -> tuple[ImportEdge, ...]:
    """Return the observed internal import graph with actionable locations."""
    edges = []
    for path in SRC_ROOT.rglob("*.py"):
        source = module_name_for_path(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = resolve_import_from_module(
                    current_module=source,
                    level=node.level,
                    module=node.module,
                )
                if target is None or not target.startswith("git_stage_batch"):
                    continue
                edges.append(
                    ImportEdge(
                        source,
                        target,
                        node.lineno,
                        frozenset(alias.name for alias in node.names),
                    )
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("git_stage_batch"):
                        edges.append(
                            ImportEdge(
                                source,
                                alias.name,
                                node.lineno,
                                frozenset(),
                            )
                        )
    return tuple(edges)


def forbidden_import_violations(
    rules: tuple[ForbiddenImportRule, ...],
) -> list[str]:
    """Describe every observed edge that violates a declarative rule."""
    violations = []
    for edge in internal_import_edges():
        for rule in rules:
            if (
                edge.source.startswith(rule.source_prefix)
                and edge.target.startswith(rule.target_prefix)
                and edge.source not in rule.allowed_sources
            ):
                violations.append(
                    f"{edge.source}:{edge.line} -> {edge.target}: {rule.reason}"
                )
    return sorted(violations)


def modules_defining(names: set[str]) -> dict[str, set[str]]:
    """Return internal modules that define any named top-level symbol."""
    definitions: dict[str, set[str]] = {}
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        found = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        }
        if found:
            definitions[module_name_for_path(path)] = found
    return definitions


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
