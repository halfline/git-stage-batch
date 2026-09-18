# Commit Message Audit Guidelines

Use repository-specific guidance first. Apply these rules as the fallback and
as the series-level checks when local guidance is silent.

## Audit order

1. Read the complete patch and identify the one state or outcome it establishes.
2. Read the message as prose for a drive-by reviewer with limited context.
3. Check the subject and each body paragraph against the patch.
4. Check the relevant state after the previous commit against earlier work.
5. Check whether each cross-commit reference explains a particular fact about
   this patch, and verify it against the referenced patch. Series position
   alone creates no introduction or conclusion duty.

Never approve prose merely because its nouns occur in the diff. Every
meaningful helper, result field, API surface, CLI branch, fixture family,
documentation section, and build hook must be either the named outcome or
necessary support for it. A patch with several independent outcomes belongs
in `refine-history`; do not disguise it with a broader message.

## Low-context prose

Assume the reader understands ordinary software development and Git but does
not know this part of the codebase. Explain the local roles and relationships
needed to understand the change when encountered in history, without
reproducing a subsystem introduction.

- Define specialized terms and unfamiliar acronyms when their meaning is
  needed and cannot reasonably be inferred.
- Keep explanations within the commit history. Do not refer to outside
  development context such as "the plan" or "review results". Explain the
  motivation directly, using only context available at that point in the
  series.
- Explain shared context in detail where it first matters. Later messages may
  rely on that explanation. Keep useful repetition, but make it a shorter
  reminder rather than repeating the detail; add new detail where it becomes
  relevant. A standalone commit still needs its own context.
- Prefer a short, complete sentence over a coined label or compressed noun
  phrase. Do not turn a relationship into phrases such as `metadata bridge`,
  `ownership path`, `state seam`, or `typing surface` when plain prose can say
  what data moves, who uses it, and why.
- Do not invent a one- or two-word name for an idea solely to shorten the
  message. A memorable label is not automatically a clear explanation.
- Use an established project term only when it is the clearest name for the
  concept. Define a codebase-specific or ambiguous term when it is first
  introduced in the series; give a concise reminder later when it helps.
- Introduce a code identifier by its role when the name alone is not
  self-explanatory: `SelectionResult, the object that carries the chosen
  hunks`, rather than treating `SelectionResult` as prior knowledge. Later
  messages can use the established name or a short role reminder as needed.
- Expand uncommon abbreviations on first use. Common terms such as Git, CLI,
  API, and JSON need no definition unless the repository gives them a special
  meaning.
- Spell out the relationship hidden by compounds such as `X-backed`,
  `X-aware`, `X-driven`, or `X-shaped` when more than one interpretation is
  plausible.
- Replace abstract verbs such as `thread`, `surface`, `normalize`, `harden`,
  or `plumb` with the concrete behavior when the abstraction would force the
  reader to inspect the patch.
- Give pronouns clear antecedents. Do not make a reader search earlier
  paragraphs to learn what `it`, `that path`, or `the state` means.

For example, `state: Harden ownership recovery` leaves both the failure and
the role of ownership unclear. `state: Restore saved line selections after
reopening a session` states the behavior directly. Likewise, replace `The
typed path lacks a metadata bridge` with a sentence such as `Saved selections
do not carry the file name and object identifier needed to find their source
files again`.

Apply a read-once test to the series: a newcomer reading it in order should
be able to paraphrase what exists, what is missing, and what each commit
changes. If that requires guessing an unexplained local term, define it or
rewrite the sentence. If the context is already established, prefer a
concise reminder over another full explanation.

## Message shape

Use a concise, single-outcome subject with the repository's normal lowercase
prefix when it has one. Keep body lines at or below 75 characters unless an
unbreakable token or explicit repository rule requires otherwise.

When repository prefixes are free-form, prefer a concrete subsystem or surface
label established by history. Generic change-type labels such as `fix:` and
`refactor:` usually add little signal. Follow Conventional Commits or another
declared type-based format when the repository requires it.

State, problem, and solution are questions to answer, not mandatory paragraphs.
Use that progression when each part contributes distinct information:

1. Establish the relevant project state after the previous commit. Use
   present tense for state descriptions. If recent work established that
   state, briefly recount the change in past tense and loosely when it
   happened.
2. Explain the underlying limitation from the maintainer or user perspective.
   Explain the actual reason at the smallest scope that makes it understandable.
   Internal simplification can be sufficient. Avoid vague judgments such as
   "cumbersome", "bad", or "hard".
3. Prefer `This commit ...` to explain precisely what the patch changes.
   Do not claim work that appears only in another commit.

Combine parts when separation repeats the same fact. A mechanical change may
need one sentence; a one-line correctness fix may need several paragraphs.
Choose depth according to the reasoning the reader needs. Do not restate the
subject just to supply a concluding solution paragraph.

