# Decompose Analyzer Reference

Prepare the concern sequence from the real unstaged input. Read
`.agents/skills/decompose-and-commit-unstaged-changes/references/decompose-planning.md`.
Write preparation artifacts only. Do not edit source, stage, commit, peel,
construct prototype histories, or author intermediate implementations, even
in a disposable repository.

## Input and work

Receive the base, mode, state directory, captured manifest path, and any local
findings. If input has not been captured, run `scripts/decompose-plan.py capture`
from the repository root. Mark `phase1-running` with the checkpoint helper.
A fresh run inventories current input; a correction retains valid existing
findings. Never silently adopt an older prototype plan or recapture a partially
peeled tree as the original input.

1. Inspect the committed base and tracked/nonignored new changes, including
   tests, configuration, packaging, deletions, modes, and gitlinks. Read
   repository conventions and discover actual check commands/prerequisites.
   Allocate checks to individual proposed commits. An implementation before its
   separate proof needs a direct behavior check or external harness using only
   present code; discovering an absent future test file can falsely pass zero
   tests. Allocate that test-file command to its later proof commit.
2. Identify narrow implementation concerns and their proposed atomic slices.
   Assign original changed regions with stable path/symbol/heading anchors;
   record shared syntax context separately. Do not equate files or definitions
   with commit boundaries.
3. Write a short development ladder before detailed ordering: earliest useful
   contracts and flows, their first adopters, and related proof. Explain planned
   earlier versions of shared files and missing future content without writing
   that code. Several concerns can share a milestone.
4. Support dependencies with actual symbols/contracts and planned first use.
   An import in the final version is not itself a historical dependency.
   Preserve code/proof adjacency required by repository conventions.
5. Audit proposed slices for distinct independent outcomes. Record concrete
   uncertainties and narrower alternatives; do not generate boilerplate
   assurances or speculative subconcerns for every private helper.
6. Write the schema 2 candidate and concise narrative/findings. Run structural
   validation and `verify-input` before returning for semantic review.

## Artifacts and corrections

Write `decompose-plan.candidate.json`, `decompose-narrative.md`, and
`decompose-refinement.md` under the state directory, using the schema in the
planning reference. The narrative summarizes the ladder; refinement records
actual disputed boundaries and their dispositions. Proposed checks identify
commands and prerequisites; do not claim they passed in nonexistent snapshots.
No prototype repository, snapshot chain, or passing-check log is required.

On feedback, save the prior plan and use `decompose-plan.py affected OLD NEW`
after updating the disputed ownership/order/dependency/slice. Rerun cheap global
structure and review only affected boundaries plus newly exposed issues.
Retain the original captured input and accepted unrelated findings.
A changed input before peeling needs a new capture and explicit comparison;
after peeling, audit the original manifest against batches and remaining source.

Mark `phase1-candidate` when ready. Return artifact paths, manifest/digest,
concerns and chronology, proposed checks, unresolved findings, and the input
preservation result. The coordinator owns semantic Gate 1 and promotion.
