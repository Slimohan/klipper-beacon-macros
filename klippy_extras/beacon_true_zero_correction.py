# Improve Beacon true zero consistency (multi-point probing)
#
# Adapted from RatOS beacon_true_zero_correction.py,
# Copyright (C) 2025 Tom Glastonbury <t@tg73.net>
# Standalone version for Klipper and Kalico: no RatOS, no numpy.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# After BEACON_AUTO_CALIBRATE has set Z=0 with a single contact probe, this module
# probes more points around the same spot with contact, keeps the lowest few
# results and applies their mean as a small correction (a named Z offset). That
# makes true zero far less sensitive to the exact spot and to stray readings.
#
# Config (all optional):
#   [beacon_true_zero_correction]
#   disabled: False
#   z_rejection_threshold: 0.075
#   low_z_rejection_threshold: -0.15
#   max_retries: 10
#   sampling_strategy: 1
#   use_error_corrected_probing: False
#
# Use "SKIP_MULTIPOINT_PROBING=1" on BEACON_AUTO_CALIBRATE to bypass it.
# Requires [beacon] and [beacon_offsets] in the config.

import logging
import math
import random

from . import probe
from . import beacon_extras_common as common

BEACON_AUTO_CALIBRATE = "BEACON_AUTO_CALIBRATE"
TITLE = "BEACON_AUTO_CALIBRATE Multi-point Probing"
OFFSET_NAME = "true_zero_correction"


def generate_points(n, x_lim, y_lim, min_dist, avoid_centre=True,
                    max_iter=1000, rng=random):
    """n random points in the rectangle, at least min_dist apart from each
    other and, optionally, from the centre (where the zero sample was taken)."""
    points = []
    centre = ((x_lim[0] + x_lim[1]) / 2.0, (y_lim[0] + y_lim[1]) / 2.0)
    iterations = 0
    while len(points) < n and iterations < max_iter:
        cand = (rng.uniform(x_lim[0], x_lim[1]),
                rng.uniform(y_lim[0], y_lim[1]))
        if ((not avoid_centre)
                or math.hypot(cand[0] - centre[0], cand[1] - centre[1])
                >= min_dist) and all(
                    math.hypot(cand[0] - p[0], cand[1] - p[1]) >= min_dist
                    for p in points):
            points.append([cand[0], cand[1]])
        iterations += 1
    if len(points) < n:
        raise ValueError(
            "Could not generate all required probe points within the "
            "iteration limit. The conditions are too strict.")
    return points


