"""Human and machine-readable frozen rewrite-plan lint reports."""

from __future__ import annotations

import json

from ..history.plan_lint import HistoryPlanDiagnostic, HistoryPlanLint
from ..i18n import _, ngettext


def _diagnostic_record(diagnostic: HistoryPlanDiagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "severity": "error",
        "message": diagnostic.message,
        "location": diagnostic.location,
        "output_index": diagnostic.output_index,
        "output_subject": diagnostic.output_subject,
        "operation": diagnostic.operation,
        "materialization": diagnostic.materialization,
        "source_commits": diagnostic.source_commits,
        "unit_ids": diagnostic.unit_ids,
        "paths": diagnostic.paths,
        "unit_kinds": diagnostic.unit_kinds,
        "dependency": (
            None
            if diagnostic.barrier is None
            else {
                "barrier": diagnostic.barrier,
                "barrier_unit_id": diagnostic.barrier_unit_id,
            }
        ),
        "exact_supported": diagnostic.exact_supported,
        "resolved_supported": diagnostic.resolved_supported,
    }


def rewrite_lint_record(result: HistoryPlanLint) -> dict[str, object]:
    """Return the stable porcelain representation of an advisory lint."""
    return {
        "schema_version": 1,
        "operation": "rewrite-lint",
        "status": "valid" if result.valid else "invalid-plan",
        "valid": result.valid,
        "authoritative": False,
        "range": {
            "base": result.snapshot.base_commit,
            "tip": result.snapshot.tip_commit,
        },
        "diagnostics": tuple(
            _diagnostic_record(diagnostic) for diagnostic in result.diagnostics
        ),
        "summary": {
            "diagnostic_count": len(result.diagnostics),
            "source_commits": len(result.snapshot.commits),
            "output_commits": len(result.plan.outputs),
            "skipped_checks": result.skipped_checks,
        },
    }


def print_rewrite_lint_document_failure(message: str) -> None:
    """Print a stable porcelain failure for an undecodable plan document."""
    print(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "rewrite-lint",
                "status": "invalid-document",
                "valid": False,
                "authoritative": False,
                "range": None,
                "diagnostics": (
                    {
                        "code": "document-invalid",
                        "severity": "error",
                        "message": message,
                        "location": "document",
                        "output_index": None,
                        "output_subject": None,
                        "operation": None,
                        "materialization": None,
                        "source_commits": (),
                        "unit_ids": (),
                        "paths": (),
                        "unit_kinds": (),
                        "dependency": None,
                        "exact_supported": None,
                        "resolved_supported": None,
                    },
                ),
                "summary": {
                    "diagnostic_count": 1,
                    "source_commits": None,
                    "output_commits": None,
                    "skipped_checks": ("all",),
                },
            },
            indent=2,
            ensure_ascii=True,
        )
    )


def print_rewrite_lint(result: HistoryPlanLint, *, porcelain: bool) -> None:
    """Print one complete advisory lint report."""
    if porcelain:
        print(json.dumps(rewrite_lint_record(result), indent=2, ensure_ascii=True))
        return
    if result.valid:
        print(_("Frozen rewrite plan passed advisory lint."))
    else:
        print(
            ngettext(
                "Frozen rewrite plan has {count} error:",
                "Frozen rewrite plan has {count} errors:",
                len(result.diagnostics),
            ).format(count=len(result.diagnostics))
        )
        for diagnostic in result.diagnostics:
            print(f"- [{diagnostic.code}] {diagnostic.location}: {diagnostic.message}")
    print(_("This result is advisory; rewrite validate authenticates live objects."))
