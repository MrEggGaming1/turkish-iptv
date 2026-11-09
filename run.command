#!/bin/bash
# Double-click (or ./run.command) to set up and launch Xtream TV on macOS.
cd "$(dirname "$0")"

pause_exit() { echo; read -n 1 -s -r -p "Press any key to close..."; exit 1; }

# Homebrew location (Apple Silicon or Intel), even if not on PATH
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)"
done
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh then run this again."
  pause_exit
fi
BREW="$(brew --prefix)"

if [ ! -f "$BREW/lib/libmpv.dylib" ]; then
  echo "Installing mpv (provides libmpv)..."
  brew install mpv || { echo "mpv install failed."; pause_exit; }
fi

# Need Python 3.10-3.13 from Homebrew (the macOS built-in python3 is not suitable)
PY=""
for v in 3.13 3.12 3.11 3.10; do
  if [ -x "$BREW/bin/python$v" ]; then PY="$BREW/bin/python$v"; break; fi
done
if [ -z "$PY" ]; then
  echo "Installing Python 3.12 via Homebrew..."
  brew install python@3.12 || { echo "Python install failed."; pause_exit; }
  PY="$BREW/bin/python3.12"
fi
echo "Using $PY"

# Rebuild the venv if it is missing or broken
if [ ! -x .venv/bin/python ]; then
  rm -rf .venv
  echo "Creating virtual environment..."
  "$PY" -m venv .venv || { echo "Could not create virtual environment."; pause_exit; }
fi

REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.deps_installed 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "Installing Python packages (first run or after an update, may take a minute)..."
  .venv/bin/python -m pip install --upgrade pip || pause_exit
  .venv/bin/python -m pip install -r requirements.txt || { echo "Package install failed."; pause_exit; }
  echo "$REQ_HASH" > .venv/.deps_installed
fi

export DYLD_FALLBACK_LIBRARY_PATH="$BREW/lib:${DYLD_FALLBACK_LIBRARY_PATH}"
.venv/bin/python main.py || { echo; echo "The app exited with an error (see above)."; pause_exit; }
