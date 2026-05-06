# Git Stage Batch: Current Batch Architecture

This document explains how the current batch system works internally.
It is meant to be usable both by contributors and by readers who have not used
the tool before.

It reflects the implementation under `src/git_stage_batch/batch/`,
`src/git_stage_batch/data/`, and the batch-facing commands in
`src/git_stage_batch/commands/`.

## Table of Contents

1. Introduction
2. Typical User Workflow
3. Mental Model
4. Storage Model
5. Session Model and Abort Semantics
6. Ownership Model
7. Materialization and Realized Batch Content
8. Applying and Discarding a Batch
9. Stale Batch Sources and Source Advancement
10. Display, Attribution, and Hidden Changes
11. Semantic Units and Line-Level Batch Selection
12. Reset and Moving Claims Between Batches
13. Sift
14. Binary Files
15. Important Invariants
16. Key Code Paths

---

## Introduction

Git itself gives you one mutable staging area: the index. That is enough when
you want to stage a change now or leave it unstaged for later. It is less
helpful when one working tree contains several unrelated changes and you want to
separate them over time.

`git-stage-batch` adds named, persistent buckets called batches. A batch lets
you set aside part of the current change set without committing it yet. Later,
you can bring that saved change back into the index or the working tree, remove
it from the working tree, move it to another batch, or reconcile it against
newer code.

Concretely, a batch can later:

- stage its changes into the index and working tree
- apply its changes back to the working tree
- discard its changes from the working tree
- move or reset its claims
- reconcile itself against the current tip with `sift`

Internally, the implementation is not patch-replay based. It stores ownership as
constraints:

- presence constraints: source lines a batch claims
- absence constraints: baseline sequences a batch says must not appear

Those constraints are stored in batch metadata and interpreted relative to a
stable per-file snapshot. The rest of this document explains that model from
the outside in.

---

## Typical User Workflow

Before the internal details, it helps to see the feature from the user's point
of view.

A common workflow looks like this:

1. Run `git-stage-batch start` to begin a staging session.
2. Review the current unstaged changes hunk by hunk.
3. Save some changes into a named batch with `include --to <batch>` or
   `discard --to <batch>` instead of staging or discarding them permanently.
4. Keep editing the working tree, possibly across multiple `again` passes.
5. Later, operate on the saved batch with commands such as
   `show --from <batch>`, `include --from <batch>`, `apply --from <batch>`,
   `discard --from <batch>`, `reset --from <batch>`, or `sift`.

Two facts drive the architecture:

- a batch is persistent state, not just a temporary UI view
- the working tree can keep changing after content has been saved into a batch

Most of the complexity in the implementation comes from preserving that saved
content accurately while the file around it keeps evolving.

---

## Mental Model

For one file in one batch, there are four states worth keeping in mind:

1. Baseline
   The committed starting point, taken from `HEAD` when the batch is created.
2. Batch source
   A stable per-file snapshot used as the coordinate space for the batch's saved
   claims.
3. Realized batch content
   The stored file view produced by applying only this batch's ownership to the
   baseline.
4. Current working tree
   The file as it exists now, which may have drifted from the source snapshot.

If you are new to the tool, the shortest useful summary is:

- the baseline is where the batch started from
- the batch source is the snapshot the batch uses to remember "which lines"
- the realized batch content is what the batch itself stores as its file view
- the working tree is whatever the user has on disk right now

The batch does not store "apply this diff later". It stores:

- which source lines must be present
- which deleted baseline sequences must stay absent

Later operations use those four states to decide how to reapply the saved change
to today's working tree, or how to remove that saved change from today's working
tree.

---

## Storage Model

### Ref layout

Current authoritative refs live under:

- `refs/git-stage-batch/batches/<name>`: realized batch content commit
- `refs/git-stage-batch/state/<name>`: normalized batch metadata plus embedded
  source snapshots