class BeaconTrueZeroCorrection:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.name = config.get_name()
        self.offsets = None
        self.toolhead = None
        self.beacon = None
        self.orig_cmd = None

        self.disabled = config.getboolean("disabled", False)
        # z values above this are rejected, they are early beacon contact
        # triggers before the nozzle touched the bed. Rare: 0.028% of samples
        # exceeded 75um in more than 32000 samples.
        self.z_rejection_threshold = config.getfloat(
            "z_rejection_threshold", 0.075, minval=0.03)
        # z values below this are rejected, a safety net that has never been
        # seen in testing
        self.low_z_rejection_threshold = config.getfloat(
            "low_z_rejection_threshold", -0.15, maxval=0.0)
        # extra points to probe when some z values were rejected
        self.max_retries = config.getint("max_retries", 10, minval=0,
                                         maxval=15)
        # 1: 7 samples, 2: 11 samples, 3: 13 samples including the zero
        # sample. In all cases the mean of the 3 lowest is used.
        self.sampling_strategy = config.getint("sampling_strategy", 1,
                                               minval=1, maxval=3)
        # probe each point several times with the standard beacon error
        # detection. No benefit was found in testing, for diagnostics only.
        self.use_error_corrected_probing = config.getboolean(
            "use_error_corrected_probing", False)
        self.extruder_name = config.get("extruder", "extruder")

        if self.disabled:
            logging.info("%s: disabled by configuration", self.name)
            return
        if not config.has_section("beacon"):
            logging.info("%s: [beacon] is not configured, disabled", self.name)
            return
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("homing:home_rails_end",
                                            self._handle_homing_end)

    def _handle_connect(self):
        self.offsets = self.printer.lookup_object("beacon_offsets", None)
        if self.offsets is None:
            raise self.printer.config_error(
                "%s requires a [beacon_offsets] section" % (self.name,))
        self.beacon = self.printer.lookup_object("beacon")
        self.toolhead = self.printer.lookup_object("toolhead")
        self.orig_cmd = self.gcode.register_command(BEACON_AUTO_CALIBRATE,
                                                    None)
        if self.orig_cmd is None:
            raise self.printer.config_error(
                "%s command is not registered, %s cannot be enabled. Make "
                "sure [beacon] is configured." % (BEACON_AUTO_CALIBRATE,
                                                   self.name))
        self.gcode.register_command(
            BEACON_AUTO_CALIBRATE, self.cmd_BEACON_AUTO_CALIBRATE,
            desc=self.desc_BEACON_AUTO_CALIBRATE)

    def _handle_homing_end(self, homing_state, rails):
        # a re-homed Z invalidates the correction
        if 2 in homing_state.get_axes() and self.offsets is not None:
            self.offsets.reset(OFFSET_NAME)

    desc_BEACON_AUTO_CALIBRATE = (
        "Automatically calibrates the Beacon probe. Extended with multi-point "
        "probing for improved true zero consistency. Use "
        "SKIP_MULTIPOINT_PROBING=1 to bypass.")

    def cmd_BEACON_AUTO_CALIBRATE(self, gcmd):
        self.offsets.reset(OFFSET_NAME)
        skip = gcmd.get("SKIP_MULTIPOINT_PROBING", "").lower() in (
            "1", "true", "yes")
        if skip:
            return self.orig_cmd(gcmd)
        zero_xy = self.toolhead.get_position()[:2]
        retval = self.orig_cmd(gcmd)
        self._check_homed()
        ProbingSession(self, gcmd, zero_xy).run()
        return retval

    def _check_homed(self):
        homed = self.toolhead.get_status(self.reactor.monotonic())[
            "homed_axes"]
        if any(axis not in homed for axis in "xyz"):
            raise self.gcode.error("Must home all axes first")

    def _nozzle_tip_diameter(self):
        return common.get_nozzle_tip_diameter(
            common.get_nozzle_diameter(self.printer, self.extruder_name))

    def _prepare_probe_command(self, gcmd):
        if not self.use_error_corrected_probing:
            args = dict(PROBE_METHOD="contact", SAMPLES="1", SAMPLES_DROP="0")
        else:
            args = dict(PROBE_METHOD="contact", SAMPLES="3", SAMPLES_DROP="1",
                        SAMPLES_TOLERANCE_RETRIES="10")
        sensor = gcmd.get("SENSOR", None)
        if sensor:
            args["SENSOR"] = sensor
        return self.gcode.create_gcode_command(
            gcmd.get_command(),
            gcmd.get_command() + "".join(" %s=%s" % kv for kv in args.items()),
            args)

    def validate_probing_region(self, range_x, range_y, span):
        regions = common.get_beacon_probing_regions(self.beacon)
        if regions is None:
            raise self.gcode.error(
                "The Beacon probing regions are not available. Multi-point "
                "probing needs a [bed_mesh] section.")
        px = (regions.mesh_contact_min[0], regions.mesh_contact_max[0])
        py = (regions.mesh_contact_min[1], regions.mesh_contact_max[1])
        ok = (px[0] <= range_x[0] <= px[1] and px[0] <= range_x[1] <= px[1]
              and py[0] <= range_y[0] <= py[1]
              and py[0] <= range_y[1] <= py[1])
        if not ok:
            common.echo(
                self.printer, TITLE, "error",
                "The required probing region (%.1fx%.1f) would probe outside "
                "the configured contact probing area. Move the safe home "
                "position towards the middle of the bed, or reduce the "
                "fuzzy radius." % (span, span))
            raise self.gcode.error(
                "The required probing region would probe outside the "
                "contact probing area")


