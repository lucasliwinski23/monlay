#!/usr/bin/env bash
#
# Install monlay
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/monlay"
SERVICE_DIR="$HOME/.config/systemd/user"

info()  { printf '\033[1;34m::\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m::\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m::\033[0m %s\n' "$*"; }
error() { printf '\033[1;31m::\033[0m %s\n' "$*" >&2; }

# ── 1. Check Python 3 ──────────────────────────────────────────────────
info "Checking Python 3..."
if ! command -v python3 &>/dev/null; then
    error "python3 not found. Please install Python 3.10 or later."
    exit 1
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYMINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYMAJOR" -lt 3 ] || { [ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 10 ]; }; then
    error "Python 3.10+ required, found $PYVER"
    exit 1
fi
ok "Python $PYVER"

# ── 2. Check / install system packages ─────────────────────────────────
MISSING_PKGS=()

python3 -c "import gi" 2>/dev/null || MISSING_PKGS+=(python3-gi)
python3 -c "import yaml" 2>/dev/null || MISSING_PKGS+=(python3-yaml)

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    info "Installing missing system packages: ${MISSING_PKGS[*]}"
    sudo apt install -y "${MISSING_PKGS[@]}"
else
    ok "System packages already installed (python3-gi, python3-yaml)"
fi

# ── 3. Create config directory ──────────────────────────────────────────
if [ ! -d "$CONFIG_DIR" ]; then
    info "Creating config directory: $CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
fi

# ── 4. Copy example config if no config exists ─────────────────────────
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    info "Copying example config to $CONFIG_DIR/config.yaml"
    cp "$REPO_DIR/config/example-config.yaml" "$CONFIG_DIR/config.yaml"
    warn "Edit $CONFIG_DIR/config.yaml or use 'monlay save-profile' to create profiles"
else
    ok "Config already exists: $CONFIG_DIR/config.yaml"
fi

# ── 5. Install package with pip ─────────────────────────────────────────
info "Installing monlay..."

PIP_ARGS=(--user)

# On Ubuntu 24.04+ (Python 3.12+), pip refuses to install into the
# system/user environment without --break-system-packages.
if [ "$PYMINOR" -ge 12 ]; then
    PIP_ARGS+=(--break-system-packages)
fi

python3 -m pip install "${PIP_ARGS[@]}" -e "$REPO_DIR"

# Verify monlay is on PATH
if ! command -v monlay &>/dev/null; then
    MONLAY_PATH="$HOME/.local/bin/monlay"
    if [ -f "$MONLAY_PATH" ]; then
        warn "monlay installed at $MONLAY_PATH but not on PATH"
        warn "Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
    else
        error "monlay binary not found after install"
        exit 1
    fi
else
    ok "monlay installed: $(command -v monlay)"
fi

# ── 6. Install systemd service ─────────────────────────────────────────
info "Installing systemd user service..."
mkdir -p "$SERVICE_DIR"
cp "$REPO_DIR/systemd/monlay.service" "$SERVICE_DIR/"
systemctl --user daemon-reload
ok "Service file installed to $SERVICE_DIR/monlay.service"

# ── 7. Enable and start service ────────────────────────────────────────
info "Enabling and starting monlay service..."
systemctl --user enable monlay.service
systemctl --user restart monlay.service
ok "Service enabled and started"

# ── 8. Status ───────────────────────────────────────────────────────────
echo
info "Installation complete. Service status:"
echo
systemctl --user status monlay.service --no-pager || true
echo
info "Next steps:"
echo "  1. Arrange monitors in GNOME Settings"
echo "  2. Run: monlay save-profile <name>"
echo "  3. Repeat for each monitor combination"
echo "  4. Verify: monlay list-profiles"
