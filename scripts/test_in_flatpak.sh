#!/usr/bin/env bash
# Run the tests inside the Flatpak QGIS (org.qgis.qgis), calling the computer's git
# through flatpak-spawn. The permission is given for this run only.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$HOME/.cache/geogit-flatpak-test"      # seen by both the sandbox and the host git
rm -rf "$tmp" && mkdir -p "$tmp"
flatpak run --talk-name=org.freedesktop.Flatpak --env=TMPDIR="$tmp" --env=QT_QPA_PLATFORM=offscreen \
    --env=PYTHONPATH=/app/share/qgis/python --command=sh org.qgis.qgis -c "
    cd '$root'
    python3 -c 'from qgis.core import Qgis; import sys; sys.path.insert(0, \"geogit\"); import git_backend as gb; print(\"QGIS\", Qgis.QGIS_VERSION, \"· git:\", gb.git_mode())'
    fail=0
    python3 -W ignore -m unittest discover -s tests 2>&1 | tail -1 | grep -q '^OK' && echo 'unit: OK' || { echo 'unit: FAILED'; fail=1; }
    for s in smoke_qgis smoke_diff smoke_raster smoke_project smoke_merge smoke_tools smoke_lfs_install; do
        if timeout 300 python3 -W ignore tests/\$s.py 2>&1 | grep -q 'SMOKE OK$'; then echo \"\$s: OK\"; else echo \"\$s: FAILED\"; fail=1; fi
    done
    exit \$fail" 2>&1 | grep -v '^Gtk'
status=${PIPESTATUS[0]}
rm -rf "$tmp"
exit $status
