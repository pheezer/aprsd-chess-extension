"""Shared startup for the chess clients (the web board and the terminal).

Connects to APRS-IS or a KISS TNC through aprsd, starts the receive threads
and wires incoming messages into a ClientGameManager.  Both front ends call
`start()` and then run their own UI on top of the manager it returns.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time

from oslo_config import cfg

from aprsd_chess_extension import compat, protocol
from aprsd_chess_extension.client.manager import ClientGameManager

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")

CACHE_FILE_NAME = "aprsd_chess_client.json"


class SetupError(RuntimeError):
    """A problem with the configuration or the connection, for the user."""


@dataclasses.dataclass
class ClientSettings:
    station: str  # the callsign-SSID we transmit as
    player: str  # the base callsign we play as
    server_callsign: str
    cache_file: str


def resolve_settings(server_override: str | None = None) -> ClientSettings:
    server = protocol.normalize_call(
        server_override or CONF.aprsd_chess_client.server_callsign
    )
    if not protocol.is_valid_callsign(server):
        raise SetupError(
            "Set [aprsd_chess_client] server_callsign in the config, or pass --server"
        )
    station = protocol.normalize_call(CONF.callsign)
    if not protocol.is_valid_callsign(station):
        raise SetupError(f"Set [DEFAULT] callsign; {station or 'it'} is not a callsign")
    if station == server:
        raise SetupError("This client must not use the chess server's own callsign")
    player = protocol.base_call(CONF.aprsd_chess_client.player_callsign or station)
    cache_file = CONF.aprsd_chess_client.cache_file or os.path.join(
        os.path.expanduser(CONF.save_location), CACHE_FILE_NAME
    )
    return ClientSettings(station, player, server, cache_file)


def _check_aprsis_login(station: str) -> None:
    """Catch config mistakes aprsd would only report deep inside its logs."""
    if not CONF.aprs_network.enabled:
        return
    if not CONF.aprs_network.password:
        raise SetupError(
            "APRS-IS is enabled but [aprs_network] password (your APRS-IS "
            "passcode) is not set"
        )
    # aprsd 4.x logs in to APRS-IS as [aprs_network] login, not [DEFAULT]
    # callsign.  APRS-IS only routes replies to the logged-in station, so the
    # two must match or the server's answers never arrive.
    if "login" in CONF.aprs_network:
        login = protocol.normalize_call(CONF.aprs_network.login)
        if login != station:
            raise SetupError(
                f"With this aprsd, set [aprs_network] login = {station} "
                f"(it is {login or 'unset'}); APRS-IS only delivers messages "
                "to the callsign you log in as"
            )


def start(server_override: str | None = None):
    """Connect, start aprsd's threads, and return (manager, settings)."""
    try:
        from aprsd.client.client import APRSDClient  # noqa: F401  (aprsd >= 4.2)
    except ImportError:
        raise SetupError("The chess client needs aprsd 4.2.0 or newer") from None

    from aprsd import threads
    from aprsd.packets import core
    from aprsd.threads import keepalive, service, tx

    from aprsd_chess_extension.client.rx import ChessClientPacketThread

    settings = resolve_settings(server_override)
    _check_aprsis_login(settings.station)

    try:
        client = compat.get_client()
    except Exception as ex:
        raise SetupError(f"Could not start the APRS connection: {ex}") from ex
    if not compat.login_success(client):
        raise SetupError(f"Could not connect: {compat.login_failure(client)}")
    try:
        new_filter = compat.add_aprsis_filter(settings.station, client)
        if new_filter:
            LOG.info(f"APRS-IS filter set to '{new_filter}'")
    except Exception:
        LOG.warning("Couldn't set the APRS-IS filter", exc_info=True)

    # msgNo -> text of what we sent; aprsd numbers messages 1-999, so this
    # never grows without bound.
    sent_messages: dict[str, str] = {}

    def send(text: str) -> None:
        packet = core.MessagePacket(
            from_call=settings.station,
            to_call=settings.server_callsign,
            message_text=text,
        )
        packet.prepare(create_msg_number=True)
        sent_messages[str(packet.msgNo)] = text
        tx.send(packet)

    manager = ClientGameManager(
        settings.player,
        settings.server_callsign,
        send,
        cache_file=settings.cache_file,
        pending_timeout=CONF.aprsd_chess_client.pending_timeout,
    )

    service_threads = service.ServiceThreads()
    service_threads.register(keepalive.KeepAliveThread())
    service_threads.register(threads.APRSDRXThread(packet_queue=threads.packet_queue))
    service_threads.register(
        ChessClientPacketThread(threads.packet_queue, manager, sent_messages)
    )
    service_threads.register(_timeout_thread(manager))
    service_threads.start()
    return manager, settings


def stop() -> None:
    from aprsd import threads

    threads.APRSDThreadList().stop_all()


def _timeout_thread(manager):
    from aprsd import threads

    class TimeoutThread(threads.APRSDThread):
        """Marks commands the server never answered so the UI can offer a retry."""

        period = 5
        daemon = True

        def __init__(self, manager):
            super().__init__("ChessTimeouts")
            self.daemon = True  # aprsd 4.x doesn't apply the class attribute
            self.manager = manager

        def loop(self):
            try:
                self.manager.check_timeouts()
            except Exception:
                LOG.exception("Chess client: timeout check failed")
            # APRSDThread.wait() only exists from aprsd 5.0.
            for _ in range(self.period):
                if self._should_quit():
                    break
                time.sleep(1)
            return True

    return TimeoutThread(manager)
