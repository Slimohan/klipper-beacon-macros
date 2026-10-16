# Beacon contact compensation mesh ("scan compensation")
#
# Adapted from RatOS beacon_mesh.py
# Copyright (C) 2024 Helge Keck <HelgeKeck@hotmail.com>
# Copyright (C) 2024-2025 Mikkel Schmidt <mikkel.schmidt@gmail.com>
# Copyright (C) 2025 Tom Glastonbury <t@tg73.net>
# Standalone version for Klipper and Kalico: no RatOS, and numpy / scipy are
# optional (a plain Python implementation is used when they are missing).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Contains portions of code adapted from beacon.py (GPLv3)
# https://github.com/beacon3d/beacon_klipper
# Copyright (C) 2020-2023 Matt Baker <baker.matt.j@gmail.com>
# Copyright (C) 2020-2023 Lasse Dalegaard <dalegaard@gmail.com>
# Copyright (C) 2023 Beacon <beacon3d.com>
#
# What it does
# ------------
# A Beacon proximity (scan) mesh reads slightly different heights than the nozzle
# would touch, and the difference depends on the position on the bed. This module
# creates a *compensation mesh* once: it probes the bed with contact and with
# proximity at the same locations (at almost the same time, so thermal drift
# cancels out), and stores contact minus proximity. Later, a quick proximity mesh
# is corrected by adding that compensation mesh.
#
# Config:
#   [beacon_scan_compensation]
#   default_desired_spacing: 10        mm between compensation mesh points
#
# Meshes carry extra metadata (kind, bed temperature, probe method) in their
# [bed_mesh <profile>] section, with option names starting with beacon_.
# Meshes without that metadata are never touched.

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import inspect
import logging
import math
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from . import bed_mesh as BedMesh
from . import probe
from . import beacon_extras_common as common

DEFAULT_REACTOR_PAUSE_OFFSET = 0.006  # 6ms

COMPENSATION_MESH_NAME_AUTO = "auto"
MESH_VERSION = 1

KIND_MEASURED = "measured"          # a regular, uncorrected bed mesh
KIND_COMPENSATION = "compensation"  # proximity versus contact difference
KIND_COMPENSATED = "compensated"    # a measured mesh with compensation applied
KIND_CHOICES = (KIND_MEASURED, KIND_COMPENSATION, KIND_COMPENSATED)

METHOD_PROXIMITY = "proximity"                      # rapid scan
METHOD_PROXIMITY_AUTOMATIC = "proximity_automatic"  # stop and sample
METHOD_CONTACT = "contact"
METHOD_COTEMPORAL_OFFSET_ALIGNED = "cotemporal_offset_aligned"
METHOD_COTEMPORAL_POINT_BY_POINT = "cotemporal_point_by_point"
METHOD_CHOICES = (METHOD_PROXIMITY, METHOD_PROXIMITY_AUTOMATIC,
                  METHOD_CONTACT, METHOD_COTEMPORAL_OFFSET_ALIGNED,
                  METHOD_COTEMPORAL_POINT_BY_POINT)

P_VERSION = "beacon_mesh_version"
P_BED_TEMP = "beacon_bed_temp"
P_CHAMBER_TEMP = "beacon_chamber_temp"
P_PROX_BOUNDS = "beacon_proximity_mesh_bounds"
P_KIND = "beacon_mesh_kind"
P_METHOD = "beacon_probe_method"
P_NOTES = "beacon_notes"
REQUIRED_PARAMS = (P_VERSION, P_BED_TEMP, P_KIND, P_METHOD)


class BeaconMeshError(Exception):
    pass


class Region:
    def __init__(self, x_min, x_max, y_min, y_max):
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max

    def is_point_within(self, x, y):
        return (self.x_min < x < self.x_max) and (self.y_min < y < self.y_max)


######################################################################
# Pure python numerics (replacing numpy / scipy)
######################################################################

def gaussian_kernel(sigma, truncate=4.0):
    radius = int(truncate * sigma + 0.5)
    weights = [math.exp(-0.5 * (x * x) / (sigma * sigma))
               for x in range(-radius, radius + 1)]
    total = sum(weights)
    return radius, [w / total for w in weights]


def gaussian_filter(data, sigma=1.0):
    """2D gaussian filter with edge replication ('nearest'), same result as
    scipy.ndimage.gaussian_filter(data, sigma, mode='nearest')."""
    radius, kernel = gaussian_kernel(sigma)
    rows = len(data)
    cols = len(data[0])

    def clamp(i, n):
        return 0 if i < 0 else (n - 1 if i >= n else i)

    # along axis 0 (rows)
    tmp = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            acc = 0.0
            for k, w in enumerate(kernel):
                acc += w * data[clamp(i + k - radius, rows)][j]
            tmp[i][j] = acc
    # along axis 1 (columns)
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        row = tmp[i]
        for j in range(cols):
            acc = 0.0
            for k, w in enumerate(kernel):
                acc += w * row[clamp(j + k - radius, cols)]
            out[i][j] = acc
    return out


def local_low_filter(data, lowpass_sigma=1.0):
    """Keeps the 'low' envelope of the data: each point becomes the weighted
    mean of the lower half of the values in its 5x5 neighbourhood, ranked by
    their high frequency deviation. Suppresses upward spikes caused by
    filament ooze on the nozzle during contact probing."""
    lowpass = gaussian_filter(data, lowpass_sigma)
    rows = len(data)
    cols = len(data[0])
    high = [[data[i][j] - lowpass[i][j] for j in range(cols)]
            for i in range(rows)]
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            neigh = []
            for di in (-2, -1, 0, 1, 2):
                for dj in (-2, -1, 0, 1, 2):
                    ni, nj = i + di, j + dj
                    if 0 <= ni < rows and 0 <= nj < cols:
                        neigh.append((high[ni][nj], ni, nj,
                                      (di * di + dj * dj) ** 0.5))
            # stable ascending order by high frequency detail
            order = sorted(range(len(neigh)), key=lambda k: neigh[k][0])
            lowest = order[:int(math.floor(len(neigh) / 2))]
            num = 0.0
            den = 0.0
            for k in lowest:
                _, ni, nj, dist = neigh[k]
                w = 1.0 / (dist + 1)
                num += w * data[ni][nj]
                den += w
            out[i][j] = num / den
    return out


def deridging_filter(points, primary_axis):
    """[0.25, 0.5, 0.25] smoothing along the axis perpendicular to the scan
    direction. Removes ridges caused by the sweeping motion."""
    rows = len(points)
    cols = len(points[0])
    out = [[0.0] * cols for _ in range(rows)]
    if primary_axis == "y":
        for i in range(rows):
            for j in range(1, cols - 1):
                out[i][j] = (0.25 * points[i][j - 1] + 0.5 * points[i][j]
                             + 0.25 * points[i][j + 1])
            out[i][0] = 0.5 * points[i][0] + 0.5 * points[i][1]
            out[i][-1] = 0.5 * points[i][-1] + 0.5 * points[i][-2]
    elif primary_axis == "x":
        for j in range(cols):
            for i in range(1, rows - 1):
                out[i][j] = (0.25 * points[i - 1][j] + 0.5 * points[i][j]
                             + 0.25 * points[i + 1][j])
            out[0][j] = 0.5 * points[0][j] + 0.5 * points[1][j]
            out[-1][j] = 0.5 * points[-1][j] + 0.5 * points[-2][j]
    else:
        raise ValueError("Invalid primary_axis: %s" % (primary_axis,))
    return out


