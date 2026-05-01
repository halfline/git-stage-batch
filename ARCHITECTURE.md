# git-stage-batch Architecture

This document gives a project-level view of how `git-stage-batch` is built.
It is intended for contributors who want to understand where behavior lives,
how data moves through the system, and which modules own which parts of the
workflow.

For the specialized architecture of named batch storage and batch operations,
see [BATCHES.md](BATCHES.md).

## What the Program Is

`git-stage-batch` is a command-line tool for constructing clean Git history
from messy working-tree changes. At its core, it does three things:

- discovers changes in the working tree and index
- presents those changes as navigable hunks or files
- applies user decisions back to Git state and session state

Most commands are not long-running daemons. The program persists enough state
on disk to make a multi-step interactive workflow feel continuous across
separate invocations.

The project is pure Python and leans on Git itself for repository truth.
Instead of reimplementing repository storage, it shells out to `git`, parses
its output, and stores tool-specific metadata alongside that workflow.

## High-Level Design

The codebase is organized as a set of layers:

- `cli/`
  Argument parsing, help, completion, and top-level command dispatch.
- `commands/`
  User-facing command implementations.
- `data/`
  Session-persistent state, selected-hunk caches, progress tracking, undo/redo,
  and other workflow bookkeeping.
- `core/`
  Neutral models and parsing logic for diffs, hunks, hashes, and line
  selections.
- `staging/`
  Helpers that build exact target file content for selective index or working
  tree updates.
- `batch/`
  The advanced named-batch subsystem: ownership, storage, merge, attribution,
  source refresh, and batch-specific selection logic.
- `output/`
  Rendering of hunks, patches, and colors for terminal display.
- `tui/`
  The interactive single-key interface built on top of the command layer.
- `utils/`
  Git subprocess wrappers, file I/O helpers, path layout, journaling, and
  text/process utilities.

That split is intentional:

- `core/` tries to describe changes
- `commands/` decide what to do about them
- `data/` remembers where the workflow is
- `batch/` adds a second persistence model for deferred changes
- `utils/` hides subprocess and filesystem details from the rest of the code

## The Main Execution Flow

The usual entry path is:

1. `cli.argument_parser` builds the command-line interface.
2. `cli.dispatch` selects the command implementation.
3. A function in `commands/` performs the operation.
4. That command uses `data/`, `core/`, `staging/`, `batch/`, and `utils/`
   modules as needed.

For a typical non-batch staging workflow:

1. `start` initializes session state and discovers the first pending change.
2. `show` or the cached hunk state renders the current change.
3. `include`, `skip`, or `discard` records a decision.
4. Navigation advances to the next pending change.
5. `again` clears per-pass progress while preserving broader session context.
6. `stop` or `abort` ends the session.

The program therefore behaves like a state machine spread across CLI
invocations. The on-disk state in `data/` is what keeps the workflow coherent.

## Core Representation of Changes

The `core/` package defines the models and parsing logic used throughout the
project.

Important pieces include:

- `core.models`
  Shared dataclasses such as `LineLevelChange`, `LineEntry`,
  `BinaryFileChange`, and hunk/header representations.
- `core.diff_parser`
  Streaming unified-diff parsing from Git output into structured models.
- `core.line_selection`
  Parsing and formatting of line ID selections like `1,3,5-7`.
- `core.hashing`
  Stable hunk hashing for tracking progress across commands.

The diff parser is especially central. Many workflows begin with raw `git diff`
output and convert it into structured hunk objects that later modules can
filter, render, and apply.

The key design choice here is that parsing and structural modeling are kept
separate from command policy. `core/` should describe "what the diff says,"
not "what the command should do."

## Session and Workflow State

The `data/` package is the backbone of the interactive workflow.

It stores information such as:

- whether a session is active
- the starting `HEAD`
- stash-like abort restoration data
- the currently selected hunk or file
- included, skipped, or discarded progress
- iteration state for `again`
- undo/redo checkpoints
- hidden consumed selections
- cached batch source mappings for the current session

