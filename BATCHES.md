# Batch internals

This document explains the code that saves changes in named batches and later
applies, stages, displays, moves, or removes those changes.

Read the [codebase guide](ARCHITECTURE.md) first. Return to that guide if the
change concerns only whole-hunk staging, ordinary session progress, argument
parsing, terminal output, or an interactive menu. Those subjects do not require
a tour of the sizeable `src/git_stage_batch/batch/` package.

Read this document when changing:

- commands that use `--to` or `--from`
- `new`, `list`, `drop`, `annotate`, `validate`, `reset`, or `sift`
- storage below `refs/git-stage-batch/`
- saved-line ownership, batch display, merge, discard reversal, or source
  refresh
- filtering that hides already-saved changes from the live diff
- the temporary batch merge used by `include --line`

The implementation described here is spread across:

- `src/git_stage_batch/batch/` for saved-batch data and operations
- `src/git_stage_batch/commands/batch_source/` for applying actions to a saved
  batch
- `src/git_stage_batch/commands/selection/` and
  `src/git_stage_batch/commands/file_scope/` for saving live changes into a
  batch
- selected modules under `src/git_stage_batch/data/` for session recovery,
  file-review safety, and hiding changes already handled during a session

Paths named below are current places to start following a behavior. They do not
prescribe how many helper modules the package must contain. Adding or combining
a helper does not change the described design when storage, ownership, command,
and session responsibilities remain in the same packages.

## How the batch package is divided

Start with the package whose responsibility matches the change:

| Path | Responsibility |
| --- | --- |
| `batch/state/` | Batch names, stored metadata, references, lifecycle changes, validation, and read-only queries |
| `batch/source/` | Stable file snapshots, the session source cache, line annotation, source advancement, and stale-source refresh |
| `batch/ownership/` | Saved presence and absence requirements, replacement relationships, line translation, display lines, and selectable ownership units |
| `batch/line_matching/` | Exact line comparison, line mappings, sequence search, range views, and source lineage |
| `batch/realization/` | Intermediate entries used while constructing stored or merged file content, including their provenance and boundaries |
| `batch/merge/` | Structural placement, validation, candidate construction, and presence and absence constraints |

Modules directly under `batch/` coordinate these packages or own complete-file
storage, attribution, display preparation, discard, replacement, and selection.
They import the specific nested module they need. The package initializers do
not provide shorter aliases for implementation values.

## Terms used by the code

These names refer to different file contents. They are not interchangeable.

| Term | Exact meaning |
| --- | --- |
| **Named batch** | A saved set of changes identified by a user-supplied name |
| **Baseline** | The current commit, named `HEAD` by Git, when the batch is created. A repository without a commit uses Git's empty tree. |
| **Batch source** | A complete, stable snapshot of one file. Saved line numbers refer to this snapshot. The initial source normally comes from the session-start file. |
| **Current working tree** | The file on disk now. It may differ from both the baseline and the batch source. |
| **Batch ownership** | A `BatchOwnership` value containing the saved requirements for one text file |
| **Presence claim** | Batch-source lines that must be present after the saved change is applied |
| **Absence claim** | Baseline bytes that must be absent after the saved change is applied. The stored metadata key is `deletions`. |
| **Replacement unit** | Stored metadata that ties presence claims and absence claims together because they are the new and old sides of one replacement |
| **Ownership unit** | An `OwnershipUnit` value derived for display and selection. A replacement or deletion-only value must be selected in full. |
| **Realized batch content** | The complete file content produced from the baseline, batch source, and ownership. The source uses the word “realized” in names such as `realized_file_content.py`. |
| **Baseline reference** | Exact before-and-after baseline line positions and bytes recorded for a claim. Merge code uses them only when those positions and bytes still match. |
| **Attribution** | A calculated answer to which visible working-tree changes are already owned by which batches. It is recalculated; it is not stored as ownership. |
| **Applied overlay** | Worktree-local, identity-bound provenance that records ownership intentionally restored by `apply --from`. It makes only that applied ownership reviewable and is not canonical batch metadata. |
| **Display line identifier** | A number printed beside a changed line for a later user selection. It is not a batch-source line number. |
| **Merge candidate** | One numbered result calculated after ordinary merge cannot choose a single structural placement. A later command can name the reviewed result. |

The phrase **batch-source line number** below always means a one-based line
number in the stable batch-source file. It never means a displayed line
identifier or a current working-tree line number.

## One saved text change

Suppose the current commit contains:

```text
line1
line2
line3
```

The working tree then becomes:

```text
line1
line2-modified
line3
line4-new
```