Use imperative voice only in the commit summary.
Write every commit-body sentence as an indicative, declarative statement.
Keep narrative present tense for the existing state and the change, regardless
of paragraph count. Never use a body sentence to instruct the reader.
Avoid reconstruction mechanics such as
`fixup`, `squash`, `rebase`, `split`, `cleanup`, `decomposition`, or
`reconstruction` unless those are literally product-domain concepts.

## Temporal framing

Describe the status quo in present tense from the viewpoint of the code
after the previous commit. Use a light cue such as `Right now, ...`,
`Currently, ...`, or `As things stand, ...` when it helps distinguish
time-dependent behavior from the change. `This commit ...` supplies the
transition wherever the explanation needs it.

Do not add a cue automatically to the first paragraph. Timeless background
or a lasting contract can stand unqualified; naming the project or component
may already make the context clear. For example, `Mutter redraws or copies
damaged regions` does not need `Right now`. If the ambiguity is in the problem
paragraph, anchor that claim instead: `Currently, buffer repair does not
consistently follow that rule.`

Avoid routinely attaching `Before this commit is applied, ...` to background
facts, since that invites a contrast even when those facts remain true
afterward.

Use `already` selectively to contrast an established capability with an
extension the patch needs. Verify it against the previous commit; do not
use the word merely to mean that something exists before the current patch.

When the relevant state comes from a recent change, describe that change
as a past event and identify it as earlier work: `Recent commits moved
buffer selection and damage history into the copy tracker.` Present tense
describes an existing state; past tense can recount the recent change that
established it. Avoid `now` alone for that transition, since it can make
the current commit sound responsible. Do not anticipate later work.

When a cue helps, use it where it resolves the ambiguity without repeating
it throughout the message. Vary the wording naturally; some repetition is
preferable to forced synonyms. Keep present tense for the change itself,
such as `This commit returns NULL on failure`, and future tense for later
work.

In message prose, use `commit`, not `revision`, and `previous commit`, not
`parent commit`.

## Cumulative state

Evaluate each message at its own historical position. Background must describe
the state selected by the previous commit, not the final branch or
the uncommitted worktree. Include only earlier changes needed to explain the
current limitation or design; do not recap unrelated work or prematurely
claim later capabilities.

Before claiming that the program begins an operation, verify that the patch
makes it active in the relevant execution path. Adding machinery that later
commits will call does not change existing callers. An internal API,
representation, or invariant is a legitimate outcome. Describe that capability
without claiming its later integration.

Distinguish documented motivation from consequences demonstrated by the code.
Do not invent a reported failure, benchmark, testing history, or rejected
alternative.

## Cross-commit references

A commit message explains its own change. Series context belongs in the
message when it explains a prerequisite, design choice, contract with a
consumer, or deliberately unfinished scope.

For each reference, identify the particular fact about this patch it explains.
Would removing it make the patch's reason, design, or stopping point unclear?
If removing it only loses knowledge of what happens elsewhere, remove it.
Merely sharing a topic or appearing next in the series is not enough.

For example, a full-damage representation change can explain why a later
shadow-copy patch can use ordinary region operations. An unused-argument
cleanup should not merely announce that the next commit fixes failed GPU
copies; it needs a meaningful connection to that work.

Describe a necessary relationship where it belongs in the explanation.
It need not occupy a separate paragraph or repeat the current patch's reason.

Being first, last, or adjacent to another commit creates no obligation to
introduce, summarize, or conclude the series. Put the series-wide overview and
recap in the review request or cover letter, while retaining the rationale
that belongs to this patch.

Verify references against actual patches. Describe work in later commits in
future tense and do not claim it is already implemented. Make references to
those commits explicit: `in a later commit`, `later commits will ...`, or
`the final commit will ...`, not bare `later`. For example, write `the
tracker introduced in a later commit`, not `the tracker introduced later`.
When a retained segue refers only to the upcoming final commit, use the
singular. Preserve useful connections; remove unrelated recaps, next-item
announcements, and repetitive boilerplate. Varying the wording does not make
an irrelevant segue useful.

## Verdicts

Use `KEEP` when the subject accurately names the patch's outcome, the body
contains the necessary reasoning at this commit, each paragraph contributes
distinct information, cross-commit context explains this patch, and the
message follows repository rules and reads naturally. Give a concrete reason.

Use `REWORD` for a named defect: an inaccurate claim, missing necessary
reasoning, ambiguity, irrelevant or repeated explanation, or a violated
repository rule. Provide a complete replacement, not isolated edits.
Do not reword solely because another phrasing is possible, the paragraph count
differs from a usual pattern, or the message lacks a series introduction or
conclusion.

Escalate to `refine-history` when no honest single-outcome message can describe
the patch. `refine-commit-messages` must never solve that problem by changing
content, order, or boundaries.
