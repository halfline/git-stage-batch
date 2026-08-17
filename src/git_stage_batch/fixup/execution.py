"""Recoverable creation of grouped staged fixup commits."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone

from ..exceptions import CommandError
from ..core.diff_parser import patch_requires_unidiff_zero
from ..i18n import _
from ..utils.git_command import run_git_command, stream_git_command
from ..utils.git_index import git_read_tree, git_write_tree, temp_git_index
from ..utils.git_refs import update_git_refs
from .commutation import (
    apply_patch_to_tree,
    load_tree_diff_as_buffer,
    relocate_patch_to_target,
    tree_for_commit,
)
from .models import (
    CreatedFixup,
    FixupCreatePlan,
    FixupCreateResult,
    FixupTargetGroup,
    FixupUnit,
)


@dataclass(frozen=True, slots=True)
class _PreparedGroup:
    group: FixupTargetGroup
    expected_tree: str


def _current_head() -> str:
    return run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        requires_index_lock=False,
    ).stdout.strip()


def _recovery_ref() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"refs/git-stage-batch/fixup/backups/{timestamp}-{uuid.uuid4().hex}"


def _commit_marker() -> str:
    return f"git-stage-batch-fixup-id: {uuid.uuid4().hex}"


def _commit_contains_marker(commit: str, marker: str) -> bool:
    marker_bytes = marker.encode("ascii")
    found = False
    for line in stream_git_command(
        ["show", "-s", "--format=%B", commit],
        requires_index_lock=False,
    ):
        if line.rstrip(b"\r\n") == marker_bytes:
            found = True
    return found


def _commit_subject(commit: str) -> str:
    return run_git_command(
        ["show", "-s", "--format=%s", commit],
        requires_index_lock=False,
    ).stdout.rstrip("\n")


def _commit_arguments(group: FixupTargetGroup, marker: str) -> list[str]:
    if group.hash_qualified:
        return ["commit", "-m", group.fixup_subject, "-m", marker]
    return ["commit", f"--fixup={group.target}", "-m", marker]


def _patch_chunks_for_units(
    units: tuple[FixupUnit, ...],
) -> Iterator[bytes]:
    for unit in units:
        if unit.patch_buffer is not None:
            yield from unit.patch_buffer.byte_chunks()


def _units_require_unidiff_zero(units: tuple[FixupUnit, ...]) -> bool:
    """Return whether any exact unit patch lacks unchanged context."""
    return any(
        patch_requires_unidiff_zero(unit.patch_buffer)
        for unit in units
        if unit.patch_buffer is not None
    )


def _validated_group_units(
    plan: FixupCreatePlan,
) -> dict[str, tuple[FixupUnit, ...]]:
    analyses_by_id = {
        analysis.unit.unit_id: analysis for analysis in plan.units
    }
    assignments_by_id = {
        assignment.unit_id: assignment for assignment in plan.assignments
    }
    if (
        len(analyses_by_id) != len(plan.units)
        or len(assignments_by_id) != len(plan.assignments)
        or not assignments_by_id.keys() <= analyses_by_id.keys()
    ):
        raise CommandError(
            _("The grouped fixup plan does not conserve the staged changes.")
        )

    grouped_ids: set[str] = set()
    group_targets: set[str] = set()
    units_by_target: dict[str, tuple[FixupUnit, ...]] = {}
    for group in plan.groups:
        if (
            not group.unit_ids
            or group.target in group_targets
            or group.target not in plan.commit_range.commits_newest_first
        ):
            raise CommandError(
                _("The grouped fixup plan does not conserve the staged changes.")
            )
        group_targets.add(group.target)

        group_units: list[FixupUnit] = []
        for unit_id in group.unit_ids:
            assignment = assignments_by_id.get(unit_id)
            if (
                assignment is None
                or assignment.target != group.target
                or unit_id in grouped_ids
            ):
                raise CommandError(
                    _(
                        "The grouped fixup plan does not conserve the staged "
                        "changes."
                    )
                )
            grouped_ids.add(unit_id)
            group_units.append(analyses_by_id[unit_id].unit)
        units_by_target[group.target] = tuple(group_units)

    if grouped_ids != assignments_by_id.keys():
        raise CommandError(
            _("The grouped fixup plan does not conserve the staged changes.")
        )
    return units_by_target


def _prepare_groups(plan: FixupCreatePlan) -> tuple[_PreparedGroup, ...]:
    units_by_target = _validated_group_units(plan)
    current_tree = plan.head_tree
    prepared: list[_PreparedGroup] = []
    with ExitStack() as target_patch_stack:
        target_patches = {}
        for group in plan.groups:
            units = units_by_target[group.target]
            group_tree = apply_patch_to_tree(
                plan.head_tree,
                _patch_chunks_for_units(units),
                three_way=False,
                unidiff_zero=_units_require_unidiff_zero(units),
            )
            if group_tree is None:
                raise CommandError(
                    _(
                        "Could not materialize the staged units assigned to "
                        "{target}."
                    ).format(target=group.target)
                )
            with load_tree_diff_as_buffer(
                plan.head_tree,
                group_tree,
            ) as group_patch:
                expected_tree = apply_patch_to_tree(
                    current_tree,
                    group_patch.byte_chunks(),
                    three_way=True,
                )
                relocated_tree = relocate_patch_to_target(
                    group_patch,
                    plan.commit_range,
                    group.target,
                )
            if expected_tree is None or expected_tree == current_tree:
                raise CommandError(
                    _(
                        "The planned fixup for {target} could not be combined "
                        "with the preceding fixup groups."
                    ).format(target=group.target)
                )
            if relocated_tree is None:
                raise CommandError(
                    _(
                        "The staged units grouped for {target} cannot be "
                        "replayed together at that target."
                    ).format(target=group.target)
                )
            target_patches[group.target] = target_patch_stack.enter_context(
                load_tree_diff_as_buffer(current_tree, expected_tree)
            )
            prepared.append(
                _PreparedGroup(group=group, expected_tree=expected_tree)
            )
            current_tree = expected_tree

        all_units = tuple(analysis.unit for analysis in plan.assigned_units)
        combined_tree = apply_patch_to_tree(
            plan.head_tree,
            _patch_chunks_for_units(all_units),
            three_way=False,
            unidiff_zero=_units_require_unidiff_zero(all_units),
        )
        if combined_tree is None or combined_tree != current_tree:
            raise CommandError(
                _("The grouped fixup plan does not conserve the staged changes.")
            )
        if not plan.remaining_units and combined_tree != plan.index_tree:
            raise CommandError(
                _(
                    "The complete fixup plan does not reproduce the staged "
                    "index tree."
                )
            )

        replayed_tree = tree_for_commit(plan.commit_range.base_commit)
        original_parent_tree = replayed_tree
        for commit in reversed(plan.commit_range.commits_newest_first):
            commit_tree = tree_for_commit(commit)
            if commit_tree != original_parent_tree:
                with load_tree_diff_as_buffer(
                    original_parent_tree,
                    commit_tree,
                ) as commit_patch:
                    next_replayed_tree = apply_patch_to_tree(
                        replayed_tree,
                        commit_patch.byte_chunks(),
                        three_way=True,
                    )
                if next_replayed_tree is None:
                    raise CommandError(
                        _(
                            "The original range cannot be replayed with the "
                            "planned fixup groups."
                        )
                    )
                replayed_tree = next_replayed_tree
            target_patch = target_patches.get(commit)
            if target_patch is not None:
                next_replayed_tree = apply_patch_to_tree(
                    replayed_tree,
                    target_patch.byte_chunks(),
                    three_way=True,
                )
                if next_replayed_tree is None:
                    raise CommandError(
                        _(
                            "The planned fixup for {target} cannot be replayed "
                            "after its target."
                        ).format(target=commit)
                    )
                replayed_tree = next_replayed_tree
            original_parent_tree = commit_tree

        if replayed_tree != combined_tree:
            raise CommandError(
                _(
                    "Replaying the planned fixups at their targets does not "
                    "reproduce the assigned staged changes."
                )
            )
    return tuple(prepared)


def _require_frozen_source(plan: FixupCreatePlan, expected_head: str) -> None:
    if _current_head() != expected_head or git_write_tree() != plan.index_tree:
        raise CommandError(
            _(
                "HEAD or the staged index changed after fixup planning. "
                "No further fixup commits were created."
            )
        )


def _restore_original_head(
    plan: FixupCreatePlan,
    expected_current_head: str,
    recovery_ref: str,
) -> None:
    live_head = _current_head()
    if live_head == plan.commit_range.head_commit:
        return
    if live_head != expected_current_head:
        raise CommandError(
            _(
                "Fixup creation stopped after HEAD moved unexpectedly. "
                "The original commit is saved at {recovery_ref}; HEAD was not "
                "overwritten."
            ).format(recovery_ref=recovery_ref)
        )
    try:
        update_git_refs(
            updates=(("HEAD", plan.commit_range.head_commit),),
            expected_old_values={"HEAD": live_head},
        )
    except subprocess.CalledProcessError as error:
        raise CommandError(
            _(
                "Could not restore HEAD after fixup creation failed. "
                "The original commit is saved at {recovery_ref}."
            ).format(recovery_ref=recovery_ref)
        ) from error


def execute_fixup_create_plan(plan: FixupCreatePlan) -> FixupCreateResult:
    """Create one normal `fixup!` commit per eligible target group."""
    prepared_groups = _prepare_groups(plan)
    if not prepared_groups:
        raise CommandError(_("The fixup plan has no eligible staged units."))

    _require_frozen_source(plan, plan.commit_range.head_commit)
    recovery_ref = _recovery_ref()
    update_git_refs(
        updates=((recovery_ref, plan.commit_range.head_commit),),
        expected_old_values={recovery_ref: None},
    )

    current_head = plan.commit_range.head_commit
    created: list[CreatedFixup] = []
    try:
        for prepared in prepared_groups:
            _require_frozen_source(plan, current_head)
            expected_parent = current_head
            marker = _commit_marker()
            with temp_git_index() as env:
                git_read_tree(prepared.expected_tree, env=env)
                run_git_command(
                    _commit_arguments(prepared.group, marker),
                    env=env,
                    requires_index_lock=True,
                )

            new_head = _current_head()
            if not _commit_contains_marker(new_head, marker):
                raise CommandError(
                    _(
                        "HEAD no longer names the fixup commit that was just "
                        "created."
                    )
                )

            # The unique marker identifies this commit as ours, so it is now
            # safe for failure recovery to compare-and-swap it away.
            current_head = new_head
            parent = run_git_command(
                ["rev-parse", "--verify", f"{new_head}^1^{{commit}}"],
                requires_index_lock=False,
            ).stdout.strip()
            actual_tree = tree_for_commit(new_head)
            if parent != expected_parent:
                raise CommandError(
                    _("A created fixup commit has an unexpected parent.")
                )
            if _commit_subject(new_head) != prepared.group.fixup_subject:
                raise CommandError(
                    _(
                        "A commit hook changed the planned fixup subject; "
                        "the operation was rolled back."
                    )
                )
            if actual_tree != prepared.expected_tree:
                raise CommandError(
                    _(
                        "A commit hook changed the planned fixup tree; "
                        "the operation was rolled back."
                    )
                )
            created.append(
                CreatedFixup(
                    target=prepared.group.target,
                    commit=new_head,
                    unit_ids=prepared.group.unit_ids,
                )
            )

        _require_frozen_source(plan, current_head)
    except BaseException:
        _restore_original_head(plan, current_head, recovery_ref)
        raise

    return FixupCreateResult(
        created=tuple(created),
        recovery_ref=recovery_ref,
    )


def validate_fixup_create_plan_materialization(plan: FixupCreatePlan) -> None:
    """Verify assignment grouping and patch conservation without creating refs."""
    _prepare_groups(plan)
    _require_frozen_source(plan, plan.commit_range.head_commit)