See `src/git_stage_batch/batch/ref_names.py` and
`src/git_stage_batch/batch/state_refs.py`.

Legacy `refs/batches/<name>` refs are still read as migration inputs, but once a
batch is rewritten by current code the authoritative refs above replace them.

### What the content ref stores

The content ref points at a normal Git commit whose tree contains the realized
batch files described in the previous section. For text files, realized content
is built from:

- batch baseline
- batch source file content
- batch ownership

The materialization path lives in `src/git_stage_batch/batch/storage.py`.

### What the state ref stores

The state ref stores a tree containing:

- `batch.json`: normalized metadata
- `sources/<path>` entries: embedded source snapshots for files in the batch

`sync_batch_state_refs()` publishes that state from file-backed metadata into
Git, then removes the old file-backed metadata directory.

This means the authoritative batch state is now Git-native, not just files under
the local state directory.

### Metadata shape

Per batch:

- `note`
- `created_at`
- `baseline` / `baseline_commit`
- `files`

Per text file:

- `batch_source_commit`
- `claimed_lines`
- `deletions`
- `replacement_units` (optional; omitted when empty)
- `mode`
- `change_type` (optional; `added` or `deleted` for whole-path lifecycle changes)

Per binary file:

- `file_type = "binary"`
- `change_type`
- `batch_source_commit`
- `mode`

`deletions` are serialized as anchored blobs, not inline text.
`replacement_units` records explicit coupling between claimed source ranges and
deletion indexes so replacement atomicity does not need to be rediscovered from
display adjacency. The field is omitted when no explicit replacement units are
stored.
Text `change_type` is omitted for ordinary modified-file batches. It is only
stored when a text batch owns the whole added path or the whole deleted path;
deleted text paths are absent from the batch content tree just like deleted
binary paths.

---

## Session Model and Abort Semantics

Batch behavior is coupled to the interactive session model started by
`git-stage-batch start`.

When a session starts, the tool snapshots:

- the starting `HEAD`
- a stash-like snapshot of tracked changes
- snapshots for untracked and intent-to-add files as needed
- the current batch refs, so batch mutations can be rolled back on `abort`

See `src/git_stage_batch/data/session.py` and
`src/git_stage_batch/data/batch_refs.py`.

Within that session model, batch-affecting commands also participate in the
tool's `undo` / `redo` checkpoint flow. That is a narrower mechanism than
`abort`:

- `undo` and `redo` step backward or forward through recent session operations
- `abort` restores the repository and batch refs to the state captured at
  session start

For someone learning the tool, the practical point is that batch operations are
not isolated from the rest of the session machinery. They are part of the same
reversible interaction model.

This matters because the initial batch source for a file is not an arbitrary
later working-tree snapshot. It is derived from the saved session-start file
content returned by
`get_saved_session_file_content()` in `src/git_stage_batch/data/batch_sources.py`.
For files that did not exist at session start, initial source creation falls
back to the current working-tree file content so the new file's claimed lines
actually exist in source space.

That design keeps later discard/abort behavior anchored to the same session
snapshot even if the user keeps editing.

---

## Ownership Model

With the session model in place, ownership can be stated precisely.
Ownership is defined in `src/git_stage_batch/batch/ownership.py`.

`BatchOwnership` has three persistent fields:

- `claimed_lines`
  Source-space line ranges like `["3-7", "10"]`
- `deletions`
  `DeletionClaim(anchor_line, content_lines)` records that a specific baseline
  sequence must be absent after a given source line, or at start-of-file if the
  anchor is `None`
- `replacement_units`
  Optional metadata linking claimed line ranges to entries in `deletions` when
  the capture path knows they form one replacement. These persisted units are
  selected atomically as a whole; display-adjacency grouping is only the fallback
  for ownership without explicit replacement metadata.

This distinction is central:

- presence claims say "this source content belongs to the batch"
- deletion claims say "this baseline content was removed by the batch"

