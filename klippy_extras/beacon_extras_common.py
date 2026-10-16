# Shared helpers for the standalone Beacon Klipper extras
#
# Adapted from RatOS (ratos.py), Copyright (C) the RatOS contributors,
# including Tom Glastonbury <t@tg73.net> and Helge Keck. Standalone version
# without any RatOS dependency.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import collections

SPINNER = "|/-\\"


def echo(printer, title, kind, msg):
    """Console output. kind is one of info, success, debug, warning, alert, error."""
    gcode = printer.lookup_object("gcode")
    text = "%s: %s" % (title, msg) if title else msg
    text = text.replace("_N_", "\n")
    if kind in ("error", "alert"):
        gcode.respond_raw("!! " + text.replace("\n", "\n!! "))
    elif kind == "warning":
        gcode.respond_raw("// WARNING " + text.replace("\n", "\n// "))
    else:
        gcode.respond_info(text)


def get_nozzle_tip_diameter(nozzle_diameter):
    # Based on V6 standard, total nozzle tip diameter is typically 2.5 times hole
    # diameter (spec'd up to 0.8mm), except below 0.25mm where it's 1.5 times
    # hole diameter. A 2.4mm nozzle has about a 3.75mm tip, so above 0.8mm an
    # increase of 1.35mm is used.
    if nozzle_diameter < 0.25:
        return 1.5 * nozzle_diameter
    if nozzle_diameter <= 0.8:
        return 2.5 * nozzle_diameter
    return nozzle_diameter + 1.35


def get_nozzle_diameter(printer, extruder_name="extruder"):
    extruder = printer.lookup_object(extruder_name)
    return extruder.nozzle_diameter


BeaconProbingRegions = collections.namedtuple(
    "BeaconProbingRegions",
    [
        "x_offset",
        "y_offset",
        "mesh_proximity_min_coil_pos",
        "mesh_proximity_max_coil_pos",
        "mesh_contact_min",
        "mesh_contact_max",
    ],
)


def get_beacon_probing_regions(beacon):
    """Probing regions of the Beacon, taken from its mesh helper (the [beacon]
    mesh_min / mesh_max options). Returns None when the Beacon has no mesh
    helper, which happens when there is no [bed_mesh] section."""
    helper = getattr(beacon, "mesh_helper", None)
    if helper is None:
        return None
    return BeaconProbingRegions(
        x_offset=beacon.x_offset,
        y_offset=beacon.y_offset,
        mesh_proximity_min_coil_pos=(helper.def_min_x, helper.def_min_y),
        mesh_proximity_max_coil_pos=(helper.def_max_x, helper.def_max_y),
        mesh_contact_min=tuple(helper.def_contact_min),
        mesh_contact_max=tuple(helper.def_contact_max),
    )


class BackgroundDisplayStatusProgressHandler:
    """Shows a spinner and a progress percentage on the printer display and in
    the status of display_status (M117 / M73 style). Does nothing when there
    is no [display_status] section."""

    def __init__(self, printer, msg_fmt="{spinner} {progress:.0f}%",
                 update_interval=0.8):
        self.reactor = printer.get_reactor()
        self.display_status = printer.lookup_object("display_status", None)
        self.update_interval = update_interval
        self.msg_fmt = msg_fmt
        self._timer = None
        self._phase = 0
        self._progress = 0.0
        self._auto_rate = 0.0
        self._auto_last = None

    def enable(self):
        if self._timer is not None or self.display_status is None:
            return
        self._timer = self.reactor.register_timer(
            self._handle_timer, self.reactor.NOW)

    def disable(self):
        if self._timer is None:
            return
        self.reactor.unregister_timer(self._timer)
        self._timer = None
        self.display_status.message = None
        self.display_status.progress = None

    @property
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, value):
        self._progress = min(1.0, max(0.0, value))

    def set_auto_rate(self, increment_per_second):
        self._auto_last = None
        self._auto_rate = increment_per_second

    def _handle_timer(self, eventtime):
        if self._auto_last is None:
            self._auto_last = eventtime
        if self._auto_rate > 0.0:
            self._progress = min(1.0, max(
                0.0, self._progress
                + self._auto_rate * (eventtime - self._auto_last)))
        self._auto_last = eventtime
        spinner = SPINNER[self._phase]
        self._phase = (self._phase + 1) % len(SPINNER)
        if self.msg_fmt is not None:
            self.display_status.message = self.msg_fmt.format(
                progress=self._progress * 100.0, spinner=spinner)
        return self.reactor.monotonic() + self.update_interval


def linear_fit(xs, ys):
    """Least squares line fit, returns (slope, intercept). Pure Python."""
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two points for a line fit")
    # centre the x values for numerical stability (times are large numbers)
    x0 = sum(xs) / n
    y0 = sum(ys) / n
    sxx = 0.0
    sxy = 0.0
    for x, y in zip(xs, ys):
        dx = x - x0
        sxx += dx * dx
        sxy += dx * (y - y0)
    if sxx == 0.0:
        raise ValueError("cannot fit a line, all x values are equal")
    slope = sxy / sxx
    return slope, y0 - slope * x0
