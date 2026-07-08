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

# Picks which `python3` binary to build the venv with.
#
# Prefer Homebrew/python.org Python over Apple's system Python: Apple's
# system SQLite lacks loadable-extension support that sqlite-vec needs (see
# companion/corpus.py's module docstring for the full story on why that
# matters). A bare `python3 -m venv` would pick up whichever python3 is
# first on PATH, which is Apple's system one unless Homebrew/python.org
# Python happens to come first.
#
# Preference order: Homebrew (Apple Silicon), Homebrew (Intel), python.org,
# then whatever `python3` resolves to on PATH (today's exact behavior, kept
# as the fallback).
select_python() {
  local candidate
  for candidate in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
  do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "python3"
}

# Writes the launchd plist for the companion service to $1.
#
# $2, if given, is a newline-separated list of KEY=VALUE pairs to inject
# into the plist as an EnvironmentVariables dict (e.g. $'FOO=bar\nBAZ=qux').
# Today no caller passes anything here, so no EnvironmentVariables key is
# written and GEMINI_API_KEY still doesn't reach the launchd-spawned
# process - fixing that means deciding where the key comes from (a .env
# file? the installer's own shell environment?) and then threading a real
# KEY=VALUE list through this same parameter.
write_launchd_plist() {
  local plist_path="$1"
  local env_pairs="${2:-}"
  local env_vars_xml=""

  if [[ -n "$env_pairs" ]]; then
    env_vars_xml+="    <key>EnvironmentVariables</key>"$'\n'
    env_vars_xml+="    <dict>"$'\n'
    while IFS= read -r pair; do
      [[ -z "$pair" ]] && continue
      local key="${pair%%=*}"
      local value="${pair#*=}"
      env_vars_xml+="        <key>$key</key>"$'\n'
      env_vars_xml+="        <string>$value</string>"$'\n'
    done <<< "$env_pairs"
    env_vars_xml+="    </dict>"$'\n'
  fi

  cat > "$plist_path" <<PLIST
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
${env_vars_xml}    <key>StandardOutPath</key>
    <string>$LOG_DIR/companion.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/companion.error.log</string>
</dict>
</plist>
PLIST
}

echo "==> Setting up Python venv at $VENV_DIR"
PYTHON_BIN="$(select_python)"
"$PYTHON_BIN" -m venv "$VENV_DIR"
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
# No env vars to inject yet - see write_launchd_plist's comment above.
write_launchd_plist "$PLIST_PATH"

echo "==> Registering and starting the launchd service"
# Unload first in case it's already registered from a previous install, so
# picking up a changed plist (e.g. a new venv path) doesn't require a manual
# unload step.
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load -w "$PLIST_PATH"

echo "==> Done. Companion should be running at http://127.0.0.1:8787"
echo "    Check with: curl http://127.0.0.1:8787/health"
echo "    Logs: $LOG_DIR/companion.log / companion.error.log"
