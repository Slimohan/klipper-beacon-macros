# Named Z/XY offsets for the Beacon macro set
#
# Adapted from RatOS named_offsets.py, Copyright (C) 2025 Tom Glastonbury
# <t@tg73.net>. Standalone version: it keeps a set of named offsets that are
# added to every move through a gcode_move transform, next to the offset of
# SET_GCODE_OFFSET, which stays free for the user.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Config:
#   [beacon_offsets]
#
# Commands:
#   GET_BEACON_OFFSETS                       list all offsets
#   SET_BEACON_OFFSET NAME= X= Y= Z= (or X_ADJUST ...) MOVE=0|1 MOVE_SPEED=
#   RESET_BEACON_OFFSET NAME=                set one offset to zero

import logging
from math import isclose

ZERO = (0.0, 0.0, 0.0, 0.0)
XYZE = "XYZE"
COMBINED = "combined_offset"


class BeaconOffsets:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.gcode = self.printer.lookup_object("gcode")
        self.offsets = {}
        self.reset_events = {}
        self.combined_offset = ZERO
        self.status = None
        self.gcode_move = None
        self.next_transform = None
        self.Coord = self.gcode.Coord
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("stepper_enable:motor_off",
                                            self._handle_motor_off)
        self.gcode.register_command(
            "GET_BEACON_OFFSETS", self.cmd_GET_BEACON_OFFSETS,
            desc="Report the named offsets of the Beacon macro set")
        self.gcode.register_command(
            "SET_BEACON_OFFSET", self.cmd_SET_BEACON_OFFSET,
            desc="Set a named offset, same parameters as SET_GCODE_OFFSET")
        self.gcode.register_command(
            "RESET_BEACON_OFFSET", self.cmd_RESET_BEACON_OFFSET,
            desc="Reset a named offset to zero")

    def _handle_connect(self):
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.next_transform = self.gcode_move.set_move_transform(
            self, force=True)

    def _handle_motor_off(self, print_time=None):
        # offsets that depend on the homed position are invalid after motors off
        changed = False
        for name in list(self.offsets):
            if "motor_off" in self.reset_events.get(name, ("motor_off",)):
                del self.offsets[name]
                changed = True
        if changed:
            self._offset_changed()

    # --- API for other extras ---
    def set(self, name, x=None, y=None, z=None, e=None,
            x_adjust=None, y_adjust=None, z_adjust=None, e_adjust=None,
            move=False, move_speed=None, reset_events=("motor_off",)):
        name = name.strip().lower()
        offset = list(self.offsets.get(name, ZERO))
        vals = (x, y, z, e)
        adjs = (x_adjust, y_adjust, z_adjust, e_adjust)
        for pos in range(4):
            if vals[pos] is not None:
                offset[pos] = vals[pos]
            elif adjs[pos] is not None:
                offset[pos] += adjs[pos]
        self.reset_events[name] = tuple(reset_events)
        if all(isclose(v, 0.0, abs_tol=1e-9) for v in offset):
            self.offsets.pop(name, None)
        else:
            self.offsets[name] = tuple(offset)
        self._offset_changed(move, move_speed)

    def reset(self, name, move=False, move_speed=None):
        name = name.strip().lower()
        if name in self.offsets:
            self.offsets.pop(name)
            self._offset_changed(move, move_speed)

    def get(self, name):
        return self.offsets.get(name.strip().lower(), ZERO)

    # --- commands ---
    def cmd_GET_BEACON_OFFSETS(self, gcmd):
        if not self.offsets:
            gcmd.respond_info("%s: all offsets are zero" % (self.name,))
            return
        lines = []
        for k, v in sorted(self.offsets.items()):
            lines.append("%s: %s" % (k, self._fmt(v)))
        lines.append("%s: %s" % (COMBINED, self._fmt(self.combined_offset)))
        gcmd.respond_info("\n".join(lines))

    def cmd_SET_BEACON_OFFSET(self, gcmd):
        name = gcmd.get("NAME").strip().lower()
        vals = [gcmd.get_float(a, None) for a in XYZE]
        adjs = [gcmd.get_float(a + "_ADJUST", None) for a in XYZE]
        move = gcmd.get_int("MOVE", 0) == 1
        speed = gcmd.get_float("MOVE_SPEED", None, above=0.0)
        self.set(name, *vals, *adjs, move=move, move_speed=speed,
                 reset_events=self.reset_events.get(name, ("motor_off",)))

    def cmd_RESET_BEACON_OFFSET(self, gcmd):
        name = gcmd.get("NAME").strip().lower()
        move = gcmd.get_int("MOVE", 0) == 1
        speed = gcmd.get_float("MOVE_SPEED", None, above=0.0)
        self.reset(name, move, speed)

    def _fmt(self, v):
        return " ".join("%s:%.6f" % (XYZE[i], p) for i, p in enumerate(v))

    # --- internals ---
    def _offset_changed(self, move=False, move_speed=None):
        previous = self.combined_offset
        total = [0.0] * 4
        for offset in self.offsets.values():
            for i in range(4):
                total[i] += offset[i]
        combined = tuple(total)
        if all(isclose(v, 0.0, abs_tol=1e-9) for v in combined):
            combined = ZERO
        self.combined_offset = combined
        self._update_status()
        delta = tuple(combined[i] - previous[i] for i in range(4))
        if delta == ZERO or self.gcode_move is None:
            return
        gm = self.gcode_move
        gm.reset_last_position()
        if move:
            speed = gm.speed if move_speed is None else move_speed
            for i in range(4):
                gm.last_position[i] += delta[i]
            gm.move_with_transform(gm.last_position, speed)
        logging.info("%s: combined offset now %s", self.name,
                     self._fmt(combined))

    # gcode_move transform interface
    def get_position(self):
        pos = list(self.next_transform.get_position())
        for i in range(4):
            pos[i] -= self.combined_offset[i]
        return pos

    def move(self, newpos, speed):
        pos = list(newpos)
        for i in range(4):
            pos[i] += self.combined_offset[i]
        self.next_transform.move(pos, speed)

    def _coord(self, v):
        # Klipper's Coord takes one tuple, Kalico's takes x, y, z, e
        try:
            return self.Coord(*v)
        except TypeError:
            return self.Coord(v)

    def _update_status(self):
        status = {n: self._coord(v) for n, v in self.offsets.items()}
        status[COMBINED] = self._coord(self.combined_offset)
        self.status = status

    def get_status(self, eventtime=None):
        if self.status is None:
            self._update_status()
        return self.status


def load_config(config):
    return BeaconOffsets(config)
