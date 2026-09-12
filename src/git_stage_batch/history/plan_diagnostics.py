"""History-plan diagnostics and ordered advisory result accumulation."""

from __future__ import annotations

from dataclasses import dataclass

from .models import HistoryPatchUnit, HistoryPlan, HistorySnapshot


@dataclass(frozen=True, slots=True)
class HistoryPlanDiagnostic:
    """One stable, machine-readable advisory plan finding."""

    code: str
    message: str
    location: str
    output_index: int | None = None
    output_subject: str | None = None
    operation: str | None = None
    materialization: str | None = None
    source_commits: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    unit_kinds: tuple[str, ...] = ()
    barrier: str | None = None
    barrier_unit_id: str | None = None
    exact_supported: bool | None = None
    resolved_supported: bool | None = None


@dataclass(frozen=True, slots=True)
class HistoryPlanLint:
    """Advisory findings derived only from the persisted document."""

    snapshot: HistorySnapshot
    plan: HistoryPlan
    diagnostics: tuple[HistoryPlanDiagnostic, ...]
    skipped_checks: tuple[str, ...]

    @property
    def valid(self) -> bool:
        """Return whether the advisory pass found no plan errors."""
        return not self.diagnostics


class PlanDiagnosticCollector:
    """Accumulate findings and explicitly skipped phases in encounter order."""

    def __init__(self, snapshot: HistorySnapshot, plan: HistoryPlan) -> None:
        self.snapshot = snapshot
        self.plan = plan
        self.diagnostics: list[HistoryPlanDiagnostic] = []
        self.skipped_checks: list[str] = []

    def finish(self) -> HistoryPlanLint:
        return HistoryPlanLint(
            self.snapshot,
            self.plan,
            tuple(self.diagnostics),
            tuple(self.skipped_checks),
        )

    def add(
        self,
        code: str,
        message: str,
        location: str,
        *,
        output_index: int | None = None,
        source_commits: tuple[str, ...] = (),
        units: tuple[HistoryPatchUnit, ...] = (),
        unit_ids: tuple[str, ...] = (),
        barrier: str | None = None,
        barrier_unit_id: str | None = None,
        exact_supported: bool | None = None,
        resolved_supported: bool | None = None,
    ) -> None:
        output = self.plan.outputs[output_index] if output_index is not None else None
        self.diagnostics.append(
            HistoryPlanDiagnostic(
                code=code,
                message=message,
                location=location,
                output_index=output_index,
                output_subject=(
                    output.message.splitlines()[0] if output and output.message else ""
                )
                if output is not None
                else None,
                operation=output.operation if output is not None else None,
                materialization=(
                    output.materialization if output is not None else None
                ),
                source_commits=source_commits,
                unit_ids=unit_ids or tuple(unit.unit_id for unit in units),
                paths=tuple(dict.fromkeys(unit.path for unit in units)),
                unit_kinds=tuple(dict.fromkeys(unit.kind for unit in units)),
                barrier=barrier,
                barrier_unit_id=barrier_unit_id,
                exact_supported=exact_supported,
                resolved_supported=resolved_supported,
            )
        )
