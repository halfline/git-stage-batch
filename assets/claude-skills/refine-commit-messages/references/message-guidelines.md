# Commit Message Audit Guidelines

Use repository-specific guidance first. Apply these rules as the fallback and
as the series-level checks when local guidance is silent.

## Audit order

1. Read the complete patch and identify the one state or outcome it establishes.
2. Read the message as prose for a drive-by reviewer with limited context.
3. Check the behavioral meaning of the final sentences against the patch,
   relevant prior code, and supplied context, including claims introduced by
   paraphrasing. Keep supplied claims and your inferences separate from
   independently verified source facts.
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

Use straight quotes and apostrophes in commit message prose. Avoid em
dashes. Use commas, parentheses, or separate sentences where needed.

In prose, prefer periods to semicolons.

After the opening, explain the decision as you would to another
maintainer who has not investigated the problem. Use ordinary verbs and
familiar terms, with contractions when they fit. Let the reader follow
the thought: a reasonable assumption, the catch, and the consequence that
makes the change worth doing. Include those turns when the reasoning
needs them; a direct fix may need only its direct reason.

Match the firmness of each claim to its basis. State a demonstrated
failure, unnecessary restriction, or supported cost plainly. Preserve
uncertainty about people's behavior, predictions, and design judgments
where the evidence leaves room. A message can explain the code confidently
and qualify an inference about its users in the next sentence.

State established behavior directly, with the condition that matters.
Use "can" or "may" for meaningful capability, contingency, or uncertainty,
not automatic precision. Check changed implications against the evidence.

Write decisions from inside the project. When relevant context explains
who made an earlier choice and why, retain that ownership and reasoning.
"We" can describe a shared decision or need; a component name is often
clearer for an operation. First person does not establish personal
experience or project history that the evidence does not supply.

## Message shape

Use a concise, single-outcome subject with the repository's normal lowercase
prefix when it has one. Keep body lines at or below 75 characters unless an
unbreakable token or explicit repository rule requires otherwise.

When repository prefixes are free-form, prefer a concrete subsystem or surface
label established by history. Generic change-type labels such as `fix:` and
`refactor:` usually add little signal. Follow Conventional Commits or another
declared type-based format when the repository requires it.

State, problem, and solution are questions to answer, not mandatory paragraphs.
Prefer the following order when each part contributes distinct information:

1. Make the first body sentence a longer, non-imperative version of the
   summary line. State the same change in plain language as an indicative,
   declarative sentence, with enough detail to make the outcome clear.
   Prefer `This commit ...` to attribute the change explicitly.
2. Walk through the relevant old behavior and follow the interaction or
   assumption that leads to the limitation. Introduce the components as
   they enter the explanation. When useful, explain why the design works
   in ordinary cases and what is different here. Describe the relevant
   state after the previous commit in present tense. Internal simplification
   can be sufficient; do not invent a larger problem.
3. Explain why the chosen change addresses that consequence. Connect
   technical details to a design choice or a remaining limit. Do not repeat
   the opening or attribute another commit's implementation to the patch.

Explicitly attribute newly introduced behavior to the current commit, using
`This commit ...` or an equally clear construction. Describe existing
behavior in present tense from the state before the patch. Leading with the
outcome does not change that temporal viewpoint. For example, `This commit
adds pattern matching to the command` identifies the patch as the cause;
`The command now accepts patterns` can blur the change with the status quo.

Spend words where the reader's expectation could go wrong. Explain how
the operation's calls or events fit together before introducing internal
paths or states. Prefer concrete nouns such as "cleanup code" over labels
such as "shared cleanup". For an unnamed branch, state its condition and
action directly. Use a local name as shorthand once its meaning is clear.
A timing or ownership problem may need a patient walkthrough of why the
apparent fix fails; an unnecessary requirement may take only a sentence
to dismiss.
Follow the chosen mechanism through to its practical consequence so the
reader understands why it is worth using. Give a judgment or a turn in
the argument its own sentence or paragraph
when that helps it land. Keep a brief reminder when it connects the next
step. Once the reason, decision, and necessary scope are clear, stop;
additional implementation facts do not create a need for more prose.
Combine parts when separation repeats the same fact. Do not restate the
subject just to supply a concluding solution paragraph.

