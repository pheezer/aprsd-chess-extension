"""`aprsd chess`: run the web chessboard client.

Heavy imports (aprsd's client and threads, Flask) happen inside the command,
so merely loading the extension -- which every `aprsd` command does -- has no
side effects.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

import click
from aprsd import cli_helper
from aprsd.main import cli
from oslo_config import cfg

from aprsd_chess_extension import conf  # noqa: F401  (registers options)

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")

CACHE_FILE_NAME = "aprsd_chess_client.json"


@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.option(
    "-p",
    "--port",
    type=int,
    default=None,
    help="Port for the web chessboard (default: [aprsd_chess_client] web_port).",
)
@click.option(
    "--server",
    "server_callsign",
    default=None,
    help="Callsign of the chess server (default: [aprsd_chess_client] server_callsign).",
)
@click.pass_context
@cli_helper.process_standard_options
def chess(ctx, port, server_callsign):
    """Run the APRS chess web client (a graphical board in your browser)."""
    try:
        from aprsd.client.client import APRSDClient  # noqa: F401  (aprsd >= 4.2)
    except ImportError:
        click.echo("aprsd chess needs aprsd 4.2.0 or newer.", err=True)
        sys.exit(1)

    import aprsd
    from aprsd import threads
    from aprsd.packets import core
    from aprsd.threads import keepalive, service, tx

    from aprsd_chess_extension import compat, protocol
    from aprsd_chess_extension.client.app import create_app
    from aprsd_chess_extension.client.manager import ClientGameManager
    from aprsd_chess_extension.client.rx import ChessClientPacketThread

    server_callsign = protocol.normalize_call(
        server_callsign or CONF.aprsd_chess_client.server_callsign
    )
    if not protocol.is_valid_callsign(server_callsign):
        LOG.error("Set [aprsd_chess_client] server_callsign (or pass --server)")
        sys.exit(1)
    if not protocol.is_valid_callsign(CONF.callsign):
        LOG.error(f"Set [DEFAULT] callsign; '{CONF.callsign}' is not a callsign")
        sys.exit(1)
    if protocol.normalize_call(CONF.callsign) == server_callsign:
        LOG.error("This client must not use the chess server's own callsign")
        sys.exit(1)
    player = protocol.base_call(
        CONF.aprsd_chess_client.player_callsign or CONF.callsign
    )
    port = port or CONF.aprsd_chess_client.web_port
    cache_file = CONF.aprsd_chess_client.cache_file or os.path.join(
        os.path.expanduser(CONF.save_location), CACHE_FILE_NAME
    )

    LOG.info(
        f"APRS chess client {aprsd.__version__} as {CONF.callsign} (player {player})"
    )

    def shutdown(signum, frame):
        LOG.info("Chess client shutting down")
        threads.APRSDThreadList().stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    client = compat.get_client()
    if not client.login_success():
        LOG.error(f"Could not connect: {client.login_failure()}")
        sys.exit(1)
    try:
        new_filter = compat.add_aprsis_filter(CONF.callsign, client)
        if new_filter:
            LOG.info(f"APRS-IS filter set to '{new_filter}'")
    except Exception:
        LOG.warning("Couldn't set the APRS-IS filter", exc_info=True)

    sent_messages: dict[str, str] = {}

    def send(text: str) -> None:
        packet = core.MessagePacket(
            from_call=CONF.callsign,
            to_call=server_callsign,
            message_text=text,
        )
        packet.prepare(create_msg_number=True)
        sent_messages[str(packet.msgNo)] = text
        # aprsd numbers messages 1-999, so this never grows without bound.
        tx.send(packet)

    manager = ClientGameManager(
        player,
        server_callsign,
        send,
        cache_file=cache_file,
        pending_timeout=CONF.aprsd_chess_client.pending_timeout,
    )

    service_threads = service.ServiceThreads()
    service_threads.register(keepalive.KeepAliveThread())
    service_threads.register(threads.APRSDRXThread(packet_queue=threads.packet_queue))
    service_threads.register(
        ChessClientPacketThread(threads.packet_queue, manager, sent_messages)
    )
    service_threads.register(TimeoutThread(manager))
    service_threads.start()

    manager.startup_sync()

    title = "DigiPi Chess" if getattr(CONF, "is_digipi", False) else "APRS Chess"
    app = create_app(manager, title=title)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    LOG.info(f"Chessboard at http://{CONF.aprsd_chess_client.web_ip}:{port}/")
    app.run(
        host=CONF.aprsd_chess_client.web_ip,
        port=port,
        threaded=True,
        debug=False,
        use_reloader=False,
    )


def _timeout_thread_class():
    from aprsd import threads

    class _TimeoutThread(threads.APRSDThread):
        """Marks commands the server never answered so the UI can offer a retry."""

        period = 5

        def __init__(self, manager):
            super().__init__("ChessTimeouts")
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

    return _TimeoutThread


def TimeoutThread(manager):  # noqa: N802  (factory standing in for a class)
    return _timeout_thread_class()(manager)