def mesh_difference_rmse(a, b):
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        raise ValueError("The two point sets must have the same shape.")
    total = 0.0
    n = 0
    for ra, rb in zip(a, b):
        for va, vb in zip(ra, rb):
            total += (va - vb) ** 2
            n += 1
    return math.sqrt(total / n)


def interpolate_faulty_values(points, x_spacing, y_spacing):
    """Replace NaN values. Uses a thin plate spline through the 64 nearest valid
    points when scipy is installed, otherwise inverse distance weighting."""
    faulty = [(i, j) for i, row in enumerate(points)
              for j, v in enumerate(row) if math.isnan(v)]
    if not faulty:
        return points
    valid = [(j * x_spacing, i * y_spacing, points[i][j])
             for i, row in enumerate(points) for j, v in enumerate(row)
             if not math.isnan(v)]
    if not valid:
        raise BeaconMeshError("All proximity values are faulty")
    result = [list(r) for r in points]
    try:
        import numpy as np
        from scipy.interpolate import RBFInterpolator
        coords = np.array([(v[0], v[1]) for v in valid])
        vals = np.array([v[2] for v in valid])
        query = np.array([(j * x_spacing, i * y_spacing) for i, j in faulty])
        interp = RBFInterpolator(coords, vals, neighbors=min(64, len(valid)))
        for (i, j), z in zip(faulty, interp(query).tolist()):
            result[i][j] = float(z)
        return result
    except ImportError:
        pass
    for i, j in faulty:
        x, y = j * x_spacing, i * y_spacing
        near = sorted(valid, key=lambda v: (v[0] - x) ** 2 + (v[1] - y) ** 2)
        near = near[:12]
        num = den = 0.0
        for vx, vy, vz in near:
            d2 = (vx - x) ** 2 + (vy - y) ** 2
            w = 1.0 / max(d2, 1e-9)
            num += w * vz
            den += w
        result[i][j] = num / den
    return result


######################################################################
# Probing
######################################################################

class ProbeCommandKind(Enum):
    CONTACT_SINGLE = 1
    CONTACT_MULTI = 2
    PROXIMITY = 3


class ProbeAction(NamedTuple):
    """A single probing action. pos_x and pos_y are toolhead coordinates, so
    proximity actions are already adjusted for the beacon offset."""
    is_contact: bool
    idx_x: int
    idx_y: int
    pos_x: float
    pos_y: float


@dataclass
class ProbeActionResult:
    contact_z: Optional[float] = None
    proximity_z: Optional[float] = None
    contact_time: Optional[float] = None
    proximity_time: Optional[float] = None


