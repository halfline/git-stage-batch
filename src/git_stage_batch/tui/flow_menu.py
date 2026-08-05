"""Source and target selection menus for interactive mode."""

from __future__ import annotations

from ..batch.state.query import list_batch_names, read_batch_metadata
from ..commands.new import command_new_batch
from ..i18n import _, bidi_isolate
from ..output.colors import Colors
from .flow import FlowLocation, FlowState, LocationRole
from .prompts import unlocked_input


def _batch_option_text(name: str, note: str, marker: str) -> str:
    """Return one localized batch option without an intermediate note copy."""
    if note:
        return _("batch: {name} - {note}{marker}").format(
            name=name,
            note=note,
            marker=marker,
        )
    return _("batch: {name}{marker}").format(name=name, marker=marker)


def handle_from_menu(flow_state: FlowState) -> None:
    """Handle [<]from action to set source."""
    use_color = Colors.enabled()
    batches = list_batch_names()

    print()
    print(_("Pull changes from:"))
    print()

    options: list[tuple[str, FlowLocation]] = []
    selected_marker = _(" (selected)")

    is_selected = flow_state.source.role is LocationRole.WORKING_TREE
    marker = selected_marker if is_selected else ""
    text = _("Working tree{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  {bidi_isolate('[1]')} {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  {bidi_isolate('[1]')} {text}")
    options.append(("working tree", FlowLocation.WORKING_TREE))

    for idx, name in enumerate(batches, 2):
        metadata = read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = (
            flow_state.source.role is LocationRole.BATCH
            and flow_state.source.batch_name == name
        )
        marker = selected_marker if is_selected else ""
        text = _batch_option_text(name, note, marker)
        if use_color and is_selected:
            print(
                f"  {bidi_isolate(f'[{idx}]')} "
                f"{Colors.BOLD}{text}{Colors.RESET}"
            )
        else:
            print(f"  {bidi_isolate(f'[{idx}]')} {text}")
        options.append((name, FlowLocation.for_batch(name)))

    print()
    try:
        choice = unlocked_input(_("Select: ")).strip()
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

    options: list[tuple[str, FlowLocation | None]] = []
    selected_marker = _(" (selected)")

    is_selected = flow_state.target.role is LocationRole.STAGING_AREA
    marker = selected_marker if is_selected else ""
    text = _("Staging for commit{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  {bidi_isolate('[1]')} {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  {bidi_isolate('[1]')} {text}")
    options.append(("staging", FlowLocation.STAGING_AREA))

    for idx, name in enumerate(batches, 2):
        metadata = read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = (
            flow_state.target.role is LocationRole.BATCH
            and flow_state.target.batch_name == name
        )
        marker = selected_marker if is_selected else ""
        text = _batch_option_text(name, note, marker)
        if use_color and is_selected:
            print(
                f"  {bidi_isolate(f'[{idx}]')} "
                f"{Colors.BOLD}{text}{Colors.RESET}"
            )
        else:
            print(f"  {bidi_isolate(f'[{idx}]')} {text}")
        options.append((name, FlowLocation.for_batch(name)))

    new_batch_idx = len(batches) + 2
    print(
        "  {} {}".format(
            bidi_isolate(f"[{new_batch_idx}]"),
            _("New Batch..."),
        )
    )
    options.append(("new", None))

    print()
    try:
        choice = unlocked_input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if idx == len(options) - 1:
            try:
                batch_id = unlocked_input(_("Batch ID: ")).strip()
                if not batch_id:
                    return
                note = unlocked_input(_("Note (optional): ")).strip()
            except (KeyboardInterrupt, EOFError):
                return

            command_new_batch(batch_name=batch_id, note=note if note else None)
            flow_state.target = FlowLocation.for_batch(batch_id)
        elif 0 <= idx < len(options) - 1:
            selected_location = options[idx][1]
            assert selected_location is not None
            flow_state.target = selected_location

        if (
            flow_state.target.role is LocationRole.BATCH
            and flow_state.source.role is LocationRole.BATCH
        ):
            flow_state.source = FlowLocation.WORKING_TREE
