import pytest
from aprsd import conf  # noqa: F401  (registers core aprsd options)
from aprsd.packets import core
from oslo_config import cfg

from aprsd_chess_extension import conf as chess_conf  # noqa: F401
from aprsd_chess_extension.store import GameStore

CONF = cfg.CONF

SERVER_CALL = "N0SRV-12"
WHITE = "KK6SPK"
BLACK = "AB1CDE"


class Clock:
    """A settable clock for expiry tests."""

    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path):
    GameStore.reset()
    s = GameStore()
    s.load(str(tmp_path / "games.json"))
    yield s
    GameStore.reset()


@pytest.fixture
def configured(tmp_path):
    """Point CONF at a temp save location; undo every override afterwards."""
    overrides = [
        ("callsign", SERVER_CALL, None),
        ("enable_save", False, None),
        ("save_location", str(tmp_path), None),
        ("data_file", str(tmp_path / "games.json"), "aprsd_chess"),
        ("aprsis_filter", False, "aprsd_chess"),
    ]
    for name, value, group in overrides:
        CONF.set_override(name, value, group=group)
    GameStore.reset()
    yield CONF
    for name, _value, group in overrides:
        CONF.clear_override(name, group=group)
    GameStore.reset()


def make_packet(message, from_call=WHITE, to_call=SERVER_CALL, msg_number=1):
    """A prepared APRS MessagePacket, as aprsd would hand it to a plugin."""
    packet = core.factory(
        {
            "from": from_call,
            "addresse": to_call,
            "to": to_call,
            "format": core.PACKET_TYPE_MESSAGE,
            "message_text": message,
            "msgNo": str(msg_number),
            "raw": "",
        }
    )
    packet.prepare()
    return packet