class CotemporalProbingHelper:
    # For offset-aligned probing, the largest offset perpendicular to the main
    # movement. Beacons mounted off-axis have not been tested.
    MAXIMUM_SECONDARY_BEACON_OFFSET = 2.0  # mm

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.beacon = None
        self._probe_finalize = None
        self._probe_helper = None
        self._offsets = None
        self.faulty_regions = []
        if not config.has_section("beacon"):
            return
        if config.has_section("bed_mesh"):
            mesh_config = config.getsection("bed_mesh")
            for i in range(1, 100):
                start = mesh_config.getfloatlist(
                    "faulty_region_%d_min" % (i,), None, count=2)
                if start is None:
                    break
                end = mesh_config.getfloatlist(
                    "faulty_region_%d_max" % (i,), count=2)
                self.faulty_regions.append(Region(
                    min(start[0], end[0]), max(start[0], end[0]),
                    min(start[1], end[1]), max(start[1], end[1])))
        self._probe_helper = probe.ProbePointsHelper(
            config, self._call_probe_finalize, [])
        self.printer.register_event_handler("klippy:connect", self._connect)

    def _connect(self):
        self.beacon = self.printer.lookup_object("beacon")
        # Not beacon.get_offsets(): it depends on the last probe method used.
        self._offsets = (self.beacon.x_offset, self.beacon.y_offset,
                         self.beacon.trigger_distance)

    def _is_faulty_coordinate(self, x, y, add_offsets=False):
        if add_offsets:
            x += self.beacon.x_offset
            y += self.beacon.y_offset
        return any(r.is_point_within(x, y) for r in self.faulty_regions)

    def do_contact_probe(self, gcmd, position, contact_reference_z=None,
                         delta_contact_z_limit=0.075):
        if not self.beacon:
            raise BeaconMeshError("Beacon module is not loaded")
        try:
            state = {"z": None, "complete": False, "force_multi": False}
            self._probe_helper.update_probe_points([position], 1)
            while not state["complete"]:
                if state["force_multi"] or contact_reference_z is None:
                    cmd = self._get_probe_command(
                        gcmd, ProbeCommandKind.CONTACT_MULTI)

                    def contact_cb(_, positions):
                        if len(positions) != 1:
                            raise BeaconMeshError(
                                "Expected exactly one position from contact "
                                "probe, got %d" % len(positions))
                        state["z"] = positions[0][2]
                        state["complete"] = True
                        return "done"
                else:
                    cmd = self._get_probe_command(
                        gcmd, ProbeCommandKind.CONTACT_SINGLE)

                    def contact_cb(_, positions):
                        if len(positions) != 1:
                            raise BeaconMeshError(
                                "Expected exactly one position from contact "
                                "probe, got %d" % len(positions))
                        z = positions[0][2]
                        if abs(z - contact_reference_z) > delta_contact_z_limit:
                            self.gcode.respond_info(
                                "Single-contact probe z=%.4f is not within "
                                "limit of %.4f from reference z=%.4f, "
                                "retrying with multi-contact" % (
                                    z, delta_contact_z_limit,
                                    contact_reference_z))
                            state["force_multi"] = True
                        else:
                            state["z"] = z
                            state["complete"] = True
                        return "done"
                self._probe_finalize = contact_cb
                self._probe_helper.start_probe(cmd)
            return state["z"]
        finally:
            self._probe_finalize = None

    def do_proximity_probe(self, gcmd, position, subtract_offset=False):
        if not self.beacon:
            raise BeaconMeshError("Beacon module is not loaded")
        try:
            result = {"z": None}
            if subtract_offset:
                position = [position[0] - self._offsets[0],
                            position[1] - self._offsets[1]]
            if self._is_faulty_coordinate(position[0], position[1],
                                          add_offsets=True):
                gcmd.respond_info(
                    "Skipping proximity probe at faulty region coordinate "
                    "(%.2f, %.2f)" % (position[0] + self._offsets[0],
                                      position[1] + self._offsets[1]))
                return float("nan")
            cmd = self._get_probe_command(gcmd, ProbeCommandKind.PROXIMITY)

            def proximity_cb(_, positions):
                if len(positions) != 1:
                    raise BeaconMeshError(
                        "Expected exactly one position from proximity "
                        "probe, got %d" % len(positions))
                result["z"] = positions[0][2]
                return "done"

            self._probe_helper.update_probe_points([position], 1)
            self._probe_finalize = proximity_cb
            self._probe_helper.start_probe(cmd)
            return result["z"] - self._offsets[2]
        finally:
            self._probe_finalize = None

    def probe_single_location(self, gcmd, position, contact_reference_z,
                              delta_contact_z_limit=0.075):
        if not self.beacon:
            raise BeaconMeshError("Beacon module is not loaded")
        contact_z = self.do_contact_probe(
            gcmd, position, contact_reference_z=contact_reference_z,
            delta_contact_z_limit=delta_contact_z_limit)
        proximity_z = self.do_proximity_probe(gcmd, position,
                                              subtract_offset=True)
        return contact_z, proximity_z

    def _call_probe_finalize(self, *args):
        # Klipper calls finalize(positions), Kalico finalize(offsets, positions)
        if self._probe_finalize is None:
            raise BeaconMeshError("_probe_finalize callback is not set")
        return self._probe_finalize(
            args[0] if len(args) > 1 else None, args[-1])

    def _get_probe_command(self, gcmd, kind):
        if kind == ProbeCommandKind.CONTACT_SINGLE:
            args = dict(PROBE_METHOD="contact", SAMPLES="1", SAMPLES_DROP="0",
                        HORIZONTAL_MOVE_Z=str(self._offsets[2]))
        elif kind == ProbeCommandKind.CONTACT_MULTI:
            args = dict(PROBE_METHOD="contact", SAMPLES="3", SAMPLES_DROP="1",
                        SAMPLES_TOLERANCE_RETRIES="15")
        elif kind == ProbeCommandKind.PROXIMITY:
            args = dict(PROBE_METHOD="proximity", SAMPLES="1",
                        SAMPLES_DROP="0",
                        HORIZONTAL_MOVE_Z=str(self._offsets[2]))
        else:
            raise BeaconMeshError("Unknown ProbeCommandKind: %s" % (kind,))
        sensor = gcmd.get("SENSOR", None)
        if sensor:
            args["SENSOR"] = sensor
        return self.gcode.create_gcode_command(
            gcmd.get_command(),
            gcmd.get_command() + "".join(" %s=%s" % kv for kv in args.items()),
            args)

    def run_probe_action_sequence(self, gcmd, count_x, count_y, actions,
                                  delta_contact_z_limit=0.075,
                                  progress_handler=None):
        """Perform a sequence of probe actions. Returns (faulty_count, results)
        where results is a grid of ProbeActionResult."""
        if not self.beacon:
            raise BeaconMeshError("Beacon module is not loaded")
        results = [[ProbeActionResult() for _ in range(count_x)]
                   for _ in range(count_y)]
        faulty_count = 0
        last_contact_z = None
        for i, action in enumerate(actions):
            if (action.idx_x < 0 or action.idx_x >= count_x
                    or action.idx_y < 0 or action.idx_y >= count_y):
                raise BeaconMeshError(
                    "ProbeAction indices out of bounds: idx_x=%d, idx_y=%d, "
                    "count_x=%d, count_y=%d" % (action.idx_x, action.idx_y,
                                                 count_x, count_y))
            res = results[action.idx_y][action.idx_x]
            if action.is_contact:
                contact_z = self.do_contact_probe(
                    gcmd, [action.pos_x, action.pos_y], last_contact_z,
                    delta_contact_z_limit=delta_contact_z_limit)
                last_contact_z = contact_z
                res.contact_z = contact_z
                res.contact_time = self.reactor.monotonic()
            else:
                prox = self.do_proximity_probe(gcmd,
                                               [action.pos_x, action.pos_y])
                res.proximity_z = prox
                res.proximity_time = self.reactor.monotonic()
                if math.isnan(prox):
                    faulty_count += 1
            if progress_handler:
                progress_handler.progress = (i + 1) / len(actions)
        return faulty_count, results

    def can_use_offset_aligned_probing(self, minimum_spacing):
        offsets = self._offsets
        if abs(offsets[0]) < 5.0 and abs(offsets[1]) < 5.0:
            # The beacon cannot physically overlap the nozzle.
            raise BeaconMeshError(
                "The configured Beacon sensor offset (%.3f, %.3f) is not "
                "valid." % (offsets[0], offsets[1]))
        primary_axis = "x" if abs(offsets[0]) > abs(offsets[1]) else "y"
        primary = offsets[0] if primary_axis == "x" else offsets[1]
        secondary = offsets[1] if primary_axis == "x" else offsets[0]
        if abs(secondary) > self.MAXIMUM_SECONDARY_BEACON_OFFSET:
            return False
        if abs(primary) < minimum_spacing:
            return False
        return True

    def generate_probe_action_sequence_beacon_offset_aligned(
            self, desired_spacing, minimum_spacing, mesh_min, mesh_max):
        """Sequence for a rectangular bed where the main movement is along the
        axis of the Beacon mounting offset. The mesh resolution is the primary
        axis offset divided by a whole number. Returns (primary_axis, count_x,
        count_y, max_x, max_y, actions)."""
        if desired_spacing < minimum_spacing:
            raise BeaconMeshError(
                "The desired spacing (%.3f mm) is less than the minimum "
                "spacing allowed (%.3f mm)." % (desired_spacing,
                                                minimum_spacing))
        offsets = self._offsets
        if abs(offsets[0]) < 5.0 and abs(offsets[1]) < 5.0:
            raise BeaconMeshError(
                "The configured Beacon sensor offset (%.3f, %.3f) is not "
                "valid." % (offsets[0], offsets[1]))
        primary_axis = "x" if abs(offsets[0]) > abs(offsets[1]) else "y"
        secondary_axis = "y" if primary_axis == "x" else "x"
        primary_offset = offsets[0] if primary_axis == "x" else offsets[1]
        secondary_offset = offsets[1] if primary_axis == "x" else offsets[0]
        abs_primary = abs(primary_offset)
        abs_secondary = abs(secondary_offset)
        if abs_secondary > self.MAXIMUM_SECONDARY_BEACON_OFFSET:
            raise BeaconMeshError(
                "The secondary Beacon sensor offset (|%s|=%s) is too large "
                "for the offset-aligned data collection method. Maximum "
                "allowed secondary offset is %.3f mm." % (
                    secondary_axis, abs_secondary,
                    self.MAXIMUM_SECONDARY_BEACON_OFFSET))
        if abs_primary < minimum_spacing:
            raise BeaconMeshError(
                "The primary Beacon sensor offset (|%s|=%s) is smaller than "
                "the finest resolution allowed (%.3f mm)." % (
                    primary_axis, abs_primary, minimum_spacing))

        divisor = round(abs_primary / desired_spacing)
        if divisor < 1:
            divisor = 1
        elif divisor > 1 and abs_primary / divisor < minimum_spacing:
            divisor -= 1
        # round to the nearest hundredth, as Klipper's bed_mesh does
        resolution = round(abs_primary / divisor, 2)
        x_count = int((mesh_max[0] - mesh_min[0]) / resolution + 1)
        y_count = int((mesh_max[1] - mesh_min[1]) / resolution + 1)
        max_x = mesh_min[0] + resolution * (x_count - 1)
        max_y = mesh_min[1] + resolution * (y_count - 1)
        primary_count, secondary_count = (
            (x_count, y_count) if primary_axis == "x" else (y_count, x_count))

        # Probing starts at mesh_min, runs along the primary axis, steps along
        # the secondary axis and returns along the primary axis, and so on.
        # If the beacon leads the nozzle, proximity is probed first, otherwise
        # contact first. With divisor > 1 the first and last (divisor - 1)
        # points are probed by one sensor only, and in between both sensors
        # probe at the same toolhead location, but at different mesh points.
        actions = []

        def add(is_contact, primary_idx, secondary_idx):
            xi = primary_idx if primary_axis == "x" else secondary_idx
            yi = secondary_idx if primary_axis == "x" else primary_idx
            xp = mesh_min[0] + xi * resolution
            yp = mesh_min[1] + yi * resolution
            if not is_contact:
                if primary_axis == "x":
                    xp -= primary_offset
                else:
                    yp -= primary_offset
            actions.append(ProbeAction(is_contact, xi, yi, xp, yp))

        for sec in range(secondary_count):
            leading = (primary_offset > 0) == (sec % 2 == 0)

            def pidx(line_idx, sec=sec):
                if sec % 2 == 0:
                    return line_idx
                return primary_count - line_idx - 1

            for line in range(divisor - 1):
                add(not leading, pidx(line), sec)
            for line in range(primary_count - (divisor - 1)):
                add(not leading, pidx(line + divisor - 1), sec)
                add(leading, pidx(line), sec)
            for line in range(divisor - 1):
                add(leading, pidx(primary_count - (divisor - 1) + line), sec)
        return primary_axis, x_count, y_count, max_x, max_y, actions


