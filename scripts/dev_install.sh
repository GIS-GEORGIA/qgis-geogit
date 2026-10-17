#!/usr/bin/env bash
# Link the plugin into the default QGIS profile so edits show up after a plugin reload.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)/geogit"
dest="${QGIS_PLUGINS:-$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins}/geogit"
if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "$dest exists and is not a link; remove it first" >&2
    exit 1
fi
ln -sfn "$src" "$dest"
echo "linked $dest -> $src"
