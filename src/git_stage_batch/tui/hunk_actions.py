"""Direct hunk actions for interactive mode."""

from __future__ import annotations

import sys

from ..commands.discard import command_discard, command_discard_to_batch
from ..commands.discard_from import command_discard_from_batch
from ..commands.include import command_include, command_include_to_batch
from ..commands.include_from import command_include_from_batch
from ..commands.skip import command_skip
from ..exceptions import BypassRefresh
from ..i18n import _
from .flow import FlowState, LocationRole
from .prompts import confirm_destructive_operation


def handle_hunk_include(flow_state: FlowState) -> None:
    """Handle include action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            command_include(quiet=True, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            command_include_to_batch(
                flow_state.target.batch_name,
                quiet=True,
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
    elif flow_state.source.role is LocationRole.BATCH:
        if flow_state.target.role is not LocationRole.STAGING_AREA:
            print(
                _("Batch-to-batch transfers not yet supported. Target must be staging."),
                file=sys.stderr,
            )
            raise BypassRefresh()
        command_include_from_batch(flow_state.source.batch_name)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def handle_hunk_skip(flow_state: FlowState) -> None:
    """Handle skip action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role not in (
            LocationRole.STAGING_AREA,
            LocationRole.BATCH,
        ):
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
        command_skip(quiet=True, auto_advance=True)
    elif flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        raise BypassRefresh()
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def handle_hunk_discard(flow_state: FlowState) -> None:
    """Handle discard action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            warning = _("This will remove the hunk from your working tree.")
            if confirm_destructive_operation("discard", warning):
                command_discard(quiet=True, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            command_discard_to_batch(
                flow_state.target.batch_name,
                quiet=True,
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
    elif flow_state.source.role is LocationRole.BATCH:
        if flow_state.target.role is not LocationRole.STAGING_AREA:
            print(
                _("Batch-to-batch transfers not yet supported. Target must be staging."),
                file=sys.stderr,
            )
            raise BypassRefresh()
        command_discard_from_batch(flow_state.source.batch_name)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")
