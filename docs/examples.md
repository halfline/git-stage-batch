# Examples

Common workflows and use cases for git-stage-batch.

## Basic: Stage Some, Skip Others

Separate features into different commits:

```bash
$ git status
modified:   feature1.py
modified:   feature2.py

$ git-stage-batch start
feature1.py :: @@ -10,5 +10,5 @@
[#1] - old_implementation()
[#2] + new_implementation()

$ git-stage-batch include

feature2.py :: @@ -5,3 +5,4 @@
[#1] + experimental_code()

$ git-stage-batch skip
No pending hunks.

$ git commit -m "feature1: Implement new algorithm"

$ git-stage-batch again
feature2.py :: @@ -5,3 +5,4 @@
[#1] + experimental_code()

$ git-stage-batch discard
No pending hunks.
```

## Line-Level: Separate Mixed Changes

You accidentally mixed two changes in one hunk:

```bash
$ git-stage-batch start
config.py :: @@ -1,5 +1,7 @@
[#1] + FEATURE_FLAG = True
      DATABASE_URL = "..."
[#2] - TIMEOUT = 30
[#3] + TIMEOUT = 60
[#4] + DEBUG = False

# Feature flag and timeout are separate concerns
# Stage only the timeout change
$ git-stage-batch include-line 2,3
config.py :: @@ -1,5 +1,7 @@
[#1] + FEATURE_FLAG = True
      DATABASE_URL = "..."
[#2] - TIMEOUT = 30
[#3] + TIMEOUT = 60
[#4] + DEBUG = False

$ git commit -m "config: Increase timeout to 60s"

$ git-stage-batch again
config.py :: @@ -1,5 +1,7 @@
[#1] + FEATURE_FLAG = True
      DATABASE_URL = "..."
[#4] + DEBUG = False

# Now handle the feature flag and debug setting
```

## File-Level: Stage Entire File

You have multiple hunks in one file that all belong together:

```bash
$ git-stage-batch start
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash()
[#2] + new_hash()

# This file has 5 more hunks, all part of the same refactor
$ git-stage-batch include-file
# All hunks in auth.py are now staged

$ git commit -m "auth: Migrate to new hashing algorithm"
```

## Machine-Readable: Script Integration

Check if there's work to do before processing:

```bash
#!/bin/bash

# Start staging
git-stage-batch start || exit 0

# Process until done
while git-stage-batch show --porcelain; do
    # Get current hunk info
    status=$(git-stage-batch status --porcelain)
    current=$(echo "$status" | jq -r '.current_hunk')

    echo "Processing: $current"

    # Apply your logic here
    if [[ $current == *"test"* ]]; then
        git-stage-batch skip
    else
        git-stage-batch include
    fi
done

echo "All hunks processed"
```

## Workflow: Atomic Commits

Create a series of atomic commits from mixed changes:

```bash
# You have changes spanning multiple concerns
$ git diff --stat
auth.py        | 10 +++++-----
database.py    |  5 +++--
config.py      |  3 ++-
logging.py     |  8 ++++----

# Session 1: Authentication changes
$ git-stage-batch start
# Include all auth.py hunks
$ git-stage-batch include-file
# Skip everything else
$ git-stage-batch skip    # database.py
$ git-stage-batch skip    # config.py
$ git-stage-batch skip    # logging.py
$ git commit -m "auth: Implement OAuth2 flow"

# Session 2: Database changes
$ git-stage-batch again
$ git-stage-batch skip    # Skip auth.py (already done)
$ git-stage-batch include # Include database.py
$ git-stage-batch skip    # Skip config.py
$ git-stage-batch skip    # Skip logging.py
$ git commit -m "database: Add connection pooling"

# Continue until all changes are committed...
```

## Advanced: Selective Line Staging

Mix include and skip at line level:

```bash
$ git-stage-batch start
utils.py :: @@ -10,8 +10,8 @@
[#1] - def helper1():
[#2] + def improved_helper1():
[#3] -     return old_logic()
[#4] +     return new_logic()
[#5] - def helper2():
[#6] + def improved_helper2():
[#7] -     return old_logic()
[#8] +     return new_logic()

# Stage only helper1 changes (lines 1-4)
$ git-stage-batch include-line 1-4
utils.py :: @@ -10,8 +10,8 @@
[#1] - def helper1():
[#2] + def improved_helper1():
[#3] -     return old_logic()
[#4] +     return new_logic()
[#5] - def helper2():
[#6] + def improved_helper2():
[#7] -     return old_logic()
[#8] +     return new_logic()

$ git commit -m "utils: Improve helper1 implementation"

# Now handle helper2 in next commit
$ git-stage-batch again
```

## Interactive: Quick Manual Review

Use interactive mode for hands-on control:

```bash
$ git-stage-batch --interactive

# First hunk
feature.py :: @@ ...
Action: i

# Second hunk - has mixed changes
mixed.py :: @@ ...
Action: l
Line selection: i 1,3,5
# Back to main menu after line selection

# Third hunk
debug.py :: @@ ...
Action: d
Confirm discard (y/N): y

# Continue until done...
```

## Block Files: Ignore Generated Code

Permanently exclude build artifacts or generated files:

```bash
$ git-stage-batch start
dist/bundle.js :: @@ ...

# This is generated, never want to commit it
$ git-stage-batch block-file
# Adds to .gitignore and internal blocked list

# Later, if you need to unblock:
$ git-stage-batch unblock-file dist/bundle.js
```

## Fast Workflow: Short Aliases

Minimize typing with short aliases:

```bash
$ git-stage-batch start
[hunk displayed]

$ git-stage-batch        # No command = include
[next hunk]

$ git-stage-batch s      # skip
[next hunk]

$ git-stage-batch        # include
[next hunk]

$ git-stage-batch il 1-5 # include-line
[updated hunk]

$ git-stage-batch        # include remaining
No pending hunks.

$ git commit -m "..."
$ git-stage-batch a      # again
```

## Status Checking

Monitor progress during a session:

```bash
$ git-stage-batch status
Session: iteration 1 (in progress)

Current hunk:
  auth.py:10
  [#1-4]

Progress this iteration:
  Included:  2 hunks
  Skipped:   1 hunks
  Discarded: 0 hunks
  Remaining: ~3 hunks

Skipped hunks:
  config.py:20 [#1-2]

# Check from scripts
$ git-stage-batch status --porcelain | jq '.progress.included'
2

# Count skipped hunks
$ git-stage-batch status --porcelain | jq '.skipped_hunks | length'
1

# Get current iteration
$ git-stage-batch status --porcelain | jq '.session.iteration'
1
```

## Tips

1. **Use `again` frequently** - After each commit, run `git-stage-batch again` to review skipped hunks
2. **Combine line operations** - You can include some lines, skip others in the same hunk
3. **Check status often** - Use `git-stage-batch status` to see what's left
4. **File operations are fast** - Use `include-file` when all hunks belong together
5. **Interactive mode for learning** - Start with `--interactive` to get familiar with the workflow

## Next Steps

- [Commands Reference](commands.md) - Full command documentation
- [AI Assistants](ai-assistants.md) - Configure for automated workflows
