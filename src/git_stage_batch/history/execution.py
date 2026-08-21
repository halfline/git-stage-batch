"""Recoverable object-based execution of validated rewrite plans."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import replace

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from ..utils.git_object_io import (
    GitObjectQuarantine,
    temporary_git_object_environment,
)
from ..utils.git_object_promotion import (
    adopt_git_object_promotion_lease,
    promote_git_object_closure,
    release_git_object_promotion_lease,
)
from ..utils.git_refs import update_git_refs
from ..utils.scratch import default_scratch_parent
from .commit_writer import create_history_commit, require_history_commit_matches
from .json_files import history_json_sha256
from .models import (
    CURRENT_HISTORY_STATE_SCHEMA_VERSION,
    HistoryNextAction,
    HistoryOperationState,
    HistoryPhase,
    HistoryPlanDocument,
    HistoryVerification,
)
from .plan_files import (
    read_and_validate_frozen_history_plan_semantics,
    read_and_validate_history_plan,
    read_and_validate_history_plan_semantics,
    require_frozen_history_plan_workspace,
)
from .records import history_plan_document_record
from .replay import (
    HistoryReplayResult,
    materialize_history_output_trees,
    validate_history_plan_materialization,
)
from .resolution_workspace import materialize_completed_history_resolution
from .safety import collect_history_safety_facts
from .state import (
    activate_prepared_history_operation,
    deactivate_history_operation,
    history_operation_plan_path,
    history_operation_resolutions_path,
    history_output_ref,
    history_recovery_ref,
    inspect_history_operation,
    load_active_history_operation,
    load_history_operation_for_status,
    prepare_history_operation,
    publish_prepared_history_operation,
    update_history_operation,
    write_history_verification_record,
)


def normalize_allowed_remote_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    """Validate explicit publication exceptions as full remote-tracking refs."""
    normalized = tuple(sorted(set(refs)))
    for refname in normalized:
        result = run_git_command(
            ["check-ref-format", refname],
            check=False,
            capture_stdout=False,
            requires_index_lock=False,
        )
        if result.returncode != 0 or not refname.startswith("refs/remotes/"):
            raise CommandError(
                _("Invalid allowed remote-tracking ref: {refname}").format(
                    refname=refname
                )
            )
    return normalized


def _symbolic_head() -> str | None:
    result = run_git_command(
        ["symbolic-ref", "--quiet", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _resolved_commit(revision: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _resolved_object(revision: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", revision],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _ref_is_symbolic(refname: str) -> bool:
    result = run_git_command(
        ["symbolic-ref", "--quiet", refname],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode in {0, 1}:
        return result.returncode == 0
    raise CommandError(
        _("Could not inspect history ref {refname}.").format(refname=refname)
    )


def _operation_document(state: HistoryOperationState) -> HistoryPlanDocument:
    document, plan_sha256 = read_and_validate_frozen_history_plan_semantics(
        str(history_operation_plan_path(state.operation_id)),
        base_commit=state.base_commit,
        tip_commit=state.original_tip,
        branch_ref=state.branch_ref,
        allowed_remote_refs=state.allowed_remote_refs,
    )
    if plan_sha256 != state.plan_sha256:
        raise CommandError(
            _("The persisted rewrite plan no longer matches its checkpoint.")
        )
    has_resolved_outputs = any(
        output.materialization == "RESOLVED" for output in document.plan.outputs
    )
    has_resolution_provenance = state.resolution_raw_plan_sha256 is not None
    if has_resolved_outputs != has_resolution_provenance:
        raise CommandError(
            _("Rewrite resolution provenance does not match the persisted plan.")
        )
    if not has_resolved_outputs:
        validate_history_plan_materialization(document)
    return document


def _operation_replay(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
    *,
    quarantine_environment: dict[str, str],
    quarantine: GitObjectQuarantine,
) -> HistoryReplayResult:
    raw_plan_sha256 = state.resolution_raw_plan_sha256
    if raw_plan_sha256 is None:
        return materialize_history_output_trees(
            document,
            env=quarantine_environment,
        )
    authenticated = materialize_completed_history_resolution(
        document,
        raw_plan_sha256,
        str(history_operation_resolutions_path(state.operation_id)),
        quarantine=quarantine,
    )
    if authenticated.complete_sha256 != state.resolution_complete_sha256:
        raise CommandError(
            _("The operation-owned rewrite resolution workspace changed.")
        )
    return authenticated.replay


def _expected_output_objects(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
    *,
    quarantine: GitObjectQuarantine,
    write: bool,
    disable_lazy_fetch: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    with quarantine.pinned_environment() as environment:
        if disable_lazy_fetch:
            environment["GIT_NO_LAZY_FETCH"] = "1"
        replay = _operation_replay(
            state,
            document,
            quarantine_environment=environment,
            quarantine=quarantine,
        )
        sources = {commit.commit_id: commit for commit in document.snapshot.commits}
        parent = document.snapshot.base_commit
        commits: list[str] = []
        for output, tree in zip(
            document.plan.outputs,
            replay.output_trees,
            strict=True,
        ):
            target = sources[output.source_commits[0]]
            commit = create_history_commit(
                tree=tree,
                parent=parent,
                output=output,
                target=target,
                write=write,
                env=environment,
            )
            commits.append(commit)
            parent = commit
        return tuple(commits), replay.output_trees


def _require_resume_ready(state: HistoryOperationState) -> None:
    inspection = inspect_history_operation(state)
    if inspection.resume_ready:
        return
    raise CommandError(
        _("Rewrite operation {operation_id} cannot continue: {blockers}.").format(
            operation_id=state.operation_id,
            blockers=", ".join(inspection.blockers),
        )
    )


def _initialize_build(state: HistoryOperationState) -> HistoryOperationState:
    building = replace(
        state,
        phase=HistoryPhase.BUILDING,
        next_action=HistoryNextAction.BUILD_OUTPUT,
        diagnostic=None,
    )
    update_history_operation(building)
    return building


def _publish_pending_output(
    state: HistoryOperationState,
    *,
    commit: str,
    tree: str,
) -> HistoryOperationState:
    completed_tip = state.output_commits[-1] if state.output_commits else None
    if state.pending_output_commit is None:
        state = replace(
            state,
            pending_output_commit=commit,
            pending_output_tree=tree,
            diagnostic=None,
        )
        update_history_operation(state)
    elif state.pending_output_commit != commit or state.pending_output_tree != tree:
        raise CommandError(
            _("The pending rewrite output does not match deterministic replay.")
        )

    live_output = _resolved_object(state.output_ref)
    if live_output == completed_tip:
        try:
            update_git_refs(
                updates=((state.output_ref, commit),),
                expected_old_values={state.output_ref: completed_tip},
                durable=True,
                no_deref=True,
            )
        except subprocess.CalledProcessError as error:
            raise CommandError(
                _("The rewrite output ref changed during commit publication.")
            ) from error
    elif live_output != commit:
        raise CommandError(
            _("The rewrite output ref no longer names an owned output commit.")
        )

    completed = replace(
        state,
        output_commits=(*state.output_commits, commit),
        completed_output_count=state.completed_output_count + 1,
        pending_output_commit=None,
        pending_output_tree=None,
        diagnostic=None,
    )
    update_history_operation(completed)
    return completed


def _build_outputs(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
) -> HistoryOperationState:
    with temporary_git_object_environment(disable_replace_objects=True) as quarantine:
        expected_commits, expected_trees = _expected_output_objects(
            state,
            document,
            quarantine=quarantine,
            write=True,
        )
        if state.output_commits != expected_commits[: state.completed_output_count]:
            raise CommandError(
                _("Completed rewrite outputs do not match deterministic replay.")
            )
        expected_objects = {
            **dict.fromkeys(expected_commits, "commit"),
            **dict.fromkeys(expected_trees, "tree"),
        }
        lease = promote_git_object_closure(
            quarantine,
            lease_id=f"rewrite-{state.operation_id}",
            include=(expected_commits[-1],),
            exclude=(state.base_commit,),
            expected_objects=expected_objects,
        )
        while state.completed_output_count < state.planned_output_count:
            index = state.completed_output_count
            state = _publish_pending_output(
                state,
                commit=expected_commits[index],
                tree=expected_trees[index],
            )
        release_git_object_promotion_lease(quarantine, lease)

    verifying = replace(
        state,
        phase=HistoryPhase.VERIFYING,
        next_action=HistoryNextAction.VERIFY_SERIES,
        diagnostic=None,
    )
    update_history_operation(verifying)
    return verifying


def _verification_record(
    verification: HistoryVerification,
    *,
    plan_sha256: str,
    state: HistoryOperationState | None = None,
    document: HistoryPlanDocument | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "operation": "rewrite-verification",
        "operation_id": verification.operation_id,
        "plan_sha256": plan_sha256,
        "original_tip": verification.original_tip,
        "output_tip": verification.output_tip,
        "final_tree": verification.final_tree,
        "output_commits": list(verification.output_commits),
        "removed_signatures": [
            {
                "source_commit": source_commit,
                "header": signature.header,
                "sha256": signature.sha256,
            }
            for source_commit, signature in verification.removed_signatures
        ],
        "signatures_preserved": False,
    }
    if state is None or state.schema_version == 2:
        return record
    if document is None:
        raise TypeError("schema-version-3 verification requires its plan document")
    raw_plan_sha256 = state.resolution_raw_plan_sha256
    record["schema_version"] = 2
    record["resolution"] = (
        None
        if raw_plan_sha256 is None
        else {
            "raw_plan_sha256": raw_plan_sha256,
            "complete_sha256": state.resolution_complete_sha256,
            "resolved_outputs": sum(
                output.materialization == "RESOLVED" for output in document.plan.outputs
            ),
        }
    )
    return record


def _invalid_persistent_output_closure() -> CommandError:
    return CommandError(
        _("The persistent rewrite output object closure is incomplete or invalid.")
    )


class _PersistentObjectBatchReader:
    """Consume Git's batch-object protocol with bounded payload buffering."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._pending = bytearray()

    def read_line(self) -> bytes:
        """Read one bounded protocol header without its newline."""
        while True:
            line_end = self._pending.find(b"\n")
            if line_end >= 0:
                line = bytes(self._pending[:line_end])
                del self._pending[: line_end + 1]
                return line
            if len(self._pending) > 256:
                raise ValueError("oversized Git batch-object header")
            self._extend()

    def discard_exactly(self, size: int) -> None:
        """Discard exactly ``size`` payload bytes in bounded chunks."""
        remaining = size
        while remaining:
            if not self._pending:
                self._extend()
            chunk_size = min(remaining, len(self._pending))
            del self._pending[:chunk_size]
            remaining -= chunk_size

    def require_delimiter(self) -> None:
        """Require the newline terminating one batch payload."""
        if not self._pending:
            self._extend()
        if self._pending[0] != ord("\n"):
            raise ValueError("invalid Git batch-object delimiter")
        del self._pending[0]

    def finish(self) -> None:
        """Require the batch protocol to end without trailing output."""
        if self._pending:
            raise ValueError("trailing Git batch-object output")
        for chunk in self._chunks:
            if chunk:
                raise ValueError("trailing Git batch-object output")

    def _extend(self) -> None:
        try:
            self._pending.extend(next(self._chunks))
        except StopIteration as error:
            raise ValueError("truncated Git batch-object output") from error


