# Beacon Macros for Klipper and Kalico

A standalone macro set for the [Beacon](https://beacon3d.com) contact/proximity probe, extracted from the Beacon workflow in [RatOS](https://github.com/Rat-OS/RatOS-configurator). It runs on stock Klipper and on Kalico. It needs no RatOS and no `math` or `do` Jinja extensions. The core macros need no extra Python modules. The three optional extras (scan compensation, adaptive heat soak, multi-point true zero) ship as small Klipper extras modules in `klippy_extras/`, see "Extras".

It also has an optional RatOS style `START_PRINT` / `END_PRINT` / `CANCEL_PRINT` (`beacon_print.cfg`) that follows the order of the RatOS print start step by step. See "Print start and end".

License: GPL-3.0, like RatOS. See "License" at the end.

## Quick start

For a printer that already prints, with a Beacon that is wired up and has a `[beacon]` section:

```
cd ~ && git clone https://github.com/beacon3d/beacon_klipper.git && ./beacon_klipper/install.sh   # if not done yet
sudo apt install libopenblas-dev                                                               # if not done yet
cd ~/printer_data/config && git clone https://github.com/Slimohan/klipper-beacon-macros beacon_macros
cd beacon_macros && ./install.sh --check      # lists what to fix in your config, changes nothing
./install.sh                                  # installs (asks before writing, backs up printer.cfg)
sudo systemctl restart klipper moonraker
```

Then `BEACON_PREFLIGHT_CHECK` in the console, the steps in "Before the first calibration", and `BEACON_FULL_CALIBRATE`. For `START_PRINT`, put the one-line start G-code from "Slicer start and end gcode" into your slicer.

**Flying gantry printers (Voron 2.4 and similar):** everything works, except that scan compensation is not proven on them yet. See the note in "Scan compensation".

## What you get

| Command | What it does |
|---|---|
| `BEACON_FULL_CALIBRATE` | One command that runs the whole calibration (cold contact calibration, poke test, thermal expansion, hot calibration) and finishes with `SAVE_CONFIG`. `BED_TEMP=85 CHAMBER_TEMP=0` |
| `BEACON_INITIAL_CALIBRATION` | Contact calibration and Beacon model on a cold bed |
| `BEACON_FINAL_CALIBRATION` | Contact calibration and model on a hot bed |
| `BEACON_POKE_TEST` | Five contact pokes. Rates the mechanical noise of your machine |
| `BEACON_CALIBRATE_NOZZLE_TEMP_OFFSET` | Measures hotend thermal expansion at two temperatures and saves it |
| `BEACON_MEASURE_BEACON_OFFSET` | Compares scan and contact at nine bed positions. Shows gantry twist or a loose mount |
| `BEACON_TRUE_ZERO` | Finds contact Z=0 on the hot bed. Optional nozzle wipe, random (fuzzy) spot, fresh model |
| `BEACON_PROBE_CLEAN X= Y=` | Series of contact probes that wipe filament off the nozzle tip |
| `BEACON_APPLY_NOZZLE_TEMP_OFFSET` | Starts a Z offset that follows the nozzle temperature |
| `BEACON_RESET_NOZZLE_TEMP_OFFSET` | Removes that offset |
| `BEACON_BED_MESH` | Bed mesh by proximity or contact, as configured |
| `BEACON_HOME_Z` | Z homing for `homing_override` |
| `BEACON_PREFLIGHT_CHECK` | Checks your setup for common mistakes. Moves nothing |
| `START_PRINT`, `END_PRINT` | *(beacon_print.cfg)* RatOS style print start and end. Same slicer parameters and hook macros as RatOS. See "Print start and end" |
| `CANCEL_PRINT` | *(beacon_cancel_print.cfg)* Runs `END_PRINT` and cancels, like RatOS. Not with Mainsail/Fluidd macros, see "Cancelling a print" |
| `BEACON_ADAPTIVE_MESH`, `BEACON_PRIME_BLOB`, `BEACON_PROBE_FOR_PRIMING` | *(beacon_print.cfg)* The adaptive mesh and prime blob pieces behind `START_PRINT` |
| `BEACON_HEAT_SOAK` | *(extras)* Waits for bed expansion: adaptive with the Beacon, or the fixed `bed_heat_soak_time` |
| `BEACON_CREATE_SCAN_COMPENSATION_MESH` | *(extras)* Creates a compensation mesh from contact and proximity probes. `BED_TEMP=85 CHAMBER_TEMP=0 PROFILE=auto KEEP_TEMP_MESHES=0` |
| `BEACON_APPLY_SCAN_COMPENSATION` | *(extras)* Adds a compensation mesh to the loaded proximity mesh. `PROFILE=` |
| `BEACON_PREFLIGHT_SCAN_COMPENSATION` | *(extras)* Errors out before a print if the compensation mesh is missing or does not fit the bed temperature |

## Before you install

This macro set sits on top of the Beacon's own Klipper module. Get that working first:

1. **Install the Beacon module** ([beacon_klipper](https://github.com/beacon3d/beacon_klipper)): `cd ~ && git clone https://github.com/beacon3d/beacon_klipper.git && ./beacon_klipper/install.sh`, then restart Moonraker so it appears in the update manager.
2. **Install OpenBLAS.** The Beacon module uses numpy. On a fresh Raspberry Pi OS it fails at startup with `Importing the numpy C-extensions failed ... libopenblas.so.0: cannot open shared object file`. Fix: `sudo apt install libopenblas-dev`, then restart Klipper.
3. **Add a `[beacon]` section** with `serial:` (copy the path from `ls /dev/serial/by-id/`, do not type it) and the probe offsets (see below).
4. **Remove what the Beacon replaces** from a stock config (Voron and most printer configs have these). Klipper stops with an error for each one left in:

| Leftover in `printer.cfg` | Error you get | What to do |
|---|---|---|
| `[safe_z_home]` | `home_xy_position cannot be used with [safe_z_home]` | Delete or comment out the whole section, also any you copied from an example. The Beacon homes Z itself, with `home_xy_position` and `home_z_hop` in `[beacon]`. |
| `[probe]` (inductive probe, Klicky, Tap) | `Option 'pin' is not valid in section 'probe'` | Delete the section and any `[include]` for it. The Beacon registers itself as the probe. |
| `endstop_pin: ^PA0` (or similar) in `[stepper_z]` | Z homes on the old switch, or not at all | `[stepper_z]` needs `endstop_pin: probe:z_virtual_endstop` and `homing_retract_dist: 0`, and **no** `position_endstop`. |

   Watch out for order: if you put the Beacon settings in their own `beacon.cfg`, a `[stepper_z]` or `[bed_mesh]` value that is still in `printer.cfg` **after** the include wins over it. Comment out the old `endstop_pin`, `position_endstop` and `homing_retract_dist` lines in `printer.cfg` itself.

### Probe offsets

`x_offset` and `y_offset` in `[beacon]` are where the Beacon coil sits relative to the nozzle, in printer coordinates: `+y` is towards the back, `+x` towards the right. Measure centre of nozzle to centre of the Beacon board. A Beacon 25 mm behind the nozzle is `x_offset: 0`, `y_offset: 25`. A wrong sign puts every contact and mesh point twice the offset away from where the macros expect it.

Keep these consistent:

- `home_xy_position` in `[beacon]`, `variable_safe_home_x/y` in `_BEACON_SETTINGS` (default `"middle"`) and `zero_reference_position` in `[bed_mesh]` should all be the same point, normally the bed centre (175, 175 on a 350 mm bed).
- `mesh_min` / `mesh_max` in `[bed_mesh]` must be reachable by the Beacon with your offsets. With `y_offset: 25`, `mesh_min` Y below about 25 would need the nozzle at a negative Y.
- Leveling points (`points` in `[z_tilt]` or `[quad_gantry_level]`) are **nozzle** positions. Klipper probes with the Beacon at that position plus the offset, so pick points where the Beacon is still over the bed. With the Beacon behind the nozzle, keep the back points about `y_offset` away from the back edge. The macros run `Z_TILT_ADJUST` or `QUAD_GANTRY_LEVEL`, whichever is configured, in the same places (print start, calibration, compensation mesh) and set Z again afterwards.

## Install

### With the installer (recommended)

1. Clone the repo into your config folder as `beacon_macros` (the includes depend on that name):
   `cd ~/printer_data/config && git clone https://github.com/Slimohan/klipper-beacon-macros beacon_macros`
2. Run `./install.sh --check` in that folder. It reads `printer.cfg` and every file it includes, the way Klipper does, and lists what would stop Klipper or the calibration, with the file and line: a leftover `[safe_z_home]` or `[probe]`, the Z endstop, `position_endstop`, `homing_retract_dist`, `contact_max_hotend_temperature`, missing probe offsets, the Beacon module or libopenblas missing, an option set to different values in two files (a placeholder serial path or a stock fan pin that silently wins), PID or input shaper values in an included file (`SAVE_CONFIG` refuses those), and `beacon_cancel_print.cfg` together with Mainsail/Fluidd. It changes nothing. Fix what it reports in your own files; it does not edit your sections.
3. Run `./install.sh`. It
   - links the extras modules into `~/klipper` and/or `~/kalico` (and hides them from Klipper's git status),
   - asks whether you want the RatOS style `START_PRINT` (`beacon_print.cfg`),
   - backs up `printer.cfg` (`printer.cfg.bak-<date>`) and adds, only where missing, the includes, `[save_variables]`, `[exclude_object]`, adaptive heat soak switched on (`variable_beacon_adaptive_heat_soak: True`, as the RatOS configurator does for Beacon printers; the macro default is off, as in RatOS) and, with Mainsail/Fluidd, a `_CLIENT_VARIABLE` so their `CANCEL_PRINT` runs `END_PRINT`. The additions are one marked block after your last include,
   - creates `variables.cfg`,
   - adds an `[update_manager beacon_macros]` entry to `moonraker.conf`, so updates come through the Mainsail/Fluidd update manager like everything else.

   Close `printer.cfg` in the web editor first. Running it again is safe: it only adds what is still missing.
4. Restart Klipper and Moonraker (`sudo systemctl restart klipper moonraker`), then run `BEACON_PREFLIGHT_CHECK` in the console. If it says "Unknown command", see "Troubleshooting".
5. Prepare for the calibration (see below), then run `G28` and `BEACON_FULL_CALIBRATE`. It heats the bed and nozzle, probes the bed a lot, then saves and restarts.

Options: `--dry-run` (show the changes, write nothing), `--yes` (no questions, take the defaults), `--print-start` / `--no-print-start`, `--no-extras`, `--no-moonraker`, `--config <folder>` (another config folder), `--klipper <folder>` (another Klipper or Kalico folder), `--copy` (copy the modules instead of linking them), `--links-only` (only link the modules, the old behaviour). `./install.sh --help` lists them.

### By hand

1. Clone as above.
2. Add to `printer.cfg`, in this order:
   ```
   [include beacon_macros/beacon_macros.cfg]
   [include beacon_macros/beacon_extras.cfg]   # only after ./install.sh --links-only
   [include beacon_macros/beacon_print.cfg]    # optional, RatOS style START_PRINT
   ```
   Do not include `beacon_extras.cfg` without the modules, Klipper would refuse to start.
3. Make sure these sections exist (see `examples/printer.cfg.example`):
   - `[beacon]` with `contact_max_hotend_temperature: 260`. It must be at least 10 degrees above `variable_beacon_contact_expansion_cal_temp_high` (default 250): the hotend briefly overshoots its target, and with both at 250 the calibration stops with "Current hotend temperature 251.1 exceeds maximum allowed temperature 250.0".
   - `[save_variables]` (stores the thermal expansion coefficient)
   - `[bed_mesh]` with a zero reference: either `zero_reference_position` at the safe home position, or none at all when you install the extras. See "Zero reference and fuzzy true zero" below.
   - For `beacon_print.cfg`: `[virtual_sdcard]` and `[pause_resume]` (Mainsail/Fluidd provide them). For a `CANCEL_PRINT` that runs `END_PRINT`, see "Cancelling a print": with Mainsail or Fluidd, do **not** include `beacon_cancel_print.cfg`.
4. Optional, for updates from the web interface, add to `moonraker.conf`:
   ```
   [update_manager beacon_macros]
   type: git_repo
   path: ~/printer_data/config/beacon_macros
   origin: https://github.com/Slimohan/klipper-beacon-macros.git
   primary_branch: main
   managed_services: klipper
   is_system_service: False
   ```
5. Restart, `BEACON_PREFLIGHT_CHECK`, then the calibration as above.

### Before the first calibration

- **PID tune both heaters first:** `PID_CALIBRATE HEATER=extruder TARGET=250`, `PID_CALIBRATE HEATER=heater_bed TARGET=85`, then one `SAVE_CONFIG`. The hot contact and thermal expansion steps need stable temperatures. If your `[extruder]` lives in an included file (a toolhead board config, for example) and has `control:` / `pid_Kp:` lines, `SAVE_CONFIG` refuses with "section 'extruder' option 'control' conflicts with included value". Comment those lines out in the included file and run `SAVE_CONFIG` again; the results stay in memory until you restart.
- **Clean the nozzle, inside and out.** Contact probing touches the bed with the nozzle. A string or blob on the tip touches first and gives false triggers. New hotends (Bambu style ones, for example) can come with test filament inside, which oozes once hot: heat to 250, let it ooze, wipe, repeat until clean, and wipe once more while it cools.
- **Run `BEACON_POKE_TEST` once.** Every poke should trigger near `z=0` (a few hundredths of a millimetre) with a low latency. A poke that triggers well above the bed (for example `z=1.95`) is a false trigger: look for filament on the nozzle, a loose toolhead, Beacon mount or carriage, or vibration, and fix it before calibrating. Slightly higher latency and overshoot on a warm nozzle are normal.
- **Close `printer.cfg` in the Mainsail/Fluidd editor.** `BEACON_FULL_CALIBRATE` and `BEACON_CREATE_SCAN_COMPENSATION_MESH` end with `SAVE_CONFIG`. An editor tab that is still open saves its old copy over the file when you press save, and that silently undoes the calibration result and any change made from the command line.
- **Stay at the printer** for the first run, with the emergency stop at hand. Stopping it is safe at any point; run `FIRMWARE_RESTART` afterwards.

### Zero reference and fuzzy true zero

The bed mesh needs a zero reference: the spot where the mesh height counts as zero, which should be where the true zero was measured. By default `BEACON_TRUE_ZERO` measures at a random ("fuzzy") spot within `variable_beacon_contact_start_print_true_zero_fuzzy_radius` (20 mm) of the safe home position, so the hot nozzle does not touch the same spot of the sheet every print. A fixed `zero_reference_position` in `[bed_mesh]` and a fuzzy true zero do not go together: `START_PRINT` uses the fixed position, and the mesh can be off by the bed height difference between the two spots. Pick one way:

| | Fuzzy true zero | `[bed_mesh]` | Result |
|---|---|---|---|
| **A** | off: `variable_beacon_contact_start_print_true_zero_fuzzy_position: False` | `zero_reference_position` at the safe home position | Exact, works without the extras. The nozzle touches the same spot every print, which can mark a smooth PEI sheet over time. |
| **B** | on (default) | no `zero_reference_position`, extras installed | `START_PRINT` references every mesh to where the true zero was measured (`[beacon_zero_reference]`). Spreads the wear. A mesh made outside `START_PRINT` is not referenced. |

`BEACON_PREFLIGHT_CHECK` warns about the combinations that do not work. A sensible start is A for the first prints and B once printing is reliable. Scan compensation works best with A.

### Cancelling a print

`beacon_cancel_print.cfg` gives a RatOS style `CANCEL_PRINT` that runs `END_PRINT`. It renames Klipper's `CANCEL_PRINT`, so it **cannot** be used with `mainsail.cfg` or `fluidd.cfg`, which rename it already (Klipper refuses to start). With those, leave `beacon_cancel_print.cfg` out and let their `CANCEL_PRINT` call `END_PRINT` instead. Add to `printer.cfg` (if you already have a `_CLIENT_VARIABLE` macro, add the two variables to it):

```
[gcode_macro _CLIENT_VARIABLE]
variable_park_at_cancel: False
variable_user_cancel_macro: "END_PRINT"
gcode:
```

`park_at_cancel: False` because `END_PRINT` parks already; without it the toolhead parks twice.

## Settings

All settings are `variable_` entries of `[gcode_macro _BEACON_SETTINGS]` in `beacon_settings.cfg`, with a comment for each. The names match RatOS, so values from a RatOS `printer.cfg` can be pasted in.

Do not edit the file. Instead, override single values in your own `printer.cfg`, after the include:

```
[gcode_macro _BEACON_SETTINGS]
variable_beacon_contact_true_zero_temp: 150
```

Settings you will most likely touch: `beacon_contact_true_zero_temp`, `safe_home_x/y`, `z_hop`, `beacon_contact_wipe_x/y` and `extruder` (if your extruder is not called `extruder`).

## Typical print start

(For a complete RatOS style `START_PRINT` see "Print start and end" below. This is the do-it-yourself version.)

```
G28
M190 S{BED}
BEACON_HEAT_SOAK          # extras: adaptive heat soak, or the fixed bed_heat_soak_time
BEACON_TRUE_ZERO          # nozzle wipe, heats nozzle to the true zero temperature, contact Z=0
BEACON_BED_MESH           # with the extras and scan compensation enabled, the compensation is applied
M109 S{EXTRUDER}
```

Run it with the bed already at printing temperature, after any Z tilt or gantry leveling. At the end of the print call `BEACON_RESET_NOZZLE_TEMP_OFFSET`.

### Thermal expansion offset

The offset is `(target - true zero temp) x coefficient / 100`. It becomes active once `BEACON_TRUE_ZERO` has run.

- **Automatic:** enable `[include beacon_heater_hooks.cfg]` in `beacon_macros.cfg`. It wraps `M104`, `M109` and `SET_HEATER_TEMPERATURE` so every temperature change updates the offset. Do not use it if another config already renames those commands.
- **Manual:** call `_BEACON_UPDATE_NOZZLE_TEMP_OFFSET` after you change the extruder temperature.

Setting a target of 0 leaves the offset unchanged, so it does not jump at the end of a print.

### Hooks for LEDs and a chamber heater

`_BEACON_HOOK_LED_START`, `_BEACON_HOOK_LED_END`, `_BEACON_HOOK_LED_ERROR`, `_BEACON_HOOK_CHAMBER_ON` and `_BEACON_HOOK_CHAMBER_OFF` do nothing by default. Redefine one in your `printer.cfg` after the include to use it.

## Extras

These three features need Python, so they are Klipper extras modules. They are optional: the macros detect whether they are installed. Adaptive heat soak falls back to the fixed time, and an enabled but missing module is reported by `BEACON_PREFLIGHT_CHECK` and stops `BEACON_FULL_CALIBRATE` before it does any work.

Install: `./install.sh` links them into `~/klipper` and/or `~/kalico` and adds `[include beacon_macros/beacon_extras.cfg]` after `beacon_macros.cfg` (by hand: `./install.sh --links-only`, then add the include yourself). Restart Klipper afterwards; after an update through Moonraker the links stay valid, Moonraker restarts Klipper. numpy and scipy are not required. If scipy happens to be installed it is used for one interpolation step, otherwise a pure Python fallback runs.

### Multi-point true zero (`[beacon_true_zero_correction]`)

Wraps `BEACON_AUTO_CALIBRATE`. After the normal contact calibration it probes 6, 10 or 12 more contact points on a small random patch around the safe home position, drops outliers, averages the 3 lowest samples (the calibration itself counts as one sample at 0) and applies the result as a Z offset named `true_zero_correction`. That makes the true zero less sensitive to a single bad touch (filament blob, textured sheet). The offset is cleared when Z is homed and when the motors turn off. All macros in this set call `BEACON_AUTO_CALIBRATE SKIP_MULTIPOINT_PROBING=1` where an extra correction would be wrong (cold calibration, models, Z homing). Only `BEACON_TRUE_ZERO` uses the multi-point version. Use `SKIP_MULTIPOINT_PROBING=1` yourself to bypass it.

Options (repeat the section in `printer.cfg` to change): `disabled`, `z_rejection_threshold` (0.075), `low_z_rejection_threshold` (-0.15), `max_retries` (10), `sampling_strategy` (1 = 7 samples, 2 = 11, 3 = 13), `use_error_corrected_probing`, `extruder`. A correction below -0.2 mm is refused as implausible.

`[beacon_offsets]` is the helper behind it: named Z offsets with `GET_BEACON_OFFSETS`, `SET_BEACON_OFFSET NAME= Z=` and `RESET_BEACON_OFFSET NAME=`. They stack with `SET_GCODE_OFFSET`, which stays free for you.

### Adaptive heat soak (`[beacon_adaptive_heat_soak]`)

`BEACON_WAIT_FOR_PRINTER_HEAT_SOAK` streams Beacon distance samples with the nozzle about 2.5 mm over the bed and measures how fast the Z distance still drifts (nm/s) while the frame expands. It returns when the drift stays below a threshold for long enough, or at the maximum wait. The threshold comes from a layer quality (1 to 5, maximum Z change over the first layer of 150, 100, 50, 20 or 10 microns) and the longest expected first layer time. `BEACON_PREDICT_HEAT_SOAK_THRESHOLD` shows the threshold without waiting.

Enable it with `variable_beacon_adaptive_heat_soak: True` in `_BEACON_SETTINGS`. The macro default is off, as in RatOS's macros; the RatOS configurator switches it on in `printer.cfg` for Beacon printers, and `install.sh` does the same. **With it off and `bed_heat_soak_time` at 0, there is no bed soak at all:** the print starts as soon as the bed reaches temperature, and `BEACON_PREFLIGHT_CHECK` warns about that. `START_PRINT` calls `BEACON_HEAT_SOAK` itself; in your own `PRINT_START`, call it after `M190`. With it disabled, `BEACON_HEAT_SOAK` just waits `bed_heat_soak_time`.

How long it takes depends on how much the bed and frame are still moving, not on the model: from cold on an open frame expect roughly 10 to 30 minutes or more, a print right after another one is much shorter, and a higher layer quality waits longer. Settings: `beacon_adaptive_heat_soak_layer_quality` (3), `_maximum_first_layer_duration` (1800 s), `_min_wait`, `_max_wait` (5400 s), `_extra_wait_after_completion`. A CSV of the measured rates is written to the temp folder.

The RatOS version fits a pygam model at runtime. This version ships the prediction as a table (`beacon_adaptive_heat_soak_thresholds.py`) sampled from a pygam 0.12.0 refit of the RatOS training data, with interpolation. At the five layer quality levels and first layer times of 200 s or more it agrees with that model within 5 percent. Below about 100 s the linear interpolation is up to 15 percent off. Thresholds can therefore differ slightly from RatOS.

### Scan compensation (`[beacon_scan_compensation]`)

> **May not apply to the Voron 2.4 and other flying gantry printers.** Scan compensation works as in RatOS, where it was developed on fixed gantry machines (the bed moves in Z on lead screws, so the gantry does not give under a contact touch). On the first test printer, a Voron 2.4 350 (flying gantry on four Z belts, Beacon RevH 25 mm behind the nozzle, open frame), two compensation meshes made at 60 °C and 85 °C both carried the same front-to-back slope of about 0.12 to 0.13 mm. Applied to a print, that slope made the first layer too high along the whole front edge; with compensation off the same layer came out even. The probe pairing, the deridging axis and drift during the run were checked and do not explain it. The most likely cause is that a gantry hanging on belts gives slightly under each contact touch, by a different amount at the front and the back, so the contact reference itself is skewed. This is still being diagnosed. Until then: on a flying gantry printer, verify it before you rely on it (make a mesh, print a full-bed single layer with and without it, keep whichever is better); `BEACON_PREFLIGHT_CHECK` and the installer remind you when quad gantry leveling is configured. A test on a fixed gantry printer running mainline Klipper is planned next.

Fast proximity meshes carry a systematic error that depends on temperature, the bed surface and the gantry. Scan compensation measures it once on a hot bed: it probes the same spots by contact and by proximity, filters the contact map (gaussian, local-low filter for filament blobs, optional deridging), and stores `contact - proximity` as a compensation mesh profile. When a proximity mesh is made afterwards, `BEACON_APPLY_SCAN_COMPENSATION` adds that profile to it.

1. Set `variable_beacon_scan_compensation_enable: True` and use the true zero workflow (`variable_beacon_contact_start_print_true_zero: True`). `[bed_mesh]` needs `zero_reference_position` (way A in "Zero reference and fuzzy true zero").
2. Run `BEACON_FULL_CALIBRATE`, which creates the mesh at the end (only when scan compensation is enabled; otherwise `BEACON_CREATE_SCAN_COMPENSATION_MESH` just reports "Beacon scan compensation is disabled!" and does nothing), or run `BEACON_CREATE_SCAN_COMPENSATION_MESH BED_TEMP=85` on its own. It heats up, soaks, levels (Z tilt or QGL if you have them) and then probes about `(bed / desired_spacing)^2` contact points. Plan for a long job.
3. `BEACON_BED_MESH` applies it automatically after each proximity mesh. `variable_beacon_scan_compensation_profile: "auto"` picks the stored profile with the closest bed temperature.
4. **Make one mesh per bed temperature you print at**, for example `BED_TEMP=60` for PLA, `85` for PETG and `105` for ABS/ASA (with the enclosure closed). Each run saves its own profile (`compensation_bed_60C`, `compensation_bed_85C`, ...) and keeps the others; with `PROFILE=auto` the closest one is picked for every print. Each run takes a long time: the heat soak alone can take up to 2.5 hours in a cold enclosure (adaptive heat soak), and leveling (QGL or Z tilt) runs *after* the soak on purpose, because the gantry moves while it warms up. Check a mesh with `BEACON_PREFLIGHT_SCAN_COMPENSATION BED_TEMP=<the temperature it was made at>`; a warning that the temperature "differs by 25.0" only means you checked it against a different temperature.
5. Put `BEACON_PREFLIGHT_SCAN_COMPENSATION` into `PRINT_START` before the mesh to fail fast if the profile is missing or made at a very different bed temperature (`variable_beacon_scan_compensation_bed_temp_mismatch_is_error`).

If your Beacon x offset is an integer multiple of the desired spacing and the y offset is below 2 mm, probing is offset aligned (contact and proximity at the same spot in one pass). Otherwise it probes point by point. Mesh metadata is stored in the profile as `beacon_*` keys (RatOS uses `ratos_*`, so its profiles are not compatible). Other bed mesh profiles are never touched or removed, unlike RatOS.

### Zero reference (`[beacon_zero_reference]`)

Adds `SET_ZERO_REFERENCE_POSITION X= Y=` (the RatOS command of the same name). It re-references the loaded bed mesh so the height at that point is zero and saves the profile again. `START_PRINT` uses it with the place where the true zero was measured, so the mesh stays correct even when the fuzzy true zero position changes every print. Not needed if `[bed_mesh]` has a fixed `zero_reference_position` and the true zero is measured at that position.

## Print start and end

`beacon_print.cfg` gives you `START_PRINT`, `END_PRINT` and the pieces behind them, written to behave like the RatOS ones. If you already use the RatOS slicer start gcode, the same line works here. If you have your own `PRINT_START` you can keep it and use the individual `BEACON_*` macros above instead.

### Slicer start and end gcode

Start gcode (one line, the same parameters RatOS uses):

```
START_PRINT EXTRUDER_TEMP=[first_layer_temperature] EXTRUDER_OTHER_LAYER_TEMP=[temperature] BED_TEMP=[first_layer_bed_temperature] X0={first_layer_print_min[0]} Y0={first_layer_print_min[1]} X1={first_layer_print_max[0]} Y1={first_layer_print_max[1]} TOTAL_LAYER_COUNT=[total_layer_count]
```

That is the PrusaSlicer / SuperSlicer spelling. End gcode is just `END_PRINT`.

**OrcaSlicer** (Printer settings, Machine G-code, Machine start G-code). Replace the **whole** field, including Orca's stock `M190`, `M109` and `PRINT_START` lines, with this one line (checked against an Orca export):

```
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] EXTRUDER_OTHER_LAYER_TEMP=[nozzle_temperature] BED_TEMP=[bed_temperature_initial_layer_single] X0={first_layer_print_min[0]} Y0={first_layer_print_min[1]} X1={first_layer_print_max[0]} Y1={first_layer_print_max[1]} TOTAL_LAYER_COUNT=[total_layer_count]
```

For the layer counter in Mainsail/Fluidd, also put this in "Before layer change G-code" (Printer settings, Machine G-code):

```
SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}
```

`START_PRINT` sets the total layer count from `TOTAL_LAYER_COUNT`, but only the slicer knows when a new layer starts. Without this line the frontend shows "Layer 0 of N" for the whole print.

Do not leave `M190` / `M109` in front of `START_PRINT`. They heat the nozzle to printing temperature before the true zero, so it oozes onto the bed during the contact probe. `START_PRINT` does all the heating itself, in the right order.

**Check the first export.** Slice something, open the G-code and look at everything above `START_PRINT`: there must be no heating commands, and every parameter must be a number (`BED_TEMP=60`, `X0=158.1`). A parameter that still shows a placeholder in brackets means that name is wrong for your slicer version. Orca also puts `EXCLUDE_OBJECT_DEFINE` lines there; add an `[exclude_object]` section to `printer.cfg` so Klipper understands them (and you can cancel single objects).

| Parameter | Needed | Meaning |
|---|---|---|
| `EXTRUDER_TEMP` | yes | first layer nozzle temperature (a comma separated list is allowed, `INITIAL_TOOL` picks the entry) |
| `EXTRUDER_OTHER_LAYER_TEMP` | yes | nozzle temperature for the other layers (stored, as in RatOS) |
| `BED_TEMP` | yes | bed temperature |
| `X0 Y0 X1 Y1` | for the adaptive mesh | bounding box of the first layer. Without them the whole bed is meshed |
| `TOTAL_LAYER_COUNT` | no, but pass it | shown in the frontend. A warning appears without it |
| `FIRST_X FIRST_Y` | no | where the first extrusion starts. RatOS gets them from its post processor. Without them the nozzle goes to the corner of the print area next to the prime blob, which is also what RatOS does when they are missing |
| `CHAMBER_TEMP START_CHAMBER_TEMP` | no | passed to the chamber hook |

### What START_PRINT does

In this order, as in RatOS: check the parameters and the Beacon model, scan compensation preflight, filament sensor check, `SAVE_GCODE_STATE`, home what is not homed, Z hop, chamber hook, `_START_PRINT_BEFORE_HEATING_BED` (preheat the nozzle, park the Beacon at the safe home position at Z2), `M190`, heat soak (adaptive or fixed, `BEACON_HEAT_SOAK`), `_START_PRINT_AFTER_HEATING_BED` (wait for the preheat, Z tilt or QGL if configured and not applied, `G28 Z` after them, `BEACON_TRUE_ZERO`), `_START_PRINT_BED_MESH` (adaptive or full mesh, with the zero reference set to where the true zero was measured), start heating the nozzle, `_START_PRINT_PARK`, wait for the nozzle, hotend heat soak, `_START_PRINT_AFTER_HEATING_EXTRUDER` (prime blob with the height probed at its start and end, then the move to the print area), `RESTORE_GCODE_STATE`, thermal expansion offset, extrusion mode, `G92 E0`, a note if a Z offset is active.

`END_PRINT`: heaters off, retract and lift (`end_print_retract_filament`, `end_print_park_z_hop`), park, `M84`, `M107`, `BED_MESH_CLEAR`, restore the G-code state, remove the thermal expansion offset (RatOS does the same through its named offsets), restore the velocity limits, chamber hook off.

### Hooks and customizing

The hook macros have the same names as RatOS, so the `_USER_...` macros of a RatOS `printer.cfg` work here unchanged. They do nothing until you redefine them in your `printer.cfg` after the include. They receive the START_PRINT parameters (`{rawparams}`).

`_USER_START_PRINT`, `_USER_START_PRINT_BEFORE_HOMING`, `_USER_START_PRINT_HEAT_CHAMBER`, `_USER_START_PRINT_AFTER_HEATING_BED`, `_USER_START_PRINT_BED_MESH`, `_USER_START_PRINT_PARK`, `_USER_START_PRINT_AFTER_HEATING_EXTRUDER`, `_USER_END_START_PRINT`, `_USER_END_PRINT_BEFORE_HEATERS_OFF`, `_USER_END_PRINT_AFTER_HEATERS_OFF`, `_USER_END_PRINT_PARK`, `_USER_END_PRINT_FINISHED`.

The customizable stage macros are also named like RatOS: `_START_PRINT_BEFORE_HEATING_BED`, `_START_PRINT_AFTER_HEATING_BED`, `_START_PRINT_BED_MESH`, `_START_PRINT_PARK`, `_START_PRINT_AFTER_HEATING_EXTRUDER`, `_PRIME`, `_END_PRINT_BEFORE_HEATERS_OFF`, `_END_PRINT_AFTER_HEATERS_OFF`, `_END_PRINT_PARK`. Copy one into your `printer.cfg` and change it to replace that stage. If you replace `_START_PRINT_AFTER_HEATING_BED`, keep the `BEACON_TRUE_ZERO HOTEND_SOAK=0` call.

LED and chamber hooks: `_BEACON_HOOK_LED_START_PRINTING`, `_BEACON_HOOK_LED_PRINTING`, `_BEACON_HOOK_LED_START_PRINTING_ERROR` and the existing `_BEACON_HOOK_CHAMBER_ON` / `_BEACON_HOOK_CHAMBER_OFF`.

Settings are in `beacon_settings.cfg` ("Print start and end" part), with the RatOS names: `preheat_extruder(_temp)`, `calibrate_bed_mesh`, `adaptive_mesh`, `bed_mesh_profile`, `nozzle_priming`, `nozzle_prime_start_x/y`, `nozzle_prime_direction`, `nozzle_prime_bridge_fan`, `start_print_park_in/x/z_height`, `end_print_park_in/x/z_hop`, `end_print_retract_filament`, `end_print_motors_off`, `relative_extrusion`, `force_absolute_position` and more. The bed size comes from your axis maximum; set `printable_x_max` / `printable_y_max` if the printable area is smaller.

### Differences from RatOS

The flow is the same. These parts are different or missing, and you should know about them:

- **Left out:** IDEX, VAOC and its automatic Z offset calibration, stowable probes, the chamber filter fan, RatOS skew profile loading, and the RatOS `_ON_LAYER_CHANGE` variable. The chamber is only a hook (`_BEACON_HOOK_CHAMBER_ON` plus `_USER_START_PRINT_HEAT_CHAMBER`); the built-in bed based chamber heating of RatOS is not ported.
- **Tool changers:** this is a single extruder print start. `INITIAL_TOOL` only picks an entry from a comma separated `EXTRUDER_TEMP`.
- **Zero reference of the mesh** needs a small Python module (`[beacon_zero_reference]`, part of the extras) because stock Klipper cannot set it at runtime. Without the module the mesh is only referenced when `[bed_mesh]` has `zero_reference_position`. With a fuzzy true zero position and a fixed `zero_reference_position` RatOS stops with an error; here you get a warning. The mesh is then referenced to the configured position.
- **Hotend soak:** `BEACON_TRUE_ZERO` called by hand also soaks the hotend (`hotend_heat_soak_time`). `START_PRINT` calls it with `HOTEND_SOAK=0` and soaks after reaching print temperature, as RatOS does.
- **Prime probe direction:** RatOS probes the end of the prime area always towards +Y. That leaves the bed when the blob starts at the back (`nozzle_prime_start_y: "max"`). Here the end probe goes the same way as the blob.
- **Scan compensation** is applied to proximity meshes only. RatOS also calls it after a contact mesh.
- **Default mesh profile name** is `default` (RatOS uses `ratos`). Set `bed_mesh_profile` to change it.
- **Error handling:** a failed parameter or sensor check runs the LED error hook first and then raises the error. Klipper prefixes it with "Error evaluating '_BEACON_RAISE'". With klipper-toolchanger a raised error resets the mounted tool, as for any G-code error.
- **Filament sensors** are checked by the RatOS names (`toolhead_filament_sensor_t0`, `bowden_filament_sensor_t0`), changeable with `filament_sensors`.
- **Z offset note** is simpler: it reports a G-code Z offset beyond the nozzle expansion offset. RatOS also reports its named user offset.
- **`CLEAR_PAUSE`** only runs when `[pause_resume]` exists.

### What was tested

On current Klipper and Kalico builds, in batch mode with a stub standing in for the Beacon (the same one used for the other macros), the whole sequence ran from `START_PRINT` to `END_PRINT` and `CANCEL_PRINT`. I read the resulting command sequence against the RatOS code for: default settings; no true zero; no priming; park positions; full bed and adaptive meshes; contact mesh; probe clean before true zero; bed and hotend soak; relative extrusion; user hooks and chamber hook; an empty filament sensor; missing parameters; a printer without the extras; the zero reference module (the mesh height at the reference point becomes zero, also after reloading the profile). The adaptive mesh limits, counts and algorithm choice matched my own Python re-implementation of the RatOS algorithm on 108 test rectangles over four mesh configurations.

**First real hardware run** (Voron 2.4, Beacon RevH, November 2026): `BEACON_FULL_CALIBRATE` and `BEACON_PREFLIGHT_CHECK` ran on a real printer. It showed three problems, now fixed in this README, the example and the preflight check: `contact_max_hotend_temperature` at 250 stopped the calibration on a 1 degree overshoot, `beacon_cancel_print.cfg` clashes with the Mainsail/Fluidd macros, and the advice on `zero_reference_position` contradicted the fuzzy true zero. A later run of `BEACON_CREATE_SCAN_COMPENSATION_MESH` saved its mesh, but the Spider then shut down with "Timer too close" during the `SAVE_CONFIG` at the end. Both calibration macros now wait for all queued motion (`M400`) before `SAVE_CONFIG`; the next run (a second compensation mesh at 85 °C) saved and restarted cleanly. The first prints also showed that a compensation mesh carries a constant of about 2 mm (the Beacon trigger distance); `START_PRINT` removed it, but `BEACON_BED_MESH` on its own left the compensated mesh about 2 mm high. Applying compensation now keeps the mesh at zero at the zero reference.

**Print start on real hardware:** two full prints from OrcaSlicer through `START_PRINT` and `END_PRINT` on the same printer: a Voron cube (adaptive 3x3 mesh, prime blob probed at both ends, zero reference set, true zero correction applied) and a full-bed single layer (full 25x25 mesh). Both first layers came out even once scan compensation was off (see the note in "Scan compensation").

**Not tested yet:** cancelling a print through Mainsail's `CANCEL_PRINT` with `user_cancel_macro`, the `Z_TILT_ADJUST` branch (only QGL ran), and printers other than a Voron 2.4. For the first print, keep a hand near the emergency stop, run `BEACON_PREFLIGHT_CHECK`, and watch the prime blob and the first move to the print area.

## Updating the Beacon module

The macros use the Beacon module's commands (`BEACON_AUTO_CALIBRATE`, `BEACON_POKE`, `BEACON_QUERY`, `BEACON_OFFSET_COMPARE`, `PROBE PROBE_METHOD=...`), its status fields (`last_z_result`, `last_poke_result`, `last_offset_result`, `model`) and, in the extras, a few of its internals (offsets, trigger distance, the active model, the data stream). All of them were checked against [beacon_klipper](https://github.com/beacon3d/beacon_klipper) `3ccd2e6` (2026-10-16). Beacon updates usually leave these alone, but nothing guarantees it.

With the extras installed, Klipper checks all of this at every start. If a Beacon update removed or renamed something, the console shows a "Beacon macros" warning that lists what is missing, before anything fails.

After a Beacon update:

1. Restart Klipper and look for that warning in the console.
2. `BEACON_PREFLIGHT_CHECK`, then `G28` and `BEACON_POKE_TEST`.
3. Start a small print and watch the print start (true zero, mesh, prime blob).

If something broke, roll the Beacon module back to the version that worked and report it as an issue on this repo:

```
git -C ~/beacon_klipper log --oneline -5           # find the commit before the update
git -C ~/beacon_klipper checkout <that commit>
sudo systemctl restart klipper
```

Moonraker shows the Beacon module as "detached" while it is held back. `git -C ~/beacon_klipper checkout master` returns it to normal updates once it is fixed.

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| `Unknown command: "BEACON_PREFLIGHT_CHECK"` (or any `BEACON_*`) | Klipper has not loaded the macros. Check `grep -n include ~/printer_data/config/printer.cfg` shows `[include beacon_macros/beacon_macros.cfg]`, then restart. If the line is gone, `printer.cfg` was open in the web editor and its old copy was saved over your change: close the tab without saving and add it again. |
| `Importing the numpy C-extensions failed ... libopenblas.so.0` | `sudo apt install libopenblas-dev`, restart Klipper. |
| `home_xy_position cannot be used with [safe_z_home]` | Remove every `[safe_z_home]` section. See "Before you install". |
| `Option 'pin' is not valid in section 'probe'` | Remove the old `[probe]` section. |
| `Current hotend temperature 251.1 exceeds maximum allowed temperature 250.0` | Set `contact_max_hotend_temperature: 260` in `[beacon]`. |
| `SAVE_CONFIG section 'extruder' option 'control' conflicts with included value` | The PID values live in an included file. Comment them out there and run `SAVE_CONFIG` again. |
| Poke test triggers far above the bed | False triggers: filament on the nozzle, something loose on the toolhead, or vibration. See "Before the first calibration". |
| `MCU 'mcu' shutdown: Timer too close` right after "Saving config and restarting Klipper..." | The host stalled while `SAVE_CONFIG` rewrote a large `printer.cfg`. Fixed by the `M400` before `SAVE_CONFIG` (update with `git pull`). The calibration result was already saved: check with `grep -n "^#\*# \[bed_mesh" ~/printer_data/config/printer.cfg`, then `FIRMWARE_RESTART`. |
| `Can not update MCU 'mcu' config as it is shutdown` | Not the real error. Something shut the MCU down first: `grep -n -i "shutdown" ~/printer_data/logs/klippy.log \| tail` shows what. |
| `Beacon scan compensation is disabled!` | Set `variable_beacon_scan_compensation_enable: True` in `_BEACON_SETTINGS`, restart. |
| First layer too high along one edge (often the front) with scan compensation on, even with it off | The compensation mesh adds a slope that is not there. Load the `compensation_bed_*` profile in the heightmap: if Max and Min sit on opposite edges, turn compensation off (`variable_beacon_scan_compensation_enable: False`). See the note in "Scan compensation". |
| Compensation warning "differs by 25.0 C" | No compensation mesh near that bed temperature. Make one at the temperature you print at. |
| Klipper refuses to start after including `beacon_cancel_print.cfg` | It clashes with `mainsail.cfg` / `fluidd.cfg`. See "Cancelling a print". |
| Console warning "The installed Beacon module is missing things the Beacon macros use" | A Beacon module update changed something the macros rely on. See "Updating the Beacon module". |
| Not sure what is wrong in the config | `./install.sh --check` reads the whole config and lists the known problems with file and line. It changes nothing. |

## RatOS name mapping

| RatOS | This macro set |
|---|---|
| `BEACON_RATOS_CALIBRATE` | `BEACON_FULL_CALIBRATE` |
| gantry twist / offset measurement | `BEACON_MEASURE_BEACON_OFFSET` |
| `_BEACON_SET_NOZZLE_TEMP_OFFSET` | `_BEACON_UPDATE_NOZZLE_TEMP_OFFSET` |
| `BEACON_PROBE_CLEAN` (Python) | `BEACON_PROBE_CLEAN` (macro, same parameters) |
| `SKIP_MULTIPOINT_PROBING` | same, handled by `[beacon_true_zero_correction]` |
| `named_offsets` | `[beacon_offsets]` (`SET_BEACON_OFFSET` etc.) |
| `BEACON_WAIT_FOR_PRINTER_HEAT_SOAK`, `BEACON_CREATE_SCAN_COMPENSATION_MESH` | same names |
| `RatOS` settings macro | `_BEACON_SETTINGS` |
| `START_PRINT`, `END_PRINT`, `CANCEL_PRINT`, `_USER_*` and `_START_PRINT_*` hooks | same names (beacon_print.cfg) |
| `CALIBRATE_ADAPTIVE_MESH`, `PRIME_BLOB`, `PROBE_FOR_PRIMING`, `PROBE_CURRENT_POSITION` | `BEACON_ADAPTIVE_MESH`, `BEACON_PRIME_BLOB`, `BEACON_PROBE_FOR_PRIMING`, `BEACON_PROBE_CURRENT_POSITION` |
| `SET_ZERO_REFERENCE_POSITION` | same (`[beacon_zero_reference]`) |
| `CACHE_TOOLHEAD_SETTINGS`, `RESTORE_TOOLHEAD_SETTINGS` | `_BEACON_CACHE_TOOLHEAD_SETTINGS`, `_BEACON_RESTORE_TOOLHEAD_SETTINGS` |

## What is not included

These RatOS parts are left out:

- IDEX / dual extruder handling, VAOC, stowable probes, contact Z tilt and QGL wrappers. See "Differences from RatOS" for the print start.
- The classic `wipe` method (RatOS itself found it ineffective). Only `probe_clean` is supported.

Fuzzy true zero positions use the printer clock as a random source, because stock Klipper macros have no random function.

## Safety

- Contact probing happens with a hot nozzle (default 170 °C, up to 250 °C during calibration). Keep the nozzle clean and test on a robust sheet first.
- Be careful on smooth PEI sheets. Hot contact can mark them.
- Contact Z homing and contact meshes are off by default. Contact readings can vary a lot on textured sheets.
- Run the first calibration while watching the printer.

## Notes on Klipper and Kalico

- Klipper strips everything after a `#` on every config line, even in macros. Comments in these files are whole lines, and no value contains a `#`.
- No `math` module, no `do` extension, no `random`: the macros use `namespace`, `** 0.5` and plain string methods.
- Tested by running every macro and extra in batch mode on current Klipper and Kalico builds (October 2026) with a stub standing in for the Beacon. The stub simulates a bed, so the scan compensation result was checked against a known injected error, the multi-point correction against known samples, and the heat soak against a decaying drift. The gaussian, local-low and deridging filters were compared with scipy and numpy and agree to 1e-16. Since then the calibration, preflight check and scan compensation have also run on a real printer (see "What was tested"); the print start has not yet. Treat your first print as a test and watch the printer.
- The extras accept both finalize callback styles (Klipper `finalize(results)`, Kalico `finalize(offsets, results)`) and Klipper's `ProbeResult` objects.
- Compensation meshes with more than 6 points per axis are stored with the bicubic algorithm, because lagrange interpolation does not allow more.

## License

GPL-3.0. Derived from the RatOS Beacon macros by Helge Keck, Mikkel Schmidt, Tom Glastonbury and the RatOS contributors. Uses commands from the [beacon_klipper](https://github.com/beacon3d/beacon_klipper) module.
