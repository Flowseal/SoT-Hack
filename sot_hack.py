"""
@Author https://github.com/DougTheDruid
@Source https://github.com/DougTheDruid/SoT-ESP-Framework
For community support, please contact me on Discord: DougTheDruid#2784
"""

import struct
import logging
import globals
from memory_helper import ReadMemory
from mapping import ship_keys, world_events_keys
from helpers import OFFSETS, CONFIG, logger
from Modules import (
    ShipModule,
    CrewsModule,
    WorldEventsModule,
    DisplayObject
)

class SoTMemoryReader:
    """
    Wrapper class to handle reading data from the game, parsing what is
    important, and returning it to be shown by pyglet
    """

    def __init__(self):
        """
        Upon initialization of this object, we want to find the base address
        for the SoTGame.exe, then begin to load in the static addresses for the
        uWorld, gName, gObject, and uLevel objects.

        We also poll the local_player object to get a first round of coords.
        When running read_actors, we update the local players coordinates
        using the camera-manager object

        Also initialize a number of class variables which help us cache some
        basic information
        """
        globals.rm = ReadMemory("SoTGame.exe")

        base_address = globals.rm.base_address
        logging.info(f"Process ID: {globals.rm.pid}")

        u_world_offset = globals.rm.read_ulong(
            base_address + globals.rm.u_world_base + 3
        )
        u_world = base_address + globals.rm.u_world_base + u_world_offset + 7
        self.world_address = globals.rm.read_ptr(u_world)

        g_name_offset = globals.rm.read_ulong(
            base_address + globals.rm.g_name_base + 3
        )
        g_name = base_address + globals.rm.g_name_base + g_name_offset + 7
        logging.info(f"SoT gName Address: {hex(g_name)}")
        self.g_name = globals.rm.read_ptr(g_name)

        g_objects_offset = globals.rm.read_ulong(
            base_address + globals.rm.g_object_base + 2
        )
        g_objects = base_address + globals.rm.g_object_base + g_objects_offset + 22
        logging.info(f"SoT gObject Address: {hex(g_objects)}")
        self.g_objects = globals.rm.read_ptr(g_objects)

        self.u_level = globals.rm.read_ptr(self.world_address +
                                        OFFSETS.get('World.PersistentLevel'))

        self.u_local_player = self._load_local_player()
        self.player_controller = globals.rm.read_ptr(
            self.u_local_player + OFFSETS.get('LocalPlayer.PlayerController')
        )

        self.my_coords = self._coord_builder(self.u_local_player)
        self.my_coords['fov'] = 90

        self.actor_name_map = {}
        self.display_objects: list[DisplayObject] = []
        self.crew_data = None

    def _load_local_player(self) -> int:
        """
        Returns the local player object out of uWorld.UGameInstance.
        Used to get the players coordinates before reading any actors
        :rtype: int
        :return: Memory address of the local player object
        """
        game_instance = globals.rm.read_ptr(
            self.world_address + OFFSETS.get('World.OwningGameInstance')
        )
        local_player = globals.rm.read_ptr(
            game_instance + OFFSETS.get('GameInstance.LocalPlayers')
        )
        return globals.rm.read_ptr(local_player)

    def update_my_coords(self):
        """
        Function to update the players coordinates and camera information
        storing that new info back into the my_coords field. Necessary as
        we dont always run a full scan and we need a way to update ourselves
        """
        manager = globals.rm.read_ptr(
            self.player_controller + OFFSETS.get('PlayerController.CameraManager')
        )
        self.my_coords = self._coord_builder(
            manager,
            OFFSETS.get('PlayerCameraManager.CameraCache')
            + OFFSETS.get('CameraCacheEntry.MinimalViewInfo'),
            fov=True)

    def _coord_builder(self, actor_address: int, offset=0x78, camera=True,
                       fov=False) -> dict:
        """
        Given a specific actor, loads the coordinates for that actor given
        a number of parameters to define the output
        :param int actor_address: Actors base memory address
        :param int offset: Offset from actor address to beginning of coords
        :param bool camera: If you want the camera info as well
        :param bool fov: If you want the FoV info as well
        :rtype: dict
        :return: A dictionary containing the coordinate information
        for a specific actor
        """
        if fov:
            actor_bytes = globals.rm.read_bytes(actor_address + offset, 44)
            unpacked = struct.unpack("<ffffff16pf", actor_bytes)
        else:
            actor_bytes = globals.rm.read_bytes(actor_address + offset, 24)
            unpacked = struct.unpack("<ffffff", actor_bytes)

        coordinate_dict = {"x": unpacked[0]/100, "y": unpacked[1]/100,
                           "z": unpacked[2]/100}
        if camera:
            coordinate_dict["cam_x"] = unpacked[3]
            coordinate_dict["cam_y"] = unpacked[4]
            coordinate_dict["cam_z"] = unpacked[5]
        if fov:
            coordinate_dict['fov'] = unpacked[7]

        return coordinate_dict

    def read_actors(self):
        """
        Represents a full scan of every actor within our render distance.
        Will create an object for each type of object we are interested in,
        and store it in a class variable (display_objects).
        Then our main game loop updates those objects
        """
        # On a full run, start by cleaning up all the existing text renders
        for display_ob in self.display_objects:
            display_ob.delete()

        self.display_objects = []
        self.update_my_coords()

        actor_raw = globals.rm.read_bytes(self.u_level + 0xa0, 0xC)
        actor_data = struct.unpack("<Qi", actor_raw)

        # Credit @mogistink https://www.unknowncheats.me/forum/members/3434160.html
        # One very large read for all the actors addresses to save us 1000+ reads every read_all
        level_actors_raw = globals.rm.read_bytes(actor_data[0], actor_data[1] * 8)

        for x in range(0, actor_data[1]):
            # We start by getting the ActorID for a given actor, and comparing
            # that ID to a list of "known" id's we cache in self.actor_name_map
            raw_name = ""
            actor_address = int.from_bytes(level_actors_raw[(x*8):(x*8+8)], byteorder='little', signed=False)
            actor_id = globals.rm.read_int(
                actor_address + OFFSETS.get('Actor.actorId')
            )

            # We save a mapping of actor id to actor name for the sake of
            # saving memory calls
            if actor_id not in self.actor_name_map and actor_id != 0:
                try:
                    raw_name = globals.rm.read_gname(actor_id)
                    self.actor_name_map[actor_id] = raw_name
                except Exception as e:
                    logger.error(f"Unable to find actor name: {e}")
            elif actor_id in self.actor_name_map:
                raw_name = self.actor_name_map.get(actor_id)

            # Ignore anything we cannot find a name for
            if not raw_name:
                continue

            if CONFIG.get('SHIPS_ENABLED') and raw_name in ship_keys:
                ship = ShipModule(actor_id, actor_address, self.my_coords, raw_name)
                self.display_objects.append(ship)

            elif CONFIG.get('CREWS_ENABLED') and raw_name == "CrewService":
                self.crew_data = CrewsModule(actor_id, actor_address)

            elif CONFIG.get("WORLD_ENABLED") and raw_name in world_events_keys:
                world_event = WorldEventsModule(actor_id, actor_address, self.my_coords, raw_name)
                self.display_objects.append(world_event)
