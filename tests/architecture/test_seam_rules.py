"""Declarative tests for policy-bearing architecture seams."""

from __future__ import annotations

from .import_boundary_helpers import (
    ArchitectureSeam,
    ConsumerRule,
    ForbiddenImportRule,
    ImportedSymbolsRule,
    PrivateModulesRule,
    SymbolOwnership,
    find_dependency_cycle,
    internal_import_edges,
    internal_module_import_edges,
    seam_violations,
)


def _owned(module: str, *names: str) -> SymbolOwnership:
    return SymbolOwnership(module, frozenset(names))


def _forbid(
    sources: str | frozenset[str],
    target: str,
    reason: str,
    *,
    allowed_sources: tuple[str, ...] = (),
    names: tuple[str, ...] = (),
) -> ForbiddenImportRule:
    return ForbiddenImportRule(
        sources=sources,
        target_prefix=target,
        reason=reason,
        allowed_sources=frozenset(allowed_sources),
        forbidden_names=frozenset(names),
    )


def _imports(
    source: str,
    target: str,
    *,
    required: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> ImportedSymbolsRule:
    return ImportedSymbolsRule(
        source=source,
        target=target,
        required_names=frozenset(required),
        forbidden_names=frozenset(forbidden),
    )


def _consumers(
    targets: tuple[str, ...],
    *,
    required: tuple[str, ...],
) -> ConsumerRule:
    return ConsumerRule(
        targets=frozenset(targets),
        required_sources=frozenset(required),
    )


FILE_JOB_MODULES = frozenset(
    {
        "git_stage_batch.utils.file_job_process",
        "git_stage_batch.utils.file_job_transport",
        "git_stage_batch.utils.file_job_workspace",
        "git_stage_batch.utils.file_jobs",
    }
)

LIVE_CHANGE_MODULE = "git_stage_batch.data.live_change_candidates"
SESSION_MARKER_MODULE = "git_stage_batch.data.session_marker"
FILE_REVIEW_STATE_MODULE = "git_stage_batch.data.file_review.state_builder"
BUFFER_IO_MODULE = "git_stage_batch.utils.buffer_io"
ATOMIC_WRITE_MODULE = "git_stage_batch.utils.atomic_write"
BATCH_REFERENCES_MODULE = "git_stage_batch.batch.state.references"
BATCH_METADATA_MODULE = "git_stage_batch.batch.state.metadata_schema"
CANDIDATE_PLANNING_MODULE = "git_stage_batch.commands.batch_source.candidate_planning"
UNDO_PREFIX = "git_stage_batch.data.undo"
UNDO_CHECKPOINTS_MODULE = f"{UNDO_PREFIX}.checkpoints"
UNDO_REFS_MODULE = f"{UNDO_PREFIX}.refs"
UNDO_SNAPSHOTS_MODULE = f"{UNDO_PREFIX}.snapshots"
UNDO_STATE_MODULE = f"{UNDO_PREFIX}.state"
BLOCK_ACTIONS_MODULE = "git_stage_batch.tui.file_review.block_actions"
SNAPSHOT_DOMAIN_MODULES = frozenset(
    {
        "git_stage_batch.batch.file_state",
        "git_stage_batch.batch.line_matching.transforms",
        "git_stage_batch.batch.source.projection",
        "git_stage_batch.batch.transformed_selection",
        "git_stage_batch.core.coordinates",
        "git_stage_batch.core.edit_plan",
    }
)
LINE_ENTRY_COMPATIBILITY_MODULES = frozenset(
    {
        "git_stage_batch.core.selection_geometry",
        "git_stage_batch.batch.ownership.hunk_line_ranges",
        "git_stage_batch.batch.ownership.hunk_replacement_translation",
        "git_stage_batch.batch.ownership.hunk_translation",
        "git_stage_batch.batch.ownership.insertion_references",
        "git_stage_batch.batch.ownership.line_entries",
        "git_stage_batch.batch.ownership.translation",
        "git_stage_batch.batch.source.annotation",
        "git_stage_batch.batch.source.line_coordinates",
        "git_stage_batch.batch.source.refresh",
        "git_stage_batch.batch.source.selected_line_refresh",
        "git_stage_batch.staging.content_buffers",
    }
)


ARCHITECTURE_SEAMS = (
    ArchitectureSeam(
        name="snapshot-bound-domain-values",
        forbidden_imports=(
            _forbid(
                SNAPSHOT_DOMAIN_MODULES,
                "git_stage_batch.core.models",
                "snapshot-bound domain values must not depend on rendered diff rows",
                names=("LineEntry", "LineLevelChange"),
            ),
        ),
        consumers=(
            _consumers(
                ("git_stage_batch.batch.file_state",),
                required=(
                    "git_stage_batch.batch.discard",
                    "git_stage_batch.batch.merge.merge",
                    "git_stage_batch.batch.source.advancement",
                    "git_stage_batch.batch.text_file_storage",
                ),
            ),
            _consumers(
                ("git_stage_batch.batch.transformed_selection",),
                required=(
                    "git_stage_batch.commands.selection.discard_line_replacement",
                ),
            ),
            _consumers(
                ("git_stage_batch.core.edit_plan",),
                required=(
                    "git_stage_batch.batch.transformed_selection",
                    "git_stage_batch.staging.content_buffers",
                ),
            ),
            _consumers(
                ("git_stage_batch.core.selection_geometry",),
                required=(
                    "git_stage_batch.batch.transformed_selection",
                    "git_stage_batch.commands.selection.discard_line_replacement",
                ),
            ),
        ),
    ),
    ArchitectureSeam(
        name="rendered-row-migration-boundary",
        forbidden_imports=(
            _forbid(
                frozenset(
                    {
                        "git_stage_batch.batch.merge",
                        "git_stage_batch.batch.ownership",
                        "git_stage_batch.batch.source",
                        "git_stage_batch.staging",
                    }
                ),
                "git_stage_batch.core.models",
                "rendered diff rows may enter only named compatibility adapters",
                allowed_sources=tuple(sorted(LINE_ENTRY_COMPATIBILITY_MODULES)),
                names=("LineEntry", "LineLevelChange"),
            ),
        ),
    ),
    ArchitectureSeam(
        name="runtime-layer-directions",
        forbidden_imports=(
            _forbid(
                "git_stage_batch.batch",
                "git_stage_batch.data",
                "batch domain code must stay below workflow state",
            ),
            _forbid(
                "git_stage_batch.core",
                "git_stage_batch.editor",
                "core values must not depend on editor mutation machinery",
            ),
            _forbid(
                "git_stage_batch.commands",
                "git_stage_batch.tui",
                "commands must remain reusable outside the interactive UI",
            ),
            *(
                _forbid(
                    package,
                    "git_stage_batch.exceptions",
                    "lower layers raise errors without command-exit policy",
                    names=("exit_with_error",),
                )
                for package in (
                    "git_stage_batch.batch",
                    "git_stage_batch.core",
                    "git_stage_batch.data",
                    "git_stage_batch.utils",
                )
            ),
        ),
    ),
    ArchitectureSeam(
        name="generic-file-job-infrastructure",
        forbidden_imports=tuple(
            _forbid(
                FILE_JOB_MODULES,
                target,
                "generic file jobs must remain independent of product policy",
            )
            for target in (
                "git_stage_batch.batch",
                "git_stage_batch.cli",
                "git_stage_batch.commands",
                "git_stage_batch.data",
                "git_stage_batch.output",
                "git_stage_batch.tui",
                "git_stage_batch.utils.journal",
                "git_stage_batch.utils.session_lock",
            )
        ),
    ),
    ArchitectureSeam(
        name="live-change-policy",
        ownership=(
            _owned(
                LIVE_CHANGE_MODULE,
                "prepare_live_change",
                "stream_eligible_live_changes",
            ),
        ),
        forbidden_imports=(
            _forbid(
                "git_stage_batch.data.hunk_tracking",
                "git_stage_batch.core.hashing",
                "navigation must consume prepared live-change candidates",
            ),
            _forbid(
                "git_stage_batch.data.remaining_hunks",
                "git_stage_batch.core.hashing",
                "status must count canonical file-job results",
            ),
            _forbid(
                "git_stage_batch.data.remaining_hunks",
                LIVE_CHANGE_MODULE,
                "status must not fall back to the lazy candidate stream",
            ),
            _forbid(
                "git_stage_batch.commands.selection.next_change_display",
                "git_stage_batch.core.hashing",
                "display must render prepared live-change candidates",
            ),
            _forbid(
                "git_stage_batch.data.hunk_tracking",
                "git_stage_batch.data.selected_change.hunk_filtering",
                "navigation must not recreate batch-ownership filtering",
            ),
            _forbid(
                "git_stage_batch.data.hunk_tracking",
                "git_stage_batch.output",
                "navigation returns state instead of rendering output",
            ),
            _forbid(
                "git_stage_batch.data.hunk_tracking",
                "git_stage_batch.commands.show",
                "navigation state must not invoke a command entry point",
            ),
        ),
        consumers=(
            _consumers(
                (LIVE_CHANGE_MODULE,),
                required=(
                    "git_stage_batch.commands.selection.next_change_display",
                    "git_stage_batch.data.hunk_tracking",
                    "git_stage_batch.data.live_change_jobs",
                ),
            ),
        ),
    ),
    ArchitectureSeam(
        name="repository-readers",
        forbidden_imports=(
            _forbid(
                "git_stage_batch.utils.repository_buffers",
                "git_stage_batch.commands",
                "repository readers are lower-level infrastructure",
            ),
            _forbid(
                "git_stage_batch.utils.repository_buffers",
                "git_stage_batch.data",
                "repository readers cannot depend on session state",
            ),
        ),
    ),
    ArchitectureSeam(
        name="active-session-marker",
        ownership=(
            _owned(
                SESSION_MARKER_MODULE,
                "active_session_marker_path",
                "session_is_active",
            ),
        ),
        forbidden_imports=(
            _forbid(
                SESSION_MARKER_MODULE,
                "git_stage_batch.data.session",
                "read-only session checks must stay below session mutation",
            ),
        ),
    ),
    ArchitectureSeam(
        name="file-review-state",
        ownership=(
            _owned(
                FILE_REVIEW_STATE_MODULE,
                "make_file_review_state",
                "resolve_default_review_pages",
            ),
        ),
        forbidden_imports=(
            _forbid(
                "git_stage_batch.data.file_review",
                "git_stage_batch.output",
                "review data and policy cannot depend on terminal presentation",
            ),
        ),
    ),
    ArchitectureSeam(
        name="atomic-buffer-publication",
        ownership=(
            _owned(
                BUFFER_IO_MODULE,
                "write_buffer_to_path",
                "write_buffer_to_working_tree_path",
            ),
            _owned(
                ATOMIC_WRITE_MODULE,
                "fsync_directory",
                "replace_symlink_atomically",
                "write_chunks_atomically",
            ),
        ),
    ),
    ArchitectureSeam(
        name="validated-batch-state-publication",
        ownership=(_owned(BATCH_REFERENCES_MODULE, "sync_batch_state_refs"),),
        imported_symbols=(
            _imports(
                BATCH_REFERENCES_MODULE,
                BATCH_METADATA_MODULE,
                required=("BatchMetadata",),
                forbidden=("metadata_from_application_dict",),
            ),
        ),
    ),
    ArchitectureSeam(
        name="batch-source-candidate-planning",
        ownership=(
            _owned(
                CANDIDATE_PLANNING_MODULE,
                "plan_apply_candidate_previews",
                "plan_include_candidate_previews",
            ),
        ),
        forbidden_imports=(
            _forbid(
                "git_stage_batch.commands.batch_source",
                "git_stage_batch.batch.operation_candidates",
                "batch-source callers must use shared candidate planning",
                allowed_sources=(CANDIDATE_PLANNING_MODULE,),
            ),
        ),
        consumers=(
            _consumers(
                (CANDIDATE_PLANNING_MODULE,),
                required=(
                    "git_stage_batch.commands.batch_source.candidate_materialization",
                    "git_stage_batch.commands.batch_source.candidate_preview_builders",
                    "git_stage_batch.commands.batch_source.candidate_preview_counts",
                ),
            ),
        ),
    ),
    ArchitectureSeam(
        name="undo-checkpoint-policy",
        ownership=(
            _owned(
                UNDO_SNAPSHOTS_MODULE,
                "filesystem_directory_state",
                "push_redo_node",
                "snapshot_current_state",
                "write_snapshot_commit",
            ),
            _owned(
                UNDO_STATE_MODULE,
                "detect_redo_conflicts",
                "detect_undo_conflicts",
                "restore_checkpoint_state",
            ),
        ),
        forbidden_imports=(
            _forbid(
                UNDO_SNAPSHOTS_MODULE,
                UNDO_CHECKPOINTS_MODULE,
                "snapshot storage must stay below stack orchestration",
            ),
            _forbid(
                UNDO_STATE_MODULE,
                UNDO_CHECKPOINTS_MODULE,
                "checkpoint state policy must stay below stack orchestration",
            ),
        ),
        consumers=(
            _consumers(
                (UNDO_SNAPSHOTS_MODULE, UNDO_STATE_MODULE),
                required=(UNDO_CHECKPOINTS_MODULE, UNDO_STATE_MODULE),
            ),
        ),
        private_modules=(
            PrivateModulesRule(
                module_prefix=UNDO_PREFIX,
                public_modules=frozenset(
                    {
                        UNDO_CHECKPOINTS_MODULE,
                        UNDO_REFS_MODULE,
                    }
                ),
            ),
        ),
    ),
    ArchitectureSeam(
        name="file-review-ignore-mutations",
        ownership=(
            _owned(
                BLOCK_ACTIONS_MODULE,
                "apply_block_action",
                "block_review_file",
                "prompt_block_local_only",
                "unblock_review_file",
            ),
        ),
        forbidden_imports=(
            _forbid(
                "git_stage_batch.tui.file_review",
                "git_stage_batch.commands.block_file",
                "file-review navigation must delegate block commands",
                allowed_sources=(BLOCK_ACTIONS_MODULE,),
            ),
            _forbid(
                "git_stage_batch.tui.file_review",
                "git_stage_batch.commands.unblock_file",
                "file-review navigation must delegate unblock commands",
                allowed_sources=(BLOCK_ACTIONS_MODULE,),
            ),
            _forbid(
                "git_stage_batch.tui.file_review",
                "git_stage_batch.data.ignore_files",
                "file-review navigation must not edit ignore files directly",
            ),
        ),
    ),
    ArchitectureSeam(
        name="tui-shell-locking",
        forbidden_imports=(
            _forbid(
                "git_stage_batch.tui.shell_command",
                "git_stage_batch.utils.session_lock",
                "shell waits must never hold the repository lock",
            ),
        ),
    ),
)


def test_internal_runtime_module_graph_is_acyclic():
    """Concrete runtime module dependencies must form a directed acyclic graph."""
    cycle = find_dependency_cycle(internal_module_import_edges())
    assert cycle is None, " -> ".join(cycle or ())


def test_subpackages_do_not_define_runtime_facades():
    """Internal callers import concrete modules instead of package facades."""
    violations = [
        f"{edge.source}:{edge.line} -> {edge.target}"
        for edge in internal_import_edges()
        if edge.source.endswith(".__init__")
        and edge.source != "git_stage_batch.__init__"
    ]
    assert violations == []


def test_policy_seams():
    """Every policy seam must retain its owner and dependency direction."""
    violations = [
        f"{seam.name}: {violation}"
        for seam in ARCHITECTURE_SEAMS
        for violation in seam_violations(seam)
    ]
    assert violations == []
