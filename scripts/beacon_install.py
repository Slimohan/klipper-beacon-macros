#!/usr/bin/env python3
# Installer and setup checker for the Beacon macro set. Run it through
# install.sh. Uses only the Python standard library.
#
# What it does, in this order:
#   1. checks the printer config for the problems that stop Klipper or the
#      calibration (a stock [safe_z_home], an old [probe], the Z endstop, ...)
#   2. links the extras modules into Klipper / Kalico
#   3. adds the includes and the missing sections to printer.cfg (after a backup)
#   4. adds the Moonraker update entry
# It never deletes or rewrites your own sections. What it cannot fix safely
# is reported with the file and line, and how to fix it.

import argparse
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys

REPO_URL = "https://github.com/Slimohan/klipper-beacon-macros.git"
FOLDER = "beacon_macros"
SAVE_MARK = "#*# <---------------------- SAVE_CONFIG ---------------------->"

USE_COLOR = sys.stdout.isatty()


def color(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


def head(text):
    print("\n" + color("1;36", "== " + text))


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, msg, fix=None):
        self.items.append((level, msg, fix))

    def count(self, level):
        return sum(1 for item in self.items if item[0] == level)

    def show(self):
        labels = {"error": color("1;31", "ERROR  "),
                  "warn": color("1;33", "WARNING"),
                  "info": color("1;34", "NOTE   "),
                  "ok": color("1;32", "OK     ")}
        for level in ("error", "warn", "info", "ok"):
            for lvl, msg, fix in self.items:
                if lvl != level:
                    continue
                print("  %s %s" % (labels[level], msg))
                if fix:
                    for line in fix.split("\n"):
                        print("          " + line)


# ---------------------------------------------------------------------------
# Reading the Klipper config the way Klipper does (enough of it for checks)
# ---------------------------------------------------------------------------
class Section:
    def __init__(self, name, path, line, saved):
        self.name = name
        self.path = path
        self.line = line
        self.saved = saved          # inside the SAVE_CONFIG block
        self.options = {}           # key -> (value, line)


