from oslo_config import cfg

server_group = cfg.OptGroup(
    name="aprsd_chess",
    title="APRSD chess server plugin settings",
)

server_opts = [
    cfg.IntOpt(
        "game_expire_days",
        default=10,
        min=1,
        help="Delete a game when nobody has sent a command for it in this many days.",
    ),
    cfg.IntOpt(
        "max_games_per_player",
        default=5,
        min=1,
        help="Most unfinished games (including pending invites) one callsign "
        "may have at a time.",
    ),
    cfg.IntOpt(
        "max_invites_per_day",
        default=10,
        min=1,
        help="Most new games one callsign may start in 24 hours. Each new "
        "game sends an unsolicited invite to the opponent.",
    ),
    cfg.StrOpt(
        "data_file",
        default=None,
        help="JSON file the games are saved in. Defaults to "
        "aprsd_chess_games.json in [DEFAULT] save_location.",
    ),
    cfg.BoolOpt(
        "aprsis_filter",
        default=True,
        help="When connected to APRS-IS, add a g/<callsign> filter so every "
        "message addressed to this server is delivered. Any existing filter "
        "is kept.",
    ),
]

client_group = cfg.OptGroup(
    name="aprsd_chess_client",
    title="aprsd chess web client settings",
)

client_opts = [
    cfg.StrOpt(
        "server_callsign",
        default=None,
        help="Callsign of the APRSD station running the chess server plugin, "
        "e.g. KK6SPK-12. Required.",
    ),
    cfg.StrOpt(
        "player_callsign",
        default=None,
        help="Callsign you play as. Defaults to the base of [DEFAULT] "
        "callsign, so a client transmitting as KK6SPK-6 plays as KK6SPK.",
    ),
    cfg.StrOpt(
        "web_ip",
        default="0.0.0.0",
        help="Address the web chessboard listens on.",
    ),
    cfg.PortOpt(
        "web_port",
        default=8056,
        help="Port the web chessboard listens on. (8001 is direwolf's KISS "
        "port and 8055 is APRS WebChat on a DigiPi.)",
    ),
    cfg.StrOpt(
        "cache_file",
        default=None,
        help="JSON file caching the client's view of its games. Defaults to "
        "aprsd_chess_client.json in [DEFAULT] save_location. Losing it is "
        "harmless; the client re-syncs from the server.",
    ),
    cfg.IntOpt(
        "pending_timeout",
        default=200,
        min=30,
        help="Seconds to wait for the server to confirm a command before "
        "offering a retry. aprsd gives up resending after about 155 seconds.",
    ),
]


def register_opts(config):
    config.register_group(server_group)
    config.register_opts(server_opts, group=server_group)
    config.register_group(client_group)
    config.register_opts(client_opts, group=client_group)


def list_opts():
    return {
        server_group.name: server_opts,
        client_group.name: client_opts,
    }
