"""Working-tree action execution for file review."""

from __future__ import annotations

from .session import FileReviewSessionState
from ..flow import LocationRole


def apply_live_line_action(
    state: FileReviewSessionState,
    action: str,
    line_ids: str,
) -> None:
    """Apply a line action from a working-tree file review."""
    if action == "i":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ...commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                line_ids=line_ids,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ...commands.include import command_include_line

        command_include_line(line_ids, file=state.file_path, auto_advance=False)
        return

    if action == "s":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ...commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                line_ids=line_ids,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ...commands.skip import command_skip_line

        command_skip_line(line_ids, file=state.file_path, auto_advance=False)
        return

    if state.flow_state.target.role is LocationRole.BATCH:
        from ...commands.discard import command_discard_to_batch

        command_discard_to_batch(
            state.flow_state.target.batch_name,
            line_ids=line_ids,
            file=state.file_path,
            quiet=True,
            auto_advance=False,
        )
        return

    from ...commands.discard import command_discard_line

    command_discard_line(line_ids, file=state.file_path, auto_advance=False)


def apply_live_replacement_action(
    state: FileReviewSessionState,
    line_ids: str,
    replacement_text: str,
) -> None:
    """Apply a replacement action from a working-tree file review."""
    if state.flow_state.target.role is LocationRole.BATCH:
        from ...commands.discard import command_discard_line_as_to_batch

        command_discard_line_as_to_batch(
            state.flow_state.target.batch_name,
            line_ids,
            replacement_text,
            file=state.file_path,
            quiet=True,
            auto_advance=False,
        )
        return

    from ...commands.include import command_include_line_as

    command_include_line_as(
        line_ids,
        replacement_text,
        file=state.file_path,
        auto_advance=False,
    )


def apply_live_file_action(
    state: FileReviewSessionState,
    action: str,
) -> None:
    """Apply a whole-file action from a working-tree file review."""
    if action == "I":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ...commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ...commands.include import command_include_file

        command_include_file(
            state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    if action == "S":
        if state.flow_state.target.role is LocationRole.BATCH:
            from ...commands.include import command_include_to_batch

            command_include_to_batch(
                state.flow_state.target.batch_name,
                file=state.file_path,
                quiet=True,
                auto_advance=False,
            )
            return

        from ...commands.skip import command_skip_file

        command_skip_file(
            state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    if state.flow_state.target.role is LocationRole.BATCH:
        from ...commands.discard import command_discard_to_batch

        command_discard_to_batch(
            state.flow_state.target.batch_name,
            file=state.file_path,
            quiet=True,
            advance=False,
            auto_advance=False,
        )
        return

    from ...commands.discard import command_discard_file

    command_discard_file(state.file_path, auto_advance=False)
