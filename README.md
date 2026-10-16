# Beacon Macros for Klipper and Kalico

A standalone macro set for the [Beacon](https://beacon3d.com) contact/proximity probe, extracted from the Beacon workflow in [RatOS](https://github.com/Rat-OS/RatOS-configurator). It runs on stock Klipper and on Kalico. It needs no RatOS and no `math` or `do` Jinja extensions. The core macros need no extra Python modules. The three optional extras (scan compensation, adaptive heat soak, multi-point true zero) ship as small Klipper extras modules in `klippy_extras/`, see "Extras".

License: GPL-3.0, like RatOS. See "License" at the end.

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
| `BEACON_HEAT_SOAK` | *(extras)* Waits for bed expansion: adaptive with the Beacon, or the fixed `bed_heat_soak_time` |
| `BEACON_CREATE_SCAN_COMPENSATION_MESH` | *(extras)* Creates a compensation mesh from contact and proximity probes. `BED_TEMP=85 CHAMBER_TEMP=0 PROFILE=auto KEEP_TEMP_MESHES=0` |
| `BEACON_APPLY_SCAN_COMPENSATION` | *(extras)* Adds a compensation mesh to the loaded proximity mesh. `PROFILE=` |
| `BEACON_PREFLIGHT_SCAN_COMPENSATION` | *(extras)* Errors out before a print if the compensation mesh is missing or does not fit the bed temperature |

## Install

1. Put the files in your config directory as a folder named `beacon_macros`, for example `cd ~/printer_data/config && git clone https://github.com/Slimohan/klipper-beacon-macros beacon_macros`.
2. Add to `printer.cfg`:
   ```
   [include beacon_macros/beacon_macros.cfg]
   ```
3. Make sure these sections exist (see `examples/printer.cfg.example`):
   - `[beacon]` with `contact_max_hotend_temperature` of at least 250 (or lower `variable_beacon_contact_expansion_cal_temp_high`)
   - `[save_variables]` (stores the thermal expansion coefficient)
   - `[bed_mesh]` with `zero_reference_position` set to the safe home position
4. Optional extras: run `./install.sh ~/klipper` (or `~/kalico`, or both; it links `klippy_extras/*.py` into `klippy/extras`, `COPY=1 ./install.sh` copies instead), add `[include beacon_macros/beacon_extras.cfg]` after the first include, and restart. See "Extras". Do not include `beacon_extras.cfg` without the modules, Klipper would refuse to start.
5. Restart, then run `BEACON_PREFLIGHT_CHECK`.
6. Run `BEACON_FULL_CALIBRATE`. It heats the bed and nozzle, probes the bed a lot, then saves and restarts.

## Settings

All settings are `variable_` entries of `[gcode_macro _BEACON_SETTINGS]` in `beacon_settings.cfg`, with a comment for each. The names match RatOS, so values from a RatOS `printer.cfg` can be pasted in.

Do not edit the file. Instead, override single values in your own `printer.cfg`, after the include:

```
[gcode_macro _BEACON_SETTINGS]
variable_beacon_contact_true_zero_temp: 150
```

Settings you will most likely touch: `beacon_contact_true_zero_temp`, `safe_home_x/y`, `z_hop`, `beacon_contact_wipe_x/y` and `extruder` (if your extruder is not called `extruder`).

## Typical print start

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

Install: `./install.sh ~/klipper ~/kalico` (whichever you use), add `[include beacon_macros/beacon_extras.cfg]` after `beacon_macros.cfg`, restart Klipper. numpy and scipy are not required. If scipy happens to be installed it is used for one interpolation step, otherwise a pure Python fallback runs.

### Multi-point true zero (`[beacon_true_zero_correction]`)

Wraps `BEACON_AUTO_CALIBRATE`. After the normal contact calibration it probes 6, 10 or 12 more contact points on a small random patch around the safe home position, drops outliers, averages the 3 lowest samples (the calibration itself counts as one sample at 0) and applies the result as a Z offset named `true_zero_correction`. That makes the true zero less sensitive to a single bad touch (filament blob, textured sheet). The offset is cleared when Z is homed and when the motors turn off. All macros in this set call `BEACON_AUTO_CALIBRATE SKIP_MULTIPOINT_PROBING=1` where an extra correction would be wrong (cold calibration, models, Z homing). Only `BEACON_TRUE_ZERO` uses the multi-point version. Use `SKIP_MULTIPOINT_PROBING=1` yourself to bypass it.

Options (repeat the section in `printer.cfg` to change): `disabled`, `z_rejection_threshold` (0.075), `low_z_rejection_threshold` (-0.15), `max_retries` (10), `sampling_strategy` (1 = 7 samples, 2 = 11, 3 = 13), `use_error_corrected_probing`, `extruder`. A correction below -0.2 mm is refused as implausible.

`[beacon_offsets]` is the helper behind it: named Z offsets with `GET_BEACON_OFFSETS`, `SET_BEACON_OFFSET NAME= Z=` and `RESET_BEACON_OFFSET NAME=`. They stack with `SET_GCODE_OFFSET`, which stays free for you.

### Adaptive heat soak (`[beacon_adaptive_heat_soak]`)

`BEACON_WAIT_FOR_PRINTER_HEAT_SOAK` streams Beacon distance samples with the nozzle about 2.5 mm over the bed and measures how fast the Z distance still drifts (nm/s) while the frame expands. It returns when the drift stays below a threshold for long enough, or at the maximum wait. The threshold comes from a layer quality (1 to 5, maximum Z change over the first layer of 150, 100, 50, 20 or 10 microns) and the longest expected first layer time. `BEACON_PREDICT_HEAT_SOAK_THRESHOLD` shows the threshold without waiting.

Enable it with `variable_beacon_adaptive_heat_soak: True` and call `BEACON_HEAT_SOAK` after `M190` in `PRINT_START`. With it disabled, `BEACON_HEAT_SOAK` just waits `bed_heat_soak_time`. Settings: `beacon_adaptive_heat_soak_layer_quality` (3), `_maximum_first_layer_duration` (1800 s), `_min_wait`, `_max_wait` (5400 s), `_extra_wait_after_completion`. A CSV of the measured rates is written to the temp folder.

The RatOS version fits a pygam model at runtime. This version ships the prediction as a table (`beacon_adaptive_heat_soak_thresholds.py`) sampled from a pygam 0.12.0 refit of the RatOS training data, with interpolation. At the five layer quality levels and first layer times of 200 s or more it agrees with that model within 5 percent. Below about 100 s the linear interpolation is up to 15 percent off. Thresholds can therefore differ slightly from RatOS.

### Scan compensation (`[beacon_scan_compensation]`)

Fast proximity meshes carry a systematic error that depends on temperature, the bed surface and the gantry. Scan compensation measures it once on a hot bed: it probes the same spots by contact and by proximity, filters the contact map (gaussian, local-low filter for filament blobs, optional deridging), and stores `contact - proximity` as a compensation mesh profile. When a proximity mesh is made afterwards, `BEACON_APPLY_SCAN_COMPENSATION` adds that profile to it.

1. Set `variable_beacon_scan_compensation_enable: True` and use the true zero workflow (`variable_beacon_contact_start_print_true_zero: True`). `[bed_mesh]` needs `zero_reference_position`.
2. Run `BEACON_FULL_CALIBRATE`, which creates the mesh at the end, or run `BEACON_CREATE_SCAN_COMPENSATION_MESH BED_TEMP=85` on its own. It heats up, soaks, levels (Z tilt or QGL if you have them) and then probes about `(bed / desired_spacing)^2` contact points. Plan for a long job.
3. `BEACON_BED_MESH` applies it automatically after each proximity mesh. `variable_beacon_scan_compensation_profile: "auto"` picks the stored profile with the closest bed temperature.
4. Put `BEACON_PREFLIGHT_SCAN_COMPENSATION` into `PRINT_START` before the mesh to fail fast if the profile is missing or made at a very different bed temperature (`variable_beacon_scan_compensation_bed_temp_mismatch_is_error`).

If your Beacon x offset is an integer multiple of the desired spacing and the y offset is below 2 mm, probing is offset aligned (contact and proximity at the same spot in one pass). Otherwise it probes point by point. Mesh metadata is stored in the profile as `beacon_*` keys (RatOS uses `ratos_*`, so its profiles are not compatible). Other bed mesh profiles are never touched or removed, unlike RatOS.

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

## What is not included

These RatOS parts are left out:

- IDEX / dual extruder handling, priming, contact Z tilt and QGL wrappers
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
- Tested by running every macro and extra in batch mode on current Klipper and Kalico builds (October 2026) with a stub standing in for the Beacon. The stub simulates a bed, so the scan compensation result was checked against a known injected error, the multi-point correction against known samples, and the heat soak against a decaying drift. The gaussian, local-low and deridging filters were compared with scipy and numpy and agree to 1e-16. None of it has run on a real Beacon, so treat the first run as a test and watch the printer.
- The extras accept both finalize callback styles (Klipper `finalize(results)`, Kalico `finalize(offsets, results)`) and Klipper's `ProbeResult` objects.
- Compensation meshes with more than 6 points per axis are stored with the bicubic algorithm, because lagrange interpolation does not allow more.

## License

GPL-3.0. Derived from the RatOS Beacon macros by Helge Keck, Mikkel Schmidt, Tom Glastonbury and the RatOS contributors. Uses commands from the [beacon_klipper](https://github.com/beacon3d/beacon_klipper) module.
