#!/usr/bin/env sh
set -eu

APP_NAME="clai"
VENV_DIR="${CLAI_VENV_DIR:-$HOME/.local/share/clai-venv}"
BIN_DIR="${CLAI_BIN_DIR:-$HOME/.local/bin}"

if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q "package $APP_NAME "; then
  pipx uninstall "$APP_NAME"
  echo "$APP_NAME uninstalled from pipx."
  exit 0
fi

rm -f "$BIN_DIR/$APP_NAME"
rm -rf "$VENV_DIR"

echo "$APP_NAME uninstalled."