The user saves the modification and new line into a batch. The program records:

- the current commit as the baseline
- a batch source containing `line1`, `line2-modified`, `line3`, and
  `line4-new`
- presence claims for the batch-source lines containing `line2-modified` and
  `line4-new`
- an absence claim for the baseline bytes containing `line2`
- a replacement unit tying `line2-modified` to the absence of `line2`, when
  the capture path can prove that relationship

The realized batch content is the baseline with those saved requirements
satisfied. If the working tree changes again, the ownership does not change to
use new working-tree line numbers. Later commands translate the stored
batch-source line numbers into the current file only when the surrounding
content provides a safe placement.

## Where a batch is stored

A Git reference is a named pointer to a Git object. Current batches use two
references:

- `refs/git-stage-batch/batches/<name>` points to a commit containing realized
  batch files.
- `refs/git-stage-batch/state/<name>` points to a commit containing validated
  metadata and embedded batch-source files.

[`batch/state/reference_names.py`](src/git_stage_batch/batch/state/reference_names.py)
defines those names. [`batch/state/references.py`](src/git_stage_batch/batch/state/references.py) reads,
writes, and deletes them.

The state commit contains:

- `batch.json`, which contains the batch name, schema version, revision, note,
  creation time, baseline, content reference, content commit, and per-file
  metadata
- `sources/<path>`, which contains the embedded batch source for each file that
  has one

The revision changes each time `sync_batch_state_refs()` publishes state. The
function compares the revision it read with the current state reference before
updating both references. If another writer changed the batch first, publication
fails instead of overwriting that newer state.

Historical `refs/batches/<name>` references and file-backed metadata are read as
migration input. A successful write through current code publishes both current
references and removes the historical storage for that batch.

### Per-file metadata

[`batch/state/metadata_schema.py`](src/git_stage_batch/batch/state/metadata_schema.py)
validates stored fields before the rest of the program uses them.
Schema version 2 adds source-alternative absence claims. Version 1 and
historical unversioned metadata are migrated in memory; the next successful
publication writes the current schema.
Because older schemas did not record whether an unowned source suffix was the
other side of an explicit transformed replacement, migration marks text
ownership with that uncertainty. Whole-file replay refuses a legacy claimed
insertion when it occupies a collapsed gap between mapped historical neighbors
and no saved boundary or replacement unit proves insertion semantics. A
reviewed line selection is the user's explicit confirmation to apply the shown
insertion. This prevents an unreviewed replay from silently preserving both
alternatives.

A text-file entry normally contains:

- `batch_source_commit`
- `source_path` in stored state
- `presence_claims`
- `deletions`
- `replacement_units` when a replacement relationship was recorded
- `mode`
- `change_type` only for a complete added or deleted path

An absence claim stores the removed bytes in a Git blob and records that blob's
object identifier. It does not repeat those bytes inline in `batch.json`.

Binary files, submodule pointers, and file mode changes use separate file types
and store only fields applicable to the complete file change. They do not use
text line ownership.

## How a new batch is created

`git-stage-batch new <name>` reaches
[`batch/state/lifecycle.py`](src/git_stage_batch/batch/state/lifecycle.py):

1. `create_batch()` validates the name and refuses an existing batch.
2. It resolves the current commit. A repository without a commit uses Git's
   empty tree.
3. It writes initial metadata with an empty `files` mapping.
4. It creates a content commit from the baseline tree.
5. `sync_batch_state_refs()` publishes the content and state references.

The same module owns deletion and note updates through `delete_batch()` and
`update_batch_note()`. Read-only listing and metadata lookup live in
[`batch/state/query.py`](src/git_stage_batch/batch/state/query.py).

## How session recovery includes batch changes

A staging session includes batch references in its recovery state.

- [`data/session.py`](src/git_stage_batch/data/session.py) calls
  `snapshot_batch_refs()` when a session starts.
- [`data/batch_refs.py`](src/git_stage_batch/data/batch_refs.py) records every
  current batch content and state reference. `abort` restores those references,
  restores a dropped batch, and removes a batch created after session start.
- [`data/undo/checkpoints.py`](src/git_stage_batch/data/undo/checkpoints.py)
  records references changed by one command. `undo` and `redo` restore only the
  command checkpoints they traverse.

An action that changes a batch must run inside the existing command checkpoint
flow. `abort` and `undo` serve different scopes: `abort` returns all batches to
session-start state, while `undo` reverses one recorded operation.

Checkpoint trees use Git modes for stored blobs, while compatible manifest
records also retain each regular file's exact Unix permission bits. Automatic
rollback, undo, and redo therefore restore permissions such as `0600` for
worktree files and scoped session, batch, and repository metadata. Checkpoints
written before that field existed remain readable and fall back to their saved
Git mode.

