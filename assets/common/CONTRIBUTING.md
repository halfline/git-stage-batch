# Contributing

Use the project's documented development setup, architecture notes, code
style, and checks. Inspect its build configuration and CI when those commands
are not documented. Do not assume a particular language, package manager,
test runner, release process, or review provider.

Keep each commit focused on one logical change and preserve unrelated work.
Keep the series understandable at each step: prerequisites come before their
callers, and related tests and documentation stay close to the behavior they
cover. Follow project rules when they call for separate follow-up commits.

Run checks appropriate to the change and report what ran and any limits in
review notes. Do not invent successful tests, benchmarks, or failure reports.

## Commit Message Guidelines

Use these conventions when the project does not provide a contribution guide.
Explicit project instructions, message hooks, and declared formats take
precedence over these defaults.

### Key Principles

- **Write for a competent developer who does not know this part of the codebase.** Explain the local roles and specialized terms needed to understand the change when encountered in history, without reproducing a subsystem introduction.
- **You are the maintainer; write to a casual reader.** The commit message is you explaining the change to someone unfamiliar with the codebase. Never refer to maintainers in the third person.
- **Keep explanations within the commit history.** Do not refer to outside development context such as "the plan" or "review results". Explain the motivation directly, using only context available at that point in the series.
- **Use series context to explain this patch.** Retain prerequisites, design choices, consumer contracts, and intentionally unfinished scope. Put the series-wide overview and recap in the review request or cover letter.
- **Separate independent series.** A dirty worktree can contain multiple unrelated commit series. Keep their explanations separate instead of forcing one narrative across all unstaged changes.
- **Explain groundwork where it matters.** Name the later capability that motivates a preparatory patch, regardless of the patch's position in the series.
- **Expand the summary in the first body sentence.** State the same change as a longer, non-imperative sentence before explaining the old behavior and the reason for the change.
- **Make the summary imply the actual change.** Choose words that convey the difference from the status quo and fit the patch. Examples illustrate the principle, not a fixed vocabulary.
- **Let structure follow the explanation.** State, problem, and solution are questions to answer, not paragraph assignments. Being first or last creates no introduction or conclusion duty.
- **Keep narrative present tense.** Describe the existing behavior from the viewpoint of the previous commit, then describe what the current commit changes. Prefer `This commit ...` for that transition wherever it belongs.
- **Use imperative voice only in the summary.** Every body sentence is an indicative, declarative statement. Never instruct the reader in the body.
- **Explain the actual reason at the smallest scope that makes it understandable.** Describe concrete consequences when they matter, without inflating a local change into a project-wide limitation. Internal simplification, removal of an unused argument, or clarification of a contract can be sufficient reasons.
- **Distinguish evidence from inference.** Do not invent reported failures, benchmarks, testing history, or rejected alternatives.
- **Be precise about scope.** If a change only improves one aspect of a problem, do not imply it fully solves it. Verify that the patch makes an operation run before describing the program as beginning it. A new helper or internal representation is a legitimate outcome even when callers will adopt it separately.
- **If the commit is a step toward a larger feature, say so explicitly.** Describe the end goal briefly, then explain how this commit moves toward it.
- **Name the feature goal in early groundwork commits.** If a commit mainly exists to enable a later user-facing feature, say what that feature is and why it matters instead of presenting the commit as isolated infrastructure work.
- **Ground judgments in concrete consequences.** A plain assessment can help explain the choice when the message supplies its basis.
- **Do not use `Co-Authored-By` for contributions produced by artificial intelligence.** Only use it for human co-authors.
- **Only use the word `this` when referring to the commit itself.** Use `that` or similar for other contexts.
- **Wrap body paragraphs at 75 characters.**
- **Be humble and forward thinking.** Avoid words like "comprehensive" or "crucial", and avoid a tone that could sound like bragging or seem short-sighted.
- **Do not invent concise self-describing labels for internal ideas and use them casually,** expecting the reader to implicitly know what they should mean. Explain things in a way that reduces cognitive load on the reader.
- **Build context across the series.** Give shared background where it first matters. Later commits may rely on that explanation; keep useful repetition, but make it a shorter reminder rather than repeating the detail. Add new detail where it becomes relevant. A standalone commit still needs its own context.
- **Define codebase-specific or ambiguous terms when first introduced in the series.** Use a concise role reminder later when it helps, rather than repeating the full definition in every message.
- **Prefer a complete plain-language sentence over compressed shorthand.** Spell out the behavior hidden by noun piles, abstract verbs, or compounds such as `X-backed` and `X-aware` when their meaning is not obvious.

