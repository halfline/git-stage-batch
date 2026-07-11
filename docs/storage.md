# Storage and Recovery Refs

Git-stage-batch stores durable batch state under `refs/git-stage-batch/` and
worktree-local session scratch files below the worktree's Git directory.

## Object identifiers are not reachability roots

Session manifests and abort snapshots serialize Git object IDs so state can be
restored later. An object ID written into JSON is not an edge in Git's object
graph. If the batch ref that previously named that object moves or is deleted,
reflog expiration followed by garbage collection may otherwise prune it.

During an active session, git-stage-batch creates internal refs below
`refs/git-stage-batch/session/anchors/`. Each ref names a commit, tree, or blob
that an undo, redo, or abort operation promises to restore. Checkpoint stack
refs describe undo and redo order; anchor refs provide reachability. Those are
separate responsibilities.

Gitlink entries name commits in a submodule's separate object database. They
cannot be rooted by refs in the superproject; their availability remains the
responsibility of the nested repository.

Anchors are created before a checkpoint is published or a command can mutate
the protected refs. They remain for the session lifetime so every checkpoint
still present on either stack remains recoverable. A successful `stop` or
`abort` removes the complete anchor namespace. Reclaiming a stale linked-
worktree owner also removes the abandoned session's anchors.

Older checkpoints may not contain recorded anchor metadata. Git-stage-batch
attempts to restore them while their serialized objects remain available. If
an object has already been pruned, restoration stops before mutation and
reports the missing recovery object rather than partially applying the
checkpoint.

## Scoped undo checkpoints

Each mutating command declares the repository paths it reads or writes. Undo
manifests record only those worktree paths and index entries. Once an operation
finishes, its checkpoint is also reduced to the session files, batch metadata,
and refs that actually changed. Unrelated dirty or staged files and unrelated
application metadata are not copied into the finalized checkpoint. Regular
files in one scope share bulk index, HEAD, and object writes, so checkpoint
process count does not grow with unrelated worktree dirtiness.

Undo and redo restore scoped index entries individually instead of replacing
the complete index, and restore only changed application-state paths and batch
refs. A later change outside the command scope remains in place. Changes to a
scoped path or ref still cause the default safety refusal, and `--force`
overwrites only state owned by that checkpoint. Legacy whole-index and
whole-state checkpoints remain readable for sessions created by older
versions.

## Atomic file permissions

Git-stage-batch writes session metadata, recovery manifests, batch
compatibility metadata as private application state. Newly created state files
use mode `0600`, including when the process has a permissive umask. Rewriting
private state also restores that restrictive mode.

Repository-owned files use a separate atomic-write policy. Updates to
`.gitignore`, `.git/info/exclude`, and previously installed assistant assets
preserve the existing file's permission bits and ownership where the platform
allows it. New repository files use the conventional mode `0644`. If ownership
cannot be restored, replacement permissions are narrowed rather than granting
access through a different group.

Atomic replacement writes and syncs a temporary file in the destination
directory before renaming it over the target, then syncs the directory where
the platform supports that operation. A failure before replacement leaves the
old file complete. Symlink targets are never followed or silently replaced;
the command stops with recovery guidance so callers can update the intended
target explicitly.

## Diagnostic journals

Diagnostic journaling is disabled by default, so ordinary commands do not
inspect the Python stack, serialize journal entries, open journal files, or run
extra Git queries for diagnostics. Set `GIT_STAGE_BATCH_JOURNAL` to one of the
following levels when investigating a problem:

- `metadata-only` records structured operation names, stable source IDs,
  object IDs, modes, sizes, and hashed path identifiers.
- `verbose` adds a bounded stack for each event. Error events also include a
  bounded stack at the metadata level.
- `content-debug` additionally records raw paths, Git command output, and
  short content previews. This level can expose repository content and should
  only be enabled for a limited reproduction.
- `disabled` turns journaling off explicitly.

The historical `GIT_STAGE_BATCH_DEBUG` switch selects `verbose` for
compatibility; it does not enable raw content capture.

Journal files are stored under
`$XDG_STATE_HOME/git-stage-batch/journals/`, or
`~/.local/state/git-stage-batch/journals/` when `XDG_STATE_HOME` is unset. The
filename contains a stable hash of the repository identity rather than its
path. The journal directory uses mode `0700` and files use mode `0600`.
`GIT_STAGE_BATCH_JOURNAL_PATH` can override the destination for a controlled
debugging environment.

Entries are queued in a bounded process buffer. The buffer flushes when it
reaches 64 KiB, at each interactive action boundary, and when a CLI command
exits. A process terminated without normal cleanup can lose entries since the
last boundary; journaling never changes the durability of repository state.
Writers use a per-journal lock so concurrent processes append complete JSON
lines.

The active file rotates at 5 MiB, retains at most three rotated files, and
expires journal files after 30 days. Use `GIT_STAGE_BATCH_JOURNAL_MAX_BYTES`
and `GIT_STAGE_BATCH_JOURNAL_RETENTION_DAYS` to adjust those limits. Run
`git-stage-batch journal` for a content-free summary,
`git-stage-batch journal --path` to locate the file, or
`git-stage-batch journal --purge` to remove it. Add `--all` to purge data for
all repositories. The disabled/event-heavy paths can be compared with
`scripts/benchmark_journal.py` from a source checkout.

## Batch metadata schema

Authoritative `batch.json` records use a versioned schema. Schema version 1
stores an opaque revision identifier, batch identity, timestamps, baseline and
content object IDs, and validated per-path metadata. Writers emit only the
canonical current schema, while readers migrate the historical unversioned
shape in memory before exposing it to batch operations.

An unversioned file-backed record is copied to `metadata.v0.json` before its
first durable rewrite. Successful publication stores the canonical metadata in
the state ref, whose parent retains the previous state-ref version, and removes
the compatibility directory. Failed publication leaves the recovery copy for
inspection. Metadata from a newer unsupported schema is never rewritten.

Run `git-stage-batch validate` to validate every batch without changing it. The
command checks schema compatibility, object IDs, content-ref agreement, and
reports whether a legacy record would be migrated. Use `--porcelain` for a
stable JSON report suitable for support tooling.

## Attribution working set

During hunk review, batch ownership is indexed by canonical file path for the
current diff scan. Files without claims skip traversal across unrelated batch
metadata. For a claimed file, source and deletion objects are requested once
per role through bounded Git batch readers. Refspecs are resolved before
content is loaded, so canonical state refs and legacy fallbacks that name the
same blob share one source read and one line mapping. Deletion payloads are
reduced to fingerprints as they stream, and normalized presence claims are
computed once per batch/file key.

Attribution processes one file and one unique source mapping at a time. It
retains compact fingerprints and result units for the file, but releases each
source payload and mapping before opening the next source. Traversal is sorted
by batch name so optimization and metadata insertion order cannot alter
ownership arbitration. Missing deletion objects and objects of the wrong Git
type are ignored conservatively rather than hiding an unverified change.
