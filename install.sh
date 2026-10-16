#!/bin/sh
# Install the Beacon macro set extras (Klipper extras modules).
# Usage: ./install.sh [path to klipper or kalico, ...]   (default: ~/klipper ~/kalico)
# Set COPY=1 to copy instead of symlinking.
set -e
here=$(cd "$(dirname "$0")" && pwd)
[ $# -eq 0 ] && set -- "$HOME/klipper" "$HOME/kalico"
found=0
for tree in "$@"; do
    dest="$tree/klippy/extras"
    [ -d "$dest" ] || continue
    found=1
    for f in "$here"/klippy_extras/*.py; do
        name=$(basename "$f")
        rm -f "$dest/$name"
        if [ -n "$COPY" ]; then cp "$f" "$dest/$name"; else ln -s "$f" "$dest/$name"; fi
    done
    echo "installed into $dest"
done
[ $found -eq 1 ] || { echo "no klippy/extras folder found, pass the path to Klipper or Kalico"; exit 1; }
echo "Restart Klipper (sudo service klipper restart). Then include beacon_extras.cfg after beacon_macros.cfg."