Batch-source commands that publish worktree state use `transaction_checkpoint()`.
An active session receives the normal undo node; outside a session, a uniquely
referenced Git snapshot exists only for the publication and is removed after
success or completed rollback. The checkpoint starts unarmed: plans verify
their captured worktree and relevant full index identities once before the
snapshot and again inside it. A full index identity includes the stage-zero
mode and object, the intent-to-add bit, and every stage 1/2/3 conflict entry.
Checkpoint creation refuses a declared path that is already unmerged; an
unmerged or other index change that races either identity check is stale and is
left untouched. The caller arms rollback immediately before its first
mutation, so a stale second check also preserves the external edit it detected.

Nested transactions share the publication arm state and outermost success
boundary. Each nested context separately records whether it reached its own
publication boundary, so a caught inner refusal before its first write does not
poison an already-armed outer operation. Output, review completion, and other
success-only effects registered through the shared boundary run only after the
outer transaction and its durable checkpoint have committed. A refusal or
rollback drops them, including when an inner operation returned successfully
before a later sibling failed. Deferred plans and their mapped workspaces close
before that commit; a teardown failure therefore rolls publication back instead
of reporting failure after leaving changed files behind. Transient snapshots
retain only the declared index paths and repository-state files alongside their
worktree scope.

## How `include --to` saves a live change

For `git-stage-batch include --to <name>`, execution crosses the following
modules:

1. [`cli/selection_subcommands.py`](src/git_stage_batch/cli/selection_subcommands.py)
   declares `--to`.
2. [`cli/include_dispatch.py`](src/git_stage_batch/cli/include_dispatch.py)
   resolves file scope and calls `command_include_to_batch()`.
3. [`commands/include.py`](src/git_stage_batch/commands/include.py) validates
   the repository, batch name, and file-review scope.
4. [`commands/selection/include_to_batch_action.py`](src/git_stage_batch/commands/selection/include_to_batch_action.py)
   opens an undo checkpoint and routes text, binary, deletion, submodule
   pointer, file mode, file-scoped, and selected-line forms to their owners.
5. A text selection reaches
   `acquire_batch_ownership_update_for_selection()` in
   [`batch/ownership_update.py`](src/git_stage_batch/batch/ownership_update.py).
   That function refreshes a stale source when necessary, translates selected
   lines into ownership, and combines new ownership with existing ownership for
   the file.
6. `add_file_to_batch()` in
   [`batch/text_file_storage.py`](src/git_stage_batch/batch/text_file_storage.py)
   obtains the baseline and source files, builds realized content, updates the
   content tree, writes metadata, and publishes both current references.
7. The command records the live hunk as handled and selects the next change
   when automatic advancement is enabled.

Whole-file binary, submodule pointer, and file mode changes use their specific
storage modules instead of `text_file_storage.py`.

`discard --to <name>` records the same saved ownership but also removes the
selected content from the working tree. Its command path lives in the matching
discard modules under `commands/selection/` and `commands/file_scope/`.

## How text ownership is stored

[`batch/ownership/model.py`](src/git_stage_batch/batch/ownership/model.py) defines
`BatchOwnership` with three fields:

- `presence_claims`: source line ranges and optional baseline references
- `deletions`: separate `AbsenceClaim` values containing an anchor and exact
  old-side bytes
- `replacement_units`: optional links between presence ranges and entries in
  `deletions`

An absence anchor is either a batch-source line after which the baseline bytes
were removed or `None` for the beginning of the file. The anchor is a placement
boundary, not an instruction to search the whole file and delete the first
matching text.

The metadata key remains `deletions` for compatibility. Code that loads it
constructs `AbsenceClaim` values because the stored requirement is “these exact
baseline bytes must be absent at this boundary.”

[`batch/ownership/hunk_translation.py`](src/git_stage_batch/batch/ownership/hunk_translation.py)
and [`batch/ownership/translation.py`](src/git_stage_batch/batch/ownership/translation.py)
translate selected displayed lines into this stored form. The hunk translator
can use the complete old and new replacement run so it does not decide
replacement membership from display adjacency alone.

## How the realized batch file is built

`build_realized_buffer_from_lines()` in
[`batch/realized_file_content.py`](src/git_stage_batch/batch/realized_file_content.py)
receives:

1. the baseline bytes
2. the batch-source bytes
3. `BatchOwnership`

It selects every claimed source line, removes baseline sequences described by
applicable absence claims, and preserves the source line-ending style. The
result becomes the file stored in the batch content commit.