class ProbingSession:
    def __init__(self, tzc, gcmd, zero_xy_position):
        self.gcmd = gcmd
        self.tzc = tzc
        self.zero_xy_position = zero_xy_position
        self.max_retries = tzc.max_retries
        self.probe_helper = probe.ProbePointsHelper(
            tzc.config, self._probe_finalize, [])
        self._finalize_result = None
        self._has_run = False
        self._points = None
        self._next_points_index = 0
        # Samples to take including the zero sample, determined from
        # extensive testing on many printers and print surfaces.
        self._take = (7, 11, 13)[tzc.sampling_strategy - 1]
        # number of lowest samples to average
        self._keep = 3
        # the zero sample is implied by BEACON_AUTO_CALIBRATE
        self._samples = [0.0]

    def run(self):
        if self._has_run:
            raise Exception("ProbingSession has already been run")
        self._has_run = True
        num_points = self._take - len(self._samples) + self.max_retries
        min_span = 9.0
        tip = self.tzc._nozzle_tip_diameter()
        # side of a square with four times the area of `num_points` nozzle tips
        nozzle_span = math.sqrt(math.pi * (tip / 2.0) ** 2 * num_points * 4.0)
        span = max(min_span, nozzle_span)
        half = span / 2.0
        logging.info("%s: count: %d min_span: %s nozzle_tip_dia: %.3f "
                     "nozzle_based_min_span: %.2f use_span: %.2f",
                     self.tzc.name, num_points, min_span, tip, nozzle_span,
                     span)
        range_x = (self.zero_xy_position[0] - half,
                   self.zero_xy_position[0] + half)
        range_y = (self.zero_xy_position[1] - half,
                   self.zero_xy_position[1] + half)
        self.tzc.validate_probing_region(range_x, range_y, span)
        probe_gcmd = self.tzc._prepare_probe_command(self.gcmd)
        try:
            self._points = generate_points(num_points, range_x, range_y, tip)
        except ValueError as e:
            raise self.gcmd.error(str(e))
        self._next_points_index = self._take - len(self._samples)
        self.probe_helper.update_probe_points(
            self._points[:self._next_points_index], 1)
        self.probe_helper.start_probe(probe_gcmd)
        self._finalize()

    def _finalize(self):
        res = self._finalize_result
        tzc = self.tzc
        if res == "retry":
            common.echo(tzc.printer, TITLE, "error",
                        "One or more z values were out of range, maximum "
                        "retries exceeded.")
            raise self.gcmd.error(
                "One or more z values were out of range, maximum retries "
                "exceeded.")
        if isinstance(res, float):
            if res < -0.2:
                common.echo(
                    tzc.printer, TITLE, "error",
                    "The measured true zero correction %.6f is below the "
                    "safety limit of -0.2mm._N_This is not expected "
                    "behaviour." % (res,))
                raise self.gcmd.error(
                    "Measured correction is below safety limit")
            logging.info("%s: applying correction %.6f", tzc.name, res)
            self.gcmd.respond_info(
                "Applying true zero correction of %.1f um" % (res * 1000.0,))
            tzc.offsets.set(OFFSET_NAME, z=res)
            return
        raise ValueError("Internal error: unexpected finalize result")

    def _probe_finalize(self, *args):
        # Klipper calls finalize(positions), Kalico finalize(offsets, positions)
        positions = args[-1]
        tzc = self.tzc
        zvals = [p[2] for p in positions]
        logging.info("%s: probed z-values: %s", tzc.name,
                     ", ".join("%.6f" % z for z in zvals))
        good = [z for z in zvals
                if tzc.low_z_rejection_threshold < z
                < tzc.z_rejection_threshold]
        self._samples.extend(good)
        if len(self._samples) == self._take:
            self._samples.sort()
            use = self._samples[:self._keep]
            logging.info("%s: samples: %s using: %s", tzc.name,
                         ", ".join("%.6f" % z for z in self._samples),
                         ", ".join("%.6f" % z for z in use))
            self._finalize_result = float(sum(use) / len(use))
            return "done"
        rejects = [z for z in zvals
                   if z >= tzc.z_rejection_threshold
                   or z <= tzc.low_z_rejection_threshold]
        logging.info("%s: rejected z-values: %s", tzc.name,
                     ", ".join("%.6f" % z for z in rejects))
        if self._next_points_index + len(rejects) <= len(self._points):
            self.gcmd.respond_info(
                "%d z value(s) were out of range, probing additional "
                "point(s)" % (len(rejects),))
            self.probe_helper.update_probe_points(
                self._points[self._next_points_index:
                             self._next_points_index + len(rejects)], 1)
            self._next_points_index += len(rejects)
            return "retry"
        self.gcmd.respond_info(
            "%d z value(s) were out of range, exceeding the number of "
            "available retry points." % (len(rejects),))
        self._finalize_result = "retry"
        return "done"


def load_config(config):
    return BeaconTrueZeroCorrection(config)
