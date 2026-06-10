#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build-executable.sh"

APP_ROOT="${LUMEN_HUB_APP_ROOT:-$HOME/.local/opt/lumen-hub}"
APP_DIR="$APP_ROOT/LumenHub"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
SKIP_BUILD=0
ONEFILE=0

usage() {
  echo "Usage: $0 [--clean] [--skip-install] [--onefile] [--no-build]" >&2
}

desktop_escape_exec() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\$/\\$}
  value=${value//\`/\\\`}
  printf '"%s"' "$value"
}

BUILD_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build)
      SKIP_BUILD=1
      ;;
    --onefile)
      ONEFILE=1
      BUILD_ARGS+=("$1")
      ;;
    --clean|--skip-install)
      BUILD_ARGS+=("$1")
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  "$BUILD_SCRIPT" "${BUILD_ARGS[@]}"
fi

mkdir -p "$APP_ROOT" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
rm -rf "$APP_DIR"

if [[ "$ONEFILE" -eq 1 ]]; then
  SOURCE_EXE="$REPO_ROOT/dist/lumen-hub"
  if [[ ! -x "$SOURCE_EXE" ]]; then
    echo "Packaged executable was not found: $SOURCE_EXE" >&2
    exit 1
  fi
  mkdir -p "$APP_DIR"
  cp "$SOURCE_EXE" "$APP_DIR/lumen-hub"
  chmod +x "$APP_DIR/lumen-hub"
else
  SOURCE_DIR="$REPO_ROOT/dist/LumenHub"
  if [[ ! -x "$SOURCE_DIR/lumen-hub" ]]; then
    echo "Packaged executable was not found: $SOURCE_DIR/lumen-hub" >&2
    exit 1
  fi
  cp -a "$SOURCE_DIR" "$APP_DIR"
fi

ln -sfn "$APP_DIR/lumen-hub" "$BIN_DIR/lumen-hub"
ln -sfn "$APP_DIR/lumen-hub" "$BIN_DIR/lumen-hub-gui"

cat > "$ICON_DIR/lumen-hub.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="g" x1="18" y1="14" x2="108" y2="116" gradientUnits="userSpaceOnUse">
      <stop stop-color="#00D4FF"/>
      <stop offset="0.48" stop-color="#1ED760"/>
      <stop offset="1" stop-color="#FFB000"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="30" fill="#11161C"/>
  <circle cx="64" cy="64" r="45" fill="none" stroke="url(#g)" stroke-width="12"/>
  <circle cx="64" cy="64" r="17" fill="#F8FAFC"/>
  <path d="M64 16v19M64 93v19M16 64h19M93 64h19" stroke="#EAF2F8" stroke-width="8" stroke-linecap="round"/>
</svg>
SVG

DESKTOP_EXEC="$(desktop_escape_exec "$BIN_DIR/lumen-hub-gui")"
cat > "$DESKTOP_DIR/lumen-hub.desktop" <<EOF_DESKTOP
[Desktop Entry]
Type=Application
Name=Lumen Hub
Name[zh_CN]=光枢
Comment=Hardware control center for LCD screens, RGB lighting, fans, and LIAN LI wireless devices
Comment[zh_CN]=屏幕、灯效、风扇与联力无线设备控制中心
Exec=$DESKTOP_EXEC
Icon=lumen-hub
Terminal=false
Categories=Settings;HardwareSettings;
StartupNotify=true
StartupWMClass=LumenHub
EOF_DESKTOP

chmod 644 "$DESKTOP_DIR/lumen-hub.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

if command -v xdg-desktop-menu >/dev/null 2>&1; then
  xdg-desktop-menu forceupdate || true
fi

echo "Installed Lumen Hub: $APP_DIR/lumen-hub"
echo "Command: $BIN_DIR/lumen-hub-gui"
echo "Desktop entry: $DESKTOP_DIR/lumen-hub.desktop"
