#!/usr/bin/env bash
#
# Sets up the Groundhog companion: Python venv, dependencies, a one-time
# shared secret, and a launchd LaunchAgent so it survives login and crashes.
#
# Safe to re-run: the venv is rebuilt in place, and the secret is generated
# only if it doesn't already exist (re-running must not invalidate a secret
# the extension may have already saved).
#
# macOS only - the auto-start mechanism is launchd, by design (see PLAN.md).

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "install.sh only supports macOS (it registers a launchd LaunchAgent)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
SECRET_FILE="$REPO_ROOT/.groundhog-secret"
LABEL="com.groundhog.companion"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$REPO_ROOT/.logs"

echo "==> Setting up Python venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$REPO_ROOT/requirements.txt"

if [[ -f "$SECRET_FILE" ]]; then
  echo "==> Secret already exists at $SECRET_FILE, leaving it untouched"
else
  echo "==> Generating a new shared secret at $SECRET_FILE"
  # 32 bytes of randomness, hex-encoded. Read-only for the owner - this is
  # what gates every request the extension makes, so treat it like a key.
  openssl rand -hex 32 > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
fi

mkdir -p "$LOG_DIR"

echo "==> Writing launchd plist to $PLIST_PATH"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/uvicorn</string>
        <string>companion.app:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8787</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/companion.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/companion.error.log</string>
</dict>
</plist>
PLIST

echo "==> Registering and starting the launchd service"
# Unload first in case it's already registered from a previous install, so
# picking up a changed plist (e.g. a new venv path) doesn't require a manual
# unload step.
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load -w "$PLIST_PATH"

echo "==> Done. Companion should be running at http://127.0.0.1:8787"
echo "    Check with: curl http://127.0.0.1:8787/health"
echo "    Logs: $LOG_DIR/companion.log / companion.error.log"
