#!/usr/bin/env bash
# Remove the user-level integration installed by scripts/install.sh.
# User data (WebKit profile, cookies, config, logs) is kept unless --purge.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="org.deepseek.DshDesktop"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"

PURGE=0
case "${1:-}" in
    --purge) PURGE=1 ;;
    "") ;;
    *) printf 'usage: %s [--purge]\n' "$0" >&2; exit 2 ;;
esac

SHIM="$HOME/.local/bin/dsh-desktop"

for file in "$DESKTOP_FILE" "$ICON_FILE" "$SHIM"; do
    if [ -e "$file" ]; then
        rm -f -- "$file"
        printf 'removed %s\n' "$file"
    fi
done

if [ "$PURGE" -eq 1 ]; then
    for dir in \
        "${XDG_DATA_HOME:-$HOME/.local/share}/dsh-desktop" \
        "${XDG_CACHE_HOME:-$HOME/.cache}/dsh-desktop" \
        "${XDG_CONFIG_HOME:-$HOME/.config}/dsh-desktop" \
        "${XDG_STATE_HOME:-$HOME/.local/state}/dsh-desktop"
    do
        if [ -d "$dir" ]; then
            rm -rf -- "$dir"
            printf 'removed %s\n' "$dir"
        fi
    done
fi

if [ -d "$ROOT/.venv" ]; then
    rm -rf -- "$ROOT/.venv"
    printf 'removed %s\n' "$ROOT/.venv"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

printf 'uninstalled\n'