Ask whether the reader can explain why the change is needed and why it
works without reconstructing the reasoning from the diff. Record a missing
connection as missing necessary reasoning, even if the API details are
accurate. Use ordinary connective prose so each sentence builds on the last.

The summary should make the difference from the status quo apparent, rather
than merely name a desirable end state. Choose wording from the actual
before-and-after behavior. For example, `Align statistics with the visible
sensor crop` implies that the measured and displayed regions differ;
`Process only the selected files` implies that unselected files are currently
included. These illustrate how meaning supplies the contrast, not a fixed
vocabulary or a list of required verbs. Choose the words that best fit the
specific patch. Use a qualifier such as `only` when the restriction is real;
do not imply exact exclusion if alignment or rounding can include extra data.

The expanded opening should state that same concrete action in ordinary
words, without cataloguing internal operations or unchanged preconditions.

Select technical details for the understanding they give the reader.
Keep a detail when it explains the problem, why the change works, a
design choice, or a meaningful limit. A new internal API can be the
outcome of a preparatory patch; explain its purpose without cataloguing
all its capabilities. When remaining work matters to the explanation,
say what still needs to be done. Use active verbs for those actions instead
of listing missing components or call sites.

For tone, given repeated reads of an unchanged file, "We read the same
file for every row. Once is enough" explains the judgment more naturally
than "Repeated file reads represent avoidable overhead". When a field is
not yet read or forwarded, "We still need to read the field and pass its
value to the worker" puts the remaining actions in active verbs. "The reader
and value forwarding still need to be added" leaves them in nouns and
passive prose. When work remains, use a person or component as the subject
and say what they still need to do. These illustrate ordinary explanation,
not phrases to copy or a requirement to use first person.

Keep verification notes outside the commit message. A condition belongs
in the message when leaving it out would obscure the reasoning or
materially misstate the claim. The body need not demonstrate every fact
checked during inspection. Scope the claim accurately and give its
explanation room.

Make each necessary scope limit qualify the claim it belongs to when
that reads plainly. Once a clarification is expressed in that claim,
remove a sentence that only restates the same distinction. Keep a separate
limit when it explains a stopping point or another consequence. Do not
append an inventory of operations or unchanged behavior simply to account
for the patch.

Before accepting the message, read it on its own as an explanation to
someone who has not inspected the diff. Consider the question each
paragraph answers at that point in the explanation. Keep the details
that answer that question; move answers to separate questions raised
only by inspecting the implementation into review notes outside the message.

Revise in both directions: restore an intermediate step the reader
needs to understand the consequence, and remove facts whose main purpose
is to demonstrate thorough inspection. A feature's presence in the patch
supports its accuracy, but does not by itself explain why the reader
needs it. Keep the maintainer's supported judgment and the connection
between ideas when shortening the message.

After drafting or rewording, verify the meaning of the final sentences
against the selected patch, relevant prior code, and supplied context.
Check the conditions, timing and effects the message states or implies.
A simpler phrase can introduce a new claim; an operation's name does not establish
its effect. Inspect the relevant implementation when that distinction
matters. Narrow or remove unsupported implications and report any
remaining uncertainty outside the message. Correct contradictions in the
affected claim, then remove any caveat or reassurance that only repeats
its corrected meaning. Repeat the accuracy check after further edits.
Keep code evidence, supplied context, and inference distinct in review
notes.

Use imperative voice only in the commit summary.
Write every commit-body sentence as an indicative, declarative statement.
Keep narrative present tense for the existing state and the change, regardless
of paragraph count. Never use a body sentence to instruct the reader.
Avoid reconstruction mechanics such as
`fixup`, `squash`, `rebase`, `split`, `decomposition`, or
`reconstruction` unless those are literally product-domain concepts.
The restriction concerns how the series was rewritten. Ordinary cleanup
code and relevant earlier refactors still belong in the explanation when
they help the reader understand the change.

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