######################################################################
# Beacon scan compensation
######################################################################

class BeaconScanCompensation:
    BED_TEMP_WARNING_MARGIN = 15.0
    POINT_BY_POINT_FORCE_MULTIPOINT_SPACING_THRESHOLD = 25.0

    @staticmethod
    def format_pretty_list(items, conjunction="or"):
        if len(items) == 0:
            return ""
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + " %s " % conjunction + items[-1]

    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.gcode = self.printer.lookup_object("gcode")
        self.reactor = self.printer.get_reactor()
        self.default_desired_spacing = config.getfloat(
            "default_desired_spacing", 10.0, minval=1.0)
        self._cotemporal = CotemporalProbingHelper(config)
        self.beacon = None
        self.bed_mesh = None
        self.heater_bed = None
        self.z_tilt = None
        self.qgl = None
        self._orig_bed_mesh_calibrate = None
        if not config.has_section("beacon"):
            return
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:ready", self._ready)
        reg = self.gcode.register_command
        reg("BEACON_APPLY_SCAN_COMPENSATION",
            self.cmd_BEACON_APPLY_SCAN_COMPENSATION,
            desc=self.desc_BEACON_APPLY_SCAN_COMPENSATION)
        reg("_BEACON_CREATE_SCAN_COMPENSATION_MESH_CORE",
            self.cmd_BEACON_CREATE_SCAN_COMPENSATION_MESH_CORE,
            desc="Do not invoke directly, use "
                 "BEACON_CREATE_SCAN_COMPENSATION_MESH instead. Creates a "
                 "compensation mesh from contact and proximity probes.")
        reg("_BEACON_CHECK_ACTIVE_MODEL_TEMP",
            self.cmd_CHECK_ACTIVE_MODEL_TEMP,
            desc="Warn if the active Beacon model temperature is far from "
                 "the current Beacon coil temperature")
        reg("_BEACON_VALIDATE_COMPENSATION_MESH_PROFILE",
            self.cmd_VALIDATE_COMPENSATION_MESH_PROFILE,
            desc="Raise an error if the profile is not a valid compensation "
                 "mesh, warn about a large bed temperature difference")
        reg("BEACON_GET_MESH_PARAMETERS", self.cmd_GET_MESH_PARAMETERS,
            desc="Show the extended Beacon parameters of the active mesh")
        reg("BEACON_TEST_COMPENSATION_MESH_SELECTION",
            self.cmd_TEST_COMPENSATION_MESH_SELECTION,
            desc="Test the automatic selection of a compensation mesh")
        reg("BEACON_BED_MESH_SUBTRACT", self.cmd_BED_MESH_SUBTRACT,
            desc="Diagnostic. Subtract mesh A from mesh B into a new mesh "
                 "with the grid of the PRIMARY mesh.")

    #####
    # Setup
    #####
    def _connect(self):
        if self.config.has_section("bed_mesh"):
            self.bed_mesh = self.printer.lookup_object("bed_mesh")
        self.beacon = self.printer.lookup_object("beacon")
        self.heater_bed = self.printer.lookup_object("heater_bed", None)
        self.z_tilt = self.printer.lookup_object("z_tilt", None)
        self.qgl = self.printer.lookup_object("quad_gantry_level", None)

    def _ready(self):
        if self.bed_mesh is None:
            return
        # Wrap BED_MESH_CALIBRATE as the outermost handler, so that every mesh
        # made by it is tagged with the extended metadata.
        orig = self.gcode.register_command("BED_MESH_CALIBRATE", None)
        if orig is not None:
            self._orig_bed_mesh_calibrate = orig
            self.gcode.register_command(
                "BED_MESH_CALIBRATE", self.cmd_BED_MESH_CALIBRATE,
                desc="Perform Mesh Bed Leveling (extended with Beacon "
                     "scan compensation metadata)")
        self.load_extra_mesh_params()

    def _echo(self, title, kind, msg):
        common.echo(self.printer, title, kind, msg)

    #####
    # BED_MESH_CALIBRATE wrapper and mesh metadata
    #####
    def cmd_BED_MESH_CALIBRATE(self, gcmd):
        self._orig_bed_mesh_calibrate(gcmd)
        try:
            self._tag_measured_mesh(gcmd)
        except Exception as e:
            # never fail a mesh because of the metadata
            logging.exception("%s: could not tag mesh", self.name)
            self._echo("Beacon mesh metadata", "warning",
                       "Could not store the extended mesh parameters: %s" % e)

    def _tag_measured_mesh(self, gcmd):
        mesh = self.bed_mesh.get_mesh()
        if mesh is None:
            return
        name = mesh.get_profile_name()
        if not name:
            return
        method = gcmd.get("METHOD", "beacon").lower()
        probe_method = gcmd.get("PROBE_METHOD",
                                self.beacon.default_probe_method).lower()
        if probe_method != "proximity":
            method = "automatic"
        if probe_method == "proximity":
            kind_method = (METHOD_PROXIMITY_AUTOMATIC
                           if method == "automatic" else METHOD_PROXIMITY)
        else:
            kind_method = METHOD_CONTACT
        params = mesh.get_mesh_params()
        params[P_VERSION] = MESH_VERSION
        params[P_BED_TEMP] = self._get_nominal_bed_temp()
        params[P_KIND] = KIND_MEASURED
        params[P_METHOD] = kind_method
        params.pop(P_NOTES, None)
        self.bed_mesh.pmgr.save_profile(name)

    def _get_nominal_bed_temp(self):
        if self.heater_bed is None:
            return 0.0
        heater = self.heater_bed.heater
        target = heater.target_temp
        actual = heater.smoothed_temp
        return round(float(target if target > 0.0 else actual), 1)

    def load_extra_mesh_params(self):
        """Copy the extended parameters from the saved [bed_mesh <profile>]
        sections into the loaded profiles (Klipper only loads its own keys).
        Profiles without extended parameters are left alone."""
        profiles = self.bed_mesh.pmgr.get_profiles()
        for profile_name in list(profiles.keys()):
            params = profiles[profile_name]["mesh_params"]
            section = self.bed_mesh.pmgr.name + " " + profile_name
            if not self.config.has_section(section):
                continue
            cfg = self.config.getsection(section)
            version = cfg.getint(P_VERSION, None)
            if version is None:
                continue
            if version != MESH_VERSION:
                self._echo(
                    "Beacon bed mesh management", "warning",
                    "Bed mesh profile '%s' has extended parameter version %s "
                    "which this version does not support. It is ignored for "
                    "scan compensation." % (profile_name, version))
                continue
            try:
                kind = cfg.getchoice(P_KIND, list(KIND_CHOICES))
                method = cfg.getchoice(P_METHOD, list(METHOD_CHOICES))
                bed_temp = cfg.getfloat(P_BED_TEMP)
                chamber_temp = cfg.getfloat(P_CHAMBER_TEMP, None)
                bounds_str = cfg.get(P_PROX_BOUNDS, None)
                bounds = None
                if bounds_str:
                    bounds = tuple(float(v) for v in
                                   bounds_str.strip("()").split(","))
                    if len(bounds) != 4:
                        raise cfg.error("Invalid value for %s: %s" % (
                            P_PROX_BOUNDS, bounds_str))
                notes = cfg.get(P_NOTES, None)
            except Exception as ex:
                self._echo("Beacon bed mesh management", "error",
                           "Bed mesh profile '%s' configuration is invalid: "
                           "%s" % (profile_name, ex))
                continue
            params[P_VERSION] = version
            params[P_KIND] = kind
            params[P_METHOD] = method
            params[P_BED_TEMP] = bed_temp
            if notes:
                params[P_NOTES] = notes
            else:
                params.pop(P_NOTES, None)
            if chamber_temp is not None:
                params[P_CHAMBER_TEMP] = chamber_temp
            else:
                params.pop(P_CHAMBER_TEMP, None)
            if bounds is not None:
                params[P_PROX_BOUNDS] = bounds
            else:
                params.pop(P_PROX_BOUNDS, None)

    def cmd_GET_MESH_PARAMETERS(self, gcmd):
        if self.bed_mesh is None:
            gcmd.respond_info("The [bed_mesh] component is not active")
            return
        mesh = self.bed_mesh.get_mesh()
        if mesh is None:
            gcmd.respond_info("There is no active bed mesh")
            return
        params = OrderedDict((k, v) for k, v in mesh.get_mesh_params().items()
                             if str(k).startswith("beacon_"))
        if not params:
            gcmd.respond_info("No extended Beacon bed mesh parameters found")
        else:
            gcmd.respond_info("\n".join("%s: %s" % kv
                                        for kv in params.items()))

    #####
    # Model temperature check
    #####
    def cmd_CHECK_ACTIVE_MODEL_TEMP(self, gcmd):
        margin = gcmd.get_int("MARGIN", 20, minval=1)
        title = gcmd.get("TITLE", "Active Beacon model temperature warning")
        self.check_active_beacon_model_temp(margin, title)

    def check_active_beacon_model_temp(self, margin=20,
                                       title="Active Beacon model "
                                             "temperature warning"):
        if self.beacon and self.beacon.model:
            coil = self.beacon.last_temp
            model = self.beacon.model.temp
            if coil < model - margin or coil > model + margin:
                self._echo(
                    title, "warning",
                    "The active Beacon model ('%s') is calibrated for a "
                    "temperature that is %0.2fC different than the current "
                    "Beacon coil temperature._N_This may result in "
                    "inaccurate compensation." % (
                        self.beacon.model.name, abs(coil - model)))

    #####
    # Profiles and validation
    #####
    def get_profiles(self, kind=None):
        result = {}
        for name, profile in self.bed_mesh.pmgr.get_profiles().items():
            params = profile["mesh_params"]
            if P_VERSION in params:
                if kind is None or params.get(P_KIND) == kind:
                    result[name] = profile
        return result

    def _get_unique_profile_name(self, base_name):
        profiles = self.bed_mesh.pmgr.get_profiles()
        if base_name not in profiles:
            return base_name, True
        i = 1
        while "%s_%d" % (base_name, i) in profiles:
            i += 1
        return "%s_%d" % (base_name, i), False

    def auto_select_compensation_mesh(self, bed_temperature=None):
        profiles = self.get_profiles(KIND_COMPENSATION)
        if not profiles:
            self._echo(
                "Auto-select compensation mesh error", "error",
                "No compensation mesh profiles found. Create a compensation "
                "mesh with BEACON_CREATE_SCAN_COMPENSATION_MESH, or disable "
                "the scan compensation feature.")
            raise self.printer.command_error(
                "No compensation mesh profiles found")
        if bed_temperature is None:
            bed_temperature = self._get_nominal_bed_temp()
        best = []
        best_diff = float("inf")
        for name, profile in profiles.items():
            temp = profile["mesh_params"][P_BED_TEMP]
            diff = abs(temp - bed_temperature)
            if diff < best_diff:
                best_diff = diff
                best = [(name, temp)]
            elif diff == best_diff:
                best.append((name, temp))
        # more than one equally suitable profile is ambiguous, unless the bed
        # temperatures differ (the same distance above and below)
        if len(set(t for _, t in best)) != len(best):
            self._echo(
                "Auto-select compensation mesh error", "error",
                "A compensation mesh cannot be selected automatically because "
                "there is more than one equally-suitable profile._N_Either "
                "delete one of these profiles, or configure the profile "
                "explicitly:_N_" + "_N_".join(
                    "  '%s' (%s C)" % (n, t) for n, t in best))
            raise self.printer.command_error(
                "Automatic compensation mesh selection is ambiguous")
        best_profile, best_temp = max(best, key=lambda x: x[1])
        if best_diff > self.BED_TEMP_WARNING_MARGIN:
            self._echo(
                "Auto-select compensation mesh warning", "warning",
                "Selected compensation mesh '%s' has a bed temperature of "
                "%s C, which differs by %.1f C from the requested %.1f C._N_"
                "This may result in inaccurate compensation." % (
                    best_profile, best_temp, best_diff, bed_temperature))
        else:
            self.gcode.respond_info(
                "Selected compensation mesh '%s' with bed temperature %s C "
                "(requested: %.1f C, difference: %.1f C)" % (
                    best_profile, best_temp, bed_temperature, best_diff))
        return best_profile

    def cmd_TEST_COMPENSATION_MESH_SELECTION(self, gcmd):
        bed_temp = gcmd.get_float("BED_TEMP", self._get_nominal_bed_temp())
        try:
            name = self.auto_select_compensation_mesh(bed_temp)
            gcmd.respond_info("Auto-selected compensation mesh profile: %s"
                              % name)
        except Exception as e:
            raise gcmd.error(str(e))

    def cmd_VALIDATE_COMPENSATION_MESH_PROFILE(self, gcmd):
        profile = gcmd.get("PROFILE").strip()
        if not profile:
            raise gcmd.error("Value for parameter 'PROFILE' must be specified")
        title = gcmd.get("TITLE", "Validate compensation mesh profile")
        subject = gcmd.get("SUBJECT", None)
        bed_temp = gcmd.get_float("COMPARE_BED_TEMP", None)
        is_error = gcmd.get("COMPARE_BED_TEMP_IS_ERROR",
                            "false").strip().lower() in ("1", "true")
        if profile.lower() == COMPENSATION_MESH_NAME_AUTO:
            profile = self.auto_select_compensation_mesh(bed_temp)
        # a negative bed temperature means: do not compare
        if bed_temp is not None and bed_temp < 0:
            bed_temp = None
        zmesh = self._create_zmesh_from_profile(
            profile, subject, "Beacon compensation mesh validation")
        if not self._validate_extended_parameters(
                zmesh.get_mesh_params(), title, subject or "Mesh",
                compare_bed_temp=bed_temp, compare_bed_temp_is_error=is_error,
                allowed_kinds=(KIND_COMPENSATION,)):
            raise self.printer.command_error(
                "%s is not a valid compensation mesh profile" % (subject,))

    def _create_zmesh_from_profile(self, profile, subject=None, purpose=None):
        if not profile:
            raise TypeError("Argument profile cannot be None")
        if subject is None:
            subject = "Profile '%s'" % profile
        purpose = " for %s" % purpose if purpose else ""
        profiles = self.bed_mesh.pmgr.get_profiles()
        if profile not in profiles:
            raise self.printer.command_error(
                "%s not found%s" % (subject, purpose))
        try:
            zmesh = self._new_zmesh(profiles[profile]["mesh_params"], profile)
            zmesh.build_mesh(profiles[profile]["points"])
            return zmesh
        except Exception as e:
            raise self.printer.command_error(
                "Could not load %s%s%s: %s" % (subject[0].lower(),
                                               subject[1:], purpose, e))

    def _new_zmesh(self, params, name):
        # older Klipper versions take the reactor as a third argument
        n = len(inspect.signature(BedMesh.ZMesh.__init__).parameters)
        if n >= 4:
            return BedMesh.ZMesh(params, name, self.reactor)
        return BedMesh.ZMesh(params, name)

    def _validate_extended_parameters(
            self, params, title, subject="Mesh", compare_bed_temp=None,
            compare_bed_temp_is_error=False, allowed_kinds=KIND_CHOICES,
            allowed_probe_methods=METHOD_CHOICES):
        """Console output for problems with the extended mesh parameters.
        Returns True when they are present, valid and allowed."""
        if not params:
            raise TypeError("Argument params cannot be None")
        err = title + " error"
        warn = title + " warning"
        if not all(p in params for p in REQUIRED_PARAMS):
            self._echo(err, "error", "%s has no extended Beacon metadata. "
                       "It was not created with this module (use "
                       "BEACON_BED_MESH or BED_MESH_CALIBRATE after enabling "
                       "[beacon_scan_compensation])." % subject)
            return False
        if params[P_VERSION] != MESH_VERSION:
            self._echo(err, "error", "%s is not compatible with this version."
                       % subject)
            return False
        if params[P_KIND] not in KIND_CHOICES or \
                params[P_METHOD] not in METHOD_CHOICES:
            self._echo(err, "error", "%s has invalid extended metadata."
                       % subject)
            return False
        bed_temp = params[P_BED_TEMP]
        if not isinstance(bed_temp, float) or bed_temp < 0:
            self._echo(err, "error", "%s has invalid extended metadata."
                       % subject)
            return False
        if params[P_KIND] not in allowed_kinds:
            self._echo(err, "error", "%s must be a %s mesh. A %s mesh cannot "
                       "be used." % (subject,
                                     self.format_pretty_list(allowed_kinds),
                                     params[P_KIND]))
            return False
        if params[P_METHOD] not in allowed_probe_methods:
            self._echo(err, "error", "%s must be a %s probe method mesh. A %s "
                       "probe method mesh cannot be used." % (
                           subject,
                           self.format_pretty_list(allowed_probe_methods),
                           params[P_METHOD]))
            return False
        if compare_bed_temp is not None and abs(compare_bed_temp - bed_temp) \
                > self.BED_TEMP_WARNING_MARGIN:
            self._echo(
                err if compare_bed_temp_is_error else warn,
                "error" if compare_bed_temp_is_error else "warning",
                "%s was created with a bed temperature that differs by %s._N_"
                "This may result in inaccurate compensation." % (
                    subject, abs(bed_temp - compare_bed_temp)))
            if compare_bed_temp_is_error:
                return False
        return True

    #####
    # Applying a compensation mesh
    #####
    desc_BEACON_APPLY_SCAN_COMPENSATION = (
        "Compensate a Beacon scan mesh with a Beacon compensation mesh. "
        "PROFILE=auto selects by bed temperature.")

    def cmd_BEACON_APPLY_SCAN_COMPENSATION(self, gcmd):
        profile = gcmd.get("PROFILE", COMPENSATION_MESH_NAME_AUTO).strip()
        if not profile:
            raise gcmd.error("Value for parameter 'PROFILE' must be specified")
        if not self.apply_scan_compensation(profile):
            raise self.printer.command_error(
                "Could not apply scan compensation")

    def apply_scan_compensation(self, comp_name):
        error_title = "Apply scan compensation error"
        try:
            measured = self.bed_mesh.z_mesh
            if not measured:
                self._echo(error_title, "error",
                           "No mesh loaded._N_Either generate a new bed mesh "
                           "or load it via BED_MESH_PROFILE LOAD=\"name\"")
                return False
            m_params = measured.get_mesh_params()
            m_name = measured.get_profile_name()
            if not self._validate_extended_parameters(
                    m_params, "Apply scan compensation",
                    "Loaded mesh '%s'" % m_name,
                    allowed_kinds=(KIND_MEASURED,),
                    allowed_probe_methods=(METHOD_PROXIMITY,
                                           METHOD_PROXIMITY_AUTOMATIC)):
                return False
            m_bed_temp = m_params[P_BED_TEMP]
            if comp_name.lower() == COMPENSATION_MESH_NAME_AUTO:
                comp_name = self.auto_select_compensation_mesh(m_bed_temp)
            comp = self._create_zmesh_from_profile(
                comp_name, purpose="Beacon scan compensation")
            c_params = comp.get_mesh_params()
            c_name = comp.get_profile_name()
            if not self._validate_extended_parameters(
                    c_params, "Apply scan compensation",
                    "Specified compensation mesh '%s'" % c_name,
                    compare_bed_temp=m_bed_temp,
                    allowed_kinds=(KIND_COMPENSATION,)):
                return False
            if m_name == c_name:
                self._echo(error_title, "error",
                           "Compensation profile name '%s' is the same as the "
                           "scan profile name '%s'" % (c_name, m_name))
                return False
            points = self.bed_mesh.pmgr.get_profiles()[m_name]["points"]
            x_step = (m_params["max_x"] - m_params["min_x"]) / (
                len(points[0]) - 1)
            y_step = (m_params["max_y"] - m_params["min_y"]) / (
                len(points) - 1)
            new_points = []
            for y in range(len(points)):
                new_points.append([])
                for x in range(len(points[0])):
                    x_pos = m_params["min_x"] + x * x_step
                    y_pos = m_params["min_y"] + y * y_step
                    new_points[y].append(points[y][x]
                                         + comp.calc_z(x_pos, y_pos))
                self.reactor.pause(self.reactor.monotonic()
                                   + DEFAULT_REACTOR_PAUSE_OFFSET)
            measured.build_mesh(new_points)
            m_params[P_KIND] = KIND_COMPENSATED
            self.bed_mesh.pmgr.save_profile(m_name)
            self.bed_mesh.set_mesh(measured)
            self.gcode.respond_info(
                "Measured mesh '%s' compensated with compensation mesh '%s'"
                % (m_name, c_name))
            return True
        except BedMesh.BedMeshError as e:
            self._echo(error_title, "error", str(e))
            return False

    #####
    # Creating a compensation mesh
    #####
    def cmd_BEACON_CREATE_SCAN_COMPENSATION_MESH_CORE(self, gcmd):
        if not self.beacon or self.bed_mesh is None:
            self._echo("Create compensation mesh error", "error",
                       "Beacon and [bed_mesh] must be configured.")
            raise gcmd.error("Beacon or bed_mesh not loaded")
        profile = gcmd.get("PROFILE", COMPENSATION_MESH_NAME_AUTO).strip()
        desired = gcmd.get_float("DESIRED_SPACING",
                                 self.default_desired_spacing, minval=1.0)
        minimum = gcmd.get_float("MINIMUM_SPACING", desired * 0.8, minval=1.0)
        chamber_temp = gcmd.get_float("CHAMBER_TEMP", 0)
        keep_temp = gcmd.get("KEEP_TEMP_MESHES",
                             "0").strip().lower() in ("1", "true", "yes")
        if desired < minimum:
            raise gcmd.error("Parameter 'DESIRED_SPACING' must be greater "
                             "than or equal to 'MINIMUM_SPACING'")
        if not profile:
            raise gcmd.error("Value for parameter 'PROFILE' must be specified")
        if profile.lower() == COMPENSATION_MESH_NAME_AUTO:
            base = "compensation_bed_%dC" % round(self._get_nominal_bed_temp())
            profile, unique = self._get_unique_profile_name(base)
            if not unique:
                self._echo("Create beacon compensation mesh", "info",
                           "The default automatic profile name '%s' already "
                           "exists. The unique name '%s' will be used instead."
                           % (base, profile))
            gcmd.respond_info("Using automatic profile name '%s' for the new "
                              "compensation mesh" % profile)
        if self.z_tilt is not None and not self.z_tilt.z_status.applied:
            self._echo("Create compensation mesh warning", "warning",
                       "Z-tilt leveling is configured but has not been "
                       "applied._N_This may result in inaccurate "
                       "compensation.")
        if self.qgl is not None and not self.qgl.z_status.applied:
            self._echo("Create compensation mesh warning", "warning",
                       "Quad gantry leveling is configured but has not been "
                       "applied._N_This may result in inaccurate "
                       "compensation.")
        if self.beacon.model is None:
            self._echo("Create compensation mesh error", "error",
                       "No active Beacon model is selected._N_Perform the "
                       "initial Beacon calibration first.")
            raise gcmd.error("No active Beacon model selected")
        self.check_active_beacon_model_temp(
            title="Create compensation mesh warning")
        self.create_compensation_mesh(gcmd, profile, desired, minimum,
                                      chamber_temp, keep_temp)

    def create_compensation_mesh(self, gcmd, profile, desired_spacing,
                                 minimum_spacing, chamber_temp, keep_temp):
        try:
            regions = common.get_beacon_probing_regions(self.beacon)
            if regions is None:
                raise BeaconMeshError(
                    "The Beacon probing regions are not available. A "
                    "[bed_mesh] section without mesh_radius is required.")
            safe_min_x = max(regions.mesh_proximity_min_coil_pos[0],
                             regions.mesh_contact_min[0])
            safe_max_x = min(regions.mesh_proximity_max_coil_pos[0],
                             regions.mesh_contact_max[0])
            safe_min_y = max(regions.mesh_proximity_min_coil_pos[1],
                             regions.mesh_contact_min[1])
            safe_max_y = min(regions.mesh_proximity_max_coil_pos[1],
                             regions.mesh_contact_max[1])
            ch = self._cotemporal
            if ch.faulty_regions:
                gcmd.respond_info(
                    "%d faulty proximity probing regions are configured. "
                    "Proximity values for points within these regions will "
                    "be interpolated." % len(ch.faulty_regions))
            use_aligned = ch.can_use_offset_aligned_probing(minimum_spacing)
            skip_low_filter = False
            primary_axis = None
            extra_notes = ""

            if use_aligned:
                pattern = "offset-aligned"
                (primary_axis, count_x, count_y, max_x, max_y,
                 actions) = ch.generate_probe_action_sequence_beacon_offset_aligned(
                    desired_spacing, minimum_spacing,
                    (safe_min_x, safe_min_y), (safe_max_x, safe_max_y))
                x_spacing = (max_x - safe_min_x) / (count_x - 1)
                y_spacing = (max_y - safe_min_y) / (count_y - 1)
                gcmd.respond_info(
                    "Using %s cotemporal probing strategy:\nGenerated %d "
                    "probe actions for the region from (%.2f, %.2f) to "
                    "(%.2f, %.2f)\nMesh points: %d x %d, max coordinates: "
                    "(%.2f, %.2f), spacing: (%.2f, %.2f)" % (
                        pattern, len(actions), safe_min_x, safe_min_y,
                        safe_max_x, safe_max_y, count_x, count_y, max_x,
                        max_y, x_spacing, y_spacing))
                progress = common.BackgroundDisplayStatusProgressHandler(
                    self.printer, "{spinner} Probing {progress:.1f}%")
                try:
                    progress.enable()
                    faulty_count, results = ch.run_probe_action_sequence(
                        gcmd, count_x, count_y, actions,
                        progress_handler=progress)
                finally:
                    progress.disable()
                contact_points = [[r.contact_z for r in row]
                                  for row in results]
                prox_points = [[r.proximity_z for r in row]
                               for row in results]
            else:
                # simple but slow point-by-point strategy, geared towards
                # high resolution meshes (about 10mm spacing)
                pattern = "point-by-point"
                count_x = max(4, int((safe_max_x - safe_min_x)
                                     / desired_spacing + 1))
                count_y = max(4, int((safe_max_y - safe_min_y)
                                     / desired_spacing + 1))
                max_x, max_y, points = self.generate_mesh_points(
                    count_x, count_y, [safe_min_x, safe_min_y],
                    [safe_max_x, safe_max_y])
                x_spacing = (max_x - safe_min_x) / (count_x - 1)
                y_spacing = (max_y - safe_min_y) / (count_y - 1)
                force_multi = (
                    x_spacing > self.POINT_BY_POINT_FORCE_MULTIPOINT_SPACING_THRESHOLD
                    or y_spacing > self.POINT_BY_POINT_FORCE_MULTIPOINT_SPACING_THRESHOLD)
                if force_multi:
                    skip_low_filter = True
                    extra_notes += (", using multi-sample probing due to "
                                    "large spacing (local-low filter skipped)")
                contact_z = None
                faulty_count = 0
                results = [[None] * count_x for _ in range(count_y)]
                gcmd.respond_info(
                    "Using %s cotemporal probing strategy:\nGenerated %d "
                    "probe points for the region from (%.2f, %.2f) to "
                    "(%.2f, %.2f)\nMesh points: %d x %d, max coordinates: "
                    "(%.2f, %.2f), spacing: (%.2f, %.2f)%s" % (
                        pattern, len(points), safe_min_x, safe_min_y,
                        safe_max_x, safe_max_y, count_x, count_y, max_x,
                        max_y, x_spacing, y_spacing,
                        ", using multi-sample probing due to large spacing"
                        if force_multi else ""))
                progress = common.BackgroundDisplayStatusProgressHandler(
                    self.printer, "{spinner} Probing {progress:.1f}%")
                try:
                    progress.enable()
                    for i, point in enumerate(points):
                        progress.progress = (i + 1) / len(points)
                        contact_z, prox_z = ch.probe_single_location(
                            gcmd, point[2:],
                            None if force_multi else contact_z)
                        if math.isnan(prox_z):
                            faulty_count += 1
                        results[point[1]][point[0]] = (point[2], point[3],
                                                       contact_z, prox_z)
                finally:
                    progress.disable()
                contact_points = [[results[y][x][2] for x in range(count_x)]
                                  for y in range(count_y)]
                prox_points = [[results[y][x][3] for x in range(count_x)]
                               for y in range(count_y)]

            if faulty_count > 0:
                gcmd.respond_info("%d faulty region proximity probe values "
                                  "will be interpolated." % faulty_count)
                prox_points = interpolate_faulty_values(
                    prox_points, x_spacing, y_spacing)

            extra = {}
            extra[P_VERSION] = MESH_VERSION
            extra[P_BED_TEMP] = self._get_nominal_bed_temp()
            extra[P_KIND] = KIND_MEASURED
            extra[P_METHOD] = (METHOD_COTEMPORAL_OFFSET_ALIGNED if use_aligned
                               else METHOD_COTEMPORAL_POINT_BY_POINT)
            extra[P_NOTES] = ("input mesh for cotemporal mesh created using "
                              "%s sampling pattern" % pattern)
            extra[P_CHAMBER_TEMP] = chamber_temp
            extra[P_PROX_BOUNDS] = (safe_min_x, safe_min_y, safe_max_x,
                                    safe_max_y)
            mmin = (safe_min_x, safe_min_y)
            mmax = (max_x, max_y)

            if primary_axis is not None:
                der_contact = deridging_filter(contact_points, primary_axis)
                der_prox = deridging_filter(prox_points, primary_axis)
                c_rmse = mesh_difference_rmse(contact_points, der_contact)
                p_rmse = mesh_difference_rmse(prox_points, der_prox)
                extra_notes += (", deridged (primary axis: %s, contact RMSE: "
                                "%.4f, proximity RMSE: %.4f)" % (
                                    primary_axis, c_rmse, p_rmse))
                filtered = (der_contact if skip_low_filter
                            else local_low_filter(der_contact))
                if keep_temp:
                    self._install_and_save_new_mesh(
                        profile + "_CONTACT", extra, mmin, mmax,
                        contact_points)
                    self._install_and_save_new_mesh(
                        profile + "_CONTACT_DERIDGED", extra, mmin, mmax,
                        der_contact)
                    self._install_and_save_new_mesh(
                        profile + "_PROXIMITY", extra, mmin, mmax,
                        prox_points)
                    self._install_and_save_new_mesh(
                        profile + "_PROXIMITY_DERIDGED", extra, mmin, mmax,
                        der_prox)
                prox_points = der_prox
            else:
                filtered = (contact_points if skip_low_filter
                            else local_low_filter(contact_points))
                if keep_temp:
                    self._install_and_save_new_mesh(
                        profile + "_CONTACT", extra, mmin, mmax,
                        contact_points)
                    self._install_and_save_new_mesh(
                        profile + "_PROXIMITY", extra, mmin, mmax,
                        prox_points)
            if keep_temp and not skip_low_filter:
                self._install_and_save_new_mesh(
                    profile + "_CONTACT_FILTERED", extra, mmin, mmax, filtered)

            comp_points = [[filtered[y][x] - prox_points[y][x]
                            for x in range(len(prox_points[y]))]
                           for y in range(len(prox_points))]
            extra[P_KIND] = KIND_COMPENSATION
            extra[P_NOTES] = ("cotemporal compensation mesh created using %s "
                              "sampling pattern%s" % (pattern, extra_notes))
            self._install_and_save_new_mesh(profile, extra, mmin, mmax,
                                            comp_points)
            gcmd.respond_info("Compensation mesh created with profile '%s'"
                              % profile)
        except BeaconMeshError as e:
            raise gcmd.error("Failed to create compensation mesh: %s" % e)

    def cmd_BED_MESH_SUBTRACT(self, gcmd):
        a = gcmd.get("A").strip()
        b = gcmd.get("B").strip()
        primary = gcmd.get("PRIMARY", "a").strip().lower()
        if a == b:
            raise gcmd.error("Profiles A and B must be different.")
        if primary not in ("a", "b"):
            raise gcmd.error("Invalid PRIMARY value '%s'. Must be 'A' or 'B'."
                             % primary)
        za = self._create_zmesh_from_profile(a)
        zb = self._create_zmesh_from_profile(b)
        pri, sec = (za, zb) if primary == "a" else (zb, za)
        pm = pri.probed_matrix
        sm = sec.probed_matrix
        same_grid = (pri.mesh_x_min == sec.mesh_x_min
                     and pri.mesh_x_max == sec.mesh_x_max
                     and pri.mesh_y_min == sec.mesh_y_min
                     and pri.mesh_y_max == sec.mesh_y_max
                     and len(pm) == len(sm) and len(pm[0]) == len(sm[0]))
        diff = [[0.0] * len(pm[0]) for _ in pm]
        for y in range(len(pm)):
            for x in range(len(pm[0])):
                xp = pri.mesh_x_min + x * (
                    (pri.mesh_x_max - pri.mesh_x_min) / (len(pm[0]) - 1))
                yp = pri.mesh_y_min + y * (
                    (pri.mesh_y_max - pri.mesh_y_min) / (len(pm) - 1))
                sz = sm[y][x] if same_grid else sec.calc_z(xp, yp)
                diff[y][x] = pm[y][x] - sz if primary == "a" else sz - pm[y][x]
        extra = {
            P_VERSION: MESH_VERSION, P_BED_TEMP: 0.0, P_KIND: KIND_MEASURED,
            P_METHOD: METHOD_PROXIMITY,
            P_NOTES: "Mesh subtraction of '%s' minus '%s' based on the grid "
                     "of '%s'." % (a, b, a if primary == "a" else b)}
        self._install_and_save_new_mesh(
            "%s_MINUS_%s" % (a, b), extra,
            (pri.mesh_x_min, pri.mesh_y_min),
            (pri.mesh_x_max, pri.mesh_y_max), diff)

    def _install_and_save_new_mesh(self, profile_name, extra_params, mesh_min,
                                   mesh_max, probed_points, mesh_pps=None,
                                   algorithm=None):
        x_count = len(probed_points[0])
        y_count = len(probed_points)
        cmd_params = dict(
            PROBE_COUNT="%d,%d" % (x_count, y_count),
            MESH_MIN="%.3f,%.3f" % (mesh_min[0], mesh_min[1]),
            MESH_MAX="%.3f,%.3f" % (mesh_max[0], mesh_max[1]))
        if mesh_pps:
            cmd_params["MESH_PPS"] = "%d,%d" % mesh_pps
        if algorithm:
            cmd_params["ALGORITHM"] = algorithm
        elif max(x_count, y_count) > 6:
            # lagrange interpolation is limited to 6 points per axis
            cmd_params["ALGORITHM"] = "bicubic"
        self.bed_mesh.set_mesh(None)
        like_cmd = self.gcode.create_gcode_command("_", "_", cmd_params)
        try:
            self.bed_mesh.bmc.update_config(like_cmd)
        except BedMesh.BedMeshError as e:
            raise BeaconMeshError("Error updating bed mesh config: %s" % e)
        params = dict(self.bed_mesh.bmc.mesh_config)
        params.update(extra_params)
        params["min_x"] = mesh_min[0]
        params["max_x"] = mesh_max[0]
        params["min_y"] = mesh_min[1]
        params["max_y"] = mesh_max[1]
        zmesh = self._new_zmesh(params, profile_name)
        try:
            zmesh.build_mesh(probed_points)
        except BedMesh.BedMeshError as e:
            raise BeaconMeshError(str(e))
        self.bed_mesh.set_mesh(zmesh)
        self.bed_mesh.pmgr.save_profile(profile_name)

    # Adapted from Klipper's bed_mesh.py, Copyright (C) 2018-2019 Eric
    # Callahan <arksine.code@gmail.com>
    def generate_mesh_points(self, x_count, y_count, mesh_min, mesh_max):
        min_x, min_y = mesh_min
        max_x, max_y = mesh_max
        x_dist = (max_x - min_x) / (x_count - 1)
        y_dist = (max_y - min_y) / (y_count - 1)
        # floor distances down to next hundredth
        x_dist = math.floor(x_dist * 100) / 100
        y_dist = math.floor(y_dist * 100) / 100
        if x_dist < 1.0 or y_dist < 1.0:
            raise BeaconMeshError("min/max points too close together")
        max_x = min_x + x_dist * (x_count - 1)
        max_y = min_y + y_dist * (y_count - 1)
        pos_y = min_y
        points = []
        for i in range(y_count):
            for j in range(x_count):
                if not i % 2:
                    pos_x = min_x + j * x_dist
                    idx_x = j
                else:
                    pos_x = max_x - j * x_dist
                    idx_x = x_count - j - 1
                points.append((idx_x, i, pos_x, pos_y))
            pos_y += y_dist
        return max_x, max_y, points


def load_config(config):
    return BeaconScanCompensation(config)
