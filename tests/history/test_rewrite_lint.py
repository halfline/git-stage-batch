"""Command coverage for offline aggregate rewrite-plan linting."""

from __future__ import annotations

import json

import pytest

from git_stage_batch.commands.rewrite_lint import command_rewrite_lint
from git_stage_batch.exceptions import CommandError
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document


def _write_plan(path, record: object) -> None:
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def test_rewrite_lint_prints_valid_advisory_porcelain(
    linear_history_repo,
    capsys,
):
    path = linear_history_repo.root / "plan.json"
    record = history_plan_document_record(
        acquire_history_plan_document(linear_history_repo.base)
    )
    _write_plan(path, record)

    command_rewrite_lint(str(path), porcelain=True)

    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "rewrite-lint"
    assert report["status"] == "valid"
    assert report["valid"] is True
    assert report["authoritative"] is False
    assert report["diagnostics"] == []
    assert report["summary"]["skipped_checks"] == []


def test_rewrite_lint_prints_all_findings_before_failing(
    linear_history_repo,
    capsys,
):
    path = linear_history_repo.root / "plan.json"
    record = history_plan_document_record(
        acquire_history_plan_document(linear_history_repo.base)
    )
    record["plan"]["outputs"][0]["message"] = "Forged keep\n"
    record["plan"]["outputs"][1]["source_unit_ids"] = []
    _write_plan(path, record)

    with pytest.raises(CommandError) as raised:
        command_rewrite_lint(str(path), porcelain=True)

    assert raised.value.exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "invalid-plan"
    assert report["summary"]["diagnostic_count"] == 2
    assert [item["code"] for item in report["diagnostics"]] == [
        "operation-message-shape",
        "operation-unit-shape",
    ]
    assert report["diagnostics"][0]["operation"] == "KEEP"
    assert report["diagnostics"][0]["materialization"] == "EXACT"


def test_rewrite_lint_porcelain_reports_invalid_documents(tmp_path, capsys):
    path = tmp_path / "broken-plan.json"
    path.write_text("{not json\n", encoding="utf-8")

    with pytest.raises(CommandError) as raised:
        command_rewrite_lint(str(path), porcelain=True)

    assert raised.value.message == ""
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "invalid-document"
    assert report["diagnostics"][0]["code"] == "document-invalid"
    assert report["summary"]["skipped_checks"] == ["all"]