Building stored content is less strict than merging into a current working
file. If an absence claim does not match its expected boundary while the stored
view is being built, the builder leaves the non-matching bytes alone. The
stored view describes what the batch owns. A later merge separately decides
whether that ownership can be placed safely in a current file.

## How a saved batch is displayed

`show --from <name>` reconstructs visible changes from the batch source and
ownership. It does not use the realized batch file as a ready-made patch.

The main path is:

1. [`commands/show_from.py`](src/git_stage_batch/commands/show_from.py) resolves
   batch and file scope.
2. [`batch/ownership/display_lines.py`](src/git_stage_batch/batch/ownership/display_lines.py) builds changed
   lines from source content and ownership.
3. [`batch/file_display_model.py`](src/git_stage_batch/batch/file_display_model.py)
   prepares the complete review model for one file.
4. Modules under `data/file_review/` save the batch name, file path, shown
   pages, permitted complete selections, and comparison values used to detect
   whether the view later changed.
5. Modules under `output/` render that prepared review.

Displayed line identifiers exist only to refer back to this saved view. Batch
operations translate them to complete ownership units before changing stored
ownership or applying a batch.

A multi-file `show --from` prints a file list and clears the selected file. The
user must open a specific file before a later pathless action. A partial
single-file review permits only action groups completely shown on the selected
pages. These checks prevent an identifier from acting on an unseen or stale
line.

## How `include --from` and `apply --from` add saved changes

`include --from <name>` changes both the index and working tree.
`apply --from <name>` changes only the working tree.

Their command modules are:

- [`commands/include_from.py`](src/git_stage_batch/commands/include_from.py)
- [`commands/apply_from.py`](src/git_stage_batch/commands/apply_from.py)

Both commands use modules under `commands/batch_source/` to:

1. resolve the batch name or reviewed candidate
2. resolve file and displayed-line scope
3. reject a stale review or an incomplete ownership unit
4. build one action plan for each selected file
5. ask the batch merge code for target file content
6. write accepted targets
7. refresh review and selected-change state

pre-existing conflict or an index/worktree change introduced after capture is
therefore refused without overwriting concurrent state. Their review-complete
state and success output are deferred until the outermost transaction commits,
so a nested success cannot survive an enclosing rollback.
For text files, [`batch/merge/merge.py`](src/git_stage_batch/batch/merge/merge.py) receives
the batch source, ownership, and current target bytes. It tries to satisfy the
presence and absence requirements against that target.

### Checks before merge writes content

The merge code requires enough exact structure to place every missing claimed
line and every absence claim:

- an already-present claimed line may remain where it is
- a missing claimed run needs surrounding mapped content or an exact recorded
  baseline boundary
- an absence anchor must still identify the intended structural boundary
- a replacement fallback requires the recorded baseline bytes at the recorded
  baseline position
A source-alternative replacement uses the same exact-boundary discipline, but
its recorded old bytes and boundary come from the post-discard live target. The
old side must still match there (or at its sole complete relocated identity)
before the owned source side replaces it. Ordinary presence claims never gain
permission to remove an adjacent unowned source line.




Recorded baseline coordinates are the first placement proof, not permanently
fixed worktree offsets. Earlier replay may insert or remove neighboring lines.
When that happens, a legacy replacement or deletion anchor may follow the sole
live occurrence of its complete saved after/deleted-span/before identity. An
independent deletion that is also visible as unowned historical source text
needs the stronger proof that its complete span and both boundaries map
byte-for-byte from the current index to the worktree. When another selected
change already requires a source-to-target mapping, an independent deletion may
instead use the exact gap between its mapped surviving source anchors. The gap
must contain the complete saved old side or none of it; matching bytes elsewhere
are unrelated and remain untouched. Mapped deletion gaps selected together must
not overlap, so each target region is inspected at most once.

When a complete replacement's historical old bytes were transformed by a
later commit or staged change, `apply --from` may instead use the current index
as a trusted target. The replacement is accepted only when the immediate source
boundaries map to the same index and worktree boundaries and every worktree line
inside that span maps unchanged from the index. An unstaged edit inside the span
therefore still causes refusal. The owned children may end immediately before
an unchanged line that belonged to the historical parent: the mapped source
neighbors must then bracket exactly the deletion-sized interior gap, and the
deletion references must cover that gap consecutively. This proof scans the
source, index, and worktree spans once and retains only compact ranges in mapped
storage. Consecutive split children that satisfy the complete-parent proof only
together are exposed as one atomic review selection.

