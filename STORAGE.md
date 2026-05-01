# Git Stage Batch: Storage Reference

## Table of Contents

1. [Introduction](#introduction)
2. [Mental Model](#mental-model)
3. [Storage Principles](#storage-principles)
4. [Top-Level Storage Areas](#top-level-storage-areas)
5. [Authority and Precedence](#authority-and-precedence)
6. [Authoritative Batch Content](#authoritative-batch-content)
7. [File-Backed Batch Metadata](#file-backed-batch-metadata)
8. [Git-Backed Batch State](#git-backed-batch-state)
9. [Batch Source Snapshots](#batch-source-snapshots)
10. [Session Scratch State](#session-scratch-state)
11. [Abort and Recovery State](#abort-and-recovery-state)
12. [Selected Change Cache](#selected-change-cache)
13. [Progress and Navigation State](#progress-and-navigation-state)
14. [Batch Ref Snapshots](#batch-ref-snapshots)
15. [Object Reachability](#object-reachability)
16. [Undo Checkpoints](#undo-checkpoints)
17. [Future Direction](#future-direction)
18. [Migration Invariants](#migration-invariants)

---

## Introduction

This document describes where git-stage-batch stores persistent batch data,
session state, snapshots, undo checkpoints, and Git-backed batch storage.

`BATCHES.md` explains the semantic model for batches: baseline commits, batch
source commits, realized content, ownership constraints, and structural merge.
This document is narrower. It answers:

* Which files and refs exist
* Which storage locations are authoritative
* Which storage locations are caches or mirrors
* Which objects preserve source snapshots
* Which state belongs to an active session
* How undo checkpoints are stored and restored

The selected implementation is intentionally hybrid. Git objects store durable
content, batch state, undo checkpoints, and durable snapshots. Local files under
`.git/git-stage-batch` store active session scratch state and compatibility
caches.

---

## Mental Model

git-stage-batch storage falls into four categories:

* **Batch content refs**: durable user-facing truth for realized batch content.
* **Batch state refs**: durable metadata needed to reconstruct, merge, validate, and restore batches.
* **Session state**: ephemeral workflow machinery for an active session.
* **Compatibility storage**: legacy paths kept only for migration and older repositories.

For any path or ref, maintainers should ask:

1. Is it durable content, durable state, ephemeral session state, or compatibility-only?
2. If multiple locations mention the same batch, which one is authoritative?

The answers are stable:

* `refs/git-stage-batch/batches/<batch>` is authoritative for content.
* `refs/git-stage-batch/state/<batch>` is authoritative for batch state.
* `.git/git-stage-batch/session/` is authoritative for active session state.
* Legacy refs and metadata files are migration inputs, not co-equal authorities.

---

## Storage Principles

### Git Objects Store Durable Content

Realized batch content is a normal Git commit. Batch source snapshots are Git
commits in the selected storage model, and are also stored as tree entries in
the Git-backed state refs.

Durable file bytes should live as blobs in trees, not embedded in JSON.

### Session Files Store Active Workflow State

The current selected hunk, blocklists, processed line IDs, progress counters,
and stale-cache snapshots are session-local scratch state. They are not project
history. They live under:

```text
.git/git-stage-batch/session/
```

These files can be checkpointed for undo, but they do not need to be live Git
refs in order for the interface to work.

### Names Are Semantic, Versions Are Commits

The storage model avoids numbered directories for source snapshots or undo
frames. When the same logical path changes over time, the parent chain of the
containing commit records the history.

For example, the Git-backed state ref stores the current source snapshot
for `src/main.py` at:

```text
sources/src/main.py
```

If that source changes, the next state commit updates the same path. The old
source remains in the previous state commit.

### Compatibility Must Not Be Confused With Authority

The authoritative batch storage is:

```text
refs/git-stage-batch/batches/<batch>
refs/git-stage-batch/state/<batch>
```

The legacy ref and metadata file are compatibility inputs:

```text
refs/batches/<batch>
.git/git-stage-batch/batches/<batch>/metadata.json
```

Legacy-only batches are read as a migration fallback. Once a batch is written
through the Git-backed storage path, the legacy ref and file-backed metadata for
that batch are removed.

### Ref Names Name Live Roots

The formal git-stage-batch ref namespace is:

```text
refs/git-stage-batch/
  batches/<batch>
  state/<batch>
  session/undo-stack
  session/redo-stack
```

These refs are moving roots for live storage. Versioned data lives in the commit
history reachable from those roots, or inside their trees. For example, undo
checkpoints are parent commits reachable from `session/undo-stack`, not
separate numbered refs.

---

## Top-Level Storage Areas

git-stage-batch currently uses four broad storage areas.

### Authoritative Git Refs

```text
refs/git-stage-batch/batches/<batch>
```

These refs store realized batch content. They are the selected content refs used
by batch display, apply, reset, sift, and diff operations.

### Git-Stage-Batch Git Refs

```text
refs/git-stage-batch/batches/<batch>
refs/git-stage-batch/state/<batch>
refs/git-stage-batch/session/undo-stack
refs/git-stage-batch/session/redo-stack
```

The batch refs store authoritative content and metadata. They are written when
batches are created, updated, deleted, or restored, and command reads prefer
them.

The session refs store the undo and redo stacks for the current active session.
They are not mirrors; they are the selected checkpoint storage.

### Repository-Local State Directory

```text
.git/git-stage-batch/
```

This directory stores active sessions, abort state, file-backed metadata, the
debug journal, and compatibility batch metadata when a batch has not yet been
published entirely through the Git-backed state refs.

### Git Object Database

Normal Git objects store:

* Batch commits
* Batch source commits
* Batch state commits
* Source blobs inside state trees
* Undo checkpoint commits
* Worktree before-image blobs inside undo checkpoint trees
* Stash commits used by abort recovery

---

## Authority and Precedence

When multiple storage locations mention the same batch, precedence is defined by
design, not inferred heuristically.

### Durable batch content

Authoritative:

`refs/git-stage-batch/batches/<batch>`

Compatibility-only:

`refs/batches/<batch>`

### Durable batch state

Authoritative:

`refs/git-stage-batch/state/<batch>`

Compatibility-only:

`.git/git-stage-batch/batches/<batch>/metadata.json`

### Session state

Authoritative during an active session:

`.git/git-stage-batch/session/`

### Undo checkpoints

Checkpoints are durable recovery units. Files captured inside them do not become
semantic truth.

---

## Authoritative Batch Content

### Authoritative Location

```text
refs/git-stage-batch/batches/<batch>
```

### Meaning

This ref points at the realized batch commit. The commit tree contains the file
content that the batch represents relative to its baseline.

This is a content-only tree. It does not contain metadata, ownership, source
snapshots, or session state.

### Creation

`create_batch()` creates an initial batch commit from the baseline tree:

```text
Batch Commit
  tree: baseline tree
  parent: baseline commit
  ref: refs/git-stage-batch/batches/<batch>
```

If there is no baseline, an empty tree is used.

### Updates

`add_file_to_batch()` and related storage helpers update the batch tree by using
a temporary index:

1. Read the current batch commit tree into a temporary index.
2. Add, update, or remove one file entry.
3. Write a new tree.
4. Create a new batch commit.
5. Move `refs/git-stage-batch/batches/<batch>` to the new commit.
6. Delete the legacy compatibility ref `refs/batches/<batch>`.

Batch commits use parents to preserve object reachability:

```text
parent 1: baseline commit
parent 2..n: batch source commits
```

### Inspection

The content ref is intentionally readable with normal Git commands:

```bash
git show refs/git-stage-batch/batches/foo:path/to/file
git diff <baseline> refs/git-stage-batch/batches/foo
git ls-tree -r refs/git-stage-batch/batches/foo
```

`refs/batches/<batch>` is read only as a migration input. Batch writes delete it
after publishing the Git-backed refs.

---

## File-Backed Batch Metadata

### Location

```text
.git/git-stage-batch/batches/<batch>/metadata.json
```

### Meaning

This file is a legacy metadata format read as a migration input when no
Git-backed state ref exists. It records:

* Batch note
* Creation timestamp
* Baseline commit
* Per-file ownership
* Per-file source snapshot commit
* Per-file mode
* Binary file markers when needed

Example:

```json
{
  "note": "Test note",
  "created_at": "2026-04-17T12:00:00+00:00",
  "baseline": "abc123...",
  "files": {
    "src/main.py": {
      "batch_source_commit": "def456...",
      "claimed_lines": ["10-12"],
      "deletions": [],
      "replacement_units": [],
      "mode": "100644"
    }
  }
}
```

### Role

The corresponding state metadata is not just descriptive. It is the declarative
model used to reconstruct and validate a batch:

* `baseline` defines the baseline side of the batch.
* `batch_source_commit` defines the coordinate space for ownership.
* `claimed_lines` define source lines that must be present.
* `deletions` define anchored absence constraints.
* `replacement_units` optionally links claimed ranges to deletion indexes for
  explicit replacement atomicity.
* `mode` defines the file mode for realized content.

### Limitations

The compatibility metadata is mutable filesystem state rather than a Git tree.
It is easy to inspect, but should not be treated as authoritative when the
Git-backed state ref exists.

It is not the right place to:

* Store the canonical batch model
* Restore atomically with content refs
* Rewind with a ref-based undo model
* Keep long-lived source snapshots obviously reachable from refs

The Git-backed state ref addresses these limitations. New writes remove this
file after importing its contents.

---

## Git-Backed Batch State

### Locations

```text
refs/git-stage-batch/batches/<batch>
refs/git-stage-batch/state/<batch>
```

### Status

These refs are authoritative for new reads. They are written by
`batch.state_refs.sync_batch_state_refs()`. The legacy `refs/batches/<batch>`
and `metadata.json` paths are fallback inputs for older state; writes remove
them after the Git-backed refs are published.

The content ref answers what the batch contains; the state ref answers how the
batch is reconstructed and validated.

### Content Ref

```text
refs/git-stage-batch/batches/<batch>
```

This ref points at the realized batch content commit. Older versions used:

```text
refs/batches/<batch>
```

Command reads prefer the Git-backed content ref and fall back to the legacy ref
only for older state. Command writes delete the legacy ref for the updated
batch.

### State Ref

```text
refs/git-stage-batch/state/<batch>
```

This ref points at a commit whose tree has this shape:

```text
batch.json
sources/
  <repo-relative paths>
```

Example:

```text
batch.json
sources/src/main.py
sources/README.md
```

### `batch.json`

`batch.json` stores the authoritative batch metadata and explicit content-ref
information:

```json
{
  "batch": "foo",
  "note": "Test note",
  "created_at": "2026-04-17T12:00:00+00:00",
  "baseline_commit": "abc123...",
  "content_ref": "refs/git-stage-batch/batches/foo",
  "content_commit": "def456...",
  "files": {
    "src/main.py": {
      "batch_source_commit": "987654...",
      "source_path": "sources/src/main.py",
      "claimed_lines": ["10-12"],
      "deletions": [],
      "replacement_units": [],
      "mode": "100644"
    }
  }
}
```

The state keeps `batch_source_commit` for compatibility with the current model
and adds `source_path` for the tree-backed source snapshot.

### Source Entries

Each `sources/<path>` entry stores the source bytes for that repository path.
These bytes are read from the existing `batch_source_commit`.

For example:

```bash
git show refs/git-stage-batch/state/foo:sources/src/main.py
```

prints the source snapshot that ownership for `src/main.py` is expressed
against.

### State History

Every sync creates a new state commit. If a file's source snapshot changes, the
same semantic path under `sources/` changes in a new commit. The previous source
snapshot remains available in the parent state commit.

This keeps path names meaningful and puts versioning in Git history rather than
in numbered directories.

### Consistency Invariant

The state can be validated by checking:

```text
refs/git-stage-batch/batches/<batch> == batch.json.content_commit
```

Command reads should reject inconsistent state rather than silently using
mismatched content and metadata.

---

## Batch Source Snapshots

### Selected Storage

The selected implementation stores batch source snapshots as Git commits. Each
per-file source commit has:

* The session baseline as its parent
* A tree based on the baseline tree
* One path replaced by the file's source bytes

The source commit SHA is part of the per-file batch metadata. In the selected
model it is persisted authoritatively in:

```text
refs/git-stage-batch/state/<batch>:batch.json
```

and may also transiently appear in:

```text
.git/git-stage-batch/batches/<batch>/metadata.json
```

under the same field:

```json
"batch_source_commit": "..."
```

### Source Content Selection

Source bytes come from the file state at session start:

* For tracked files, the abort stash is used when available.
* If the file is unchanged from the baseline, the baseline commit is used.
* For untracked or intent-to-add files, the lazy abort snapshot is used.
* For new files that did not exist at session start, the current working tree
  content is used when the source is first created.

### Lazy Creation

Batch source commits are created lazily. A source exists only after a file is
first added to a batch.

The session cache:

```text
.git/git-stage-batch/session/batch-sources.json
```

maps repository paths to source commit SHAs for the active session.

### Source Advancement

If existing ownership uses stale source coordinates, source-refresh logic can
advance the source. The selected model creates a new source commit, remaps the
existing ownership, and updates the session source cache.

### State Ref Source Entries

The Git-backed state ref stores source commit contents under:

```text
refs/git-stage-batch/state/<batch>:sources/<path>
```

These source bytes are normal Git blobs reachable from the authoritative state
ref.

---

## Session Scratch State

### Root

Active session state lives under:

```text
.git/git-stage-batch/session/
```

The layout is grouped by purpose:

```text
session/
  abort/
  config/
  fixup/
  processed/
  progress/
  selected/
  batch-sources.json
  consumed-selections.json
```

### Lifecycle

Session state is created by `git-stage-batch start` and cleared by:

* `git-stage-batch stop`
* `git-stage-batch abort`

Iteration-specific parts are cleared by:

* `git-stage-batch again`

Compatibility batch metadata under:

```text
.git/git-stage-batch/batches/
```

is not cleared by `again`, `stop`, or `abort` except when abort restores batch
refs and compatibility metadata to their session-start state.

### Scratch vs Durable State

Session files are scratch state. They are allowed to be direct files because:

* They are small.
* They change frequently.
* They represent UI and traversal state.
* They can be regenerated or cleared in many cases.

Undo support can checkpoint these files without making the live state itself a
Git ref.

---

## Abort and Recovery State

### Location

```text
.git/git-stage-batch/session/abort/
```

### Files

```text
head.txt
stash.txt
untracked-paths.txt
untracked/
auto-added-files.txt
intent-to-add-files.txt
batch-refs.json
```

### `head.txt`

Stores the selected `HEAD` commit at session start. Abort uses this as the
reset target:

```bash
git reset --hard <head>
```

### `stash.txt`

Stores the commit SHA returned by:

```bash
git stash create
```

The stash commit captures tracked worktree and index changes at session start.
Abort applies it with `--index` to restore both staged and unstaged tracked
state.

### `untracked-paths.txt` and `untracked/`

Untracked and intent-to-add file bytes are snapshotted lazily under:

```text
session/abort/untracked/<repo-relative-path>
```

`untracked-paths.txt` records which paths have snapshots.

This exists because `git stash create` does not include untracked files, and
intent-to-add files need special care around `git reset --hard`.

### `auto-added-files.txt`

Records files that git-stage-batch added to the index with `git add -N` so they
can appear in diffs.

Abort resets those paths before restoring the original state.

### `intent-to-add-files.txt`

Records files that had intent-to-add status at session start. Abort restores
that status after resetting and applying the stash.

### `batch-refs.json`

Stores a session-start snapshot of batch refs and file-backed metadata.

Abort uses it to:

* Delete batches created during the session
* Restore batches deleted during the session
* Revert batches modified during the session
* Restore file-backed metadata
* Resync the Git-backed state refs

---

## Selected Change Cache

### Location

```text
.git/git-stage-batch/session/selected/
```

### Files

```text
hunk.hash.txt
hunk.patch
hunk.lines.json
change-kind.txt
binary-file.json
index.snapshot
working-tree.snapshot
```

### `hunk.patch`

Stores the currently selected text hunk as patch bytes.

Commands such as `include`, `discard`, and `show` use this cache to operate on
the selected hunk.

### `hunk.hash.txt`

Stores the stable hash of the selected hunk. The hash is used for progress
tracking and blocking already processed hunks.

### `hunk.lines.json`

Stores the parsed line-level representation of the selected hunk. Line-level
commands use this file to map user-visible line IDs to actual source and target
lines.

### `binary-file.json`

Stores selected binary-file metadata when the selected change is binary rather
than a text hunk.

### `change-kind.txt`

Stores whether the selected cached item is a text hunk, a file-scoped view, a
binary file, or a batch-file view. Commands use it to decide how to interpret
the rest of the selected-state cache.

### `index.snapshot`

Stores the file's index bytes when the hunk is selected.

### `working-tree.snapshot`

Stores the file's working tree bytes when the hunk is selected.

### Staleness Detection

Before line-level operations, the selected snapshots are compared against the
current index and working tree. If either changed, the cached hunk is stale and
the operation is rejected.

Batch hunks and file-scoped live operations do not use these staleness
snapshots in the same way, because they are rendered from batch state or live
working tree state rather than from the cached selected hunk.

---

## Progress and Navigation State

### Location

```text
.git/git-stage-batch/session/progress/
```

### Files

```text
blocked-hunks.txt
included-hunks.txt
discarded-hunks.txt
skipped-hunks.jsonl
batched-hunks.txt
blocked-files.txt
```

### `blocked-hunks.txt`

Stores hunk hashes that should be skipped by forward traversal. This is the
core forward-progress mechanism.

When the next hunk is fetched, hunks whose hash appears here are ignored.

### `included-hunks.txt`

Stores hashes of hunks that have been staged into the Git index during the
current iteration.

### `discarded-hunks.txt`

Stores hashes of hunks that have been removed from the working tree during the
current iteration.

### `skipped-hunks.jsonl`

Stores one JSON object per skipped hunk. This records enough metadata to report
skipped hunk progress and locations.

JSON Lines is used because skipped hunk records are append-oriented and each
record has structure.

### `batched-hunks.txt`

Reserved for hunk-level batch progress.

### `blocked-files.txt`

Stores repository paths that should be excluded from traversal.

---

## Processed Line State

### Location

```text
.git/git-stage-batch/session/processed/
```

### Files

```text
included-lines.json
skipped-lines.json
batched-lines.json
```

These files track line IDs that have already been handled within the selected
hunk or file-scoped operation.

The filenames use `.json` because they are structured line-id sets, even though
the selected serialization is still compatible with the existing line-id helper
format.

---

## Config and Auxiliary Session State

### Location

```text
.git/git-stage-batch/session/config/
```

### Files

```text
context-lines.txt
iteration-count.txt
```

`context-lines.txt` stores the selected unified diff context line count.

`iteration-count.txt` stores the current pass number. `again` increments it.

### TUI Start State

```text
.git/git-stage-batch/session/start-head.txt
.git/git-stage-batch/session/start-index-tree.txt
```

These files are written by interactive mode to decide whether quitting should
prompt to keep or undo staged changes.

### Suggest-Fixup State

```text
.git/git-stage-batch/session/fixup/state.json
```

Stores the current iterative state for `suggest-fixup`.

### Journal

```text
.git/git-stage-batch/journal.jsonl
```

The journal is a debug log. It is session-level, but it remains outside
`session/` so it can be preserved across `again` and cleared with the full
session.

---

## Batch Ref Snapshots

### Location

```text
.git/git-stage-batch/session/abort/batch-refs.json
```

### Meaning

This file stores the batch state at session start. Its selected format is:

```json
{
  "batch-name": {
    "commit_sha": "...",
    "state_commit_sha": "...",
    "metadata": {}
  }
}
```

The snapshot includes complete file-backed metadata so abort can recreate
deleted batches and roll back modified batches.

### Restore Behavior

Abort compares the snapshot with current batch refs:

* Current batch not in snapshot: delete it.
* Snapshot batch missing from current refs: recreate it.
* Batch exists with different commit: reset its ref.
* Metadata differs or was deleted: rewrite metadata from the snapshot.

When the snapshot includes an exact Git-backed state ref commit, abort restores
the content, state, and compatibility refs together through `update_git_refs()`.
Older snapshots without a saved state ref are restored by resyncing the
Git-backed state from the restored content ref and metadata.

---

## Object Reachability

### Selected Batch Source Commits

Batch source commits are made reachable by using them as parents of realized
batch commits. Compatibility metadata can mention their SHAs, but metadata alone
is not a Git reachability root.

### Git-Backed Source Entries

The Git-backed state refs make source snapshot bytes reachable from:

```text
refs/git-stage-batch/state/<batch>
```

The source bytes are ordinary blobs under the `sources/` tree.

### Stash Commits

Abort stash commits are stored by SHA in `stash.txt`. They are not refs. They
remain available while the object is not garbage-collected. Because sessions are
short-lived, this is acceptable for the selected model, but a more durable
future model could store abort data in a Git-backed session ref.

### Local File Snapshots

Abort untracked snapshots are currently filesystem copies under:

```text
.git/git-stage-batch/session/abort/untracked/
```

These are not Git objects yet. They are candidates for a future Git-backed
session state ref.

---

## Undo Checkpoints

### Location

```text
refs/git-stage-batch/session/undo-stack
```

This ref points at the newest undo checkpoint for the active session. Each
checkpoint commit has the previous checkpoint as its parent, so the undo stack
is ordinary Git history rather than numbered frame directories.

`git-stage-batch stop` and `git-stage-batch abort` delete this ref when they
clear session state.

### Checkpoint Tree

Each checkpoint stores a before-image of the state needed to undo one operation:

```text
manifest.json
session/
batches/
worktree/
  <repo-relative paths>
```

### `manifest.json`

`manifest.json` records the non-file state needed to restore the checkpoint:

```json
{
  "operation": "include --line 1",
  "head": "abc123...",
  "index_tree": "def456...",
  "refs": {
    "refs/batches/foo": "111111...",
    "refs/git-stage-batch/batches/foo": "111111...",
    "refs/git-stage-batch/state/foo": "222222..."
  },
  "after": {
    "index_tree": "999999...",
    "refs": {
      "refs/batches/foo": "333333...",
      "refs/git-stage-batch/batches/foo": "333333...",
      "refs/git-stage-batch/state/foo": "444444..."
    },
    "worktree_paths": [
      {
        "path": "src/main.py",
        "exists": true,
        "mode": "100644",
        "blob": "555555..."
      }
    ]
  },
  "worktree_paths": [
    {
      "path": "src/main.py",
      "exists": true,
      "mode": "100644"
    }
  ]
}
```

The `operation` string is user-facing output for `git-stage-batch undo`.
`index_tree` is restored with `git read-tree`. The top-level `refs` map is the
complete set of batch-related refs that undo restores.

The `after` object is recorded after the mutating command completes. Undo uses
it for conflict detection: if the current index tree, batch refs, or tracked
worktree path bytes differ from `after`, undo refuses by default.

### Session and Metadata Before-Images

Undo snapshots these filesystem trees:

```text
.git/git-stage-batch/session/
.git/git-stage-batch/batches/
```

They are stored in the checkpoint as:

```text
session/
batches/
```

Restoring a checkpoint replaces the live session and file-backed batch metadata
trees with these before-images.

### Ref Before-Images

Undo restores all refs under:

```text
refs/batches/
refs/git-stage-batch/batches/
refs/git-stage-batch/state/
```

Refs created after the checkpoint are deleted. Refs present in the checkpoint
are moved back to the saved object IDs. Undo applies those ref updates through
one `update_git_refs()` transaction so the batch-related refs are restored
together.

### Index and Worktree Before-Images

Undo restores two separate views of repository state:

1. The index tree saved in `manifest.json`.
2. Worktree file bytes saved under `worktree/`.

Because Git trees do not encode intent-to-add entries, undo reapplies
intent-to-add for paths listed in the restored
`session/abort/auto-added-files.txt` after restoring the saved index tree and
worktree bytes.

The worktree snapshot covers paths reported by:

```bash
git diff --name-only HEAD
git diff --cached --name-only
git ls-files --others --exclude-standard
```

For each path, the manifest records whether it existed and its executable mode.
If a file did not exist at checkpoint time, undo removes it. If it did exist,
undo writes the saved blob and restores the executable bit.

### Restore Order

`git-stage-batch undo` restores state in this order:

1. Restore `.git/git-stage-batch/session/`.
2. Restore `.git/git-stage-batch/batches/`.
3. Restore batch refs and Git-backed state refs.
4. Restore the Git index from `index_tree`.
5. Restore worktree before-images.
6. Move `refs/git-stage-batch/session/undo-stack` to the parent checkpoint, or
   delete it when the stack is empty.

The checkpoint is created immediately before mutating commands such as
`include`, `skip`, `discard`, batch transfers, `reset`, `new`, `drop`, and
`annotate`.

### Conflict Detection

Undo is guarded by default. It compares the current state to the checkpoint's
recorded `after` state before restoring anything:

* If the index tree changed, undo refuses.
* If batch refs changed, undo refuses.
* If a tracked worktree path changed, undo refuses.

The user can bypass this guard with:

```bash
git-stage-batch undo --force
```

Forced undo restores the checkpoint before-images and overwrites those later
changes.

### Limitations

Undo is intentionally session-scoped. It requires an active session and only
tracks state that git-stage-batch knows how to restore.

Undo does not attempt to merge arbitrary user edits made after the checkpoint.
It either refuses or, with `--force`, writes the saved before-images for the
affected paths.

Undo checkpoint commits are reachable only from:

```text
refs/git-stage-batch/session/undo-stack
```

When that ref is deleted by stop or abort, the checkpoint commits become normal
unreferenced objects and may eventually be garbage-collected.

---

## Redo Stack

### Location

```text
refs/git-stage-batch/session/redo-stack
```

This ref points at the newest redo node for the active session. Each redo node
commit has the previous redo node as its parent, so the redo stack is ordinary
Git history analogous to the undo stack.

`git-stage-batch stop` and `git-stage-batch abort` delete this ref when they
clear session state. Any new undoable operation also clears the redo stack,
because redo history becomes invalid once the user performs a new operation
after undo.

### Redo Node Tree

Each redo node stores the target state to restore when redoing:

```text
manifest.json
session/
batches/
worktree/
  <repo-relative paths>
```

### `manifest.json`

Redo node `manifest.json` records the state needed to restore and validate:

```json
{
  "operation": "include --line 1",
  "undo_checkpoint": "<sha of original undo checkpoint>",
  "head": "<HEAD sha>",
  "index_tree": "<target index tree>",
  "refs": {
    "refs/git-stage-batch/batches/foo": "...",
    "refs/git-stage-batch/state/foo": "..."
  },
  "worktree_paths": [
    {
      "path": "src/main.py",
      "exists": true,
      "mode": "100644"
    }
  ],
  "after_undo": {
    "index_tree": "<state immediately after undo>",
    "refs": {},
    "worktree_paths": [
      {
        "path": "src/main.py",
        "exists": true,
        "mode": "100644",
        "blob": "..."
      }
    ]
  }
}
```

The top-level `index_tree`, `refs`, `session/`, `batches/`, and `worktree/` are
the state to restore when running redo.

The `after_undo` object is the conflict-detection baseline. It records the state
immediately after undo completed. If current state differs from `after_undo`,
redo refuses unless `--force`.

### Lifecycle

Redo nodes are created during `undo_last_checkpoint()`. Each undo pushes a redo
node whose target is the pre-undo state and whose `after_undo` is a snapshot of
the post-undo state.

`redo_last_checkpoint()` restores the target state from a redo node, pushes the
original undo checkpoint back onto the undo stack, and pops the redo stack.

### Redo Stack Invalidation

The redo stack is cleared when:

* A new undoable operation creates a checkpoint (any call to
  `_create_undo_checkpoint()`).
* `git-stage-batch stop` or `git-stage-batch abort` clears session state.

This matches the standard editor undo/redo model: performing a new operation
after undo discards the redo history.

---

## Future Direction

The selected batch storage is:

```text
refs/git-stage-batch/batches/<batch>
refs/git-stage-batch/state/<batch>
```

In this model:

* `refs/git-stage-batch/batches/<batch>` stores realized content.
* `refs/git-stage-batch/state/<batch>` stores `batch.json` and `sources/`.
* File-backed batch metadata is a migration input.
* Batch reads validate `batch.json.content_commit` against the content ref.
* Batch updates move content and state refs together through `update_git_refs()`,
  which uses `git update-ref --stdin`.
* Undo restores ref values and session checkpoints instead of copying batch
  metadata files.

Abort recovery could also move further into Git-backed session state:

```text
refs/git-stage-batch/session/state
  abort.json
  untracked/
```

That would make untracked abort snapshots Git blobs rather than filesystem
copies. The selected branch does not implement that yet.

The remaining compatibility paths are import-only: they keep older state
readable, and new writes remove them for the batches they migrate.

---

## Migration Invariants

The compatibility layer should preserve these rules:

* Migration is idempotent.
* Once Git-backed refs exist, they win.
* Stale compatibility metadata must not override authoritative refs.
* Partial migration must not leave the repository in a more ambiguous state.
* Compatibility code should remain removable without redesigning storage.

Compatibility paths are transitional and intended to be removed before the
1.0.0 release.
