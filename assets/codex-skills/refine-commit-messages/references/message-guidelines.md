# Commit Message Audit Guidelines

Use repository-specific guidance first. Apply these rules as the fallback and
as the series-level checks when local guidance is silent.

## Audit order

1. Read the complete patch and identify the one state or outcome it establishes.
2. Read the message as prose for a drive-by reviewer with limited context.
3. Check the subject and each body paragraph against the patch.
4. Check the cumulative story against preceding commits.
5. Check the fourth paragraph against the next commit and the series goal.

Never approve prose merely because its nouns occur in the diff. Every
meaningful helper, result field, API surface, CLI branch, fixture family,
documentation section, and build hook must be either the named outcome or
necessary support for it. A patch with several independent outcomes belongs
in `refine-history`; do not disguise it with a broader message.

## Low-context prose

Assume the reader understands ordinary software development and Git but is
new to the codebase. For a series, expect that drive-by reader to read the
commits together in order. Each message should make its own state,
limitation, and change clear in that context; it need not rebuild all the
background for someone reading only that commit in isolation.

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

Use three body paragraphs for one standalone commit:

1. Establish the relevant project state after the previous commit. Use
   present tense for state descriptions. If recent work established that
   state, briefly recount the change in past tense and loosely when it
   happened. Do not describe the current patch or begin with `This commit`.
2. Explain the underlying limitation from the maintainer or user perspective.
   Describe a missing capability or concrete constraint, not merely a symptom
   such as "cumbersome", "bad", or "hard".
3. Begin with `This commit` and explain precisely how this patch addresses the
   limitation. Do not claim work that appears only in another commit.

Use a fourth body paragraph for every commit in a multi-commit series. It
connects this commit to the series rather than restating the third paragraph.

Avoid merging the selected state and problem with `but` or `however`. Use
imperative voice only in the commit summary. Write every commit-body sentence
as an indicative, declarative statement, including the selected-state,
problem, `This commit`, and series-transition paragraphs. Never use a body
sentence to instruct the reader. Avoid reconstruction mechanics such as
`fixup`, `squash`, `rebase`, `split`, `cleanup`, `decomposition`, or
`reconstruction` unless those are literally product-domain concepts.

## Temporal framing

Describe the status quo in present tense from the viewpoint of the code
after the previous commit. Use a light cue such as `Right now, ...`,
`Currently, ...`, or `As things stand, ...` when it helps distinguish
time-dependent behavior from the change. The third paragraph's `This
commit ...` supplies the transition.

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

Evaluate each message at its own historical position. The first paragraph must
describe the state selected by the previous commit, not the final branch or
the uncommitted worktree. Include only earlier changes needed to explain the
current limitation or design; do not recap unrelated work or prematurely
claim later capabilities.

For a series, use progression language in the third paragraph:

- the opening commit begins or lays groundwork for the larger goal;
- middle commits continue or advance it; and
- the final commit completes or concludes it.

The exact word may vary when the meaning remains unambiguous.

## Fourth-paragraph transitions

Check transitions against the actual next patches, not only against prose.

- Earlier non-final commits use future tense and name the remaining capability
  specifically. `Subsequent commits will ...` is acceptable; vague statements
  such as `More work will follow` are not.
- The penultimate commit refers to `the final commit` in the singular and says
  what that commit will establish. Do not say `subsequent commits`.
- The final commit concludes the goal introduced by the opening message. It
  describes the resulting series-level state and does not promise future
  commits.

Vary the phrasing across the series. Repeated boilerplate can be structurally
correct while still producing a poor narrative.

## Verdicts

Use `KEEP` only when the complete message matches the complete patch, its
historical position, and all applicable rules. Give a concrete reason.

Use `REWORD` when message prose can be corrected without changing the patch
boundary. Provide a complete replacement, not isolated edits.

Escalate to `refine-history` when no honest single-outcome message can describe
the patch. `refine-commit-messages` must never solve that problem by changing
content, order, or boundaries.
