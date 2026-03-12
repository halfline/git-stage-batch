# Examples

Common workflows and use cases for git-stage-batch.

## Basic: Stage Some, Skip Others

Separate features into different commits:

```
❯ git status
modified:   feature1.py
modified:   feature2.py

❯ git-stage-batch start
feature1.py :: @@ -10,5 +10,5 @@
[#1] - old_implementation()
[#2] + new_implementation()

❯ git-stage-batch include

feature2.py :: @@ -5,3 +5,4 @@
[#1] + experimental_code()

❯ git-stage-batch skip
No pending hunks.

❯ git commit -m "feature1: Implement new algorithm"

❯ git-stage-batch again
feature2.py :: @@ -5,3 +5,4 @@
[#1] + experimental_code()

❯ git-stage-batch discard
No pending hunks.
```

---

## Line-Level: Separate Mixed Changes

You accidentally mixed two changes in one hunk:

```
❯ git-stage-batch start
config.py :: @@ -1,5 +1,7 @@
[#1] + FEATURE_FLAG = True
      DATABASE_URL = "..."
[#2] - TIMEOUT = 30
[#3] + TIMEOUT = 60
[#4] + DEBUG = False

# Feature flag and timeout are separate concerns
# Stage only the timeout change
❯ git-stage-batch include --line 2,3

❯ git commit -m "config: Increase timeout to 60s"

❯ git-stage-batch again
config.py :: @@ -1,5 +1,7 @@
[#1] + FEATURE_FLAG = True
      DATABASE_URL = "..."
[#2] + DEBUG = False

# Now handle the feature flag and debug setting
```

---

## File-Level: Stage Entire File

You have multiple hunks in one file that all belong together:

```
❯ git-stage-batch start
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash()
[#2] + new_hash()

❯ git-stage-batch include --file
✓ 3 hunk(s) staged from auth.py

❯ git commit -m "auth: Upgrade to SHA-256"
```

---

## Block Build Artifacts

Permanently exclude generated files:

```
❯ git-stage-batch start
build/output.js :: @@ -1,0 +1,3 @@
[#1] + // Generated code
[#2] + function compiled() {}

# Don't want to see this again
❯ git-stage-batch block-file
Blocked file: build/output.js

# It's now in .gitignore
❯ git-stage-batch include
.gitignore :: @@ -1,0 +1,1 @@
[#1] + build/output.js

❯ git commit -m "gitignore: Exclude build artifacts"
```

---

## Surgical Debug Removal

Remove debug code without touching the rest of a hunk:

```
❯ git-stage-batch start
handler.py :: @@ -15,8 +15,10 @@
[#1] + def process_request(data):
[#2] +     print("DEBUG:", data)  # TODO: remove
[#3] +     result = validate(data)
[#4] +     return result

# Remove only the debug line
❯ git-stage-batch discard --line 2

handler.py :: @@ -15,8 +15,9 @@
[#1] + def process_request(data):
[#2] +     result = validate(data)
[#3] +     return result

# Now stage the clean implementation
❯ git-stage-batch include
```

---

## Find Fixup Target

Create fixup commits for historical changes:

```
❯ git-stage-batch start
auth.py :: @@ -23,5 +23,5 @@
[#1] - if age > 18:
[#2] + if age >= 18:

# Which commit does this fix?
❯ git-stage-batch suggest-fixup
Candidate 1: a1b2c3d Add age validation

# That's the one!
❯ git commit --fixup=a1b2c3d

# Later, during interactive rebase:
❯ git rebase -i --autosquash
```

---

## Multi-Pass Workflow

Building multiple commits from mixed changes:

```
# You have unrelated changes scattered across files

❯ git-stage-batch start

# First pass: collect all bug fixes
❯ git-stage-batch include  # bug fix in file1
❯ git-stage-batch skip     # feature in file2
❯ git-stage-batch include  # bug fix in file3
❯ git commit -m "fix: Various bug fixes"

# Second pass: feature implementation
❯ git-stage-batch again
❯ git-stage-batch include  # feature from file2
❯ git-stage-batch skip     # refactoring in file4
❯ git commit -m "feat: Add new feature"

# Third pass: cleanup and refactoring
❯ git-stage-batch again
❯ git-stage-batch include  # refactoring from file4
❯ git commit -m "refactor: Clean up code structure"
```

---

## Status Tracking

Monitor your progress through a session:

```
❯ git-stage-batch status
Session: iteration 1 (in progress)

Current hunk:
  src/auth.py:42
  [#1-3]

Progress this iteration:
  Included:  5 hunks
  Skipped:   2 hunks
  Discarded: 1 hunks
  Remaining: ~3 hunks

Skipped hunks:
  src/config.py:10 [#1]
  src/utils.py:25 [#1-2]
```

This helps you:
- See what you've already processed
- Know how much work remains
- Plan which skipped hunks to handle next

---

## Recovering from Mistakes

Made a wrong decision? Use `abort`:

```
❯ git-stage-batch start
❯ git-stage-batch include
❯ git commit -m "oops wrong commit"

# Wait, that wasn't right!
❯ git-stage-batch abort
✓ Session aborted, repository restored

# Back to where you started
```

The `abort` command:
- Resets HEAD to the session start point
- Restores your working tree
- Undoes all includes, skips, and discards

---

These examples demonstrate the flexibility of git-stage-batch for creating clean, atomic commits from messy working directory state. Combine these techniques to match your workflow.
