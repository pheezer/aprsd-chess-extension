"""Putting the chess server on APRS maps.

Two kinds of position report, each optional:

* the station beacon: KK6SPK-4's own position (aprsd's built-in beacon always
  says "APRSD GPS Beacon", so this one carries our symbol and comment);
* an APRS object: a position report *about* something, with a name of up to
  9 characters, such as CHESS.  The object is still sent by, and owned by,
  the licensed station callsign -- CHESS is only a label on the map, never a
  callsign anyone logs in or transmits as.
"""

from __future__ import annotations

import datetime
import logging
import time

from aprsd import packets, threads
from aprsd.threads import tx
from aprslib import util as aprslib_util
from oslo_config import cfg

from aprsd_chess_extension import protocol

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")

OBJECT_NAME_LEN = 9


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


class ObjectReport(packets.Packet):
    """An APRS object report (APRS101 chapter 11), sent as-is.

    aprsd's own ObjectPacket can't send a killed object, and since aprsd 5.0
    a beacon queued through tx.send() is silently dropped.  A plain Packet
    with a prebuilt information field avoids both and behaves the same on
    aprsd 4.x and 5.x.
    """

    def _build_payload(self):
        pass  # `payload` is set when the report is made


def validate_object_name(name: str | None) -> str:
    """Return the object name, or raise ValueError saying what's wrong."""
    name = (name or "").strip()
    if not 1 <= len(name) <= OBJECT_NAME_LEN:
        raise ValueError(f"an object name is 1 to {OBJECT_NAME_LEN} characters")
    if not (name.isascii() and name.isprintable()):
        raise ValueError("an object name must be printable ASCII")
    if any(c in name for c in protocol.FORBIDDEN_CHARS):
        raise ValueError("an object name can't contain | ~ or {")
    return name


def make_object(
    callsign: str,
    name: str,
    latitude: float,
    longitude: float,
    comment: str = "",
    symbol: str = "e",
    symbol_table: str = "/",
    alive: bool = True,
    when: datetime.datetime | None = None,
) -> ObjectReport:
    """An object report named `name`, owned and sent by `callsign`.

    Format: ;NAME_____*DDHHMMzDDMM.mmN/DDDMM.mmWe<comment>, where the name
    is padded to 9 characters and `*` (alive) becomes `_` to remove it.
    """
    name = validate_object_name(name)
    when = when or datetime.datetime.now(datetime.timezone.utc)
    info = (
        f";{name:<{OBJECT_NAME_LEN}}{'*' if alive else '_'}{when:%d%H%M}z"
        f"{aprslib_util.latitude_to_ddm(float(latitude))}{symbol_table}"
        f"{aprslib_util.longitude_to_ddm(float(longitude))}{symbol}"
        f"{protocol.wrap_lines(comment or '', 43)[0] if comment else ''}"
    )
    report = ObjectReport(from_call=callsign, to_call=name)
    report.payload = info
    return report


def send_once(packet) -> None:
    """Send a report a single time; no acknowledgement is ever expected.

    Queue it, as aprsd's own beacon does on 4.x, unless aprsd 5.x would
    drop it from the queue (BeaconPackets are skipped there); then send it
    directly, which is how 5.x's built-in beacon goes out.
    """
    packet.retry_count = 1
    skips_beacons = "BeaconPackets are skipped" in (
        packets.PacketTrack.tx.__doc__ or ""
    )
    direct = skips_beacons and isinstance(packet, packets.BeaconPacket)
    tx.send(packet, direct=direct)


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def beacon_settings():
    """(latitude, longitude) for the station beacon, or None if unset."""
    latitude = CONF.aprsd_chess.beacon_latitude
    longitude = CONF.aprsd_chess.beacon_longitude
    if latitude is None:
        latitude = _float(CONF.latitude)
    if longitude is None:
        longitude = _float(CONF.longitude)
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def object_settings():
    """(latitude, longitude) for the object, or None if unset."""
    latitude = CONF.aprsd_chess.object_latitude
    longitude = CONF.aprsd_chess.object_longitude
    fallback = beacon_settings()
    if latitude is None and fallback:
        latitude = fallback[0]
    if longitude is None and fallback:
        longitude = fallback[1]
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def object_comment() -> str:
    return (CONF.aprsd_chess.object_comment or "").replace("{callsign}", CONF.callsign)


class ChessBeaconThread(threads.APRSDThread):
    """Sends the station beacon and/or the object at startup, then every
    beacon_interval.  On a clean shutdown it removes the object from maps."""

    daemon = True

    def __init__(self, beacon_position=None, object_position=None, object_name=None):
        super().__init__("ChessBeacon")
        self.daemon = True  # aprsd 4.x doesn't apply the class attribute
        self.beacon_position = beacon_position
        self.object_position = object_position
        self.object_name = object_name
        self.period = max(60, CONF.aprsd_chess.beacon_interval)
        self._first = True
        self._object_sent = False

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
        if self.beacon_position:
            try:
                send_once(
                    make_beacon(
                        CONF.callsign,
                        *self.beacon_position,
                        CONF.aprsd_chess.beacon_comment,
                        CONF.aprsd_chess.beacon_symbol,
                        CONF.aprsd_chess.beacon_symbol_table,
                    )
                )
                LOG.info(f"Chess: sent beacon '{CONF.aprsd_chess.beacon_comment}'")
            except Exception:
                LOG.exception("Chess: beacon failed")  # never stop the thread
        if self.object_position and self.object_name:
            try:
                send_once(self._object(alive=True))
                self._object_sent = True
                LOG.info(f"Chess: sent object {self.object_name}")
            except Exception:
                LOG.exception("Chess: object failed")

    def _object(self, alive: bool) -> ObjectReport:
        return make_object(
            CONF.callsign,
            self.object_name,
            *self.object_position,
            object_comment(),
            CONF.aprsd_chess.object_symbol,
            CONF.aprsd_chess.object_symbol_table,
            alive=alive,
        )

    def remove_object(self) -> None:
        """Mark the object killed so maps drop it instead of letting it linger."""
        if not self._object_sent:
            return
        try:
            report = self._object(alive=False)
            report.prepare()
            client = _client()
            if client is not None:
                client.send(report)
                LOG.info(f"Chess: removed object {self.object_name} from the map")
        except Exception:
            LOG.warning("Chess: couldn't remove the map object", exc_info=True)
        self._object_sent = False

    def stop(self):
        # Send the kill before aprsd closes the connection: on shutdown aprsd
        # stops every thread in creation order, and the RX thread (which
        # closes the socket on aprsd 5.x) is created after the plugins.  By
        # the time this thread's _cleanup() runs the socket may be gone.
        self.remove_object()
        super().stop()


def _client():
    """The live client to send through directly (any aprsd version)."""
    try:
        from aprsd_chess_extension import compat

        return compat.get_client()
    except Exception:
        return None
