"""Tests for TUI flow shortcut actions."""

from git_stage_batch.tui.flow import FlowLocation, FlowState
from git_stage_batch.tui.flow_actions import handle_flow_action


def test_handle_flow_action_selects_batch_source_and_resets_batch_target():
    """Test source batch shortcuts prevent batch-to-batch flow."""
    flow_state = FlowState(
        source=FlowLocation.WORKING_TREE,
        target=FlowLocation.for_batch("target"),
    )

    handled = handle_flow_action("<source", flow_state)

    assert handled is True
    assert flow_state.source == FlowLocation.for_batch("source")
    assert flow_state.target == FlowLocation.STAGING_AREA


def test_handle_flow_action_selects_batch_target_and_resets_batch_source():
    """Test target batch shortcuts prevent batch-to-batch flow."""
    flow_state = FlowState(
        source=FlowLocation.for_batch("source"),
        target=FlowLocation.STAGING_AREA,
    )

    handled = handle_flow_action(">target", flow_state)

    assert handled is True
    assert flow_state.source == FlowLocation.WORKING_TREE
    assert flow_state.target == FlowLocation.for_batch("target")
