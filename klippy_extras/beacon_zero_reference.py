# Runtime zero reference position for the loaded bed mesh
#
# Adapted from the SET_ZERO_REFERENCE_POSITION command of RatOS (ratos.py),
# Copyright (C) Helge Keck and the RatOS contributors. Standalone version for
# stock Klipper and Kalico.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Config:
#   [beacon_zero_reference]
#   save_profile: True        # save the mesh profile again after the change (as RatOS does)
#
# Command:
#   SET_ZERO_REFERENCE_POSITION X= Y=
#       Re-references the currently loaded bed mesh so that the mesh height at
#       X,Y becomes zero, and saves the mesh profile again. The print start macro
#       uses it with the position where the true zero was measured, which can
#       change at every print when the fuzzy true zero position is on.

import logging


class BeaconZeroReference:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.save_profile = config.getboolean("save_profile", True)
        self.gcode.register_command(
            "SET_ZERO_REFERENCE_POSITION", self.cmd_SET_ZERO_REFERENCE_POSITION,
            desc=self.cmd_SET_ZERO_REFERENCE_POSITION_help)

    cmd_SET_ZERO_REFERENCE_POSITION_help = (
        "Set the zero reference position of the loaded bed mesh. X= Y=")

    def cmd_SET_ZERO_REFERENCE_POSITION(self, gcmd):
        bed_mesh = self.printer.lookup_object("bed_mesh", None)
        if bed_mesh is None:
            raise gcmd.error("The bed_mesh module is not configured. "
                             "This command requires a [bed_mesh] section.")
        if bed_mesh.z_mesh is None:
            gcmd.respond_info("SET_ZERO_REFERENCE_POSITION: no bed mesh is "
                              "loaded. Create a mesh or load one with "
                              "BED_MESH_PROFILE LOAD=name first.")
            return
        x_pos = gcmd.get_float("X")
        y_pos = gcmd.get_float("Y")
        # the ZMesh class lives in the bed_mesh module: use the type of the
        # loaded mesh, so this works on stock Klipper and on Kalico
        org_mesh = bed_mesh.get_mesh()
        new_mesh = type(org_mesh)(org_mesh.get_mesh_params(),
                                  org_mesh.get_profile_name())
        new_mesh.build_mesh(org_mesh.get_probed_matrix())
        new_mesh.set_zero_reference(x_pos, y_pos)
        bed_mesh.set_mesh(new_mesh)
        name = new_mesh.get_profile_name()
        if name and self.save_profile:
            bed_mesh.pmgr.save_profile(name)
        logging.info("beacon_zero_reference: X:%.2f Y:%.2f", x_pos, y_pos)
        gcmd.respond_info("Zero reference position of the bed mesh set to "
                          "X:%.2f Y:%.2f" % (x_pos, y_pos))


def load_config(config):
    return BeaconZeroReference(config)
