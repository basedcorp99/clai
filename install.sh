#!/usr/bin/env sh
set -eu

APP_NAME="clai"
BIN_DIR="${CLAI_BIN_DIR:-$HOME/.local/bin}"
OLD_VENV_DIR="${CLAI_VENV_DIR:-$HOME/.local/share/clai-venv}"
GO="${GO:-go}"

cd "$(dirname "$0")"

if ! command -v "$GO" >/dev/null 2>&1; then
  echo "install.sh: could not find Go. Install Go or set GO=/path/to/go" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

"$GO" build -trimpath -o "$TMP_DIR/$APP_NAME" .

mkdir -p "$BIN_DIR"
rm -f "$BIN_DIR/$APP_NAME"
cp "$TMP_DIR/$APP_NAME" "$BIN_DIR/$APP_NAME"
chmod 0755 "$BIN_DIR/$APP_NAME"

# Clean up the pre-Go installer layout if present.
rm -rf "$OLD_VENV_DIR"
if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q "package $APP_NAME "; then
  pipx uninstall "$APP_NAME" >/dev/null 2>&1 || true
fi

cat <<EOF
$APP_NAME installed to:
  $BIN_DIR/$APP_NAME

If '$APP_NAME' is not on your PATH, add this to your shell config:
  export PATH="$BIN_DIR:\$PATH"
EOF
