# Beacon Macros for Klipper and Kalico

A standalone macro set for the [Beacon](https://beacon3d.com) contact/proximity probe, extracted from the Beacon workflow in [RatOS](https://github.com/Rat-OS/RatOS-configurator). It runs on stock Klipper and on Kalico. It needs no RatOS and no `math` or `do` Jinja extensions. The core macros need no extra Python modules. The three optional extras (scan compensation, adaptive heat soak, multi-point true zero) ship as small Klipper extras modules in `klippy_extras/`, see "Extras".

It also has an optional RatOS style `START_PRINT` / `END_PRINT` / `CANCEL_PRINT` (`beacon_print.cfg`) that follows the order of the RatOS print start step by step. See "Print start and end".

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
| `START_PRINT`, `END_PRINT` | *(beacon_print.cfg)* RatOS style print start and end. Same slicer parameters and hook macros as RatOS. See "Print start and end" |
| `CANCEL_PRINT` | *(beacon_cancel_print.cfg)* Runs `END_PRINT` and cancels, like RatOS |
| `BEACON_ADAPTIVE_MESH`, `BEACON_PRIME_BLOB`, `BEACON_PROBE_FOR_PRIMING` | *(beacon_print.cfg)* The adaptive mesh and prime blob pieces behind `START_PRINT` |
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
5. Optional: the RatOS style print start. Add `[include beacon_macros/beacon_print.cfg]` (and `beacon_cancel_print.cfg` if you want `CANCEL_PRINT`) after the other includes. It needs `[virtual_sdcard]` and `[pause_resume]`. See "Print start and end".
6. Restart, then run `BEACON_PREFLIGHT_CHECK`.
7. Run `BEACON_FULL_CALIBRATE`. It heats the bed and nozzle, probes the bed a lot, then saves and restarts.

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

### Zero reference (`[beacon_zero_reference]`)

Adds `SET_ZERO_REFERENCE_POSITION X= Y=` (the RatOS command of the same name). It re-references the loaded bed mesh so the height at that point is zero and saves the profile again. `START_PRINT` uses it with the place where the true zero was measured, so the mesh stays correct even when the fuzzy true zero position changes every print. Not needed if `[bed_mesh]` has a fixed `zero_reference_position` and the true zero is measured at that position.

## Print start and end

`beacon_print.cfg` gives you `START_PRINT`, `END_PRINT` and the pieces behind them, written to behave like the RatOS ones. If you already use the RatOS slicer start gcode, the same line works here. If you have your own `PRINT_START` you can keep it and use the individual `BEACON_*` macros above instead.

### Slicer start and end gcode

Start gcode (one line, the same parameters RatOS uses):

```
START_PRINT EXTRUDER_TEMP=[first_layer_temperature] EXTRUDER_OTHER_LAYER_TEMP=[temperature] BED_TEMP=[first_layer_bed_temperature] X0={first_layer_print_min[0]} Y0={first_layer_print_min[1]} X1={first_layer_print_max[0]} Y1={first_layer_print_max[1]} TOTAL_LAYER_COUNT=[total_layer_count]
```

That is the PrusaSlicer / SuperSlicer spelling. For OrcaSlicer the temperatures are `[nozzle_temperature_initial_layer]`, `[nozzle_temperature]` and `[bed_temperature_initial_layer_single]`. I did not run a slicer: check the placeholder names of your version and read the start of the exported G-code before the first print. End gcode is just `END_PRINT`.

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

**Not tested:** a real printer, a real Beacon (`PROBE`, `BED_MESH_CALIBRATE` and `BEACON_AUTO_CALIBRATE` were stubs), a slicer, the Z tilt / quad gantry leveling branch, and an actual print being cancelled. The motion sequence was checked by reading it, not by watching a machine. For the first print, keep a hand near the emergency stop, run `BEACON_PREFLIGHT_CHECK`, and watch the prime blob and the first move to the print area.

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
- Tested by running every macro and extra in batch mode on current Klipper and Kalico builds (October 2026) with a stub standing in for the Beacon. The stub simulates a bed, so the scan compensation result was checked against a known injected error, the multi-point correction against known samples, and the heat soak against a decaying drift. The gaussian, local-low and deridging filters were compared with scipy and numpy and agree to 1e-16. None of it has run on a real Beacon, so treat the first run as a test and watch the printer.
- The extras accept both finalize callback styles (Klipper `finalize(results)`, Kalico `finalize(offsets, results)`) and Klipper's `ProbeResult` objects.
- Compensation meshes with more than 6 points per axis are stored with the bicubic algorithm, because lagrange interpolation does not allow more.

## License

GPL-3.0. Derived from the RatOS Beacon macros by Helge Keck, Mikkel Schmidt, Tom Glastonbury and the RatOS contributors. Uses commands from the [beacon_klipper](https://github.com/beacon3d/beacon_klipper) module.
