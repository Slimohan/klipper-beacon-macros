#!/bin/sh
# Install the Beacon macro set: checks your printer config, links the extras
# modules into Klipper / Kalico, adds the includes and missing sections to
# printer.cfg (after a backup) and the Moonraker update entry.
#
#   ./install.sh              check, then install (asks before writing)
#   ./install.sh --check      only check the config, change nothing
#   ./install.sh --dry-run    show what would change, change nothing
#   ./install.sh --help       all options
#
# The old behaviour (only link the extras modules) is ./install.sh --links-only
here=$(cd "$(dirname "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo "python3 is needed"; exit 1; }
exec python3 "$here/scripts/beacon_install.py" "$@"
