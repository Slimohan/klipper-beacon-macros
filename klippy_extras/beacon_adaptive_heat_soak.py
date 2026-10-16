# Adaptive heat soak with thermal stability detection using Beacon proximity data
#
# Adapted from RatOS beacon_adaptive_heat_soak.py,
# Copyright (C) 2025 Tom Glastonbury <t@tg73.net>
# Standalone version for Klipper and Kalico. The numpy and pygam dependencies
# are replaced by plain Python and a precomputed threshold table, see
# beacon_adaptive_heat_soak_thresholds.py.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Config (all optional):
#   [beacon_adaptive_heat_soak]
#   default_layer_quality: 3                 1 rough .. 5 maximum
#   default_maximum_first_layer_duration: 1800   seconds
#   default_maximum_wait: 5400               seconds
#   default_minimum_wait: 0                  seconds
#
# Command:
#   BEACON_WAIT_FOR_PRINTER_HEAT_SOAK [LAYER_QUALITY=] [MAXIMUM_FIRST_LAYER_DURATION=]
#       [MINIMUM_WAIT=] [MAXIMUM_WAIT=] [_FORCE_THRESHOLD=nm/s]
#
# The nozzle must be close to the bed (about 2.5 mm) and the Beacon must have an
# active model. The command measures how fast the Beacon-to-bed distance drifts
# (thermal deflection) and returns when the drift stays under a threshold.

import logging
import math
import os
import tempfile
import time

from . import beacon_extras_common as common
from . import beacon_adaptive_heat_soak_thresholds as table


def _interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - xs[lo]) / float(xs[hi] - xs[lo])
    return ys[lo] + t * (ys[hi] - ys[lo])


class ThresholdPredictor:
    """Looks up the z rate threshold (nm/s) that keeps the Z change in the
    first layer period below a limit. Pure Python, instant."""

    def predict_threshold(self, maximum_z_change_microns, period_seconds):
        start, stop, step = table.PERIODS
        periods = list(range(start, stop + 1, step))
        zs = sorted(table.THRESHOLDS)
        z = float(maximum_z_change_microns)
        p = float(period_seconds)

        def at_z(zk):
            return _interp(p, periods, table.THRESHOLDS[zk])

        if z <= zs[0]:
            t = at_z(zs[0])
        elif z >= zs[-1]:
            t = at_z(zs[-1])
        else:
            hi = next(k for k in zs if k >= z)
            lo = max(k for k in zs if k <= z)
            if hi == lo:
                t = at_z(lo)
            else:
                f = (math.log(z) - math.log(lo)) / (
                    math.log(hi) - math.log(lo))
                t = at_z(lo) + f * (at_z(hi) - at_z(lo))
        # below 12.5 nm/s the noise floor of the system is reached
        return max(t, 12.5)


