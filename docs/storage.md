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

## Atomic file permissions

Git-stage-batch writes session metadata, recovery manifests, batch
compatibility metadata, and journals as private application state. Newly
created state files use mode `0600`, including when the process has a
permissive umask. Rewriting private state also restores that restrictive mode.

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
