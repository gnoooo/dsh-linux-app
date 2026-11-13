#!/usr/bin/env bash
# Install dsh-desktop for the current user:
#   * a virtualenv that can import the system PyGObject,
#   * the app icon and a .desktop entry in the XDG data directories.
#
# Re-running is safe and upgrades an existing install in place. Works on any
# distribution as long as PyGObject with GTK 3 + WebKitGTK 4.1 is present; the
# check below decides that by importing, never by guessing package names.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="org.deepseek.DshDesktop"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
VENV="$ROOT/.venv"

say() { printf '%s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }

# PyGObject is a distribution package built for the *system* Python. `uv` would
# otherwise be free to download its own CPython, which cannot see it, so the
# interpreter is pinned to the system one (override with DSH_DESKTOP_PYTHON).
SYSTEM_PYTHON="${DSH_DESKTOP_PYTHON:-$(command -v python3 || true)}"
[ -n "$SYSTEM_PYTHON" ] && [ -x "$SYSTEM_PYTHON" ] || die "python3 not found on PATH"

system_version() {
    "$SYSTEM_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])'
}

dependency_hint() {
    PYTHONPATH="$ROOT/src" "$SYSTEM_PYTHON" -m dsh_desktop.distro 2>/dev/null || true
}

# -- preflight ---------------------------------------------------------------
# Creating a virtualenv before knowing the bindings work just leaves a broken
# install behind, so fail early with the right command for this distribution.
say "==> checking GTK 3 / WebKitGTK 4.1 bindings ($SYSTEM_PYTHON)"
if ! gi_error="$("$SYSTEM_PYTHON" - <<'PY' 2>&1
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2
PY
)"; then
    printf 'install: PyGObject with GTK 3 and WebKitGTK 4.1 is required.\n' >&2
    dependency_hint | sed 's/^/  /' >&2
    printf '  Import error: %s\n' "$gi_error" >&2
    exit 1
fi

# -- virtualenv --------------------------------------------------------------
# A leftover venv from another distribution (or another Python) cannot import
# this system's PyGObject, so recreate it when the interpreter version differs.
if [ -d "$VENV" ]; then
    venv_version="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
    if [ "$venv_version" != "$(system_version)" ]; then
        say "==> recreating $VENV (python $venv_version -> $(system_version))"
        rm -rf "$VENV"
    fi
fi

say "==> creating virtualenv at $VENV (system site-packages enabled)"
if command -v uv >/dev/null 2>&1; then
    # Never let uv fetch its own Python: it would not see the system PyGObject.
    UV_PYTHON_DOWNLOADS=never \
        uv venv --system-site-packages --allow-existing --python "$SYSTEM_PYTHON" "$VENV"
    UV_PROJECT_ENVIRONMENT="$VENV" \
        uv pip install --python "$VENV/bin/python" --quiet -e "$ROOT"
else
    if [ ! -d "$VENV" ]; then
        "$SYSTEM_PYTHON" -m venv --system-site-packages "$VENV" || die \
            "could not create a virtualenv. Install 'uv', or the venv/pip packages
  for this distribution (e.g. python-pip on Arch, python3-venv on Debian)."
    fi
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -e "$ROOT"
fi

# -- verify ------------------------------------------------------------------
if ! gi_report="$("$VENV/bin/python" - <<'PY' 2>&1
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2
print(f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, "
      f"WebKitGTK {WebKit2.get_major_version()}.{WebKit2.get_minor_version()}")
PY
)"; then
    die "the virtualenv cannot import the bindings: $gi_report"
fi
say "    $gi_report"

EXEC="$VENV/bin/dsh-desktop"
[ -x "$EXEC" ] || die "expected console script at $EXEC"

# A shim in ~/.local/bin makes `dsh-desktop` reachable by name, which is how a
# launcher (dsh-romeo, a script, a user) discovers it.
BIN_DIR="$HOME/.local/bin"
if mkdir -p "$BIN_DIR" 2>/dev/null && ln -sfn "$EXEC" "$BIN_DIR/dsh-desktop" 2>/dev/null; then
    say "==> linked $BIN_DIR/dsh-desktop"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) say "    note: $BIN_DIR is not on PATH; run the absolute path, or add it to PATH" ;;
    esac
else
    say "    note: could not link $BIN_DIR/dsh-desktop; run $EXEC"
fi

# -- desktop integration -----------------------------------------------------
say "==> installing icon to $ICON_DIR"
install -Dm644 "$ROOT/src/dsh_desktop/icon.svg" "$ICON_DIR/$APP_ID.svg"

say "==> installing desktop entry to $DESKTOP_DIR"
mkdir -p "$DESKTOP_DIR"
sed -e "s|@EXEC@|$EXEC|g" \
    -e "s|@ICON@|$APP_ID|g" \
    -e "s|@WMCLASS@|$APP_ID|g" \
    "$ROOT/assets/dsh-desktop.desktop.in" > "$DESKTOP_DIR/$APP_ID.desktop"
chmod 644 "$DESKTOP_DIR/$APP_ID.desktop"

# Both helpers come from optional packages and are only a cache warm-up.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" \
        >/dev/null 2>&1 || true
fi

say ""
say "Installed. Launch \"DeepSeek Harness\" from your application menu, or run:"
say "    $EXEC"
say ""
say "Diagnostics:  $EXEC --check"
say "Uninstall:    $ROOT/scripts/uninstall.sh"
