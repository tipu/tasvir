#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/linux-camera"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$APP_DIR" "$BIN_DIR/linux-camera" "$DESKTOP_DIR/linux-camera.desktop"
    echo "Removed Linux Camera. Your photos and videos stay in place."
    exit 0
fi

if ! command -v python3 >/dev/null; then
    echo "python3 is required. Install it with your package manager." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64) GLIBC_MIN=2.34 ;;
    aarch64) GLIBC_MIN=2.39 ;;
    *) echo "Only x86_64 and aarch64 are supported: PySide6 ships no wheels for $(uname -m)." >&2; exit 1 ;;
esac
GLIBC="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}' || true)"
if [[ -z "$GLIBC" ]] || [[ "$(printf '%s\n%s\n' "$GLIBC_MIN" "$GLIBC" | sort -V | head -1)" != "$GLIBC_MIN" ]]; then
    echo "glibc $GLIBC_MIN or newer is required on $(uname -m); this system has ${GLIBC:-no glibc}." >&2
    exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    echo "The Python venv module is missing. On Debian/Ubuntu run: sudo apt install python3-venv" >&2
    exit 1
fi
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/venv/bin/python" -m pip install --quiet -r "$SRC_DIR/requirements.txt"
install -m 0644 "$SRC_DIR/linux_camera.py" "$APP_DIR/linux_camera.py"

QT_DIR="$("$APP_DIR/venv/bin/python" -c 'import os, PySide6; print(os.path.dirname(PySide6.__file__))')"
MISSING="$(ldd "$QT_DIR"/QtMultimedia*.so "$QT_DIR"/QtMultimediaWidgets*.so "$QT_DIR/Qt/plugins/multimedia/libffmpegmediaplugin.so" | awk '/not found/ {print $1}' | sort -u)"
if [[ -n "$MISSING" ]]; then
    echo "Error: the camera backend needs these system libraries:" >&2
    echo "$MISSING" | sed 's/^/  /' >&2
    echo "Install the packages that provide them (Fedora: dnf provides '*/<library>', Debian/Ubuntu: apt-file search <library>) and run install.sh again." >&2
    exit 1
fi
for platform in xcb wayland; do
    PLATFORM_MISSING="$(ldd "$QT_DIR/Qt/plugins/platforms/libq$platform.so" | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ')"
    if [[ -n "$PLATFORM_MISSING" ]]; then
        echo "Warning: the $platform display backend needs: $PLATFORM_MISSING" >&2
    fi
done

cat > "$BIN_DIR/linux-camera" <<LAUNCHER
#!/usr/bin/env bash
exec "$APP_DIR/venv/bin/python" "$APP_DIR/linux_camera.py" "\$@"
LAUNCHER
chmod 0755 "$BIN_DIR/linux-camera"

cat > "$DESKTOP_DIR/linux-camera.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Linux Camera
Comment=Switch cameras, take photos and record videos
Exec=$BIN_DIR/linux-camera
Icon=camera-photo
Terminal=false
Categories=AudioVideo;Video;Recorder;
Keywords=camera;webcam;photo;video;record;
DESKTOP
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "Installed. Start it from the application menu or run: $BIN_DIR/linux-camera"