Important modules include:

- `data.session`
  Session lifecycle, abort initialization, snapshotting, and cleanup.
- `data.hunk_tracking`
  Discovery, caching, navigation, hunk selection, and live filtering.
- `data.undo`
  Undo/redo checkpoints for session operations.
- `data.line_state`
  Serialization of the currently selected line-level view.
- `data.progress`, `data.file_tracking`, `data.hunk_tracking`
  Progress bookkeeping across files and hunks.

This layer is what makes the tool feel interactive even though many commands
are separate processes. A later invocation does not recompute everything from
scratch; it resumes from persisted session state.

## How Normal Staging Works

For ordinary include/skip/discard workflows, the architecture is:

1. Discover a diff hunk with `git diff`.
2. Parse it into `LineLevelChange` or `BinaryFileChange`.
3. Cache the selected change in session state.
4. Render it for the user.
5. Apply the user's decision back to Git state and progress state.

There are two broad paths:

- text hunks
- binary file changes

### Text hunks

Text hunks are handled either as whole hunks or line-scoped selections.

- Whole-hunk include/discard often delegates directly to Git patch application.
- Line-level include/discard uses the `staging/operations.py` helpers to build
  exact target content for the index or working tree from the parsed hunk plus
  the selected line IDs.

That split is important. The program does not always ask Git to apply a smaller
patch. For fine-grained line operations, it often computes the intended file
content itself and then writes that result back through Git-aware helpers.

### Binary changes

Binary file changes are treated as file-level units. They are detected in the
diff parser and routed through separate command handling because line-level
operations do not make sense there.

## The Batch Subsystem

The `batch/` package is the largest specialized subsystem in the project.
It exists to let users defer, recall, split, reapply, and reconcile saved
changes independently of the immediate staging pass.

At a high level, the batch subsystem is responsible for:

- storing named batch state
- representing batch ownership over file content
- reconstructing batch views for display
- merging batch-owned changes into the current working tree or index
- reversing batch-owned changes out of the working tree
- filtering already-batched content out of live diffs
- handling stale batch sources as files evolve
- reconciling batches against newer tip state with `sift`

The most important conceptual rule is that batches are not just stored diffs.
They are a constraint-based model over per-file source snapshots.

The deeper model, data layout, and merge behavior are documented in
[BATCHES.md](BATCHES.md). This file only describes where that logic sits in the
project.

Key modules:

- `batch.storage`
  Persisting batch file entries and realized content.
- `batch.state_refs`
  Publishing authoritative batch state into Git refs.
- `batch.query`
  Reading batch metadata and refs.
- `batch.ownership`
  Ownership data structures and ownership transformations.
- `batch.merge`
  Structural merge and reverse-merge logic.
- `batch.attribution`
  Ownership attribution for filtering live diffs.
- `batch.source_refresh`
  Stale-source detection and repair orchestration.
- `batch.selection`
  File scope and line-level batch selection rules.
- `batch.display`
  Reconstructing batch views for `show --from` and related workflows.
- `batch.comparison`
  Shared semantic-run comparison logic used by attribution and sift.

## Display, Output, and TUI

The program separates data modeling from terminal rendering.

- `output/`
  Knows how to print line-level changes, binary changes, patches, and colors.
- `tui/`
  Adds the interactive menu-driven front end.

The interactive UI is not a separate architecture stack. It mostly sits on top
of the same command and state machinery used by the plain CLI. That keeps the
behavior consistent: the TUI is another way to drive the same underlying
workflow rather than a parallel implementation.

## Git Integration Philosophy

The `utils.git` layer wraps subprocess calls to Git and provides streaming and
transactional helpers such as:

- `run_git_command()`
- `stream_git_command()`
- `update_git_refs()`
- blob/object readers and writers

The project relies on Git as the source of truth for:

- the working tree
- the index
- committed history
- object storage
- ref updates

This is a deliberate architectural choice. The tool owns workflow state and
batch-specific metadata, but it delegates repository semantics to Git whenever
possible.

## State Layout and Persistence Strategy

There are two broad kinds of state in the project:

- session/workflow state
- Git-backed batch state

Session state lives under the tool's state directory and supports:

- resuming selected hunks
- `again`
- `undo` / `redo`
- `abort`
- hidden masking of already-consumed selections

Batch state is more durable and increasingly Git-native:

- content refs under `refs/git-stage-batch/batches/*`
- state refs under `refs/git-stage-batch/state/*`

That split lets the tool provide both:

- ephemeral workflow control for the current pass
- durable saved changes that outlive one pass

## Error Handling and Safety

A recurring theme in the codebase is conservative refusal.

This shows up in several places:

- stale selected hunks are rejected and recalculated
- batch merge paths fail when structure is ambiguous
- atomic ownership units cannot be partially selected
- abort snapshots are captured up front so destructive operations can be rolled
  back to session start

The general rule is that the project prefers refusing an operation over
guessing when the guess could silently damage the working tree, the index, or
saved batch state.

## Build, Packaging, and Documentation

Top-level project files provide the surrounding system:

- `pyproject.toml`
  Python project metadata and development tooling config.
- `meson.build`
  Build and packaging integration.
- `docs/`
  User-facing website documentation.
- `completions/`
  Shell completion support.
- `assets/`
  Bundled assistant assets and related material.

The architecture documents at the repository root serve a different purpose from
the website docs:

- website docs explain how to use the tool
- root-level architecture docs explain how the implementation is organized

## Testing Strategy

The test suite mirrors the code structure closely:

- `tests/cli/`
  CLI parsing and dispatch behavior.
- `tests/commands/`
  User-facing command behavior.
- `tests/core/`
  Diff parsing, models, line selection, encoding, and hashing.
- `tests/data/`
  Session and state-management logic.
- `tests/staging/`
  Selective content construction and application helpers.
- `tests/batch/`
  Batch storage, merge, ownership, sift, validation, and attribution behavior.
- `tests/functional/`
  Multi-step end-to-end workflows.
- `tests/tui/`
  Interactive UI behavior.

That layout is useful when making changes:

- if you are touching diff structure, start in `tests/core/`
- if you are touching session flow, look in `tests/data/` and
  `tests/functional/`
- if you are touching named batches, expect most coverage in `tests/batch/`
  plus command-level tests

## Where to Start Reading

For a contributor new to the codebase, a good reading order is:

1. `README.md`
   Understand the product-level workflow.
2. `src/git_stage_batch/cli/argument_parser.py`
   See the public command surface.
3. `src/git_stage_batch/cli/dispatch.py`
   See how parsed commands are routed.
4. `src/git_stage_batch/commands/start.py`,
   `show.py`, `include.py`, `skip.py`, `discard.py`
   Understand the core non-batch workflow.
5. `src/git_stage_batch/data/session.py` and
   `src/git_stage_batch/data/hunk_tracking.py`
   Understand state and navigation.
6. `src/git_stage_batch/core/diff_parser.py` and `core/models.py`
   Understand the shared representation of change.
7. `BATCHES.md` and then `src/git_stage_batch/batch/*`
   Understand the advanced deferred-change architecture.

## Summary

At the project level, `git-stage-batch` is an interactive workflow engine built
on top of Git:

- `core/` models change
- `commands/` define behavior
- `data/` preserves workflow state
- `staging/` computes selective file results
- `batch/` adds durable deferred-change semantics
- `output/` and `tui/` present the workflow to the user
- `utils/` keeps Git and filesystem integration manageable

If you keep that separation in mind, most of the codebase becomes easier to
navigate. The batch subsystem is the deepest specialized part, but it still sits
inside that larger pattern rather than replacing it.