class ConfigReader:
    def __init__(self):
        self.sections = []          # in the order Klipper reads them
        self.files = []
        self.missing_includes = []

    def read(self, path, from_main=True):
        path = os.path.abspath(path)
        if path in self.files:
            return
        self.files.append(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            self.missing_includes.append(path)
            return
        current = None
        in_saved = False
        for idx, raw in enumerate(lines, 1):
            if from_main and raw.startswith(SAVE_MARK):
                in_saved = True
                continue
            text = raw
            if in_saved:
                if not text.startswith("#*#"):
                    continue
                text = text[3:].strip()
            if not in_saved and text[:1] in ("#", ";"):
                continue
            # Klipper strips everything after a hash sign
            text = re.split(r"\s[#;]", " " + text, maxsplit=1)[0][1:] if not in_saved else text
            if not text.strip():
                continue
            m = re.match(r"^\[([^\]]+)\]\s*$", text.strip()) if not text[:1].isspace() else None
            if m:
                name = " ".join(m.group(1).split())
                if name.startswith("include "):
                    pattern = name[len("include "):].strip()
                    full = os.path.join(os.path.dirname(path), pattern)
                    matches = sorted(glob.glob(full))
                    if not matches and not glob.has_magic(pattern):
                        self.missing_includes.append(full)
                    for inc in matches:
                        self.read(inc, from_main=False)
                    current = None
                    continue
                current = Section(name, path, idx, in_saved)
                self.sections.append(current)
                continue
            if current is None or text[:1].isspace():
                continue
            m = re.match(r"^([^:=\s][^:=]*?)\s*[:=]\s*(.*)$", text)
            if m:
                current.options[m.group(1).strip().lower()] = (m.group(2).strip(), idx)

    def find(self, name):
        return [s for s in self.sections if s.name.lower() == name.lower()]

    def has(self, name):
        return bool(self.find(name))

    def value(self, section, key):
        """Effective value (the last one Klipper reads) and where it is set."""
        result = None
        for s in self.find(section):
            if key in s.options:
                value, line = s.options[key]
                result = (value, s.path, line)
        return result


def tilde(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_host(report, trees):
    if not trees:
        report.add("error", "No Klipper or Kalico found (looked for klippy/extras).",
                   "Pass the path: ./install.sh --klipper ~/klipper")
        return
    for tree in trees:
        if os.path.exists(os.path.join(tree, "klippy", "extras", "beacon.py")):
            report.add("ok", "Beacon module found in %s." % tree)
        else:
            report.add("error", "The Beacon module is not installed in %s." % tree,
                       "cd ~ && git clone https://github.com/beacon3d/beacon_klipper.git\n"
                       "./beacon_klipper/install.sh")
    try:
        out = subprocess.run(["ldconfig", "-p"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True,
                             timeout=10).stdout
        if "libopenblas" not in out:
            report.add("error", "libopenblas is missing. The Beacon module (numpy) fails at startup without it.",
                       "sudo apt install libopenblas-dev")
    except (OSError, subprocess.SubprocessError):
        pass


def check_config(report, cfg, config_dir):
    where = lambda p, l: "%s line %d" % (rel(p, config_dir), l)
    own = lambda s: os.sep + FOLDER + os.sep not in s.path

    # the Beacon itself
    beacon = [s for s in cfg.find("beacon") if not s.saved]
    if not beacon:
        report.add("error", "No [beacon] section found.",
                   "Add [beacon] with serial:, x_offset:, y_offset:, see examples/printer.cfg.example.")
    else:
        serial = cfg.value("beacon", "serial")
        if not serial:
            report.add("error", "[beacon] has no serial: line.",
                       "Copy the path from: ls /dev/serial/by-id/")
        elif os.path.isdir("/dev/serial/by-id") and not os.path.exists(serial[0]):
            report.add("warn", "The Beacon serial path in %s does not exist on this machine:\n%s"
                       % (where(serial[1], serial[2]), serial[0]),
                       "Copy the path from: ls /dev/serial/by-id/  (do not type it)")
        xo, yo = cfg.value("beacon", "x_offset"), cfg.value("beacon", "y_offset")
        def num(v):
            try:
                return float(v[0]) if v else 0.0
            except ValueError:
                return 0.0
        if num(xo) == 0 and num(yo) == 0:
            report.add("warn", "Beacon x_offset and y_offset are both 0.",
                       "Measure nozzle centre to Beacon coil centre: +y = Beacon behind the nozzle, +x = to the right.")
        cmax = cfg.value("beacon", "contact_max_hotend_temperature")
        try:
            cmax_v = float(cmax[0]) if cmax else 180.0
        except ValueError:
            cmax_v = 180.0
        if cmax_v < 260:
            report.add("error", "contact_max_hotend_temperature is %g%s. The calibration heats to 250 and stops on the first degree of overshoot."
                       % (cmax_v, (" (%s)" % where(cmax[1], cmax[2])) if cmax else " (default)"),
                       "Set contact_max_hotend_temperature: 260 in [beacon].")
        if cfg.value("beacon", "home_xy_position") is None:
            report.add("warn", "[beacon] has no home_xy_position, so the Beacon does not home Z at a fixed spot.",
                       "Add home_xy_position: <bed centre> and home_z_hop: 5 to [beacon].")

    # what the Beacon replaces
    for s in cfg.find("safe_z_home"):
        report.add("error", "[safe_z_home] in %s. Klipper stops with 'home_xy_position cannot be used with [safe_z_home]'."
                   % where(s.path, s.line),
                   "Comment out the whole section (the Beacon homes Z itself).")
    for s in cfg.find("probe"):
        if not s.saved:
            report.add("error", "Old [probe] section in %s. The Beacon registers itself as the probe."
                       % where(s.path, s.line), "Delete or comment out the section and any include for it.")
    ep = cfg.value("stepper_z", "endstop_pin")
    if ep and ep[0].replace(" ", "") != "probe:z_virtual_endstop":
        report.add("error", "[stepper_z] endstop_pin is '%s' (%s), so Z does not home with the Beacon."
                   % (ep[0], where(ep[1], ep[2])),
                   "Use endstop_pin: probe:z_virtual_endstop, or comment that line out if another file sets it.")
    elif not ep:
        report.add("error", "[stepper_z] has no endstop_pin.", "Add endstop_pin: probe:z_virtual_endstop")
    pe = cfg.value("stepper_z", "position_endstop")
    if pe:
        report.add("error", "[stepper_z] position_endstop is still set (%s)." % where(pe[1], pe[2]),
                   "Comment it out (also in the SAVE_CONFIG block at the end, if it is there):\n"
                   "with the Beacon as Z endstop it must not be set.")
    hr = cfg.value("stepper_z", "homing_retract_dist")
    try:
        hr_v = float(hr[0]) if hr else 5.0
    except ValueError:
        hr_v = 5.0
    if hr_v != 0:
        report.add("warn", "[stepper_z] homing_retract_dist is %s. The Beacon needs 0 (anything else gives occasional wrong Z)."
                   % (("%s (%s)" % (hr[0], where(hr[1], hr[2]))) if hr else "not set (Klipper default 5)"),
                   "Set homing_retract_dist: 0 in [stepper_z], or comment out the line that overrides it.")

    # bed mesh
    if not cfg.has("bed_mesh"):
        report.add("error", "No [bed_mesh] section.", "Add one, see examples/printer.cfg.example.")

    # same option set to different values in two places (the later one wins)
    seen = {}
    names = {}
    for s in cfg.sections:
        if s.saved or not own(s):
            continue
        for key, (val, line) in s.options.items():
            if key == "gcode":
                continue
            if s.name.lower() == "stepper_z" and key in ("endstop_pin", "position_endstop", "homing_retract_dist"):
                continue  # reported above
            names.setdefault(s.name.lower(), s.name)
            seen.setdefault((s.name.lower(), key), []).append((val, s.path, line))
    for (name, key), places in sorted(seen.items()):
        values = set(v for v, _, _ in places)
        files = set(p for _, p, _ in places)
        if len(values) > 1 and len(files) > 1:
            last = places[-1]
            others = ", ".join("%s = %s" % (where(p, l), v) for v, p, l in places[:-1])
            report.add("warn", "[%s] %s is set in more than one file: %s. Klipper uses the last one: %s = %s."
                       % (names[name], key, others, where(last[1], last[2]), last[0]),
                       "Keep it in one place. This is how a placeholder serial path or a stock fan pin silently wins.")

    # values SAVE_CONFIG writes must not sit in an included file
    main_cfg = os.path.join(config_dir, "printer.cfg")
    for name, keys in (("extruder", ("control", "pid_kp", "pid_ki", "pid_kd")),
                       ("heater_bed", ("control", "pid_kp", "pid_ki", "pid_kd")),
                       ("input_shaper", ("shaper_type_x", "shaper_freq_x", "shaper_type_y", "shaper_freq_y"))):
        for s in cfg.find(name):
            if s.saved or os.path.abspath(s.path) == os.path.abspath(main_cfg):
                continue
            hit = [k for k in keys if k in s.options]
            if hit:
                report.add("warn", "[%s] %s is in an included file (%s). SAVE_CONFIG refuses with "
                           "'conflicts with included value' when PID tuning or input shaping saves it."
                           % (name, ", ".join(hit), where(s.path, s.line)),
                           "Comment those lines out in that file and let SAVE_CONFIG write them to printer.cfg.")

    # web interface macros and CANCEL_PRINT
    ui = [f for f in cfg.files if os.path.basename(f) in ("mainsail.cfg", "fluidd.cfg")]
    if ui and any(os.path.basename(f) == "beacon_cancel_print.cfg" for f in cfg.files):
        report.add("error", "beacon_cancel_print.cfg is included together with %s. Klipper refuses to start."
                   % os.path.basename(ui[0]),
                   "Remove the beacon_cancel_print.cfg include. The installer sets up _CLIENT_VARIABLE instead.")
    if not cfg.has("virtual_sdcard") or not cfg.has("pause_resume"):
        report.add("warn", "[virtual_sdcard] or [pause_resume] is missing. START_PRINT needs both (mainsail.cfg / fluidd.cfg provide them).")

    # missing files referenced by includes
    for path in cfg.missing_includes:
        report.add("error", "An [include] points to a file that does not exist: %s. Klipper refuses to start."
                   % rel(path, config_dir), "Remove that [include] line, or restore the file.")

    # flying gantry note
    if cfg.has("quad_gantry_level"):
        report.add("info", "Quad gantry leveling found (flying gantry, Voron 2.4 style). Scan compensation is not proven on "
                   "this kind of printer yet: on a Voron 2.4 it added a front-to-back slope. Everything else applies. "
                   "See 'Scan compensation' in README.md before you enable it.")


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------
def plan_printer_cfg(cfg, main_cfg, text, want_extras, want_print):
    """Returns the new printer.cfg text and a list of change descriptions."""
    changes = []
    included = [os.path.normpath(f) for f in cfg.files]
    config_dir = os.path.dirname(main_cfg)

    def is_included(name):
        return os.path.normpath(os.path.join(config_dir, FOLDER, name)) in included

    inc_lines = []
    for name, want in (("beacon_macros.cfg", True), ("beacon_extras.cfg", want_extras),
                       ("beacon_print.cfg", want_print)):
        if want and not is_included(name):
            inc_lines.append("[include %s/%s]" % (FOLDER, name))
            changes.append("add [include %s/%s]" % (FOLDER, name))

    blocks = []
    if not cfg.has("save_variables"):
        blocks.append("[save_variables]\nfilename: %s" % tilde(os.path.join(config_dir, "variables.cfg")))
        changes.append("add [save_variables] (stores the nozzle expansion coefficient)")
    if not cfg.has("exclude_object"):
        blocks.append("[exclude_object]")
        changes.append("add [exclude_object] (slicers like OrcaSlicer send EXCLUDE_OBJECT_DEFINE)")
    ui = any(os.path.basename(f) in ("mainsail.cfg", "fluidd.cfg") for f in cfg.files)
    if want_print and ui:
        client = cfg.find("gcode_macro _CLIENT_VARIABLE")
        own_client = [s for s in client if os.path.basename(s.path) not in ("mainsail.cfg", "fluidd.cfg")]
        if not own_client:
            blocks.append("# Mainsail/Fluidd CANCEL_PRINT runs END_PRINT, which does the parking\n"
                          "[gcode_macro _CLIENT_VARIABLE]\nvariable_park_at_cancel: False\n"
                          "variable_user_cancel_macro: \"END_PRINT\"\ngcode:")
            changes.append("add [gcode_macro _CLIENT_VARIABLE] so CANCEL_PRINT runs END_PRINT")

    if not inc_lines and not blocks:
        return text, changes

    lines = text.split("\n")
    stop = next((i for i, l in enumerate(lines) if l.startswith("#*#")), len(lines))
    last_inc = None
    for i, l in enumerate(lines[:stop]):
        if re.match(r"^\[include\s", l):
            last_inc = i
    if last_inc is None:
        # after the leading comment block
        last_inc = -1
        for i, l in enumerate(lines[:stop]):
            if l.strip() and not l.lstrip().startswith(("#", ";")):
                break
            last_inc = i
    added = ["# --- added by beacon_macros/install.sh %s ---" % datetime.date.today().isoformat()]
    added += inc_lines
    for b in blocks:
        added += ["", b]
    added += ["# --- end of beacon_macros/install.sh additions ---", ""]
    new_lines = lines[:last_inc + 1] + added + lines[last_inc + 1:]
    return "\n".join(new_lines), changes


MOONRAKER_BLOCK = """
[update_manager beacon_macros]
type: git_repo
path: {path}
origin: {url}
primary_branch: main
managed_services: klipper
is_system_service: False
"""


def ask(question, default, assume_yes):
    if assume_yes:
        return default
    if not sys.stdin.isatty():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input("%s %s " % (question, hint)).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(
        prog="install.sh",
        description="Install the Beacon macro set and check the printer config.")
    ap.add_argument("trees", nargs="*", help="Klipper or Kalico folders (default: ~/klipper ~/kalico)")
    ap.add_argument("--klipper", action="append", default=[], help="Klipper or Kalico folder, can be repeated")
    ap.add_argument("--config", default=os.path.expanduser("~/printer_data/config"), help="Klipper config folder")
    ap.add_argument("--check", action="store_true", help="only check, change nothing")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--yes", "-y", action="store_true", help="answer every question with the default")
    ap.add_argument("--print-start", dest="print_start", action="store_true", default=None,
                    help="include beacon_print.cfg (RatOS style START_PRINT)")
    ap.add_argument("--no-print-start", dest="print_start", action="store_false")
    ap.add_argument("--no-extras", action="store_true", help="do not install the extras modules")
    ap.add_argument("--no-moonraker", action="store_true", help="do not add the Moonraker update entry")
    ap.add_argument("--links-only", action="store_true", help="only link the extras modules (old install.sh behaviour)")
    ap.add_argument("--copy", action="store_true", help="copy the extras modules instead of linking them")
    args = ap.parse_args()
    copy = args.copy or bool(os.environ.get("COPY"))
    change = not (args.check or args.dry_run)

    candidates = args.klipper + args.trees
    if not candidates:
        candidates = [os.path.expanduser("~/klipper"), os.path.expanduser("~/kalico")]
    trees = [os.path.abspath(os.path.expanduser(t)) for t in candidates
             if os.path.isdir(os.path.join(os.path.expanduser(t), "klippy", "extras"))]

    # -- links only (compatible with the old installer) ----------------------
    if args.links_only:
        if not trees:
            print("no klippy/extras folder found, pass the path to Klipper or Kalico")
            return 1
        link_extras(here, trees, copy, change)
        return 0

    config_dir = os.path.abspath(os.path.expanduser(args.config))
    main_cfg = os.path.join(config_dir, "printer.cfg")
    print(color("1", "Beacon macro set installer") + "  (%s)" % ("check only" if args.check else
          "dry run, nothing is changed" if args.dry_run else "install"))
    print("config folder: %s" % config_dir)

    if not os.path.isfile(main_cfg):
        print(color("1;31", "ERROR") + " %s not found. Use --config to point at your config folder." % main_cfg)
        return 1
    expected = os.path.join(config_dir, FOLDER)
    if os.path.realpath(here) != os.path.realpath(expected):
        print(color("1;31", "ERROR") + " this folder must be %s (the includes use that path).\n"
              "        It is %s.\n        cd %s && git clone %s %s"
              % (expected, here, config_dir, REPO_URL, FOLDER))
        return 1

    head("Checking")
    report = Report()
    check_host(report, trees)
    cfg = ConfigReader()
    cfg.read(main_cfg)
    check_config(report, cfg, config_dir)
    own_client = [s for s in cfg.find("gcode_macro _CLIENT_VARIABLE")
                  if os.path.basename(s.path) not in ("mainsail.cfg", "fluidd.cfg")]
    if own_client and not any("variable_user_cancel_macro" in s.options for s in own_client):
        s = own_client[0]
        report.add("warn", "Your [gcode_macro _CLIENT_VARIABLE] (%s line %d) has no user_cancel_macro, so CANCEL_PRINT will not run END_PRINT."
                   % (rel(s.path, config_dir), s.line),
                   "Add to it:\nvariable_park_at_cancel: False\nvariable_user_cancel_macro: \"END_PRINT\"")
    if not report.items:
        report.add("ok", "No problems found.")
    report.show()

    if args.check:
        return 1 if report.count("error") else 0

    # -- what to install ------------------------------------------------------
    head("Installing")
    want_extras = bool(trees) and not args.no_extras
    want_print = args.print_start
    if want_print is None:
        already = os.path.normpath(os.path.join(config_dir, FOLDER, "beacon_print.cfg")) in \
            [os.path.normpath(f) for f in cfg.files]
        want_print = already or ask("Use the RatOS style START_PRINT / END_PRINT (beacon_print.cfg)?",
                                    True, args.yes)

    if want_extras:
        link_extras(here, trees, copy, change)

    with open(main_cfg, encoding="utf-8") as f:
        text = f.read()
    new_text, changes = plan_printer_cfg(cfg, main_cfg, text, want_extras, want_print)
    if changes:
        print("  printer.cfg:")
        for c in changes:
            print("    - " + c)
        if change:
            print(color("1;33", "  Close printer.cfg in the Mainsail/Fluidd editor first,") +
                  " or saving there later puts the old copy back.")
            if ask("  Write these changes to printer.cfg?", True, args.yes):
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = main_cfg + ".bak-" + stamp
                shutil.copy2(main_cfg, backup)
                with open(main_cfg, "w", encoding="utf-8") as f:
                    f.write(new_text)
                print("  written, backup: %s" % backup)
            else:
                print("  skipped")
    else:
        print("  printer.cfg: nothing to add")

    variables = os.path.join(config_dir, "variables.cfg")
    if not os.path.exists(variables):
        print("  create %s" % variables)
        if change:
            open(variables, "a").close()

    if not args.no_moonraker:
        moon = os.path.join(config_dir, "moonraker.conf")
        if os.path.isfile(moon):
            with open(moon, encoding="utf-8") as f:
                mtext = f.read()
            if "[update_manager beacon_macros]" in mtext:
                print("  moonraker.conf: update entry already there")
            else:
                print("  moonraker.conf: add [update_manager beacon_macros] (updates from the web interface)")
                if change:
                    shutil.copy2(moon, moon + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
                    with open(moon, "a", encoding="utf-8") as f:
                        f.write(MOONRAKER_BLOCK.format(path=tilde(expected), url=REPO_URL))
        else:
            print("  moonraker.conf not found, update entry skipped")

    # -- next steps -----------------------------------------------------------
    head("Next")
    errors = report.count("error")
    if args.dry_run:
        print("  This was a dry run. Run ./install.sh without --dry-run to apply it.")
    if errors:
        print(color("1;31", "  Fix the %d error(s) above before restarting Klipper," % errors) +
              " otherwise it stops at the first one. Then run ./install.sh --check again.")
    print("  1. sudo systemctl restart klipper moonraker")
    print("  2. In the console: BEACON_PREFLIGHT_CHECK")
    print("  3. Before BEACON_FULL_CALIBRATE: PID tune both heaters, clean the nozzle, run BEACON_POKE_TEST.")
    print("     See 'Before the first calibration' in README.md.")
    if want_print:
        print("  4. Slicer start G-code: one START_PRINT line, see 'Slicer start and end gcode' in README.md.")
    return 1 if errors else 0


def link_extras(here, trees, copy, change):
    for tree in trees:
        dest = os.path.join(tree, "klippy", "extras")
        for src in sorted(glob.glob(os.path.join(here, "klippy_extras", "*.py"))):
            target = os.path.join(dest, os.path.basename(src))
            if change:
                if os.path.lexists(target):
                    os.remove(target)
                if copy:
                    shutil.copy2(src, target)
                else:
                    os.symlink(src, target)
        print("  extras modules %s%s into %s" % ("" if change else "would be ",
              "copied" if copy else "linked", dest))
        if change:
            exclude = os.path.join(tree, ".git", "info", "exclude")
            if os.path.isdir(os.path.dirname(exclude)):
                names = [os.path.basename(p) for p in glob.glob(os.path.join(here, "klippy_extras", "*.py"))]
                try:
                    existing = open(exclude, encoding="utf-8").read()
                except OSError:
                    existing = ""
                new = [("klippy/extras/" + n) for n in names if ("klippy/extras/" + n) not in existing]
                if new:
                    with open(exclude, "a", encoding="utf-8") as f:
                        f.write("\n# beacon_macros extras\n" + "\n".join(new) + "\n")


if __name__ == "__main__":
    sys.exit(main())
