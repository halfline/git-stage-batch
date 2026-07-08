"""Source and target selection menus for interactive mode."""

from __future__ import annotations

from ..batch.query import list_batch_names, read_batch_metadata
from ..commands.new import command_new_batch
from ..i18n import _
from ..output.colors import Colors
from .flow import FlowLocation, FlowState, LocationRole


def handle_from_menu(flow_state: FlowState) -> None:
    """Handle [<]from action to set source."""
    use_color = Colors.enabled()
    batches = list_batch_names()

    print()
    print(_("Pull changes from:"))
    print()

    options = []
    selected_marker = _(" (selected)")

    is_selected = flow_state.source.role is LocationRole.WORKING_TREE
    marker = selected_marker if is_selected else ""
    text = _("Working tree{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  [1] {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  [1] {text}")
    options.append(("working tree", FlowLocation.WORKING_TREE))

    for idx, name in enumerate(batches, 2):
        metadata = read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = (
            flow_state.source.role is LocationRole.BATCH
            and flow_state.source.batch_name == name
        )
        marker = selected_marker if is_selected else ""
        note_display = f" - {note}" if note else ""
        text = _("batch: {name}{note}{marker}").format(
            name=name,
            note=note_display,
            marker=marker,
        )
        if use_color and is_selected:
            print(f"  [{idx}] {Colors.BOLD}{text}{Colors.RESET}")
        else:
            print(f"  [{idx}] {text}")
        options.append((name, FlowLocation.for_batch(name)))

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            flow_state.source = options[idx][1]

            if (
                flow_state.source.role is LocationRole.BATCH
                and flow_state.target.role is LocationRole.BATCH
            ):
                flow_state.target = FlowLocation.STAGING_AREA


def handle_to_menu(flow_state: FlowState) -> None:
    """Handle [>]to action to set target."""
    use_color = Colors.enabled()
    batches = list_batch_names()

    print()
    print(_("Push changes to:"))
    print()

    options = []
    selected_marker = _(" (selected)")

    is_selected = flow_state.target.role is LocationRole.STAGING_AREA
    marker = selected_marker if is_selected else ""
    text = _("Staging for commit{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  [1] {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  [1] {text}")
    options.append(("staging", FlowLocation.STAGING_AREA))

    for idx, name in enumerate(batches, 2):
        metadata = read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = (
            flow_state.target.role is LocationRole.BATCH
            and flow_state.target.batch_name == name
        )
        marker = selected_marker if is_selected else ""
        note_display = f" - {note}" if note else ""
        text = _("batch: {name}{note}{marker}").format(
            name=name,
            note=note_display,
            marker=marker,
        )
        if use_color and is_selected:
            print(f"  [{idx}] {Colors.BOLD}{text}{Colors.RESET}")
        else:
            print(f"  [{idx}] {text}")
        options.append((name, FlowLocation.for_batch(name)))

    new_batch_idx = len(batches) + 2
    print(f"  [{new_batch_idx}] {_('New Batch...')}")
    options.append(("new", None))

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if idx == len(options) - 1:
            try:
                batch_id = input(_("Batch ID: ")).strip()
                if not batch_id:
                    return
                note = input(_("Note (optional): ")).strip()
            except (KeyboardInterrupt, EOFError):
                return

            command_new_batch(batch_name=batch_id, note=note if note else None)
            flow_state.target = FlowLocation.for_batch(batch_id)
        elif 0 <= idx < len(options) - 1:
            flow_state.target = options[idx][1]

        if (
            flow_state.target.role is LocationRole.BATCH
            and flow_state.source.role is LocationRole.BATCH
        ):
            flow_state.source = FlowLocation.WORKING_TREE
