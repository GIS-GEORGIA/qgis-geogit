#!/usr/bin/env bash
# Run the unit and headless PyQGIS tests inside an official QGIS image, on a
# copy of the repository (the host files are mounted read-only).
#
#   scripts/test_in_docker.sh 4.0          # QGIS 4.0 (Qt6)
#   scripts/test_in_docker.sh 4.2 3.44     # several versions in a row
#
# A git-lfs binary on the host (GIT_LFS, default: the one on PATH) is mounted
# too, so the tests also run with Git LFS.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
lfs="${GIT_LFS:-$(command -v git-lfs || true)}"
[ $# -eq 0 ] && set -- 4.0
status=0
for tag in "$@"; do
    mount_lfs=()
    [ -n "$lfs" ] && mount_lfs=(-v "$(readlink -f "$lfs"):/usr/local/bin/git-lfs:ro")
    echo "=== qgis/qgis:$tag"
    docker pull -q "qgis/qgis:$tag" >/dev/null || { status=1; continue; }
    docker run --rm -v "$root:/src:ro" "${mount_lfs[@]}" -e QT_QPA_PLATFORM=offscreen -e DEBIAN_FRONTEND=noninteractive "qgis/qgis:$tag" bash -c '
        set -o pipefail
        command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git >/dev/null) || exit 1
        git config --global user.name test && git config --global user.email test@example.com
        cp -r /src /tmp/w && cd /tmp/w && rm -rf server/data
        echo "$(python3 -c "from qgis.core import Qgis; from qgis.PyQt.QtCore import QT_VERSION_STR; print(\"QGIS\", Qgis.QGIS_VERSION, \"Qt\", QT_VERSION_STR)") · $(git lfs version 2>/dev/null || echo no git-lfs)"
        fail=0
        python3 -W ignore -m unittest discover -s tests 2>&1 | tail -1 | sed "s/^/unit: /" | grep -q "unit: OK" \
            && echo "unit: OK" || { echo "unit: FAILED"; python3 -W ignore -m unittest discover -s tests 2>&1 | tail -30; fail=1; }
        for s in smoke_qgis smoke_diff smoke_raster smoke_project smoke_merge smoke_tools smoke_lfs_install smoke_create_repo; do
            out=$(timeout 300 python3 -W ignore tests/$s.py 2>&1)
            # Qt6 may print QThreadStorage notes after the last line, so look for the result anywhere
            if echo "$out" | grep -q "SMOKE OK$"; then echo "$s: OK"; else echo "$s: FAILED"
                echo "$out" | grep -v "QStandardPaths\|propagateSizeHints\|QThreadStorage" | tail -25; fail=1; fi
        done
        exit $fail' || status=1
done
exit $status
