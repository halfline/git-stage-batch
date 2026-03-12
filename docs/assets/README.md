# Assets

## Generating the Demo GIF

**Note:** The `demo.gif` file is **not** checked into git. It's generated during the documentation deployment process.

To regenerate the demo animation locally:

### Quick Method (Recommended)

```bash
./scripts/generate-demo.sh
```

This script:
- Auto-downloads VHS and ttyd if not installed (to `~/.cache/git-stage-batch/`)
- Creates a temporary throwaway git repository
- Runs the VHS demo script in isolation
- Generates `docs/assets/demo.gif`
- Cleans up automatically

**Requirements:**
- `ffmpeg` - `sudo dnf install ffmpeg` (Fedora) or `brew install ffmpeg` (macOS)
- `git` and `curl`
- VHS and ttyd are auto-downloaded if not present

### Manual Method

If you prefer to run VHS manually:

1. **Create a throwaway directory:**
   ```bash
   DEMO_DIR=$(mktemp -d)
   cd "$DEMO_DIR"
   git init
   git config user.name "Demo User"
   git config user.email "demo@example.com"
   ```

2. **Copy and run the tape:**
   ```bash
   cp /path/to/git-stage-batch/scripts/demo.tape .
   vhs demo.tape
   ```

3. **Copy the result:**
   ```bash
   mv demo.gif /path/to/git-stage-batch/docs/assets/demo.gif
   ```

### Deploying

To deploy updated documentation to GitHub Pages:

```bash
# Generate the demo GIF first (required - not in git)
./scripts/generate-demo.sh

# Deploy to GitHub Pages
uv run mkdocs gh-deploy
```

The demo GIF must be generated before each deployment since it's not checked into version control.

### Alternative: Manual Recording

You can also record manually with any screen recording tool:

1. Set up a clean terminal with good contrast
2. Run through the demo workflow
3. Convert to GIF (keep under 5MB)
4. Save as `docs/assets/demo.gif`
