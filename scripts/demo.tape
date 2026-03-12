# VHS tape file for git-stage-batch demo
# This tape expects DEMO_DIR environment variable to point to a prepared git repo
# Run via: ./scripts/generate-demo.sh

Output demo.gif

Set FontSize 14
Set Width 1200
Set Height 600
Set Theme "Catppuccin Mocha"

# Start in the demo directory
Type "cd $DEMO_DIR"
Enter
Sleep 500ms

Type "# Check current git status"
Sleep 500ms
Enter
Type "git status"
Enter
Sleep 2s

Type "# Start staging hunks"
Sleep 500ms
Enter
Type "git-stage-batch start"
Enter
Sleep 2.5s

Type "# Discard debug trash (line 3)"
Sleep 500ms
Enter
Type "git-stage-batch discard --line 3"
Enter
Sleep 2.5s

Type "# Include bug fix (lines 1-3)"
Sleep 500ms
Enter
Type "git-stage-batch include --line 1-3"
Enter
Sleep 2.5s

Type "# Skip validation for now"
Sleep 500ms
Enter
Type "git-stage-batch skip"
Enter
Sleep 2.5s

Type "# Skip artifact for now"
Sleep 500ms
Enter
Type "git-stage-batch skip"
Enter
Sleep 2s

Type "# Commit bug fix"
Sleep 500ms
Enter
Type "git commit -m 'Fix age check (>= not >)' -q"
Enter
Sleep 1s

Type "# Re-process remaining changes"
Sleep 500ms
Enter
Type "git-stage-batch again"
Enter
Sleep 2.5s

Type "# Include validation"
Sleep 500ms
Enter
Type "git-stage-batch include"
Enter
Sleep 2s

Type "# Skip build artifact again"
Sleep 500ms
Enter
Type "git-stage-batch skip"
Enter
Sleep 2s

Type "# Commit validation"
Sleep 500ms
Enter
Type "git commit -m 'Add name validation' -q"
Enter
Sleep 1s

Type "# Now deal with build artifact"
Sleep 500ms
Enter
Type "git-stage-batch again"
Enter
Sleep 2.5s

Type "# Block build artifact"
Sleep 500ms
Enter
Type "git-stage-batch block-file"
Enter
Sleep 2.5s

Type "# Include .gitignore update"
Sleep 500ms
Enter
Type "git-stage-batch include"
Enter
Sleep 2.5s

Type "# Commit .gitignore"
Sleep 500ms
Enter
Type "git commit -m 'Ignore build artifacts' -q"
Enter
Sleep 1s

Type "# All done!"
Sleep 500ms
Enter
Type "git status"
Enter
Sleep 3s