Older replacement units without parent-origin metadata can use the same trusted
target proof when a duplicate historical source alternative hides an immediate
source boundary. Their saved after/span-length/before context must identify one
live span, at most a small bounded number of context candidates are inspected,
and that span plus both boundaries must map unchanged from the index. This is a
runtime compatibility proof: it does not rewrite the batch metadata.

A batch may own only one child of a historical parent whose unowned sibling was
already committed and reordered. In a multi-unit replay, the coordinate planner
may remove that child's exact old bytes and insert its new bytes separately when
the index and worktree agree on the complete parent, a unique line proves the
old child's location inside that bounded parent, and the immediate mapped source
neighbors agree on one insertion boundary. A lone reordered child remains a
reviewed candidate rather than silently changing the existing review workflow.
[`batch/merge/validation.py`](src/git_stage_batch/batch/merge/validation.py)
owns structural validation. The merge helpers separate these structural and
exact-coordinate responsibilities:

- [`batch/merge/coordinate_strategy.py`](src/git_stage_batch/batch/merge/coordinate_strategy.py)
  detects whether a merge has recorded positions and defines the reviewed
  choice between using those positions and structural content matching.
- [`batch/merge/baseline_anchor_matching.py`](src/git_stage_batch/batch/merge/baseline_anchor_matching.py)
  proves recorded boundaries identify the intended live-target positions and
  supplies safe deletion anchors to structural matching.
- [`batch/merge/baseline_replacement_edits.py`](src/git_stage_batch/batch/merge/baseline_replacement_edits.py)
  plans replacement edits at verified baseline positions and records which
  source ranges supply their new content.
- [`batch/merge/baseline_removal_edits.py`](src/git_stage_batch/batch/merge/baseline_removal_edits.py)
  plans removals that are not part of replacement units and detects when
  claimed content is already absent.
- [`batch/merge/baseline_presence_edits.py`](src/git_stage_batch/batch/merge/baseline_presence_edits.py)
  plans insertions for required source lines at recorded baseline positions
  and checks that structurally matched lines survive other planned edits.
- [`batch/merge/baseline_edits.py`](src/git_stage_batch/batch/merge/baseline_edits.py)
  coordinates the separate replacement, removal, and insertion plans and opens
  the resulting edit stream.
- [`batch/merge/absence_constraints.py`](src/git_stage_batch/batch/merge/absence_constraints.py)
  resolves which matching baseline bytes an absence claim may suppress.
- [`batch/merge/presence_constraints.py`](src/git_stage_batch/batch/merge/presence_constraints.py)
  places missing claimed content.

If the required boundary is missing or several placements are possible, the
operation refuses. It does not choose the first similar text. That refusal
prevents saved content from being inserted into or removed from the wrong part
of a file.

Applying a batch to an unchanged copy of its batch source also does not add the
same content twice. The merge checks whether claimed content and required
absences are already satisfied before writing.

## How `discard --from` removes saved changes

`discard --from <name>` removes the selected batch's effect from the working
tree. Text, binary, and mode actions do not change the index. Restoring the
absence that preceded an added submodule pointer also removes that pointer's
intent-to-add index entry, so that exact index path participates in validation
and rollback.

This removal accepts only an absent entry or the existing intent-to-add
sentinel. It refuses both independently staged content and unmerged index
stages, rather than treating either state as an expendable empty entry.

[`commands/discard_from.py`](src/git_stage_batch/commands/discard_from.py) uses
the same action-context, selection, planning, refusal, and completion modules as
the other batch-source actions. Text reversal reaches the discard functions
under `batch/`.

An explicit multi-file scope remains one ordered literal command, rather than
running one independently checkpointed command per path. Discard captures every
selected worktree target (and immutable text input artifact), builds every text,
binary, mode, and submodule plan, and reports any planning failure before it
opens the transaction. It then repeats the identity checks inside the unarmed
checkpoint and publishes the plans in order. A later exception or cancellation
rolls back every earlier publication both inside and outside a session.

The reversal code compares the batch source with the baseline. It determines
whether each region is unchanged, inserted, a line-for-line replacement, or a
replacement that must be handled as one complete hunk. The implementation uses
the enumeration values `EQUAL`, `INSERT`, `REPLACE_LINE_BY_LINE`, and
`REPLACE_BY_HUNK` for those cases.

Start with these files when following that reversal:

- [`batch/merge/baseline_correspondence.py`](src/git_stage_batch/batch/merge/baseline_correspondence.py)
  maps batch-source regions back to baseline regions.
- [`batch/discard_reversal.py`](src/git_stage_batch/batch/discard_reversal.py)
  reverses presence requirements.
