"""`aprsd chess`: the chess clients.

    aprsd chess play    play in the terminal (a text board and a prompt)
    aprsd chess web     run the graphical web board
    aprsd chess         same as `aprsd chess web` (what the DigiPi service runs)

Both connect through aprsd, to APRS-IS or a KISS TNC, as configured.  Heavy
imports (aprsd's client and threads, Flask) happen inside the commands, so
merely loading the extension -- which every `aprsd` command does -- has no
side effects.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

import click
from aprsd import cli_helper
from aprsd.main import cli
from click.core import ParameterSource
from oslo_config import cfg

from aprsd_chess_extension import conf  # noqa: F401  (registers options)

CONF = cfg.CONF
LOG = logging.getLogger("APRSD")

TERMINAL_LOG_NAME = "aprsd_chess_terminal.log"

_port_option = click.option(
    "-p",
    "--port",
    type=int,
    default=None,
    help="Port for the web chessboard (default: [aprsd_chess_client] web_port).",
)
_server_option = click.option(
    "--server",
    "server_callsign",
    default=None,
    help="Callsign of the chess server (default: [aprsd_chess_client] server_callsign).",
)


def _inherit_group_options(ctx, kwargs):
    """Apply options given before the subcommand, as in `aprsd chess -c x play`.

    The subcommand's own value wins when it was typed explicitly.
    """
    given = (ctx.obj or {}).get("chess_group_options", {})
    for name, value in given.items():
        if name in kwargs and ctx.get_parameter_source(name) == ParameterSource.DEFAULT:
            kwargs[name] = value
    return kwargs


@cli.group(invoke_without_command=True)
@cli_helper.add_options(cli_helper.common_options)
@_port_option
@_server_option
@click.pass_context
def chess(ctx, **kwargs):
    """Play chess over APRS: `aprsd chess play` (terminal) or `aprsd chess web`."""
    ctx.ensure_object(dict)
    ctx.obj["chess_group_options"] = {
        name: value
        for name, value in kwargs.items()
        if ctx.get_parameter_source(name) != ParameterSource.DEFAULT
    }
    if ctx.invoked_subcommand is None:
        _web_with_options(ctx, **kwargs)


@chess.command("web")
@cli_helper.add_options(cli_helper.common_options)
@_port_option
@_server_option
@click.pass_context
def web(ctx, **kwargs):
    """Run the graphical web chessboard."""
    _web_with_options(ctx, **_inherit_group_options(ctx, kwargs))


@chess.command("play")
@cli_helper.add_options(cli_helper.common_options)
@_server_option
@click.option(
    "--game", "game_code", default=None, help="Load this game (its code) at startup."
)
@click.option(
    "--ascii",
    "use_ascii",
    is_flag=True,
    default=False,
    help="Draw pieces as letters (PNBRQK, pnbrqk) instead of chess symbols.",
)
@click.option("--no-color", is_flag=True, default=False, help="Don't color the board.")
@click.option(
    "--show-logs",
    is_flag=True,
    default=False,
    help="Print aprsd's log in the terminal too (it goes to a file otherwise).",
)
@click.pass_context
def play(ctx, **kwargs):
    """Play chess in the terminal."""
    _play_with_options(ctx, **_inherit_group_options(ctx, kwargs))


def _web(ctx, port, server_callsign):
    from aprsd_chess_extension.client import runtime
    from aprsd_chess_extension.client.app import create_app

    def shutdown(signum, frame):
        LOG.info("Chess client shutting down")
        runtime.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        manager, settings = runtime.start(server_callsign)
    except runtime.SetupError as ex:
        LOG.error(str(ex))
        sys.exit(1)
    LOG.info(f"APRS chess web client as {settings.station} (player {settings.player})")
    manager.startup_sync()

    port = port or CONF.aprsd_chess_client.web_port
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


def _play(ctx, server_callsign, game_code, use_ascii, no_color, show_logs):
    from aprsd_chess_extension.client import runtime
    from aprsd_chess_extension.client.terminal import TerminalClient

    if not show_logs:
        click.echo(f"(aprsd's log is going to {_log_to_file()})")
    try:
        manager, settings = runtime.start(server_callsign)
    except runtime.SetupError as ex:
        click.echo(f"aprsd chess: {ex}", err=True)
        sys.exit(1)
    click.echo(
        f"Connected as {settings.station}, playing as {settings.player} "
        f"on server {settings.server_callsign}."
    )
    terminal = TerminalClient(
        manager,
        use_unicode=False if use_ascii else None,
        use_color=False if no_color else None,
    )
    try:
        import readline  # noqa: F401  (line editing and history for input())
    except ImportError:
        pass
    manager.startup_sync()
    if game_code:
        terminal.handle(f"load {game_code}")
    try:
        terminal.run()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
    click.echo("73")
    # aprsd's send threads may still be waiting to retry; don't hang on them.
    sys.stdout.flush()
    os._exit(0)


_web_with_options = cli_helper.process_standard_options(_web)
_play_with_options = cli_helper.process_standard_options(_play)


def _log_to_file() -> str:
    """Send aprsd's log (loguru) to a file so it doesn't scribble on the board."""
    from loguru import logger

    directory = os.path.expanduser(CONF.save_location)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, TERMINAL_LOG_NAME)
    logger.configure(
        handlers=[
            {
                "sink": path,
                "level": "INFO",
                "format": "{time:YYYY-MM-DD HH:mm:ss.SSS} | {thread.name: <18} | "
                "{level: <8} | {message}",
                "colorize": False,
            }
        ]
    )
    return path
