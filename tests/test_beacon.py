import warnings
from unittest import mock

import pytest

from aprsd_chess_extension import beacon
from aprsd_chess_extension.plugin import ChessExpiryThread, ChessPlugin
from tests.conftest import CONF, SERVER_CALL

LAT, LON = 38.224491, -85.689713


@pytest.fixture
def beacon_conf(configured):
    overrides = {
        "beacon_enabled": True,
        "beacon_latitude": LAT,
        "beacon_longitude": LON,
    }
    for name, value in overrides.items():
        CONF.set_override(name, value, group="aprsd_chess")
    yield CONF
    for name in overrides:
        CONF.clear_override(name, group="aprsd_chess")


def make_plugin():
    with mock.patch.object(ChessPlugin, "start_threads"):
        plugin = ChessPlugin()
    for thread in plugin.threads:
        thread.stop()
    return plugin


def test_defaults_are_a_horse_saying_aprs_chess():
    assert CONF.aprsd_chess.beacon_enabled is False
    assert CONF.aprsd_chess.beacon_symbol_table == "/"
    assert CONF.aprsd_chess.beacon_symbol == "e"  # /e is "Horse, equestrian"
    assert CONF.aprsd_chess.beacon_comment == "APRS Chess"
    assert CONF.aprsd_chess.beacon_interval == 1800


def test_beacon_packet_on_this_aprsd():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)  # no legacy kwargs on 5.x
        packet = beacon.make_beacon(SERVER_CALL, LAT, LON, "APRS Chess")
    packet.prepare()
    # Position report: lat, primary table "/", lon, symbol "e", then the comment.
    assert "3813.47N/08541.38We" in packet.payload
    assert packet.payload.endswith("eAPRS Chess")
    assert packet.raw.startswith(f"{SERVER_CALL}>") and packet.payload in packet.raw


def test_plugin_starts_the_beacon_thread(beacon_conf):
    plugin = make_plugin()
    kinds = [type(t) for t in plugin.threads]
    assert kinds == [ChessExpiryThread, beacon.ChessBeaconThread]
    thread = plugin.threads[1]
    assert (thread.latitude, thread.longitude) == (LAT, LON)
    assert thread.period == 1800


def test_no_beacon_unless_enabled(configured):
    plugin = make_plugin()
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_beacon_needs_a_position(beacon_conf):
    CONF.set_override("beacon_latitude", None, group="aprsd_chess")
    CONF.set_override("beacon_longitude", None, group="aprsd_chess")
    with (
        mock.patch.object(CONF, "latitude", None),
        mock.patch.object(CONF, "longitude", None),
    ):
        plugin = make_plugin()
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_beacon_falls_back_to_the_station_position(beacon_conf):
    CONF.set_override("beacon_latitude", None, group="aprsd_chess")
    CONF.set_override("beacon_longitude", None, group="aprsd_chess")
    CONF.set_override("latitude", "40.5")
    CONF.set_override("longitude", "-74.25")
    try:
        assert beacon.beacon_settings() == (40.5, -74.25)
    finally:
        CONF.clear_override("latitude")
        CONF.clear_override("longitude")


def test_thread_sends_first_beacon_immediately(beacon_conf):
    thread = beacon.ChessBeaconThread(LAT, LON)
    sent = []
    with mock.patch("aprsd_chess_extension.beacon.tx.send", side_effect=sent.append):
        assert thread.loop() is True
    thread.stop()
    (packet,) = sent
    packet.prepare()  # the real tx.send (mocked here) builds the payload
    assert packet.retry_count == 1
    assert packet.from_call == SERVER_CALL
    assert packet.payload.endswith("eAPRS Chess")


def test_failed_beacon_does_not_stop_the_thread(beacon_conf):
    thread = beacon.ChessBeaconThread(LAT, LON)
    with mock.patch(
        "aprsd_chess_extension.beacon.tx.send", side_effect=RuntimeError("no radio")
    ):
        assert thread.loop() is True
    thread.stop()