- [`batch/realization/boundaries.py`](src/git_stage_batch/batch/realization/boundaries.py)
  checks exact boundaries in the current realized sequence.

When selectable children divide one historical replacement, consecutive
children may share the same source anchor. Reversal restores those absence
claims in recorded order at successive offsets from that anchor. The queued
insertions remain in mapped storage and are materialized through one line
buffer, avoiding repeated whole-result rebuilding and line-scale Python
objects.



Reversal restores a replacement's old side only when at least one selected
new-side line actually maps into the working tree. Thus a whole-file discard can
remove a partially replayed batch without inserting historical old content for
replacement units that were never replayed. Deletion-only claims are unaffected.

For a complete replacement that apply proved was overwriting an index-matched
worktree span, the applied overlay also records its compact source-line range.
If the overlay, index, worktree boundaries, and exact applied source lines are
still fresh at discard time, reversal restores the exact current-index span.
This preserves committed or staged transformations of the historical old side
instead of reconstructing stale bytes from the batch's older deletion claim.
The in-memory overlay view also derives every application's selected presence
ranges from its already stored compact attribution claims. If a selected line
was a no-op because the identical source line already existed in the captured
index, discard preserves it only when source-to-index, source-to-worktree, and
index-to-worktree mappings identify the same line and all three byte sequences
agree. The selected ranges are reconstructed rather than duplicated. One
optional application boolean records that the overlay is still bound to its
original apply-time index target; index drift or session rebinding removes that
authority. This changes no canonical batch metadata.
The same refusal rule applies: when the current file no longer provides one
safe reversal, the command stops without guessing.

## What happens when the working file changes

Saved ownership uses batch-source line numbers. A later selection may contain a
working-tree line that has no corresponding line in that older source. In the
selected `LineEntry`, that condition appears as `source_line is None`.

`ensure_batch_source_current_for_selection()` in
[`batch/source/refresh.py`](src/git_stage_batch/batch/source/refresh.py) handles
that condition:

1. It reads the old batch source and current working file.
2. [`batch/source/advancement.py`](src/git_stage_batch/batch/source/advancement.py)
   constructs a new source. It preserves previously claimed lines even when an
   earlier `discard --to` removed them from the working file.
3. While constructing that source, it records two exact line maps: old source
   line to new source line, and working-tree line to new source line.
4. It creates a new source commit.
5. It remaps existing ownership with the old-source map.
6. It annotates the new selection with the working-tree map.
7. It updates the active session's per-file source cache.

The constructed source is therefore not always identical to the working file.
The two maps record where every carried or current line came from. Text matching
is used only when the construction path did not provide one of those maps.

Initial source loading, storage, and caching are split across:

- [`batch/source/buffers.py`](src/git_stage_batch/batch/source/buffers.py) for
  session-start file buffers
- [`batch/source/snapshots.py`](src/git_stage_batch/batch/source/snapshots.py)
  for source commits
- [`batch/source/cache.py`](src/git_stage_batch/batch/source/cache.py) for the
  active session's per-file source commit mapping

A file absent at session start uses its current working-tree content for the
initial source so every newly claimed line exists in that source.

## Why some displayed lines must be selected together

Line-level batch actions do not remove arbitrary individual metadata rows.
[`batch/ownership/units.py`](src/git_stage_batch/batch/ownership/units.py)
builds `OwnershipUnit` values from the reconstructed display. The code records three
kinds in [`batch/ownership/unit_types.py`](src/git_stage_batch/batch/ownership/unit_types.py):

- `PRESENCE_ONLY`: claimed source lines with no coupled absence claim
- `REPLACEMENT`: claimed source lines coupled to one or more absence claims
- `DELETION_ONLY`: one or more absence claims with no claimed source line

A replacement or deletion-only ownership unit must be selected in full. Partial
selection would leave only one side of a replacement or only part of one
removed baseline sequence. The command reports the full displayed identifier
range that must be selected.

`reset --from` uses the same ownership units, so removing ownership cannot leave a saved
absence without its coupled presence or leave a partial deletion claim.

## How live changes are hidden after they are saved

The live `show` command must avoid presenting changes already owned by a batch.
[`batch/attribution.py`](src/git_stage_batch/batch/attribution.py) calculates
that answer for one file:

1. compare the baseline file with the current working file
2. divide that comparison into changed regions
3. load saved ownership that can affect the file
4. determine which batch owns each visible changed region
5. project the answer onto displayed diff lines

[`batch/attribution_projection.py`](src/git_stage_batch/batch/attribution_projection.py)
owns the final projection onto line entries. Selected-change filtering under
`data/selected_change/` removes fully owned lines or hunks before display.