class BeaconZRateSession:
    def __init__(self, config, beacon, samples_per_mean=1000, window_size=30,
                 window_step=1):
        self.name = config.get_name()
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.beacon = beacon
        self.samples_per_mean = samples_per_mean
        self.window_size = window_size
        self.window_step = window_step
        self._mean_distances = []
        self._times = []
        self._step_phase = window_step - window_size

    def get_estimated_delay_for_first_z_rate(self, sampling_rate=1000.0):
        return self.window_size * (self.samples_per_mean / sampling_rate)

    def _get_next_mean(self):
        n = self.samples_per_mean
        buf = []
        state = {"bad": 0, "first": None, "last": None}

        def cb(s):
            if len(buf) < n:
                dist = s["dist"]
                if dist is None or math.isinf(dist) or math.isnan(dist):
                    state["bad"] += 1
                else:
                    buf.append(dist)
                    if len(buf) == 1:
                        state["first"] = s["time"]
                    if len(buf) == n:
                        state["last"] = s["time"]

        with self.beacon.streaming_session(cb):
            eventtime = self.reactor.monotonic()
            while len(buf) < n:
                if self.printer.is_shutdown():
                    raise self.printer.command_error(
                        "%s: Printer is shutting down" % (self.name,))
                eventtime = self.reactor.pause(eventtime + 0.1)
                if state["bad"] > 100:
                    raise self.printer.command_error(
                        "%s: Beacon failed to measure a valid distance for "
                        "%d out of %d samples." % (
                            self.name, state["bad"], state["bad"] + len(buf)))
        if state["bad"] > 0:
            logging.warning("%s: %d out of %d samples were invalid.",
                            self.name, state["bad"], state["bad"] + n)
        self._step_phase = (self._step_phase + 1) % self.window_step
        mean_time = (state["first"] + state["last"]) / 2.0
        return mean_time, math.fsum(buf) / n

    def get_next_z_rate(self):
        while True:
            if len(self._mean_distances) == self.window_size:
                self._mean_distances.pop(0)
                self._times.pop(0)
            while len(self._mean_distances) < self.window_size:
                t, mean = self._get_next_mean()
                self._mean_distances.append(mean)
                self._times.append(t)
            if self._step_phase == 0:
                break
        slope, intercept = common.linear_fit(self._times, self._mean_distances)
        mid_time = (self._times[0] + self._times[-1]) / 2.0
        mid_z = slope * mid_time + intercept
        # millimeters per second to nanometers per second
        return mid_time, slope * 1e6, mid_z


class RunningAverage:
    def __init__(self, size):
        if size <= 0:
            raise ValueError("Size must be greater than 0")
        self.size = size
        self.buffer = [0.0] * size
        self.index = 0
        self.sum = 0.0
        self.count = 0
        self._adds = 0

    def get_average(self):
        return 0.0 if self.count == 0 else self.sum / self.count

    def is_full(self):
        return self.count == self.size

    def add(self, value):
        if self.count < self.size:
            self.count += 1
        else:
            self.sum -= self.buffer[self.index]
        self.buffer[self.index] = value
        self.sum += value
        self.index = (self.index + 1) % self.size
        self._adds += 1
        if self._adds % (self.size * 10) == 0:
            # remove accumulated floating point error
            self.sum = math.fsum(self.buffer[:self.count])

    def reset(self):
        self.buffer = [0.0] * self.size
        self.index = 0
        self.sum = 0.0
        self.count = 0
        self._adds = 0


