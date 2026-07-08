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
ENV_FILE="$REPO_ROOT/.env"
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
# then whatever `python3` resolves to on PATH - unless that's specifically
# Apple's system Python, in which case we refuse rather than silently
# building the venv on it (see below).
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

  # No Homebrew/python.org Python found. Fall back to whatever `python3`
  # resolves to on PATH - but if that's specifically Apple's system Python
  # (/usr/bin/python3), refuse: it's EOL, linked against an ancient LibreSSL,
  # and its SQLite lacks loadable-extension support (see companion/corpus.py).
  # Anything else on PATH (pyenv, asdf, etc.) is a reasonable python3 and is
  # allowed through unchanged - this is not a version check.
  local resolved
  resolved="$(command -v python3 || true)"
  if [[ -z "$resolved" || "$resolved" == "/usr/bin/python3" ]]; then
    echo "No modern Python found - install one first: brew install python@3.12" >&2
    exit 1
  fi
  echo "$resolved"
}

# Writes the launchd plist for the companion service to $1.
#
# $2, if given, is a newline-separated list of KEY=VALUE pairs to inject
# into the plist as an EnvironmentVariables dict (e.g. $'FOO=bar\nBAZ=qux').
# Called with GEMINI_API_KEY=... below (see resolve_gemini_api_key), so the
# launchd-spawned companion process has it from the moment launchd starts
# it, with no separate `launchctl setenv` step required.
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

# Resolves the Gemini API key to inject into the launchd plist's
# EnvironmentVariables (companion/verdict.py's genai.Client() reads it
# straight from the process environment, so it has to actually be in the
# plist, not just in the shell that ran this script).
#
# Preference order: already exported in the installer's own shell env, then
# a GEMINI_API_KEY=... line in the gitignored .env file at the repo root,
# then an interactive prompt - which persists the answer to .env so future
# re-runs of install.sh don't ask again. If none of those yield a key (e.g.
# a non-interactive run with no .env), print a warning and continue without
# one rather than blocking install on it.
resolve_gemini_api_key() {
  if [[ -n "${GEMINI_API_KEY:-}" ]]; then
    echo "$GEMINI_API_KEY"
    return 0
  fi

  if [[ -f "$ENV_FILE" ]]; then
    local existing
    existing="$(grep -m1 '^GEMINI_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
    if [[ -n "$existing" ]]; then
      echo "$existing"
      return 0
    fi
  fi

  local entered=""
  read -r -p "Enter your Gemini API key (saved to $ENV_FILE for future runs, leave blank to skip): " entered || true
  if [[ -z "$entered" ]]; then
    echo "==> No Gemini API key provided - /verdict calls will fail until GEMINI_API_KEY is set (add it to $ENV_FILE and re-run install.sh)." >&2
    return 0
  fi

  echo "GEMINI_API_KEY=$entered" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "$entered"
}

echo "==> Resolving Gemini API key"
GEMINI_API_KEY_VALUE="$(resolve_gemini_api_key)"

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
ENV_PAIRS=""
if [[ -n "$GEMINI_API_KEY_VALUE" ]]; then
  ENV_PAIRS="GEMINI_API_KEY=$GEMINI_API_KEY_VALUE"
fi
write_launchd_plist "$PLIST_PATH" "$ENV_PAIRS"

echo "==> Registering and starting the launchd service"
# Unload first in case it's already registered from a previous install, so
# picking up a changed plist (e.g. a new venv path) doesn't require a manual
# unload step.
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load -w "$PLIST_PATH"

echo "==> Done. Companion should be running at http://127.0.0.1:8787"
echo "    Check with: curl http://127.0.0.1:8787/health"
echo "    Logs: $LOG_DIR/companion.log / companion.error.log"
