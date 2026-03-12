#!/bin/bash
# Generate demo.gif in a throwaway repository
set -e

# Check required dependencies
for cmd in ffmpeg git curl; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is not installed"
        exit 1
    fi
done

# Check for VHS or download it
VHS_CMD=""
if command -v vhs &> /dev/null; then
    VHS_CMD="vhs"
    echo "Using system VHS: $(which vhs)"
else
    # Download VHS to cache directory
    VHS_VERSION="0.8.0"
    CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/git-stage-batch"
    VHS_DIR="$CACHE_DIR/vhs-$VHS_VERSION"
    VHS_CMD="$VHS_DIR/vhs"

    if [ ! -f "$VHS_CMD" ]; then
        echo "VHS not found in system, downloading v$VHS_VERSION to $VHS_DIR..."
        mkdir -p "$VHS_DIR"

        # Detect architecture
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) VHS_ARCH="x86_64" ;;
            aarch64|arm64) VHS_ARCH="arm64" ;;
            *) echo "Error: Unsupported architecture $ARCH"; exit 1 ;;
        esac

        # Download and extract
        VHS_URL="https://github.com/charmbracelet/vhs/releases/download/v${VHS_VERSION}/vhs_${VHS_VERSION}_Linux_${VHS_ARCH}.tar.gz"
        curl -sL "$VHS_URL" | tar xz -C "$VHS_DIR"

        # Move binary to expected location
        mv "$VHS_DIR/vhs_${VHS_VERSION}_Linux_${VHS_ARCH}/vhs" "$VHS_CMD"
        rm -rf "$VHS_DIR/vhs_${VHS_VERSION}_Linux_${VHS_ARCH}"
        chmod +x "$VHS_CMD"
        echo "✓ VHS downloaded to $VHS_CMD"
    else
        echo "Using cached VHS: $VHS_CMD"
    fi
fi

# Check for ttyd or download it
TTYD_CMD=""
if command -v ttyd &> /dev/null; then
    TTYD_CMD="ttyd"
    echo "Using system ttyd: $(which ttyd)"
else
    # Download ttyd to cache directory
    TTYD_VERSION="1.7.7"
    CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/git-stage-batch"
    TTYD_DIR="$CACHE_DIR/ttyd-$TTYD_VERSION"
    TTYD_CMD="$TTYD_DIR/ttyd"

    if [ ! -f "$TTYD_CMD" ]; then
        echo "ttyd not found in system, downloading v$TTYD_VERSION to $TTYD_DIR..."
        mkdir -p "$TTYD_DIR"

        # Detect architecture
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) TTYD_ARCH="x86_64" ;;
            aarch64|arm64) TTYD_ARCH="aarch64" ;;
            *) echo "Error: Unsupported architecture $ARCH"; exit 1 ;;
        esac

        # Download ttyd binary
        TTYD_URL="https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${TTYD_ARCH}"
        curl -sL "$TTYD_URL" -o "$TTYD_CMD"
        chmod +x "$TTYD_CMD"
        echo "✓ ttyd downloaded to $TTYD_CMD"
    else
        echo "Using cached ttyd: $TTYD_CMD"
    fi

    # Add to PATH for VHS
    export PATH="$TTYD_DIR:$PATH"
fi

# Find repository root
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
cd "$REPO_ROOT"

if [ ! -f "scripts/demo.tape" ]; then
    echo "Error: scripts/demo.tape not found"
    exit 1
fi

# Create temporary directory
DEMO_DIR=$(mktemp -d -t git-stage-batch-demo.XXXXXX)
echo "Creating demo in temporary directory: $DEMO_DIR"

# Install dev version of git-stage-batch in a venv for the demo
echo "Installing dev version in demo environment..."
(cd "$DEMO_DIR" && uv venv --quiet && uv pip install --quiet "$REPO_ROOT")
export PATH="$DEMO_DIR/.venv/bin:$PATH"

# Set up demo repository "off camera"
(
    cd "$DEMO_DIR"
    git init -q
    git config user.name "Demo User"
    git config user.email "demo@example.com"

    # Create initial files with a bug
    cat > app.py << 'EOF'
def is_valid_age(age):
    # Bug: should be >= 18, not > 18
    return age > 18

def process_user(name, age):
    if is_valid_age(age):
        return f"Welcome {name}"
    return "Too young"
EOF

    cat > utils.py << 'EOF'
def format_name(name):
    return name.upper()
EOF

    cat > .gitignore << 'EOF'
__pycache__/
*.pyc
.venv/
EOF

    git add .
    git commit -q -m "Initial commit"

    # Create working tree changes:
    # 1. Debug print "here" (trash to discard)
    # 2. Fix the >= bug
    # 3. Add input validation (separate feature)
    # 4. Add logging (another feature)
    # 5. Build artifact (to block)

    cat > app.py << 'EOF'
def is_valid_age(age):
    print("here")  # Debug trash
    return age >= 18  # Fix: was > 18

def process_user(name, age):
    if not name:  # Add: input validation
        return "Name required"
    if is_valid_age(age):
        return f"Welcome {name}"
    return "Too young"
EOF

    cat > utils.py << 'EOF'
def format_name(name):
    return name.upper()
EOF

    # Create a build artifact
    mkdir -p build
    echo "artifact" > build/output.dat
)

# Create a temporary directory for VHS output
VHS_DIR=$(mktemp -d -t git-stage-batch-vhs.XXXXXX)

# Run VHS with DEMO_DIR and PATH environment variables
echo "Running VHS to generate demo.gif..."
(cd "$VHS_DIR" && DEMO_DIR="$DEMO_DIR" PATH="$DEMO_DIR/.venv/bin:$PATH" "$VHS_CMD" "$REPO_ROOT/scripts/demo.tape")

# Move the generated GIF to docs/assets/
if [ -f "$VHS_DIR/demo.gif" ]; then
    mv "$VHS_DIR/demo.gif" "$REPO_ROOT/docs/assets/demo.gif"
    rm -rf "$VHS_DIR"
    echo "✓ Demo GIF generated successfully: docs/assets/demo.gif"
    ls -lh "$REPO_ROOT/docs/assets/demo.gif"
else
    rm -rf "$VHS_DIR"
    echo "Error: demo.gif was not generated"
    exit 1
fi

# Clean up
rm -rf "$DEMO_DIR"
echo "✓ Cleaned up temporary directory"