The system treats deletions as constraints, not as negative patches to replay.

### Ownership versus attribution

This document uses two similar words for two different layers:

- ownership
  The persistent claims stored in batch metadata.
- attribution
  The derived answer to "which visible parts of the current working tree appear
  to belong to which batches right now?"

Ownership is stored and declarative.
Attribution is derived and ephemeral.

That distinction matters because the tool can hide already-batched content from
the live diff even though the batch metadata itself has not changed. Attribution
is one view over the current file state. Ownership is the underlying saved
state.

### Coordinate space

Ownership is always expressed in batch-source coordinates, not working-tree
coordinates.

That means:

- line numbers remain stable while the working tree evolves
- line-level batch operations must translate between display IDs,
  working-tree lines, and source-space lines

---

## Materialization and Realized Batch Content

Once baseline, source, and ownership exist, the realized file content for a
batch is built by
`_build_realized_content()` in `src/git_stage_batch/batch/storage.py`.

That function:

1. reads baseline bytes
2. reads batch source bytes
3. resolves ownership
4. calls `_satisfy_constraints()` from `src/git_stage_batch/batch/merge.py`
5. emits full file bytes while preserving the source line-ending style

Important detail: realization uses lenient absence enforcement. If a deletion's
baseline sequence is not found exactly at the expected boundary while building
the stored batch view, realization does not fail. It simply avoids suppressing a
non-matching sequence. This is deliberate because the stored view answers
"what does this batch claim?" rather than "can it be merged into today's
working tree right now?"

---

## Applying and Discarding a Batch

The user-facing commands that consume this model fall into three groups:

- commands that stage batch content into the index and working tree
- commands that apply batch content to the working tree
- commands that remove batch content from the working tree

### `include --from`

`include --from <batch>` stages batch content into the index and writes it to
the working tree.

Implementation:

- `src/git_stage_batch/commands/include_from.py`
- `src/git_stage_batch/batch/merge.py`

For each file, the command:

1. reads batch metadata
2. reads the file's batch source content
3. optionally narrows ownership by display line IDs or file scope
4. merges source constraints into current index bytes with `merge_batch()`
5. merges source constraints into current working-tree bytes with `merge_batch()`
6. writes the merged targets to the index and working tree

### `apply --from`

`apply --from <batch>` uses the same merge model, but writes the merged result
only to the working tree, leaving the index untouched.

Implementation:

- `src/git_stage_batch/commands/apply_from.py`

### `discard --from`

`discard --from <batch>` is the structural inverse. It removes the batch's
effects from the working tree by calling `discard_batch()`.

Implementation:

- `src/git_stage_batch/commands/discard_from.py`
- `src/git_stage_batch/batch/merge.py`

The discard path uses source-to-baseline correspondence rather than raw patch
reversal. It classifies baseline/source regions as:

- `EQUAL`
- `INSERT`
- `REPLACE_LINE_BY_LINE`
- `REPLACE_BY_HUNK`

That classification determines whether content can be restored line-by-line or
must be restored atomically as a hunk.

### Merge-time safety checks

`merge_batch()` does not blindly insert missing lines. It first validates that
the batch can still be applied safely:

- claimed source lines must either still be present or have enough surrounding
  mapped context
- deletion anchors must still be structurally meaningful
- missing claimed runs must still fit coherently into the target structure

Those checks are implemented in `_check_structural_validity()` and
`_check_claimed_region_compatibility()` in `src/git_stage_batch/batch/merge.py`.

The design is intentionally conservative. When context is lost or ambiguous, the
tool fails instead of guessing.

This is worth emphasizing. A wrong guess here would not just produce a merge
conflict. It could silently place saved lines into the wrong structural context
or remove content the batch never actually owned. The implementation therefore
prefers false negatives over silent corruption:

- if claimed lines no longer have trustworthy surrounding context, fail
- if a deletion anchor can no longer be placed reliably, fail
- if a missing claimed run seems to come from a structurally incompatible region,
  fail

