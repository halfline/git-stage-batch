---
name: decompose-batch-peeler
description: "Single-batch agent for decompose-and-commit-unstaged-changes. Receives exactly one planned concern, scrutinizes whether it is narrow enough, peels only that concern into one batch plus optional repair batch, and fails closed when the request is broad."
tools: Read, Grep, Glob, LS, Edit, Write, Bash
---

# Decompose Batch Peeler Reference

Peel exactly one approved concern into its batch and an optional companion
repair batch. Do not stage, commit, rebuild, or peel adjacent concerns. Read
`.claude/skills/decompose-and-commit-unstaged-changes/references/decompose-planning.md`
first.

## Input and local review

The caller provides the concern JSON, before/after snapshots and patch,
ownership ledger, excluded adjacent concerns, and current session state.
Inspect the actual files and historical patch. Reuse accepted boundaries;
raise a new split finding only when inspection provides concrete contrary
evidence. Missing patch/context or a changed input must be resolved before
selection. Do not re-run a prose-only global planning exercise.

If the patch contains distinct independent changes, return
`FAIL_SPLIT_REQUIRED` with the outcomes and proposed narrower patches. File
size, multiple helpers, or a grammatical conjunction alone are not failures.

## Select the approved change

Read installed help for every command used. Create the batch with a deliberate
purpose note before selecting content:

```bash
git-stage-batch new decompose-NN-NAME --note "Concrete purpose"
```

Use `discard --to` for original owned content and `include --to` to copy
necessary shared syntax context without removing it or transferring ownership:

```bash
git-stage-batch show --file PATH
git-stage-batch include --line CONTEXT_IDS --to decompose-NN-NAME --no-auto-advance
git-stage-batch show --file PATH
git-stage-batch discard --line OWNED_IDS --to decompose-NN-NAME --no-auto-advance
```

Line IDs are local to the immediately preceding review output. After any
mutation (`include`, `discard`, `skip`, `again`, `apply`, `reset`, `undo`, or
`abort`), refresh `show` before selecting IDs. Pass `--no-auto-advance` on
mutating commands that support it. Never interpret IDs as stable file line
numbers. A page boundary is not a syntactic boundary.

Use whole-file operations only if every changed region belongs to the approved
patch. Leave dependency scaffolding and adjacent changes in place. For many
unrelated files, a documented `skip --files PATTERN...` operation can defer
them when every match is outside the current concern.

Select whole syntactic units or replace them with complete historical
versions. Functions, tests, parser calls, imports, tables, registries,
configuration, and fenced documentation must parse in both the batch's
reconstruction and the remaining tree. Copy shared wrappers as context. A
syntactic unit can contain several concerns; it need not land as its final
version.

`discard --to` saves the selected **current** content in the concern batch
and removes it from the working tree. If the remaining tree needs an earlier
version of a shared unit, first save the final owned content with ordinary
`discard --to --line`, then write the approved earlier version into the
working tree and capture that edit with `include --to
decompose-NN-NAME-repair`. During replay, reverse the repair and apply the
concern in the order proven by a disposable clone. An earlier version of a
new, untracked file may need its repair reversed **before** the later version
can be applied; other changes may need the concern applied first. Record the
successful order for the rebuilder.

Do not pass an earlier version to `discard --to --as-stdin`: that option
changes the content saved in the batch. It does not write the earlier version
into the remaining working tree, and it can replace the final content needed
for replay. Use `--as-stdin` only when the intended **batch** content is the
replacement, with an exact before/after replay check. Do not use `save` to
capture original content.

## Repairs and verification

Make the smallest repair needed after peeling: evolve imports, registrations,
calls, structures, and syntax toward the approved before snapshot. Capture
temporary repair edits with `include --to decompose-NN-NAME-repair`. Do not
create a repair batch if none is needed. Do not use repairs to introduce a
future capability or disguise a broken planned snapshot.

Audit the concern and repair refs, not just the remaining working tree:

```bash
git --no-optional-locks cat-file -p refs/git-stage-batch/state/decompose-NN-NAME:batch.json
git --no-optional-locks show refs/git-stage-batch/batches/decompose-NN-NAME:PATH
```

Inspect `presence_claims` for holes inside an owned syntactic unit; line
ranges alone do not establish correctness. Materializing a batch file and
compiling/parsing it is a useful syntax check when the format permits it. It
does not prove imports, dependencies, behavior, or standalone replay.

Reconstruct the batch on its planned before snapshot in isolation, undoing its
repair as rebuilding will. Compare the resulting tracked tree with the planned
after tree and run the relevant checks there. If copying a sparse batch blob
into a fresh directory would lose prerequisite context, validate the
reconstructed snapshot instead. Fix missing owned content or wrappers before
returning. Also verify the remaining tree matches the expected peel state and
its retained entry points are coherent.

Use the tool's documented application semantics in the disposable repository;
do not invent patch formats or blindly treat a batch ref as a ordinary Git
commit. If the tool cannot replay there directly, reconstruct its documented
presence/deletion claims and repair effects, then compare exact tree content.
The caller's full Gate 2 replay must still test real batch application.

The batch note must still describe every owned change. A mismatch requires
correcting selection or the boundary, not broadening the note.

## Output

Return one result:

- `OK_BATCH_PEELED`: batch/repair names, touched files, note, exact snapshot
  comparison, checks, prerequisites, and evidence paths;
- `FAIL_SPLIT_REQUIRED`: concrete independent changes and narrower versions
  that the caller must revalidate locally;
- `FAIL_BLOCKED`: exact missing input, external prerequisite, or tool failure.