class BeaconAdaptiveHeatSoak:
    LAYER_QUALITY_NAMES = ("rough", "draft", "normal", "high", "maximum")
    # maximum Z change in microns for layer quality 1..5, from empirical data
    MAX_Z_CHANGE_MICRONS = (150, 100, 50, 20, 10)

    def __init__(self, config):
        self.config = config
        self.name = config.get_name()
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.def_layer_quality = config.getint("default_layer_quality", 3,
                                               minval=1, maxval=5)
        self.def_maximum_first_layer_duration = config.getint(
            "default_maximum_first_layer_duration", 1800, minval=60,
            maxval=7200)
        self.def_maximum_wait = config.getint("default_maximum_wait", 5400,
                                              minval=0)
        self.def_minimum_wait = config.getint("default_minimum_wait", 0,
                                              minval=0)
        self.beacon = None
        self.gcode.register_command(
            "BEACON_WAIT_FOR_PRINTER_HEAT_SOAK",
            self.cmd_BEACON_WAIT_FOR_PRINTER_HEAT_SOAK,
            desc=self.desc_BEACON_WAIT_FOR_PRINTER_HEAT_SOAK)
        self.gcode.register_command(
            "BEACON_PREDICT_HEAT_SOAK_THRESHOLD",
            self.cmd_BEACON_PREDICT_HEAT_SOAK_THRESHOLD,
            desc="Show the adaptive heat soak threshold for a layer quality "
                 "and first layer duration")
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)

    def _handle_connect(self):
        if self.config.has_section("beacon"):
            self.beacon = self.printer.lookup_object("beacon")

    def _check_beacon(self):
        if self.beacon is None:
            raise self.printer.command_error(
                "Beacon is not available. Add a [beacon] section.")
        if self.beacon.model is None:
            raise self.printer.command_error(
                "Beacon model is not set. Calibrate the Beacon before running "
                "this command.")

    def _prepare_for_sampling_and_get_sampling_frequency(self):
        # The first streaming session after a restart or a calibration can start
        # with bogus data, so retry a few times.
        remaining = 3
        while remaining > 0:
            try:
                return self._prepare_core()
            except Exception as e:
                remaining -= 1
                if remaining == 0:
                    raise
                logging.warning("%s: failed to prepare beacon for sampling, "
                                "retrying: %s", self.name, e)
                self.reactor.pause(self.reactor.monotonic() + 2.0)

    def _prepare_core(self):
        st = {"good": 0, "bad": 0, "first": None, "last": None}

        def cb(s):
            if st["first"] is None:
                st["first"] = s["time"]
            st["last"] = s["time"]
            dist = s["dist"]
            if dist is None or math.isinf(dist) or math.isnan(dist):
                st["bad"] += 1
            else:
                st["good"] += 1

        with self.beacon.streaming_session(cb):
            start = eventtime = self.reactor.monotonic()
            while st["good"] < 1000 and (eventtime - start) < 5:
                if self.printer.is_shutdown():
                    raise self.printer.command_error(
                        "%s: Printer is shutting down" % (self.name,))
                eventtime = self.reactor.pause(eventtime + 0.1)
        logging.info("%s: prepared for sampling, %d good and %d bad samples",
                     self.name, st["good"], st["bad"])
        if st["good"] < 1000:
            raise self.printer.command_error(
                "Failed to prepare beacon for sampling, timed out waiting for "
                "good samples. The Beacon must be calibrated and positioned "
                "correctly (nozzle about 2.5mm above the bed).")
        return (st["good"] + st["bad"]) / (st["last"] - st["first"])

    def _check_trend_projection(self, history, times, fit_window, projection,
                                threshold):
        if len(history) < fit_window:
            return False
        start = self.reactor.monotonic()
        ts = times[-fit_window:]
        vs = history[-fit_window:]
        slope, intercept = common.linear_fit(ts, vs)
        check_value = slope * (ts[-1] + projection) + intercept
        taken = self.reactor.monotonic() - start
        if taken > 3.0:
            logging.warning("%s: trend projection check took %.1f ms",
                            self.name, taken * 1000.0)
        self.reactor.pause(self.reactor.NOW)
        return abs(check_value) <= threshold

    def get_layer_quality_name(self, quality):
        if quality < 1 or quality > 5:
            raise ValueError("Invalid layer quality %s" % (quality,))
        return self.LAYER_QUALITY_NAMES[quality - 1]

    def _predict(self, quality, max_first_layer):
        z = self.MAX_Z_CHANGE_MICRONS[quality - 1]
        # The period is the first layer duration plus 120 s for the time between
        # true zero probing and the print start (scan, heating, priming, ...)
        period = max_first_layer + 120
        return z, period, ThresholdPredictor().predict_threshold(z, period)

    desc_BEACON_WAIT_FOR_PRINTER_HEAT_SOAK = (
        "Wait for the printer to reach thermal stability using the Beacon to "
        "monitor deflection changes")

    def cmd_BEACON_PREDICT_HEAT_SOAK_THRESHOLD(self, gcmd):
        q = gcmd.get_int("LAYER_QUALITY", self.def_layer_quality, minval=1,
                         maxval=5)
        d = gcmd.get_int("MAXIMUM_FIRST_LAYER_DURATION",
                         self.def_maximum_first_layer_duration, minval=60,
                         maxval=7200)
        z, period, thr = self._predict(q, d)
        gcmd.respond_info(
            "Adaptive heat soak threshold for layer quality %d (%s), maximum "
            "first layer duration %s: %.2f nm/s (maximum Z change %d um over "
            "%d s)" % (q, self.get_layer_quality_name(q),
                       self._format_seconds(d), thr, z, period))

    def cmd_BEACON_WAIT_FOR_PRINTER_HEAT_SOAK(self, gcmd):
        self._check_beacon()
        threshold = gcmd.get_int("_FORCE_THRESHOLD", None, minval=8)
        minimum_wait = gcmd.get_int("MINIMUM_WAIT", self.def_minimum_wait,
                                    minval=0)
        maximum_wait = gcmd.get_int("MAXIMUM_WAIT", self.def_maximum_wait,
                                    minval=0)
        layer_quality = gcmd.get_int("LAYER_QUALITY", self.def_layer_quality,
                                     minval=1, maxval=5)
        first_layer = max(60, min(7200, gcmd.get_int(
            "MAXIMUM_FIRST_LAYER_DURATION",
            self.def_maximum_first_layer_duration, minval=0)))
        params_msg = ""
        origin = "forced" if threshold is not None else "predicted"
        if threshold is None:
            z, period, threshold = self._predict(layer_quality, first_layer)
            params_msg = (
                "\nto suit layer quality %d (%s) with maximum first layer "
                "duration of %s" % (layer_quality,
                                    self.get_layer_quality_name(layer_quality),
                                    self._format_seconds(first_layer)))
            logging.info("%s: predicted threshold for %d um (quality %d) over "
                         "%d s: %.2f nm/s", self.name, z, layer_quality,
                         period, threshold)
        else:
            logging.info("%s: using forced threshold %.2f nm/s", self.name,
                         threshold)

        sampling_rate = self._prepare_for_sampling_and_get_sampling_frequency()

        # The following control values were determined experimentally and
        # should not be changed without reference to the original data.
        ma_hold_target = 150
        ma_size = 210
        ma_trend_checks = ((75, 675), (200, 675))
        ma2_hold_target = 150
        ma2_size = 400
        ma2_trend_checks = ((45, 675),)
        ma_hold = 0
        ma2_hold = 0
        z_rate_ra = RunningAverage(ma_size)
        z_rate_count = 0
        ma_ra = RunningAverage(ma2_size)
        ma_hist, ma_times = [], []
        ma2_hist, ma2_times = [], []

        gcmd.respond_info(
            "Adaptive heat soak started, waiting for the printer to reach "
            "thermal stability%s.\nPlease wait..." % (params_msg,))

        progress = None
        try:
            start_time = self.reactor.monotonic()
            session = BeaconZRateSession(self.config, self.beacon)
            progress = common.BackgroundDisplayStatusProgressHandler(
                self.printer, "{spinner} Heat soaking {progress:.1f}%")
            est_first_ma = (
                session.get_estimated_delay_for_first_z_rate(sampling_rate)
                + ma_size * (session.samples_per_mean / sampling_rate))
            progress.set_auto_rate(0.05 / est_first_ma)
            progress.enable()
            fn = os.path.join(tempfile.gettempdir(), "heat_soak_%s.csv" %
                              time.strftime("%Y%m%d_%H%M%S"))
            logging.info("%s: starting: threshold=%s (%s), min_wait=%d, "
                         "max_wait=%d, z_rates_file=%s", self.name, threshold,
                         origin, minimum_wait, maximum_wait, fn)
            time_zero = None
            p_start = p_start_rate = p_range = None
            final_approach = False
            with open(fn, "w") as out:
                out.write("time,z_rate,z\n")
                while True:
                    if self.reactor.monotonic() - start_time > maximum_wait:
                        gcmd.respond_info(
                            "Maximum wait time of %s exceeded, wait "
                            "completed." % (self._format_seconds(maximum_wait),))
                        return
                    try:
                        res = session.get_next_z_rate()
                    except Exception as e:
                        if self.printer.is_shutdown():
                            raise
                        raise self.printer.command_error(
                            "Error calculating Z-rate, wait ended "
                            "prematurely: %s" % (e,))
                    if time_zero is None:
                        time_zero = res[0]
                    out.write("%.8e,%.8e,%.8e\n" % (res[0] - time_zero,
                                                    res[1], res[2]))
                    z_rate_ra.add(res[1])
                    z_rate_count += 1
                    should_log = z_rate_count % 20 == 0
                    elapsed = self.reactor.monotonic() - start_time
                    if not z_rate_ra.is_full():
                        if should_log:
                            logging.info("%s: elapsed=%.1f s, waiting for the "
                                         "first moving average...", self.name,
                                         elapsed)
                        continue
                    ma = z_rate_ra.get_average()
                    ma_ra.add(ma)
                    ma_hist.append(ma)
                    ma_times.append(res[0])
                    ma2 = None
                    if ma_ra.is_full():
                        ma2 = ma_ra.get_average()
                        ma2_hist.append(ma2)
                        ma2_times.append(res[0])
                    if p_start is None:
                        progress.set_auto_rate(0)
                        p_start = progress.progress
                        p_start_rate = abs(ma)
                        # rate change until the threshold, plus 10% since we
                        # will pass it. If we are already within the threshold
                        # (a quick first layer) the max applies and progress
                        # promptly goes to 95%.
                        p_range = max(1.0, (p_start_rate - threshold)
                                      + 0.1 * threshold)
                        if p_start > 0.1:
                            progress.progress = p_start = 0.1
                    progress.progress = max(
                        progress.progress,
                        p_start + (0.95 - p_start) * min(
                            1.0, (p_start_rate - abs(ma)) / p_range))
                    if progress.progress >= 0.949 and not final_approach:
                        final_approach = True
                        progress.set_auto_rate(0.04 / 600.0)
                    elif progress.progress >= 0.989 and final_approach:
                        progress.set_auto_rate(0.0)

                    ma_ok = ma2_ok = None
                    if abs(ma) <= threshold:
                        ma_hold += 1
                    else:
                        ma_hold = 0
                    if ma_hold >= ma_hold_target:
                        # A short and a long linear trend check together make
                        # sure the rate is stable and not heading away.
                        ma_ok = all(self._check_trend_projection(
                            ma_hist, ma_times, c[0], c[1], threshold)
                            for c in ma_trend_checks)
                    if ma2 is not None:
                        if abs(ma2) <= threshold:
                            ma2_hold += 1
                        else:
                            ma2_hold = 0
                        if ma2_hold >= ma2_hold_target:
                            ma2_ok = all(self._check_trend_projection(
                                ma2_hist, ma2_times, c[0], c[1], threshold)
                                for c in ma2_trend_checks)
                    if ma_ok is True or ma2_ok is True:
                        if elapsed >= minimum_wait:
                            gcmd.respond_info(
                                "Adaptive heat soak completed in %s." %
                                (self._format_seconds(elapsed),))
                            return
                    if should_log:
                        logging.info(
                            "%s: elapsed=%.1f s, progress=%.2f%%, ma=%.2f "
                            "nm/s, ma_hold=%d/%d, ma_ok=%s, ma2=%s, "
                            "ma2_hold=%d/%d, ma2_ok=%s, threshold=%.2f nm/s",
                            self.name, elapsed, progress.progress * 100.0, ma,
                            ma_hold, ma_hold_target, ma_ok,
                            "n/a" if ma2 is None else "%.2f" % ma2,
                            ma2_hold, ma2_hold_target, ma2_ok, threshold)
        finally:
            if progress is not None:
                progress.disable()

    def _format_seconds(self, seconds):
        seconds = int(seconds)
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            if m > 0 or s > 0:
                return "%dh %dm %ds" % (h, m, s) if s else "%dh %dm" % (h, m)
            return "%dh" % h
        if m > 0:
            return "%dm %ds" % (m, s) if s else "%dm" % m
        return "%ds" % s


def load_config(config):
    return BeaconAdaptiveHeatSoak(config)
