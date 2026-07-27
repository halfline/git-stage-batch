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


def internal_module_import_edges(
    *,
    include_type_checking: bool = False,
) -> tuple[ImportEdge, ...]:
    """Return imports between concrete internal modules.

    ``from package import child`` contributes an edge to ``package.child`` when
    that child is a module. This closes a gap in the broader boundary scanner,
    whose package-level edges are sufficient for policy checks but not cycle
    detection.
    """
    module_paths = {
        _importable_module_name_for_path(path): path
        for path in SRC_ROOT.rglob("*.py")
    }
    known_modules = set(module_paths)
    edges: list[ImportEdge] = []

    for source, path in module_paths.items():
        resolution_source = (
            f"{source}.__init__" if path.name == "__init__.py" else source
        )
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in _import_nodes(
            tree,
            include_type_checking=include_type_checking,
        ):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in known_modules and alias.name != source:
                        edges.append(
                            ImportEdge(
                                source,
                                alias.name,
                                node.lineno,
                                frozenset(),
                            )
                        )
                continue

            target = resolve_import_from_module(
                current_module=resolution_source,
                level=node.level,
                module=node.module,
            )
            if target is None:
                continue

            imported_names = frozenset(alias.name for alias in node.names)
            targets = {target} if target in known_modules else set()
            targets.update(
                candidate
                for name in imported_names
                if (candidate := f"{target}.{name}") in known_modules
            )
            edges.extend(
                ImportEdge(source, imported, node.lineno, imported_names)
                for imported in sorted(targets)
                if imported != source
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


def _importable_module_name_for_path(path: Path) -> str:
    relative_path = path.relative_to(SRC_ROOT).with_suffix("")
    parts = relative_path.parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("git_stage_batch", *parts))


def _is_type_checking_guard(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
        or isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, *, include_type_checking: bool) -> None:
        self.include_type_checking = include_type_checking
        self.nodes: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.nodes.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.nodes.append(node)

    def visit_If(self, node: ast.If) -> None:
        if not self.include_type_checking and _is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)


def _import_nodes(
    tree: ast.AST,
    *,
    include_type_checking: bool,
) -> tuple[ast.Import | ast.ImportFrom, ...]:
    visitor = _ImportVisitor(include_type_checking=include_type_checking)
    visitor.visit(tree)
    return tuple(visitor.nodes)


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