### Format

State, problem, and solution are questions to answer, not paragraphs to fill.
Combine them when separation repeats the same fact, and omit background the
subject or problem already establishes. A mechanical cleanup may need one
sentence; a one-line correctness fix may need several paragraphs. The topics
below guide the explanation without assigning paragraphs.

#### Explain the Change First

The first body sentence is a longer, non-imperative version of the summary
line. State the same change in plain language as an indicative, declarative
sentence, with enough detail to make the outcome clear. Keep that opening
before the background and reasoning.

Explicitly attribute newly introduced behavior to the current commit, using
`This commit ...` or an equally clear construction. Describe existing
behavior in present tense from the state before the patch. Leading with the
outcome does not change that temporal viewpoint. For example, `This commit
adds pattern matching to the command` identifies the patch as the cause;
`The command now accepts patterns` can blur the change with the status quo.

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

Before drafting a behavior fix, explain the old operation to a developer
who knows programming but not the subsystem's domain: what is being
processed, what result is intended, and what policy relates them. Trace
the condition that exposes the defect through the intermediate effect to
the wrong result. Use prior source and supplied context to fill gaps in
that account before composing the message.

Draft from that account rather than from the sequence of edits. A reader
may understand every technical term and still not know why the bad case
arises. Establish both the relationship that creates that case and the
old code's incorrect handling of it. Use the changed code to verify the
correction and its limits. This is a drafting aid, not a required
paragraph structure.

Once the opening states the correction, the rest of the message may
explain just the old behavior and why it is wrong. Return to the fix when
the reader still needs a design choice, a non-obvious mechanism, or a
meaningful limit explained. A separate solution paragraph is not required.
Give a judgment or a turn in the argument its own sentence or paragraph
when that helps it land. Keep a brief reminder when it connects the next
step. Once the reason, decision, and necessary scope are clear, stop.

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
For a behavior fix, describe what changes for the affected input or
operation. Establish the existing relationships and policy that explain
when the result is wrong, even when that context is unchanged by the
patch. Explain the correction at that level before introducing formulas,
fields, or helper calls. Those mechanics belong when they explain a
design choice or meaningful limit, rather than merely demonstrate how
the patch implements the correction.

A new internal API can be the outcome of a preparatory patch; explain
its purpose without cataloguing all its capabilities. When remaining
work matters to the explanation, say what still needs to be done. Use
active verbs for those actions instead of listing missing components or
call sites.

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

#### First Line (Summary)

```
prefix: Concise summary of the change
```

- Use a short, lowercase prefix based on the affected subsystem. Prefer the affected subsystem over generic `fix:` or `refactor:` labels when prefixes are free-form; Conventional Commits and other declared type-based formats take precedence.
- Capitalize the first word of the summary after the colon
- Keep the entire line under 72 characters
- If unsure which prefix to use, run `git log --pretty=oneline FILE` and see what prefixes were used previously

#### Relevant State

Establish the program's selected state at this point in history. If recent
work established that state, briefly recount the change in past tense and
loosely when it happened.

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

Summarize what capabilities, interfaces, or documentation exist in the
project immediately before this commit is applied. This is the program's
state, not the user's situation. Focus on what the program has or provides,
not on what users must do or cannot do.