The same calculation includes consumed selections from
[`data/consumed_selections.py`](src/git_stage_batch/data/consumed_selections.py).
Those records hide changes already handled during the current session even when
they are not persistent named-batch ownership.

It also includes applied overlays from
[`data/applied_batch_overlays.py`](src/git_stage_batch/data/applied_batch_overlays.py).
Ordinarily, named ownership hides an equivalent live change. `apply --from`
records the exact selected attribution claims as an overlay so the intentionally
restored change remains available to a later fresh `start` and `include --files`
pass. Merge-only baseline references and replacement-unit coupling are omitted;
presence line ranges and deletion blob/anchor pairs are sufficient to identify
the visible ownership.
The overlay lives in `.git/git-stage-batch/applied-batch-overlays.json`; it does
not modify the batch state ref or `batch.json`.

An applied overlay is accepted only while all of these still match its record:

- the batch metadata revision
- `HEAD`
- the path's index entry (an absent entry and `start`'s intent-to-add sentinel
  are equivalent)
- the exact worktree path kind, mode, size, and streamed content digest

Any external worktree edit, pre-session staging operation, commit, or batch
mutation makes the old overlay inapplicable. Staging part of a fresh overlay
through git-stage-batch during an active session keeps its remaining ownership
reviewable; `stop` binds the overlay to the final index state. Multiple
consecutive `apply --from` operations on the same unchanged target carry forward
the still-fresh ownership, so layered recomposition remains reviewable as a
whole. Active-session undo checkpoints and abort recovery include the overlay
file.

When a fresh `start` finds only changes hidden by ordinary named-batch
ownership, it reports the sorted batch names instead of the generic “No changes
to process” diagnostic. Applied-overlay ownership remains reviewable, so it does
not appear in that list.

Ownership and attribution answer different questions:

- ownership records what a batch saved in batch-source line numbers
- attribution calculates which current visible changes satisfy that saved
  ownership now
- an applied overlay records that the user intentionally restored particular
  ownership and wants it visible for the exact current repository state

## Why `include --line` creates a temporary batch

Live selected-line inclusion uses the same ownership and merge checks without
leaving a named batch behind.

[`commands/selection/include_line_selection.py`](src/git_stage_batch/commands/selection/include_line_selection.py):

1. creates a uniquely named temporary batch
2. translates the complete live hunk and selected displayed line identifiers
   into ownership
3. asks `batch/merge/merge.py` to apply that ownership to the current index content
4. asks the same merge code to apply it to the current working-tree content
5. accepts the index result only when the working-tree result is byte-for-byte
   unchanged
6. deletes the temporary batch and restores the session source cache before
   returning

This path ensures that selected-line staging and named-batch merge agree about
replacements and structural placement. Whole-hunk inclusion does not use this
path.

## How ownership is removed or moved

`reset --from <source>` removes selected ownership from a batch.
`reset --from <source> --to <destination>` moves it to another batch.

[`commands/reset.py`](src/git_stage_batch/commands/reset.py) owns the command
sequence. Selection uses the complete ownership units described above.

- A complete-file reset removes that file from the source batch.
- A selected-line reset removes complete ownership units.
- A move requires compatible baselines.
- A move reuses the same batch source when possible and refuses incompatible
  source files.

These checks prevent one saved line number from being interpreted against two
different source files.

## How `sift` rewrites a batch for the current working tree

`sift --from <source> --to <destination>` keeps only the portion of a saved
batch still missing from the current working tree.

The command starts in
[`commands/sift.py`](src/git_stage_batch/commands/sift.py). Comparison lives in
[`batch/line_matching/comparison.py`](src/git_stage_batch/batch/line_matching/comparison.py).
Persistence of
the result is split between
[`commands/batch_transform/sift_results.py`](src/git_stage_batch/commands/batch_transform/sift_results.py)
and [`commands/batch_transform/sift_persistence.py`](src/git_stage_batch/commands/batch_transform/sift_persistence.py).

For a text file, the destination batch uses constructed source content equal to
the saved target content. Ownership is expressed in that constructed source's
line numbers. This differs from ordinary capture, whose initial source normally
comes from the session-start file.

The difference is required by the command's input:

- ordinary capture asks which live change the user selected
- `sift` asks which part of an existing saved result is not present now

Merging the destination batch with the current working file must produce the
still-missing target content.

## Complete-file changes

Text line ownership does not represent every Git change.

- [`batch/binary_file_storage.py`](src/git_stage_batch/batch/binary_file_storage.py)
  stores binary additions, modifications, and deletions as complete-file
  changes.
