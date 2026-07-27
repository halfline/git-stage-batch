"""Source scanners for declarative architecture seam tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cache
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "git_stage_batch"


@dataclass(frozen=True)
class ImportEdge:
    """One internal module import with its source location."""

    source: str
    target: str
    line: int
    names: frozenset[str]


@dataclass(frozen=True)
class ForbiddenImportRule:
    """One prohibited dependency direction within a policy seam."""

    sources: str | frozenset[str]
    target_prefix: str
    reason: str
    allowed_sources: frozenset[str] = frozenset()
    forbidden_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SymbolOwnership:
    """Top-level symbols that must have exactly one module owner."""

    module: str
    names: frozenset[str]


@dataclass(frozen=True)
class ImportedSymbolsRule:
    """Names that one module must or must not import from another."""

    source: str
    target: str
    required_names: frozenset[str] = frozenset()
    forbidden_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConsumerRule:
    """Required consumers of concrete modules."""

    targets: frozenset[str]
    required_sources: frozenset[str]


@dataclass(frozen=True)
class PrivateModulesRule:
    """Internal modules that callers outside a subtree must not import."""

    module_prefix: str
    public_modules: frozenset[str]


@dataclass(frozen=True)
class ArchitectureSeam:
    """A policy boundary expressed through ownership and dependency rules."""

    name: str
    ownership: tuple[SymbolOwnership, ...] = ()
    forbidden_imports: tuple[ForbiddenImportRule, ...] = ()
    imported_symbols: tuple[ImportedSymbolsRule, ...] = ()
    consumers: tuple[ConsumerRule, ...] = ()
    private_modules: tuple[PrivateModulesRule, ...] = ()


@cache
def _parsed_modules() -> tuple[tuple[str, Path, ast.Module], ...]:
    modules = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        module = _module_name_for_path(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        modules.append((module, path, tree))
    return tuple(modules)


@cache
def internal_import_edges() -> tuple[ImportEdge, ...]:
    """Return internal imports with actionable source locations."""
    edges = []
    for source, _path, tree in _parsed_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _resolve_import_from_module(
                    current_module=source,
                    level=node.level,
                    module=node.module,
                )
                if target is None or not target.startswith("git_stage_batch"):
                    continue
                edges.append(
                    ImportEdge(
                        source=source,
                        target=target,
                        line=node.lineno,
                        names=frozenset(alias.name for alias in node.names),
                    )
                )
            elif isinstance(node, ast.Import):
                edges.extend(
                    ImportEdge(
                        source=source,
                        target=alias.name,
                        line=node.lineno,
                        names=frozenset(),
                    )
                    for alias in node.names
                    if alias.name.startswith("git_stage_batch")
                )
    return tuple(edges)


@cache
def internal_module_import_edges(
    *,
    include_type_checking: bool = False,
) -> tuple[ImportEdge, ...]:
    """Return imports resolved to concrete internal modules.

    ``from package import child`` contributes an edge to ``package.child`` when
    that child is a module. Type-checking-only edges are omitted by default so
    callers can inspect the runtime dependency graph.
    """
    module_paths = {
        _importable_module_name_for_path(path): (path, tree)
        for _module, path, tree in _parsed_modules()
    }
    known_modules = set(module_paths)
    edges: list[ImportEdge] = []

    for source, (path, tree) in module_paths.items():
        resolution_source = (
            f"{source}.__init__" if path.name == "__init__.py" else source
        )
        for node in _import_nodes(
            tree,
            include_type_checking=include_type_checking,
        ):
            if isinstance(node, ast.Import):
                edges.extend(
                    ImportEdge(
                        source=source,
                        target=alias.name,
                        line=node.lineno,
                        names=frozenset(),
                    )
                    for alias in node.names
                    if alias.name in known_modules and alias.name != source
                )
                continue

            target = _resolve_import_from_module(
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
                ImportEdge(
                    source=source,
                    target=imported,
                    line=node.lineno,
                    names=imported_names,
                )
                for imported in sorted(targets)
                if imported != source
            )

    return tuple(edges)


def find_dependency_cycle(
    edges: tuple[ImportEdge, ...],
) -> tuple[str, ...] | None:
    """Return one deterministic dependency cycle, including its repeated root."""
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set())

    state: dict[str, int] = {}
    stack: list[str] = []
    stack_indexes: dict[str, int] = {}

    def visit(module: str) -> tuple[str, ...] | None:
        state[module] = 1
        stack_indexes[module] = len(stack)
        stack.append(module)

        for dependency in sorted(adjacency[module]):
            dependency_state = state.get(dependency, 0)
            if dependency_state == 0:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
            elif dependency_state == 1:
                return tuple((*stack[stack_indexes[dependency] :], dependency))

        stack.pop()
        stack_indexes.pop(module)
        state[module] = 2
        return None

    for module in sorted(adjacency):
        if state.get(module, 0) == 0:
            cycle = visit(module)
            if cycle is not None:
                return cycle
    return None


def seam_violations(seam: ArchitectureSeam) -> list[str]:
    """Return every ownership or dependency violation for one seam."""
    violations = forbidden_import_violations(seam.forbidden_imports)

    for ownership in seam.ownership:
        observed = modules_defining(ownership.names)
        expected = {ownership.module: set(ownership.names)}
        if observed != expected:
            violations.append(
                f"{sorted(ownership.names)!r} must be defined only by "
                f"{ownership.module}; found {_sorted_mapping(observed)!r}"
            )

    for rule in seam.imported_symbols:
        observed = {
            name
            for edge in internal_import_edges()
            if edge.source == rule.source and edge.target == rule.target
            for name in edge.names
        }
        missing = rule.required_names - observed
        forbidden = rule.forbidden_names & observed
        if missing:
            violations.append(
                f"{rule.source} must import {sorted(missing)!r} from {rule.target}"
            )
        if forbidden:
            violations.append(
                f"{rule.source} must not import {sorted(forbidden)!r} "
                f"from {rule.target}"
            )

    concrete_edges = internal_module_import_edges()
    for rule in seam.consumers:
        observed = {
            edge.source for edge in concrete_edges if edge.target in rule.targets
        }
        missing = rule.required_sources - observed
        if missing:
            violations.append(
                f"{sorted(rule.targets)!r} must be consumed by {sorted(missing)!r}"
            )

    for rule in seam.private_modules:
        violations.extend(
            f"{edge.source}:{edge.line} -> {edge.target}: "
            f"{rule.module_prefix} implementation modules are private"
            for edge in concrete_edges
            if _in_module_tree(edge.target, rule.module_prefix)
            and not _in_module_tree(edge.source, rule.module_prefix)
            and edge.target not in rule.public_modules
        )

    return sorted(violations)


def forbidden_import_violations(
    rules: tuple[ForbiddenImportRule, ...],
) -> list[str]:
    """Describe every observed edge that violates a declarative rule."""
    violations = []
    for edge in internal_import_edges():
        for rule in rules:
            if not _source_matches(edge.source, rule.sources):
                continue
            if not _in_module_tree(edge.target, rule.target_prefix):
                continue
            if edge.source in rule.allowed_sources:
                continue

            forbidden_names = edge.names & rule.forbidden_names
            if rule.forbidden_names and not forbidden_names:
                continue

            names = (
                f" ({', '.join(sorted(forbidden_names))})" if forbidden_names else ""
            )
            violations.append(
                f"{edge.source}:{edge.line} -> {edge.target}{names}: {rule.reason}"
            )
    return sorted(violations)


def modules_defining(names: frozenset[str]) -> dict[str, set[str]]:
    """Return internal modules that define any named top-level symbol."""
    definitions: dict[str, set[str]] = {}
    for module, _path, tree in _parsed_modules():
        found = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        }
        if found:
            definitions[module] = found
    return definitions


def _source_matches(
    module: str,
    sources: str | frozenset[str],
) -> bool:
    if isinstance(sources, str):
        return _in_module_tree(module, sources)
    return module in sources


def _in_module_tree(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _sorted_mapping(mapping: dict[str, set[str]]) -> dict[str, list[str]]:
    return {module: sorted(names) for module, names in sorted(mapping.items())}


def _module_name_for_path(path: Path) -> str:
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