If this commit is part of a series, describe the relevant state after the
previous commit. Do not claim later work already exists, but do not recap
unrelated earlier work either. A French translation does not need a list of
languages added by preceding commits.

If the patch mainly exists to enable a later feature, explain that connection
where it helps establish the problem or design. Do not describe it as generic
cleanup when a specific capability motivates the work.

Combine that background with the problem or change when separate paragraphs
would repeat the same fact.

#### Problem

Explain the underlying problem from the appropriate perspective.

**Choose the perspective based on who experiences the problem:**
- Use the maintainer's perspective for internal concerns. Describe the
  project's needs, choices, and limitations from inside the project; use
  first person when natural. Keep supported judgments and explain their
  basis. Never refer to maintainers as outsiders.
- Use **user perspective** for external concerns (confusing interfaces, missing documentation, poor workflows). Frame as "Users cannot X" or "Users must Y."

Describe what is non-obvious, hard to discover, confusing, missing, or limited
about the selected state. Explain the actual reason at the smallest scope that
makes it understandable. Do not broaden a local reason merely to make the
patch sound important.

Useful tests:
- Does the explanation identify the actual reason for this patch?
- Is this something users would notice, or only you as the maintainer?

When a helper exists specifically for a later capability, explain that
capability and the constraint it places on the helper. The relationship matters
because it explains the design, regardless of the commit's position.

#### Change

Describe how the commit addresses one part of that problem.

Be precise about scope. If the commit only addresses one path (such as the man
page, command help, or internal structure), say so clearly rather than implying
the entire problem is solved.

If the commit introduces infrastructure or an early step toward a larger
feature, describe it as such.

Describe the capability or intermediate state established by the patch.
Do not force "begins", "continues", or "completes" wording based on position.

Prefer `This commit ...` to mark the change in narrative present tense.
It can share a paragraph with the reason when that reads naturally. Do not
repeat the subject merely to supply a concluding solution paragraph.

Use natural prose such as:
- `This commit addresses that by ...`
- `This commit begins adding support for ... by ...`
- `This commit lays groundwork for ... by ...`

#### Series Context

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

### Checklist

Before finalizing a commit message, check:

- Does the summary use a fitting prefix and stay under 72 characters?
- Does its wording imply the actual change from the status quo?
- Does the opening make the changed behavior clear in plain language?
- Are implementation details and absolute claims necessary and supported?
- Does the description of existing behavior match the state before the patch?
- Is the status quo clear, with temporal cues only where they resolve an
  ambiguity rather than make lasting background sound temporary?
- Does any `already` claim identify an existing capability the patch builds
  on, rather than assume later work?
- Does the message use declarative body sentences and narrative present tense?
- Does the selected state match the previous commit without recapping
  unrelated earlier work?
- If the worktree contains multiple independent series, are they split into separate series?
- Can you name the particular fact about this patch that each cross-commit reference explains?
- Can the reader explain why the change is needed and why it works without reconstructing the reasoning from the diff?
- Are unrelated roadmaps, recaps, and next-item announcements omitted?
- Does the explanation use the appropriate perspective (first-person maintainer for internal concerns, user for external concerns)?
- Does it describe the actual reason without artificially broadening the scope?
- If the patch is groundwork, does it explain the later capability that motivates the design?
- Does the explanation clearly state what this commit does without overstating its impact or claiming later integration?
- If this is an incremental step, does it clearly say so?
- Would removing any cross-commit reference make this patch's reason, design, or stopping point unclear?
- Is retained series context accurate, brief, and not repeated elsewhere in the message?
- Does every paragraph advance understanding, retaining useful reminders and removing repeated conclusions?
- Can a newcomer understand the state, limitation, and change without decoding coined shorthand?
- Are unfamiliar terms introduced where they first matter, with concise reminders where later messages need them?
- Does repeated context become more concise while retaining what helps explain the current patch?
- Do body paragraphs wrap at 75 characters?