- [`batch/gitlink_storage.py`](src/git_stage_batch/batch/gitlink_storage.py)
  stores submodule pointer changes.
- [`batch/file_mode_storage.py`](src/git_stage_batch/batch/file_mode_storage.py)
  stores executable-mode changes.
- [`batch/file_entry_storage.py`](src/git_stage_batch/batch/file_entry_storage.py)
  copies and removes generic stored entries.

Line options cannot select part of these changes.

## Where to make a batch change

| Change | Owning code |
| --- | --- |
| Create or delete a batch, or update its note | `batch/state/lifecycle.py` |
| Validate a batch name | `batch/state/batch_names.py` |
| List batches or read metadata | `batch/state/query.py` |
| Validate or encode stored metadata | `batch/state/metadata_schema.py` |
| Publish or delete Git references | `batch/state/references.py` |
| Persist text content and ownership | `batch/text_file_storage.py` |
| Persist binary, submodule pointer, or mode content | The matching `*_storage.py` module |
| Build the stored text file | `batch/realized_file_content.py` |
| Translate a live selection into ownership | `batch/ownership/hunk_translation.py` or `batch/ownership/translation.py` |
| Combine a new selection with stored ownership | `batch/ownership_update.py` and `batch/ownership/merging.py` |
| Refresh an old source | `batch/source/refresh.py` and `batch/source/advancement.py` |
| Display a saved file | `batch/ownership/display_lines.py` and `batch/file_display_model.py` |
| Apply saved text to a current target | `batch/merge/merge.py` and the validation and constraint helpers it calls |
| Remove saved text from a current working file | The correspondence, reversal, and boundary modules named in the discard section |
| Decide which lines must be selected together | `batch/ownership/units.py` and the ownership-unit support modules |
| Hide already-saved live changes | `batch/attribution.py` and `batch/attribution_projection.py` |
| Coordinate an action from a saved batch | `commands/batch_source/` |
| Move or remove ownership | `commands/reset.py` plus ownership modules |
| Rewrite only the still-missing saved result | `commands/sift.py`, `commands/batch_transform/`, and `batch/line_matching/comparison.py` |

Do not put session storage in `batch/`. The architecture test
`test_batch_package_stays_below_workflow_data` requires `batch/` not to import
`data/`. Command modules may coordinate both packages.

## Add or remove a batch feature

For a new user-visible batch behavior:

1. Add parser options under `cli/batch_subcommands.py` for a batch command, or
   under `cli/selection_subcommands.py` for a new `--from` or `--to` form.
2. Add or update the command entry under `commands/`. Use
   `commands/batch_source/` when the operation consumes a saved batch and needs
   shared context, selection, planning, refusal, or completion behavior.
3. Change `batch/` only when the stored representation, source translation,
   ownership, display, comparison, merge, or reversal rule changes.
4. If stored metadata changes, update schema validation and migration before
   writing the new field. Keep older stored batches readable or reject them
   with an explicit version error.
5. Update the manual page, `docs/batches.md`, `docs/commands.md`, shell
   completion, and interactive batch menus when they expose the behavior.
6. Add focused tests under `tests/batch/`, command tests under
   `tests/commands/`, and a functional test when the behavior spans several
   invocations or recovery operations.

When removing a batch behavior, remove interactive and parser callers first,
then the command sequence, then unreferenced batch functions. Do not remove a
metadata reader merely because current code no longer writes that field; stored
batches may still contain it. Search manual pages, website documentation,
completion, assistant assets, state migration, and tests for both the command
spelling and the stored field name.

## Rules protected by tests

The implementation and architecture tests rely on these rules:

1. Every text ownership line number refers to the file's batch source.
2. The initial batch source comes from the session-start file, except for a file
   that did not exist then.
3. A refreshed source may retain previously claimed lines that are no longer in
   the working tree.
4. When source construction returns exact origin maps, remapping uses those
   maps. Text matching is only a fallback when a map is unavailable.
5. The content reference and state reference are published as one checked
   update.
6. An absence claim applies at its stored structural boundary, not at the first
   equal text anywhere in the file.
7. A replacement or deletion-only ownership unit cannot be partly selected.
8. Merge and discard stop when one safe placement or reversal cannot be
   established.
9. Modules under `batch/` do not import workflow storage from `data/`.
10. Imports among modules under `batch/` do not form a cycle.

Run the focused architecture and batch tests with:

```console
uv run pytest -n auto tests/architecture/test_import_boundaries.py tests/batch
```

Run affected command and functional tests whenever a change crosses the
`batch/` boundary into user-visible behavior.