Caller-supplied motivation, observations, assessments, and relevant history
can explain the change even when absent from the diff. Carry their meaning
and degree of certainty through the drafting handoff. In the message,
combine clarifications that explain the same fact. Include history when
it helps explain the present decision. Keep provenance and
verification limits outside the commit message. Report contradictions
with source there and avoid relying on a contradicted account as fact.
Never claim independent verification merely because context was supplied.

Supplied context establishes meaning, not a list of points to cover. Treat
a wording clarification as a constraint on the relevant claim. Do not turn
it into a separate explanation of a hypothetical misunderstanding.
Do not invent a reported failure, benchmark, testing history, or rejected
alternative.

## Cross-commit references

A commit message explains its own change. Series context belongs in the
message when it explains a prerequisite, design choice, contract with a
consumer, or deliberately unfinished scope. A future use for a capability
does not establish follow-up commits. Mention later commits only when the
supplied series or plan establishes them; otherwise explain the purpose
without promising a sequence.

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
reasoning, a behavioral change that remains unclear until the code is read,
ambiguity, a concrete voice or pacing defect, irrelevant or repeated
explanation, or a violated repository rule.
Provide a complete replacement, not isolated edits. During a necessary
reword, preserve supported judgments, their degree of certainty, concessions,
first-person stance, and useful pacing. Repair the named defect while keeping natural existing
prose; do not standardize it or insert voice markers merely to resemble
examples.
Do not reword solely because another phrasing is possible, the paragraph count
differs from a usual pattern, or the message lacks a series introduction or
conclusion.

Escalate to `refine-history` when no honest single-outcome message can describe
the patch. `refine-commit-messages` must never solve that problem by changing
content, order, or boundaries.

## Draft and compare

Use this procedure for a new draft or a replacement justified by a named
defect. An accepted existing message stays unchanged. A concrete voice or
pacing defect is a reason to edit; the possibility of another phrasing is
not. Honor exact wording explicitly supplied by the user, checking it for
accuracy and required conventions without silently polishing it.

Review only the selected message. This procedure does not authorize
staging, applying a rewrite plan, changing commit boundaries, or editing
other messages. In a message-only audit, keep the source as KEEP if it wins
the comparison and no named defect remains. Complete prose comparisons
before validating or applying the caller's plan.

For a new message or a justified replacement, edit the draft into one
alternative using the same source evidence and supplied context. Keep the
first body sentence as a longer, non-imperative version of the summary. Preserve the intended
behavior, supported judgments, actual uncertainty, and necessary scope.

Edit the summary and its expanded first sentence too when their wording
hides the action in an abstract label. Keep the expanded sentence first,
and use it to state the actual change in ordinary words.

After that opening, edit as if a colleague has asked why we need the
change. Put the action or need in ordinary verbs. Let a short judgment
stand on its own. Remove a sentence that merely labels the change or
repeats a consequence the surrounding prose already makes clear. Keep inspection notes outside the message.

Compare the reasoning and how naturally it is expressed. A wording
improvement may keep every fact and causal step unchanged; it does not
need a new reason or extra detail to earn its place. Preserve useful
causal links instead of replacing them with a list of operations. Check
local references from the prose alone. Unpacking an unexplained reference
is a real improvement even when it takes more words. A descriptive label
or a related word used earlier does not establish its meaning. Prefer
stating the condition and action when that saves the reader from having
to reconstruct the relationship. Keep the original if the alternative is
less natural, loses needed reasoning,
or offers no improvement. Let difficult interactions take room and direct
reasons stay brief. Length, contractions and first person do not decide
which version is better.

Repeat the edit-and-compare step using the best version so far. Make at
most three comparisons in total, including the first. Stop after two
consecutive comparisons offer no clear improvement. Keep wording that
already works; an extra comparison does not require accepting an edit.
Fix factual errors and repository-rule violations before accepting the
message, regardless of that limit.

The calling workflow owns the comparison. Keep it internal and use the
selected message within that workflow's existing scope.


Apply the accuracy check in this guide to the selected wording. Repeat
that check after any further wording edits.
