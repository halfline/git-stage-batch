"""Group verification resources close on every replacement planning exit."""

import pytest

from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.merge import replacement_unit_planning
from git_stage_batch.batch.merge.baseline_edit_plan import BaselineEditPlan
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit


@pytest.mark.parametrize("raise_during_selection", [False, True])
def test_group_context_closes_on_refusal_and_exception(
    monkeypatch, raise_during_selection
):
    closes = []

    class Context:
        def close(self):
            closes.append(True)

    monkeypatch.setattr(
        replacement_unit_planning,
        "_trusted_partial_replacement_context",
        lambda *args: Context(),
    )
    monkeypatch.setattr(
        replacement_unit_planning,
        "_plan_complete_unrealized_origin_group",
        lambda *args, **kwargs: False,
    )
    if raise_during_selection:

        def interrupted(*args):
            raise RuntimeError("interrupted selection")

        monkeypatch.setattr(
            replacement_unit_planning, "_collect_replacement_source_ranges", interrupted
        )

    with MatcherWorkspace() as workspace:
        plan = BaselineEditPlan(workspace, edit_capacity=2, source_range_capacity=2)
        bounds = workspace.record_vector(2, "QQQQ", length=2)
        ranges = workspace.record_vector(2, "QQ")
        mapped = workspace.record_vector(2, "Q")

        def run():
            return replacement_unit_planning.plan_replacement_unit_edits(
                workspace,
                plan,
                [],
                [],
                [ReplacementUnit(["1"], [0]), ReplacementUnit(["2"], [1])],
                [AbsenceClaim(None, [b"old\n"]), AbsenceClaim(None, [b"other\n"])],
                bounds,
                ranges,
                mapped,
                None,
                max_resolution_choices=10,
                source_to_working_mapping=None,
                spool_dir=None,
            )

        if raise_during_selection:
            with pytest.raises(RuntimeError, match="interrupted selection"):
                run()
        else:
            assert not run()
        assert closes == [True]
