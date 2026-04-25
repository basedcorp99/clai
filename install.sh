#!/usr/bin/env sh
set -eu

APP_NAME="clai"
VENV_DIR="${CLAI_VENV_DIR:-$HOME/.local/share/clai-venv}"
BIN_DIR="${CLAI_BIN_DIR:-$HOME/.local/bin}"

cd "$(dirname "$0")"

if command -v pipx >/dev/null 2>&1; then
  pipx install --force --editable .
  echo "$APP_NAME installed with pipx."
  exit 0
fi

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
  else
    echo "install.sh: could not find python3 or python" >&2
    exit 1
  fi
fi

"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --editable .

mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/$APP_NAME" "$BIN_DIR/$APP_NAME"

cat <<EOF
$APP_NAME installed to:
  $BIN_DIR/$APP_NAME

If '$APP_NAME' is not on your PATH, add this to your shell config:
  export PATH=\"$BIN_DIR:\$PATH\"
EOF