def _require_persistent_output_closure(
    state: HistoryOperationState,
    *,
    output_trees: tuple[str, ...],
    environment: dict[str, str],
) -> None:
    object_id_width = 40 if state.object_format == "sha1" else 64
    requested_count = 0
    reported_count = 0
    scratch_parent = default_scratch_parent()
    with (
        tempfile.TemporaryFile(dir=scratch_parent) as object_ids,
        tempfile.TemporaryFile(dir=scratch_parent) as expected_object_ids,
        tempfile.TemporaryFile(dir=scratch_parent) as expected_object_metadata,
        tempfile.TemporaryFile(dir=scratch_parent) as tree_roots,
    ):
        # Check rebuilt commit bodies explicitly without walking parents across the
        # trusted base boundary. Traverse every output tree without an exclusion so
        # objects shared with the base or an intermediate tree remain required.
        for object_id_text in state.output_commits:
            if len(object_id_text) != object_id_width or any(
                character not in "0123456789abcdef" for character in object_id_text
            ):
                raise _invalid_persistent_output_closure()
            object_id = object_id_text.encode("ascii")
            if object_ids.write(object_id + b"\n") != object_id_width + 1:
                raise _invalid_persistent_output_closure()
            if expected_object_ids.write(object_id + b"\n") != object_id_width + 1:
                raise _invalid_persistent_output_closure()
            requested_count += 1
        for tree_text in output_trees:
            if len(tree_text) != object_id_width or any(
                character not in "0123456789abcdef" for character in tree_text
            ):
                raise _invalid_persistent_output_closure()
            tree = tree_text.encode("ascii")
            if tree_roots.write(tree + b"\n") != object_id_width + 1:
                raise _invalid_persistent_output_closure()
        tree_roots.seek(0)
        try:
            for line in stream_git_command(
                [
                    "rev-list",
                    "--objects",
                    "--no-object-names",
                    "--missing=error",
                    "--stdin",
                ],
                stdin_chunks=tree_roots,
                env=environment,
                requires_index_lock=False,
            ):
                object_id = line.removesuffix(b"\n")
                if len(object_id) != object_id_width or any(
                    byte not in b"0123456789abcdef" for byte in object_id
                ):
                    raise CommandError(
                        _(
                            "Git returned malformed object closure data while "
                            "verifying rewrite output."
                        )
                    )
                if object_ids.write(object_id + b"\n") != object_id_width + 1:
                    raise _invalid_persistent_output_closure()
                if expected_object_ids.write(object_id + b"\n") != object_id_width + 1:
                    raise _invalid_persistent_output_closure()
                requested_count += 1
        except subprocess.CalledProcessError as error:
            raise _invalid_persistent_output_closure() from error
        object_ids.seek(0)
        expected_object_ids.seek(0)
        try:
            for line in stream_git_command(
                [
                    "cat-file",
                    "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                ],
                stdin_chunks=object_ids,
                env=environment,
                requires_index_lock=False,
            ):
                record = line.removesuffix(b"\n")
                fields = record.split(b" ")
                expected_object_id = expected_object_ids.readline().removesuffix(b"\n")
                expected_types = (
                    {b"commit"}
                    if reported_count < len(state.output_commits)
                    else {b"blob", b"tree"}
                )
                if (
                    len(fields) != 3
                    or fields[0] != expected_object_id
                    or fields[1] not in expected_types
                    or len(fields[0]) != object_id_width
                    or any(byte not in b"0123456789abcdef" for byte in fields[0])
                    or not fields[2].isdigit()
                ):
                    raise _invalid_persistent_output_closure()
                if expected_object_metadata.write(record + b"\n") != len(record) + 1:
                    raise _invalid_persistent_output_closure()
                reported_count += 1
        except subprocess.CalledProcessError as error:
            raise _invalid_persistent_output_closure() from error
        object_ids.seek(0)
        expected_object_metadata.seek(0)
        try:
            reader = _PersistentObjectBatchReader(
                stream_git_command_bytes(
                    [
                        "cat-file",
                        "--batch",
                    ],
                    stdin_chunks=object_ids,
                    env=environment,
                    requires_index_lock=False,
                )
            )
            content_count = 0
            while content_count < requested_count:
                record = reader.read_line()
                fields = record.split(b" ")
                expected_metadata = expected_object_metadata.readline().removesuffix(
                    b"\n"
                )
                if (
                    len(fields) != 3
                    or record != expected_metadata
                    or fields[1] not in {b"blob", b"commit", b"tree"}
                    or len(fields[0]) != object_id_width
                    or any(byte not in b"0123456789abcdef" for byte in fields[0])
                    or not fields[2].isdigit()
                ):
                    raise _invalid_persistent_output_closure()
                reader.discard_exactly(int(fields[2]))
                reader.require_delimiter()
                content_count += 1
            reader.finish()
        except (subprocess.CalledProcessError, ValueError) as error:
            raise _invalid_persistent_output_closure() from error
    if reported_count != requested_count or content_count != requested_count:
        raise _invalid_persistent_output_closure()


def _verify_output_objects(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
) -> tuple[HistoryVerification, tuple[str, ...]]:
    with temporary_git_object_environment(
        disable_replace_objects=True
    ) as persistent_view:
        with persistent_view.pinned_environment() as persistent_environment:
            persistent_environment["GIT_NO_LAZY_FETCH"] = "1"
            with temporary_git_object_environment(
                disable_replace_objects=True
            ) as quarantine:
                expected_commits, expected_trees = _expected_output_objects(
                    state,
                    document,
                    quarantine=quarantine,
                    write=False,
                    disable_lazy_fetch=True,
                )
            if state.output_commits != expected_commits:
                raise CommandError(
                    _("Built history commits do not match deterministic replay.")
                )
            _require_persistent_output_closure(
                state,
                output_trees=expected_trees,
                environment=persistent_environment,
            )
            if _resolved_object(state.output_ref) != expected_commits[-1]:
                raise CommandError(
                    _("The rewrite output ref changed before verification.")
                )

            sources = {commit.commit_id: commit for commit in document.snapshot.commits}
            parent = document.snapshot.base_commit
            for commit, tree, output in zip(
                expected_commits,
                expected_trees,
                document.plan.outputs,
                strict=True,
            ):
                target = sources[output.source_commits[0]]
                require_history_commit_matches(
                    commit,
                    tree=tree,
                    parent=parent,
                    output=output,
                    target=target,
                    env=persistent_environment,
                )
                parent = commit

    removed_signatures = tuple(
        (source.commit_id, signature)
        for source in document.snapshot.commits
        for signature in source.signatures
    )
    verification = HistoryVerification(
        operation_id=state.operation_id,
        original_tip=state.original_tip,
        output_tip=expected_commits[-1],
        final_tree=expected_trees[-1],
        output_commits=expected_commits,
        removed_signatures=removed_signatures,
    )
    return verification, expected_trees


def _verify_outputs(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
) -> tuple[HistoryOperationState, HistoryVerification]:
    verification, expected_trees = _verify_output_objects(state, document)
    for commit, tree in zip(
        verification.output_commits,
        expected_trees,
        strict=True,
    ):
        state = replace(
            state,
            last_verified_commit=commit,
            last_verified_tree=tree,
            diagnostic=None,
        )
        update_history_operation(state)

    verification_digest = write_history_verification_record(
        state.operation_id,
        _verification_record(
            verification,
            plan_sha256=state.plan_sha256,
            state=state,
            document=document,
        ),
    )
    ready = replace(
        state,
        phase=HistoryPhase.READY_TO_UPDATE,
        next_action=HistoryNextAction.UPDATE_BRANCH,
        last_verified_commit=verification.output_tip,
        last_verified_tree=verification.final_tree,
        verification_sha256=verification_digest,
        diagnostic=None,
    )
    update_history_operation(ready)
    return ready, verification


def _finalize_branch(state: HistoryOperationState) -> HistoryOperationState:
    if not state.output_commits:
        raise CommandError(_("The rewrite operation has no output commit."))
    # Building and verification can take long enough for an external Git
    # process to change a mutation precondition. Recheck every owned fact at
    # the last possible point before the only user-branch update.
    _require_resume_ready(state)
    output_tip = state.output_commits[-1]
    if _symbolic_head() != state.branch_ref:
        raise CommandError(_("The checked-out branch changed before finalization."))
    live_tip = _resolved_commit(state.branch_ref)
    if live_tip == state.original_tip:
        try:
            update_git_refs(
                updates=((state.branch_ref, output_tip),),
                expected_old_values={state.branch_ref: state.original_tip},
                durable=True,
                no_deref=True,
            )
        except subprocess.CalledProcessError as error:
            raise CommandError(
                _("The branch changed before the history rewrite was finalized.")
            ) from error
    elif live_tip != output_tip:
        raise CommandError(_("The branch no longer names an operation-owned commit."))

    complete = replace(
        state,
        phase=HistoryPhase.COMPLETE,
        next_action=HistoryNextAction.NONE,
        expected_branch_tip=output_tip,
        diagnostic=None,
    )
    update_history_operation(complete)
    deactivate_history_operation(complete)
    return complete


def _pause_after_failure(error: BaseException) -> None:
    try:
        state = load_active_history_operation()
        if state is None or state.phase in {
            HistoryPhase.COMPLETE,
            HistoryPhase.ABORTED,
        }:
            return
        if state.phase is HistoryPhase.PREPARED:
            next_action = HistoryNextAction.BUILD_OUTPUT
        elif state.phase is HistoryPhase.BUILDING:
            next_action = HistoryNextAction.BUILD_OUTPUT
        elif state.phase is HistoryPhase.VERIFYING:
            next_action = HistoryNextAction.VERIFY_SERIES
        elif state.phase is HistoryPhase.READY_TO_UPDATE:
            next_action = HistoryNextAction.UPDATE_BRANCH
        else:
            next_action = state.next_action
        paused = replace(
            state,
            phase=HistoryPhase.PAUSED,
            next_action=next_action,
            diagnostic=str(error),
        )
        update_history_operation(paused)
    except BaseException:
        return


def _require_start_facts_unchanged(
    document: HistoryPlanDocument,
    *,
    allowed_remote_refs: tuple[str, ...],
) -> None:
    snapshot = document.snapshot
    current_branch = _symbolic_head()
    current_tip = _resolved_commit("HEAD")
    safety = collect_history_safety_facts(
        tip=snapshot.tip_commit,
        final_tree=snapshot.final_tree,
        branch_ref=current_branch,
        source_commits=tuple(commit.commit_id for commit in snapshot.commits),
        allowed_remote_refs=allowed_remote_refs,
    )
    blockers = list(safety.blockers)
    if current_branch != snapshot.branch_ref:
        blockers.append("branch-ref-changed")
    if current_tip != snapshot.tip_commit:
        blockers.append("branch-tip-changed")
    if blockers:
        raise CommandError(
            _("Rewrite apply is blocked by: {blockers}.").format(
                blockers=", ".join(dict.fromkeys(blockers))
            )
        )


def start_history_operation(
    plan_path: str,
    *,
    resolutions_path: str | None = None,
    allowed_remote_refs: tuple[str, ...] = (),
) -> HistoryOperationState:
    """Validate, checkpoint, build, verify, and atomically finalize one plan."""
    allowed_refs = normalize_allowed_remote_refs(allowed_remote_refs)
    require_frozen_history_plan_workspace(plan_path, resolutions_path)
    raw_plan_sha256: str | None = None
    resolution_complete_sha256: str | None = None
    if resolutions_path is None:
        document = read_and_validate_history_plan(
            plan_path,
            allowed_remote_refs=allowed_refs,
        )
    else:
        document, raw_plan_sha256 = read_and_validate_history_plan_semantics(
            plan_path,
            allowed_remote_refs=allowed_refs,
        )
    if not document.safety.mutation_ready:
        raise CommandError(
            _("Rewrite apply is blocked by: {blockers}.").format(
                blockers=", ".join(document.safety.blockers)
            )
        )
    branch_ref = document.snapshot.branch_ref
    if branch_ref is None:
        raise CommandError(_("Rewrite apply requires a checked-out local branch."))
    if resolutions_path is not None:
        assert raw_plan_sha256 is not None
        with temporary_git_object_environment(
            disable_replace_objects=True
        ) as quarantine:
            authenticated = materialize_completed_history_resolution(
                document,
                raw_plan_sha256,
                resolutions_path,
                quarantine=quarantine,
            )
        resolution_complete_sha256 = authenticated.complete_sha256

    operation_id = uuid.uuid4().hex
    recovery_ref = history_recovery_ref(operation_id)
    output_ref = history_output_ref(operation_id)
    plan_record = history_plan_document_record(document)
    state = HistoryOperationState(
        schema_version=CURRENT_HISTORY_STATE_SCHEMA_VERSION,
        operation_id=operation_id,
        phase=HistoryPhase.PREPARED,
        next_action=HistoryNextAction.BUILD_OUTPUT,
        plan_sha256=history_json_sha256(plan_record),
        resolution_raw_plan_sha256=raw_plan_sha256,
        resolution_complete_sha256=resolution_complete_sha256,
        object_format=document.snapshot.object_format,
        branch_ref=branch_ref,
        base_commit=document.snapshot.base_commit,
        original_tip=document.snapshot.tip_commit,
        original_final_tree=document.snapshot.final_tree,
        source_commits=tuple(commit.commit_id for commit in document.snapshot.commits),
        allowed_remote_refs=allowed_refs,
        recovery_ref=recovery_ref,
        output_ref=output_ref,
        expected_branch_tip=document.snapshot.tip_commit,
        planned_output_count=len(document.plan.outputs),
        output_commits=(),
        completed_output_count=0,
        pending_output_commit=None,
        pending_output_tree=None,
        last_verified_commit=None,
        last_verified_tree=None,
        verification_sha256=None,
        diagnostic=None,
    )
    preparation = prepare_history_operation(
        state,
        document,
        resolutions_source=resolutions_path,
    )
    _require_start_facts_unchanged(
        document,
        allowed_remote_refs=allowed_refs,
    )
    update_git_refs(
        updates=((recovery_ref, document.snapshot.tip_commit),),
        expected_old_values={recovery_ref: None},
        durable=True,
        no_deref=True,
    )
    publish_prepared_history_operation(state, preparation)
    activate_prepared_history_operation(state)
    return continue_history_operation()


def continue_history_operation() -> HistoryOperationState:
    """Execute the exact next transition for one active operation."""
    try:
        state = load_active_history_operation()
        if state is None:
            raise CommandError(_("No rewrite operation is active."))
        if state.phase in {
            HistoryPhase.COMPLETE,
            HistoryPhase.ABORTED,
        }:
            deactivate_history_operation(state)
            return state
        if (
            state.phase is HistoryPhase.PAUSED
            and state.next_action is HistoryNextAction.RESTORE_ORIGINAL
        ):
            return abort_history_operation()
        _require_resume_ready(state)
        document = _operation_document(state)

        if state.phase is HistoryPhase.PAUSED:
            if state.next_action is HistoryNextAction.BUILD_OUTPUT:
                state = _initialize_build(state)
            elif state.next_action is HistoryNextAction.VERIFY_SERIES:
                state = replace(
                    state,
                    phase=HistoryPhase.VERIFYING,
                    diagnostic=None,
                )
                update_history_operation(state)
            elif state.next_action is HistoryNextAction.UPDATE_BRANCH:
                state = replace(
                    state,
                    phase=HistoryPhase.READY_TO_UPDATE,
                    diagnostic=None,
                )
                update_history_operation(state)
        if state.phase is HistoryPhase.PREPARED:
            state = _initialize_build(state)
        if state.phase is HistoryPhase.BUILDING:
            state = _build_outputs(state, document)
        if state.phase is HistoryPhase.VERIFYING:
            state, _verification = _verify_outputs(state, document)
        if state.phase is HistoryPhase.READY_TO_UPDATE:
            state = _finalize_branch(state)
        return state
    except BaseException as error:
        _pause_after_failure(error)
        raise


def _retract_pending_output(state: HistoryOperationState) -> HistoryOperationState:
    pending = state.pending_output_commit
    if pending is None:
        return state
    completed_tip = state.output_commits[-1] if state.output_commits else None
    live_output = _resolved_object(state.output_ref)
    if live_output == pending:
        try:
            if completed_tip is None:
                update_git_refs(
                    deletes=(state.output_ref,),
                    ignore_missing_deletes=False,
                    expected_old_values={state.output_ref: pending},
                    durable=True,
                    no_deref=True,
                )
            else:
                update_git_refs(
                    updates=((state.output_ref, completed_tip),),
                    expected_old_values={state.output_ref: pending},
                    durable=True,
                    no_deref=True,
                )
        except subprocess.CalledProcessError as error:
            raise CommandError(
                _("The pending output ref changed before rewrite abort.")
            ) from error
    elif live_output != completed_tip:
        raise CommandError(_("The output ref changed before rewrite abort."))
    retracted = replace(
        state,
        pending_output_commit=None,
        pending_output_tree=None,
    )
    update_history_operation(retracted)
    return retracted


def _release_history_promotion_lease(state: HistoryOperationState) -> None:
    with temporary_git_object_environment(disable_replace_objects=True) as quarantine:
        promotion_lease = adopt_git_object_promotion_lease(
            quarantine,
            lease_id=f"rewrite-{state.operation_id}",
        )
        if promotion_lease is not None:
            release_git_object_promotion_lease(quarantine, promotion_lease)


def abort_history_operation() -> HistoryOperationState:
    """Restore an owned original tip and terminate one active operation."""
    state = load_active_history_operation()
    if state is None:
        raise CommandError(_("No rewrite operation is active."))
    if state.phase in {HistoryPhase.COMPLETE, HistoryPhase.ABORTED}:
        deactivate_history_operation(state)
        return state
    if _ref_is_symbolic(state.recovery_ref) or (
        _resolved_commit(state.recovery_ref) != state.original_tip
    ):
        raise CommandError(
            _("The recovery ref changed; automatic rewrite abort is unsafe.")
        )
    if _symbolic_head() != state.branch_ref:
        raise CommandError(
            _("The checked-out branch changed; automatic rewrite abort is unsafe.")
        )

    state = _retract_pending_output(state)
    intent = replace(
        state,
        phase=HistoryPhase.PAUSED,
        next_action=HistoryNextAction.RESTORE_ORIGINAL,
        diagnostic="abort requested",
    )
    update_history_operation(intent)
    state = intent

    live_tip = _resolved_commit(state.branch_ref)
    output_tip = state.output_commits[-1] if state.output_commits else None
    output_was_finalized = (
        output_tip is not None
        and state.completed_output_count == state.planned_output_count
        and state.last_verified_commit == output_tip
        and state.last_verified_tree == state.original_final_tree
        and state.verification_sha256 is not None
    )
    if live_tip == state.original_tip:
        pass
    elif live_tip == output_tip and output_was_finalized:
        try:
            update_git_refs(
                updates=((state.branch_ref, state.original_tip),),
                expected_old_values={state.branch_ref: output_tip},
                durable=True,
                no_deref=True,
            )
        except subprocess.CalledProcessError as error:
            raise CommandError(
                _("The branch changed while restoring the original history.")
            ) from error
    else:
        raise CommandError(
            _(
                "The branch does not name an operation-owned tip; recover "
                "manually from {recovery_ref}."
            ).format(recovery_ref=state.recovery_ref)
        )

    _release_history_promotion_lease(state)

    aborted = replace(
        state,
        phase=HistoryPhase.ABORTED,
        next_action=HistoryNextAction.NONE,
        expected_branch_tip=state.original_tip,
        pending_output_commit=None,
        pending_output_tree=None,
        diagnostic="aborted by user",
    )
    update_history_operation(aborted)
    deactivate_history_operation(aborted)
    return aborted


def verify_history_operation() -> tuple[HistoryOperationState, HistoryVerification]:
    """Independently verify the active or latest complete replacement series."""
    state, active = load_history_operation_for_status()
    if state is None:
        raise CommandError(_("No rewrite operation is available to verify."))
    if (
        state.completed_output_count != state.planned_output_count
        or state.pending_output_commit is not None
        or state.phase is HistoryPhase.ABORTED
    ):
        raise CommandError(_("The rewrite operation has no complete output to verify."))
    inspection = inspect_history_operation(state, require_active=active)
    proof_blockers = tuple(
        blocker
        for blocker in inspection.blockers
        if blocker
        in {
            "active-rewrite-operation",
            "recovery-ref-changed",
            "plan-changed",
            "resolution-bundle-changed",
            "output-object-missing",
            "output-ref-changed",
            "verification-record-changed",
        }
    )
    if proof_blockers:
        raise CommandError(
            _("Rewrite verification is blocked by: {blockers}.").format(
                blockers=", ".join(proof_blockers)
            )
        )
    document = _operation_document(state)
    verification, _trees = _verify_output_objects(state, document)
    record = _verification_record(
        verification,
        plan_sha256=state.plan_sha256,
        state=state,
        document=document,
    )
    digest = history_json_sha256(record)
    if state.verification_sha256 is not None and digest != state.verification_sha256:
        raise CommandError(_("The regenerated rewrite verification record changed."))
    return state, verification