From a user perspective, that can feel strict. From an architecture perspective,
it is one of the core safety properties of the system.

### One concrete example

Suppose `HEAD:file.txt` contains:

```text
line1
line2
line3
```

During a session, the working tree becomes:

```text
line1
line2-modified
line3
line4-new
```

The user saves the modification and the new line into a batch.

At that point:

- baseline: the `HEAD` version with `line1 / line2 / line3`
- batch source: a stable snapshot that contains
  `line1 / line2-modified / line3 / line4-new`
- ownership: claims the source lines for `line2-modified` and `line4-new`, plus
  a deletion claim that says the original baseline `line2` should be absent
- realized batch content: the file view the batch itself stores, which is
  effectively "baseline plus this batch's claimed changes"

Later, if the user edits the file again, the batch does not forget what it saved.
Instead, `include --from`, `apply --from`, and `discard --from` interpret those
saved claims against the new working-tree state. That is why the source snapshot
and the ownership model exist in the first place.

---

## Stale Batch Sources and Source Advancement

The initial batch source comes from the session-start snapshot, but later
selections may reference working-tree lines that no longer exist in the current
source coordinate space. That is the stale-source problem.

The authoritative repair path lives in
`src/git_stage_batch/batch/source_refresh.py`.

### When a source is considered stale

`detect_stale_batch_source_for_selection()` reports staleness when a selected
context or addition line cannot be expressed in source space, meaning its
`source_line` is `None`.

### How source advancement works

The stale-source repair path coordinates
`advance_batch_source_for_file_with_provenance()` in
`src/git_stage_batch/batch/ownership.py` with selection refresh in
`src/git_stage_batch/batch/source_refresh.py`:

1. reads the old source content
2. reads the current working-tree content
3. builds a new synthetic source that preserves already-owned presence lines,
   even if they have been removed from the working tree by earlier discard-to-
   batch operations
4. records provenance maps while building that source:
   - old source line -> advanced source line
   - working-tree line -> advanced source line
5. creates a new batch source commit from that advanced content
6. remaps existing ownership into the new source coordinate space
7. returns the working-tree provenance map so the selected lines can be
   re-annotated without matching synthesized source text again

This is a major detail the old document missed: advanced sources are not
necessarily equal to the live working tree. They can intentionally carry forward
already-owned lines that are absent from the current file.

The provenance maps are part of the safety model. Existing ownership is remapped
through the old-source map, while newly selected lines are re-annotated through
the working-tree map when that map is available. That avoids rediscovering line
identity from text in synthesized sources, especially when repeated lines would
make structural matching ambiguous.

### Session cache

Per-file source commits are cached for the active session in
`session-batch-sources.json`. `add_file_to_batch()` uses that cache so repeated
operations on the same file reuse the current source commit unless stale-source
repair advances it.

---

## Display, Attribution, and Hidden Changes

Two different mechanisms drive what the user sees:

1. batch display reconstruction
2. attribution-based filtering of the live diff

### Batch display reconstruction

`show --from <batch>` reconstructs a file's batch view from source content plus
ownership using `build_display_lines_from_batch_source()` in
`src/git_stage_batch/batch/display.py`.

For a single file, the command caches that reconstructed view as a selected
batch-file view and renders it through the page-aware file review output. For
multiple files, `show --from <batch>` prints a navigational file list instead
of caching one hidden selected file. Pathless or omitted-path actions after
that list refuse until the user opens a specific file with
`show --from <batch> --file <path>`.

The reconstructed single-file view contains:

- claimed lines
- deleted lines represented from deletion claims
- optional context
- ephemeral display IDs

Those display IDs are not source line numbers. They are UI identifiers used for
line-level batch operations. Batch display has two related line-ID spaces:

- mergeable gutter IDs, used by historical line actions when there is no fresh
  matching file review
