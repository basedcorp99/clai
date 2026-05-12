#!/usr/bin/env sh
set -eu

APP_NAME="clai"
BIN_DIR="${CLAI_BIN_DIR:-$HOME/.local/bin}"
OLD_VENV_DIR="${CLAI_VENV_DIR:-$HOME/.local/share/clai-venv}"

rm -f "$BIN_DIR/$APP_NAME"
rm -rf "$OLD_VENV_DIR"

if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q "package $APP_NAME "; then
  pipx uninstall "$APP_NAME" >/dev/null 2>&1 || true
fi

echo "$APP_NAME uninstalled."
