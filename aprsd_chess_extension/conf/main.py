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
    cfg.BoolOpt(
        "beacon_enabled",
        default=False,
        help="Send a position beacon so the chess server shows up on APRS maps. "
        "Turn off [DEFAULT] enable_beacon too, or the station beacons twice.",
    ),
    cfg.StrOpt(
        "beacon_comment",
        default="APRS Chess",
        help="Text shown with the beacon on the map.",
    ),
    cfg.StrOpt(
        "beacon_symbol",
        default="e",
        help="APRS symbol character. With the primary table (/), e is a horse.",
    ),
    cfg.StrOpt(
        "beacon_symbol_table",
        default="/",
        help="APRS symbol table: / (primary) or \\ (alternate).",
    ),
    cfg.FloatOpt(
        "beacon_latitude",
        default=None,
        help="Beacon latitude in decimal degrees. Defaults to [DEFAULT] latitude.",
    ),
    cfg.FloatOpt(
        "beacon_longitude",
        default=None,
        help="Beacon longitude in decimal degrees. Defaults to [DEFAULT] longitude.",
    ),
    cfg.IntOpt(
        "beacon_interval",
        default=1800,
        min=60,
        help="Seconds between beacons (and object reports). The first one "
        "goes out at startup.",
    ),
    cfg.BoolOpt(
        "object_enabled",
        default=False,
        help="Put a named APRS object on the map (for example CHESS). An "
        "object is a position report *about* something, sent by and owned by "
        "this station's licensed callsign; the name is only a map label. "
        "This is the proper way to show a name that isn't a callsign: never "
        "log in or transmit as one.",
    ),
    cfg.StrOpt(
        "object_name",
        default="CHESS",
        help="The object's name on the map: 1 to 9 printable characters.",
    ),
    cfg.StrOpt(
        "object_comment",
        default="APRS Chess: msg {callsign} chess help",
        help="Text shown with the object. {callsign} becomes this station's "
        "callsign, so people know who to message. At most 43 characters.",
    ),
    cfg.StrOpt(
        "object_symbol",
        default="e",
        help="APRS symbol character for the object. With table /, e is a horse.",
    ),
    cfg.StrOpt(
        "object_symbol_table",
        default="/",
        help="APRS symbol table for the object: / (primary) or \\ (alternate).",
    ),
    cfg.FloatOpt(
        "object_latitude",
        default=None,
        help="Object latitude in decimal degrees. Defaults to the beacon position.",
    ),
    cfg.FloatOpt(
        "object_longitude",
        default=None,
        help="Object longitude in decimal degrees. Defaults to the beacon position.",
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
        "e.g. KK6SPK-4. Required.",
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
