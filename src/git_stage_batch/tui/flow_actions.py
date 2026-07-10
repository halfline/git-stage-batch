"""Flow selection actions for interactive mode."""

from __future__ import annotations

from .flow import FlowLocation, FlowState, LocationRole
from .flow_menu import handle_from_menu, handle_to_menu


def handle_flow_action(action: str, flow_state: FlowState) -> bool:
    """Apply source or target flow actions, returning whether one matched."""
    if action.startswith("<"):
        if len(action) > 1:
            batch_name = action[1:]
            flow_state.source = FlowLocation.for_batch(batch_name)
            if flow_state.target.role is LocationRole.BATCH:
                flow_state.target = FlowLocation.STAGING_AREA
        else:
            handle_from_menu(flow_state)
        return True

    if action.startswith(">"):
        if len(action) > 1:
            batch_name = action[1:]
            flow_state.target = FlowLocation.for_batch(batch_name)
            if flow_state.source.role is LocationRole.BATCH:
                flow_state.source = FlowLocation.WORKING_TREE
        else:
            handle_to_menu(flow_state)
        return True

    return False
