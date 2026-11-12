"""The APRSD plugin: `chess ...` messages to this station play chess."""

from __future__ import annotations

import logging
import os
import time

from aprsd import packets, plugin, threads
from aprsd.threads import tx
from oslo_config import cfg

import aprsd_chess_extension
from aprsd_chess_extension import (
    beacon,
    compat,
    conf,  # noqa: F401  (registers options)
    protocol,
)
from aprsd_chess_extension.server import ChessServer, Response, Settings
from aprsd_chess_extension.store import GameStore

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")

DATA_FILE_NAME = "aprsd_chess_games.json"


def data_file_path() -> str:
    path = CONF.aprsd_chess.data_file
    if path:
        return os.path.expanduser(path)
    return os.path.join(os.path.expanduser(CONF.save_location), DATA_FILE_NAME)


class ChessExpiryThread(threads.APRSDThread):
    """Deletes games nobody has touched for `game_expire_days`."""

    period = 3600
    daemon = True  # games are saved on every change; never block exit

    def __init__(self, server: ChessServer):
        super().__init__("ChessExpiry")
        self.daemon = True  # aprsd 4.x doesn't apply the class attribute
        self.server = server

    def loop(self) -> bool:
        try:
            self.server.expire()
        except Exception:
            # aprsd 4.x kills a thread whose loop() raises.
            LOG.exception("Chess: expiry sweep failed")
        # APRSDThread.wait() doesn't exist in aprsd 4.x, so sleep in short
        # slices and stop promptly on shutdown.
        slept = 0
        while slept < self.period and not self._should_quit():
            time.sleep(1)
            slept += 1
        return True

    def _cleanup(self):
        try:
            self.server.store.save()
        except Exception:
            LOG.exception("Chess: failed to save games on shutdown")


class ChessPlugin(plugin.APRSDRegexCommandPluginBase):
    """Host chess games between callsigns over APRS."""

    version = aprsd_chess_extension.__version__
    command_regex = r"^chess(\s|$)"
    command_name = "chess"
    short_description = "Play chess over APRS: chess help"
    enabled = False

    def setup(self):
        self.enabled = False
        self.server = None
        try:
            store = GameStore()
            store.load(data_file_path())
            store.save()  # fail now, not mid-game, if the file isn't writable
            self.server = ChessServer(
                CONF.callsign,
                store,
                Settings(
                    expire_days=CONF.aprsd_chess.game_expire_days,
                    max_games_per_player=CONF.aprsd_chess.max_games_per_player,
                    max_invites_per_day=CONF.aprsd_chess.max_invites_per_day,
                ),
            )
            self.server.expire()
        except Exception:
            LOG.exception("Chess: plugin disabled, could not set up the game store")
            return

        if CONF.aprsd_chess.aprsis_filter:
            try:
                new_filter = compat.add_aprsis_filter(CONF.callsign)
                if new_filter:
                    LOG.info(f"Chess: APRS-IS filter is now '{new_filter}'")
            except Exception:
                LOG.warning(
                    "Chess: couldn't set the APRS-IS filter; messages may not "
                    "reach this station",
                    exc_info=True,
                )
        LOG.info(
            f"Chess: serving {len(self.server.store)} games from {data_file_path()}"
        )
        self.enabled = True

    def create_threads(self):
        if not (self.enabled and self.server):
            return []
        created = [ChessExpiryThread(self.server)]
        if CONF.aprsd_chess.beacon_enabled:
            position = beacon.beacon_settings()
            if position:
                created.append(beacon.ChessBeaconThread(*position))
            else:
                LOG.warning(
                    "Chess: beacon_enabled but no position; set "
                    "[aprsd_chess] beacon_latitude and beacon_longitude"
                )
        return created

    def help(self):
        return list(protocol.HELP_LINES)

    def process(self, packet: packets.MessagePacket):
        from_call = packet.from_call
        response: Response = self.server.handle(from_call, packet.message_text or "")
        # Notices to the other player are sent here rather than returned:
        # aprsd 5.x may attach the sender's piggyback ack to a returned
        # packet regardless of who it's addressed to.
        for notice in response.notices:
            try:
                tx.send(
                    packets.MessagePacket(
                        from_call=CONF.callsign,
                        to_call=notice.to_call,
                        message_text=notice.text,
                    )
                )
            except Exception:
                LOG.exception(f"Chess: failed to send notice to {notice.to_call}")
        if not response.replies:
            return packets.NULL_MESSAGE
        if len(response.replies) == 1:
            return response.replies[0]
        return list(response.replies)