- review gutter IDs, used by page-aware file reviews

The review gutter ID space can include resettable lines that are not currently
mergeable into the working tree. Review selections therefore persist
action-specific groups: `include --from`, `apply --from`, and `discard --from`
only accept groups that are mergeable for that action, while `reset --from` can
also accept reset-only groups.

Page-aware batch reviews also persist short-lived safety state in
`data.file_review_state`. That state records the batch name, file path, shown
pages, complete action-specific review selections, and fingerprints of the
selected batch view. Pathless line actions from batch commands, the
corresponding omitted-path `--file` forms, and explicit `--file <path> --line`
forms with a fresh matching review validate against this state so users cannot
accidentally act on unshown pages, stale display IDs, or a selection that is not
supported by the requested action. Whole-file batch actions may use the reviewed
file only after a fresh full-file review; partial reviews refuse until all pages
are shown or the file path is named explicitly.

This is an important separation of concerns:

- the display model is for presenting batch-owned content to the user
- the merge model is for satisfying ownership constraints against a target file

The display model creates ephemeral IDs and groups adjacent visible lines in a
way that is useful for selection. The merge model works in source-space
coordinates and structural alignment. They are related, but they are not the
same layer and should not be treated as interchangeable.

### Attribution for the live working tree

Live `show` filtering uses file-centric attribution built in
`src/git_stage_batch/batch/attribution.py`.

The attribution layer:

1. compares `HEAD:file` to the working tree
2. derives semantic change units
3. supplements that with stored batch-owned units that may no longer be visible
   in the working tree
4. determines which batches currently own which working-tree fragments
5. projects that ownership back onto rendered diff lines

This is how already-batched content can be hidden from the interactive staging
pass while still allowing the user to keep editing nearby code.

### Consumed selections

The same attribution machinery also incorporates hidden consumed selections
recorded during the current session in
`src/git_stage_batch/data/consumed_selections.py`.

Those hidden claims persist across `again` and participate in masking so the UI
does not keep surfacing changes the user already consumed.

---

## Semantic Units and Line-Level Batch Selection

Line-level batch operations are not raw line slicing. They are semantic-unit
selection derived from the reconstructed batch display described above.

### Ownership units

For text batches, `select_batch_ownership_for_display_ids()` uses
`build_ownership_units_from_display()` to group reconstructed display lines into
ownership units:

- `PRESENCE_ONLY`
- `REPLACEMENT`
- `DELETION_ONLY`

Grouping is based on adjacency in the reconstructed batch display, not simple
source-line proximity.

### Atomicity rules

`REPLACEMENT` and `DELETION_ONLY` units are atomic. If the user selects only part
of one, the command raises an `AtomicUnitError` and reports the required gutter
ID range.

This prevents:

- dropping the addition side of a replacement while leaving its deletion claim
- removing only part of a deletion block

That same semantic-unit model is reused by `reset --from` so line-level resets
cannot create orphaned deletion claims.

---

## Reset and Moving Claims Between Batches

`reset --from <batch>` removes claims from a batch. With `--to`, it moves those
claims into another batch instead of dropping them.

Implementation:

- `src/git_stage_batch/commands/reset.py`

Key behaviors:

- full-file resets remove that file from the batch
- line-level resets operate through ownership units, not raw line ranges
- `reset --from A --to B` requires compatible baselines
- moving claims between batches reuses the same batch source commit when
  possible, and refuses incompatible source mismatches

This keeps ownership moves lossless and prevents two batches from silently
interpreting the same line ranges in different source spaces.

---

## Sift

`git-stage-batch sift --from <source> --to <dest>` is not just "drop lines
already present at tip". It creates a new batch representation for the remaining
delta between the batch's target content and the current working tree.

Implementation:

- `src/git_stage_batch/commands/sift.py`
- `src/git_stage_batch/batch/comparison.py`

