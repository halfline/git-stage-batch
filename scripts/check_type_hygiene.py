#!/usr/bin/env python3
"""Reject explicit ``Any`` outside reviewed dynamic boundaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "git_stage_batch"

# Each exception identifies one exact annotation slot or reflective cast.
# These boundaries consume untrusted JSON, mirror deliberately permissive
# third-party APIs, or provide generic JSON/pickle transport. Keeping the
# allowlist symbol-specific prevents a whole module from becoming an Any sink.
ALLOWED_EXPLICIT_ANY = frozenset({
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "decode_batch_metadata::param:payload",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_load_json_object::param:payload",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_load_json_object::return",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_migrate_v0_to_v1::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_migrate_v0_to_v1::return",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_decode_v1::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_decode_file_metadata::param:path",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_decode_file_metadata::param:values",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_claims::param:values",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_baseline_reference::param:reference",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_replacement_origin::param:origin",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_reject_unknown_keys::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_line_ranges::param:values",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_freeze_mapping::param:values",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_freeze_json_value::param:value",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_json_value::param:value",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_thaw_mapping::return",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_thaw_json_value::return",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_required_string::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_optional_string::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_required_object_id::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_optional_object_id::param:data",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_object_id::param:value",
    "src/git_stage_batch/batch/state/metadata_schema.py::"
    "_validate_hex_object_id::param:value",
    "src/git_stage_batch/cli/git_help.py::"
    "GitHelpArgumentParser.__init__::param:args",
    "src/git_stage_batch/cli/git_help.py::"
    "GitHelpArgumentParser.__init__::param:kwargs",
    "src/git_stage_batch/cli/git_help.py::"
    "GitHelpArgumentParser.print_help::cast",
    "src/git_stage_batch/cli/subcommand_parser.py::"
    "Subparsers.add_parser::param:kwargs",
    "src/git_stage_batch/cli/subcommand_parser.py::"
    "add_subcommand_parser::param:kwargs",
    "src/git_stage_batch/utils/file_job_transport.py::"
    "_assert_transport_value::cast",
    "src/git_stage_batch/utils/file_job_transport.py::"
    "_assert_transport_dataclass_shape::cast",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.write_json::param:value",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.read_json::return",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.write_jsonl::param:values",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.stream_jsonl::return",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.write_pickle::param:value",
    "src/git_stage_batch/utils/file_job_workspace.py::"
    "FileJobWorkspace.read_pickle::return",
})


@dataclass(frozen=True)
class ExplicitAnyUse:
    """One explicit Any occurrence found in a type-bearing expression."""

    identity: str
    path: Path
    line: int
    annotation: str


def _contains_any(
    node: ast.AST | None,
    *,
    any_names: set[str],
    typing_names: set[str],
) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval")
        except SyntaxError:
            return False
    return any(
        (
            isinstance(item, ast.Name)
            and item.id in any_names
        )
        or (
            isinstance(item, ast.Attribute)
            and item.attr == "Any"
            and isinstance(item.value, ast.Name)
            and item.value.id in typing_names
        )
        for item in ast.walk(node)
    )


def _is_type_alias_annotation(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TypeAlias"
    ) or (
        isinstance(node, ast.Attribute)
        and node.attr == "TypeAlias"
    )


class _ExplicitAnyVisitor(ast.NodeVisitor):
    """Collect explicit Any uses with stable symbol-level identities."""

    def __init__(self, relative_path: Path, tree: ast.Module) -> None:
        self.relative_path = relative_path
        self.qualifiers: list[str] = []
        self.uses: list[ExplicitAnyUse] = []
        self.any_names = {"Any"}
        self.typing_names = {"typing"}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                for name in node.names:
                    if name.name == "Any":
                        self.any_names.add(name.asname or name.name)
            elif isinstance(node, ast.Import):
                for name in node.names:
                    if name.name == "typing":
                        self.typing_names.add(name.asname or name.name)

    def _qualified_name(self) -> str:
        return ".".join(self.qualifiers) or "<module>"

    def _record(self, node: ast.AST, slot: str) -> None:
        identity = (
            f"{self.relative_path.as_posix()}::"
            f"{self._qualified_name()}::{slot}"
        )
        self.uses.append(
            ExplicitAnyUse(
                identity=identity,
                path=self.relative_path,
                line=getattr(node, "lineno", 1),
                annotation=ast.unparse(node),
            )
        )

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self.qualifiers.append(node.name)
        arguments = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            if _contains_any(
                argument.annotation,
                any_names=self.any_names,
                typing_names=self.typing_names,
            ):
                self._record(
                    argument.annotation or argument,
                    f"param:{argument.arg}",
                )
        if _contains_any(
            node.returns,
            any_names=self.any_names,
            typing_names=self.typing_names,
        ):
            self._record(node.returns or node, "return")
        for statement in node.body:
            self.visit(statement)
        self.qualifiers.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.qualifiers.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.qualifiers.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _contains_any(
            node.annotation,
            any_names=self.any_names,
            typing_names=self.typing_names,
        ):
            self._record(
                node.annotation,
                f"variable:{ast.unparse(node.target)}",
            )
        if (
            node.value is not None
            and _is_type_alias_annotation(node.annotation)
            and _contains_any(
                node.value,
                any_names=self.any_names,
                typing_names=self.typing_names,
            )
        ):
            self._record(
                node.value,
                f"type-alias:{ast.unparse(node.target)}",
            )
        if node.value is not None:
            self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "cast"
            and node.args
            and _contains_any(
                node.args[0],
                any_names=self.any_names,
                typing_names=self.typing_names,
            )
        ):
            self._record(node.args[0], "cast")
        self.generic_visit(node)


def explicit_any_uses() -> list[ExplicitAnyUse]:
    """Return every explicit Any use in package type-bearing expressions."""
    uses: list[ExplicitAnyUse] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(REPOSITORY_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _ExplicitAnyVisitor(relative_path, tree)
        visitor.visit(tree)
        uses.extend(visitor.uses)
    return uses


def main() -> int:
    uses = explicit_any_uses()
    identities = {use.identity for use in uses}
    unexpected = [
        use for use in uses if use.identity not in ALLOWED_EXPLICIT_ANY
    ]
    stale = sorted(ALLOWED_EXPLICIT_ANY - identities)
    if not unexpected and not stale:
        print(
            "Type hygiene passed: explicit Any is confined to "
            f"{len(identities)} reviewed boundary slots."
        )
        return 0
    for use in unexpected:
        print(
            f"{use.path}:{use.line}: explicit Any is not an approved "
            f"dynamic boundary ({use.identity}; {use.annotation})",
            file=sys.stderr,
        )
    for identity in stale:
        print(
            f"stale explicit-Any allowlist entry: {identity}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
