"""The chess server's position beacon, so the station shows up on APRS maps.

aprsd's own beacon always says "APRSD GPS Beacon"; this one carries the
configured symbol (a horse, `/e`, by default) and comment ("APRS Chess").
"""

from __future__ import annotations

import logging
import time

from aprsd import packets, threads
from aprsd.threads import tx
from oslo_config import cfg

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")


def make_beacon(
    callsign: str,
    latitude: float,
    longitude: float,
    comment: str,
    symbol: str = "e",
    symbol_table: str = "/",
):
    """A BeaconPacket on any aprsd: 5.x wants a Position, 4.x flat fields."""
    fields = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "comment": comment,
        "symbol": symbol,
        "symbol_table": symbol_table,
    }
    position_type = getattr(packets, "Position", None)
    if position_type is not None:
        return packets.BeaconPacket(
            from_call=callsign, to_call="APRS", position=position_type(**fields)
        )
    return packets.BeaconPacket(from_call=callsign, to_call="APRS", **fields)


def beacon_settings():
    """(latitude, longitude) to beacon from, or None if no position is set."""
    latitude = CONF.aprsd_chess.beacon_latitude
    longitude = CONF.aprsd_chess.beacon_longitude
    if latitude is None or longitude is None:
        # Fall back to the station position aprsd itself is configured with.
        latitude = latitude if latitude is not None else _float(CONF.latitude)
        longitude = longitude if longitude is not None else _float(CONF.longitude)
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ChessBeaconThread(threads.APRSDThread):
    """Sends the beacon right after startup, then every beacon_interval."""

    daemon = True

    def __init__(self, latitude: float, longitude: float):
        super().__init__("ChessBeacon")
        self.daemon = True  # aprsd 4.x doesn't apply the class attribute
        self.latitude = latitude
        self.longitude = longitude
        self.period = max(60, CONF.aprsd_chess.beacon_interval)
        self._first = True

    def loop(self) -> bool:
        if self._first:
            self._first = False
        else:
            # APRSDThread.wait() doesn't exist in aprsd 4.x.
            slept = 0
            while slept < self.period and not self._should_quit():
                time.sleep(1)
                slept += 1
            if self._should_quit():
                return False
        self.send()
        return True

    def send(self) -> None:
        try:
            packet = make_beacon(
                CONF.callsign,
                self.latitude,
                self.longitude,
                CONF.aprsd_chess.beacon_comment,
                CONF.aprsd_chess.beacon_symbol,
                CONF.aprsd_chess.beacon_symbol_table,
            )
            packet.retry_count = 1  # a beacon is never acknowledged
            tx.send(packet)
            LOG.info(f"Chess: sent beacon '{CONF.aprsd_chess.beacon_comment}'")
        except Exception:
            # A failed beacon must never stop the thread.
            LOG.exception("Chess: beacon failed")