For text files, the sifted batch intentionally uses different persistence
semantics than ordinary batches:

- the synthetic batch source commit stores the target content directly
- the realized batch file also stores that target content directly
- ownership is then expressed in that target-content coordinate space so that
  merging the sifted batch with the current working tree reproduces the intended
  target

This is deliberate and validated. A sifted batch is still a valid batch, but
its source snapshot is synthetic rather than derived from the session-start file
snapshot.

The reason this matters is that sift is answering a different question from
ordinary batch creation.

Ordinary batch persistence starts from "what did the user save out of the
session's file snapshot?" Sift starts from "what part of this batch's intended
result is still missing from the current tree?" Once that becomes the problem,
persisting the target content directly is the simplest faithful representation.

So the sifted representation is different on purpose:

- it keeps only the still-needed portion of the batch
- it re-expresses ownership relative to that reduced target
- it preserves the guarantee that merging the sifted batch should reconstruct
  the intended remaining change

---

## Binary Files

Binary files are stored as atomic file changes.

Implementation:

- `add_binary_file_to_batch()` in `src/git_stage_batch/batch/storage.py`

There is no line-level ownership for binary files. A binary batch entry records:

- added / modified / deleted state
- source commit
- file mode

Commands can move or apply those files as whole units only.

---

## Important Invariants

The current implementation relies on these invariants:

1. Ownership is always interpreted in batch-source coordinate space.
2. The initial batch source is derived from the session-start snapshot, not from
   an arbitrary later working-tree state, except that files absent at session
   start use their current working-tree content.
3. Advanced batch sources may preserve already-owned lines that are absent from
   the live working tree.
4. When source content is synthesized with known provenance, remapping and
   selected-line re-annotation should treat those provenance maps as
   authoritative. Text matching is a fallback only when no provenance map is
   available.
5. The content ref and state ref must stay in sync.
6. Deletion claims are anchored structural constraints, not generic search and
   delete instructions.
7. Line-level batch operations must preserve semantic atomicity.
8. Merge and discard favor refusal over ambiguous structural guesses.

If any of these change, the command behavior and the safety model change with
them.

---

## Key Code Paths

Core batch state:

- `src/git_stage_batch/batch/state_refs.py`
- `src/git_stage_batch/batch/query.py`
- `src/git_stage_batch/batch/storage.py`
- `src/git_stage_batch/batch/operations.py`

Ownership and repair:

- `src/git_stage_batch/batch/ownership.py`
- `src/git_stage_batch/batch/source_refresh.py`
- `src/git_stage_batch/batch/selection.py`

Merge / discard engine:

- `src/git_stage_batch/batch/merge.py`
- `src/git_stage_batch/batch/match.py`

Display and attribution:

- `src/git_stage_batch/batch/display.py`
- `src/git_stage_batch/batch/attribution.py`
- `src/git_stage_batch/batch/comparison.py`

Session coupling:

- `src/git_stage_batch/data/session.py`
- `src/git_stage_batch/data/batch_sources.py`
- `src/git_stage_batch/data/batch_refs.py`
- `src/git_stage_batch/data/consumed_selections.py`

User-facing commands:

- `src/git_stage_batch/commands/include_from.py`
- `src/git_stage_batch/commands/apply_from.py`
- `src/git_stage_batch/commands/discard_from.py`
- `src/git_stage_batch/commands/reset.py`
- `src/git_stage_batch/commands/sift.py`

---

## Summary

The current batch system is a Git-backed, constraint-based persistence layer on
top of the interactive staging workflow. Its key design choices are:

- authoritative content and state refs under `refs/git-stage-batch/*`
- ownership stored in source-space, not as replayable patches
- session-start source snapshots with explicit stale-source advancement
- structural merge/discard with conservative failure modes
- attribution and semantic-unit filtering to keep the UI coherent as the working
  tree evolves

That is the model contributors should use when reasoning about bugs or adding
new batch features.
