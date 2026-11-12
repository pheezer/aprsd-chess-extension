import datetime
import warnings
from unittest import mock

import aprslib
import pytest

from aprsd_chess_extension import beacon
from aprsd_chess_extension.plugin import ChessExpiryThread, ChessPlugin
from tests.conftest import CONF, SERVER_CALL

LAT, LON = 38.224491, -85.689713
OBJ_LAT = 38.227691  # about 350 m north of the beacon
WHEN = datetime.datetime(2026, 11, 12, 17, 39, tzinfo=datetime.timezone.utc)


def override(**values):
    for name, value in values.items():
        CONF.set_override(name, value, group="aprsd_chess")
    return list(values)


@pytest.fixture
def beacon_conf(configured):
    names = override(beacon_enabled=True, beacon_latitude=LAT, beacon_longitude=LON)
    yield CONF
    for name in names:
        CONF.clear_override(name, group="aprsd_chess")


@pytest.fixture
def object_conf(configured):
    names = override(object_enabled=True, object_latitude=OBJ_LAT, object_longitude=LON)
    yield CONF
    for name in names:
        CONF.clear_override(name, group="aprsd_chess")


def make_plugin():
    with mock.patch.object(ChessPlugin, "start_threads"):
        plugin = ChessPlugin()
    for thread in plugin.threads:
        thread.stop()  # nothing was sent, so stopping sends nothing either
    return plugin


def beacon_thread(plugin):
    (thread,) = [t for t in plugin.threads if isinstance(t, beacon.ChessBeaconThread)]
    return thread


def test_defaults():
    assert CONF.aprsd_chess.beacon_enabled is False
    assert CONF.aprsd_chess.object_enabled is False
    assert CONF.aprsd_chess.beacon_symbol_table == "/"
    assert CONF.aprsd_chess.beacon_symbol == "e"  # /e is "Horse, equestrian"
    assert CONF.aprsd_chess.beacon_comment == "APRS Chess"
    assert CONF.aprsd_chess.object_name == "CHESS"
    assert CONF.aprsd_chess.object_symbol == "e"
    assert CONF.aprsd_chess.beacon_interval == 1800


# -- station beacon ------------------------------------------------------


def test_beacon_packet_on_this_aprsd():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)  # no legacy kwargs on 5.x
        packet = beacon.make_beacon(SERVER_CALL, LAT, LON, "APRS Chess")
    packet.prepare()
    # Position report: lat, primary table "/", lon, symbol "e", then the comment.
    assert "3813.47N/08541.38We" in packet.payload
    assert packet.payload.endswith("eAPRS Chess")
    assert packet.raw.startswith(f"{SERVER_CALL}>") and packet.payload in packet.raw


# -- the CHESS object -----------------------------------------------------


def test_object_report_format_and_parse():
    report = beacon.make_object(
        "KK6SPK-4",
        "CHESS",
        OBJ_LAT,
        LON,
        "APRS Chess: msg KK6SPK-4 chess help",
        when=WHEN,
    )
    report.prepare()
    assert report.payload == (
        ";CHESS    *121739z3813.66N/08541.38WeAPRS Chess: msg KK6SPK-4 chess help"
    )
    # Sent by (and owned by) the licensed station, never as CHESS.
    assert report.raw.startswith("KK6SPK-4>")
    parsed = aprslib.parse(report.raw)
    assert parsed["format"] == "object"
    assert parsed["from"] == "KK6SPK-4"
    assert parsed["object_name"].strip() == "CHESS"
    assert parsed["alive"] is True
    assert (parsed["symbol_table"], parsed["symbol"]) == ("/", "e")
    assert parsed["comment"] == "APRS Chess: msg KK6SPK-4 chess help"
    assert abs(parsed["latitude"] - OBJ_LAT) < 0.0002
    assert abs(parsed["longitude"] - LON) < 0.0002


def test_killed_object_uses_underscore():
    report = beacon.make_object(
        "KK6SPK-4", "CHESS", OBJ_LAT, LON, alive=False, when=WHEN
    )
    report.prepare()
    assert report.payload.startswith(";CHESS    _121739z")
    assert aprslib.parse(report.raw)["alive"] is False


def test_short_object_names_are_padded_to_nine():
    report = beacon.make_object("KK6SPK-4", "CHESS", OBJ_LAT, LON, when=WHEN)
    report.prepare()
    assert report.payload[:11] == ";CHESS    *"
    full = beacon.make_object("KK6SPK-4", "CHESSCLUB", OBJ_LAT, LON, when=WHEN)
    full.prepare()
    assert full.payload[:11] == ";CHESSCLUB*"


@pytest.mark.parametrize(
    "name", ["", "ABCDEFGHIJ", "CH|ESS", "CH~", "CH{", "CHÉSS", "CH\tS"]
)
def test_bad_object_names_are_refused(name):
    with pytest.raises(ValueError):
        beacon.validate_object_name(name)


@pytest.mark.parametrize("name", ["CHESS", "Chess", "CHESS-1", "A", "CHESSCLUB"])
def test_good_object_names(name):
    assert beacon.validate_object_name(name) == name


def test_long_comment_is_trimmed_at_a_word():
    report = beacon.make_object(
        "KK6SPK-4", "CHESS", OBJ_LAT, LON, "word " * 20, when=WHEN
    )
    report.prepare()
    comment = aprslib.parse(report.raw)["comment"]
    assert len(comment) <= 43 and comment.startswith("word word")


def test_comment_callsign_placeholder(object_conf):
    assert beacon.object_comment() == f"APRS Chess: msg {SERVER_CALL} chess help"


# -- the thread -----------------------------------------------------------


def test_object_only_thread(object_conf):
    plugin = make_plugin()
    assert [type(t) for t in plugin.threads] == [
        ChessExpiryThread,
        beacon.ChessBeaconThread,
    ]
    thread = beacon_thread(plugin)
    assert thread.beacon_position is None
    assert thread.object_position == (OBJ_LAT, LON)
    assert thread.object_name == "CHESS"


def test_beacon_and_object_together(beacon_conf, object_conf):
    thread = beacon_thread(make_plugin())
    assert thread.beacon_position == (LAT, LON)
    assert thread.object_position == (OBJ_LAT, LON)


def test_object_position_falls_back_to_the_beacon_position(object_conf):
    names = override(
        object_latitude=None,
        object_longitude=None,
        beacon_latitude=LAT,
        beacon_longitude=LON,
    )
    try:
        assert beacon.object_settings() == (LAT, LON)
    finally:
        for name in names:
            CONF.clear_override(name, group="aprsd_chess")


def test_no_thread_unless_enabled(configured):
    plugin = make_plugin()
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_object_named_like_the_station_is_refused(object_conf):
    override(object_name=SERVER_CALL)
    try:
        plugin = make_plugin()
    finally:
        CONF.clear_override("object_name", group="aprsd_chess")
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_beacon_needs_a_position(beacon_conf):
    override(beacon_latitude=None, beacon_longitude=None)
    with (
        mock.patch.object(CONF, "latitude", None),
        mock.patch.object(CONF, "longitude", None),
    ):
        plugin = make_plugin()
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_beacon_falls_back_to_the_station_position(beacon_conf):
    override(beacon_latitude=None, beacon_longitude=None)
    CONF.set_override("latitude", "40.5")
    CONF.set_override("longitude", "-74.25")
    try:
        assert beacon.beacon_settings() == (40.5, -74.25)
    finally:
        CONF.clear_override("latitude")
        CONF.clear_override("longitude")


def test_first_loop_sends_beacon_and_object(beacon_conf, object_conf):
    thread = beacon.ChessBeaconThread((LAT, LON), (OBJ_LAT, LON), "CHESS")
    sent = []
    with mock.patch("aprsd_chess_extension.beacon.tx.send") as send:
        send.side_effect = lambda packet, direct=False: sent.append((packet, direct))
        assert thread.loop() is True
    assert [type(p).__name__ for p, _ in sent] == ["BeaconPacket", "ObjectReport"]
    for packet, _ in sent:
        assert packet.retry_count == 1
        packet.prepare()
        assert packet.from_call == SERVER_CALL
    assert sent[1][0].payload.startswith(";CHESS    *")
    # aprsd 5.x drops queued beacons, so the beacon goes direct there.
    skips = "BeaconPackets are skipped" in (beacon.packets.PacketTrack.tx.__doc__ or "")
    assert sent[0][1] is skips
    assert sent[1][1] is False
    thread._object_sent = False
    thread.stop()


def test_stop_removes_the_object_from_the_map(object_conf):
    thread = beacon.ChessBeaconThread(None, (OBJ_LAT, LON), "CHESS")
    client = mock.Mock()
    with mock.patch("aprsd_chess_extension.beacon.tx.send"):
        thread.loop()
    with mock.patch("aprsd_chess_extension.beacon._client", return_value=client):
        thread.stop()
        thread.stop()  # only once
    (call,) = client.send.call_args_list
    killed = call.args[0]
    assert killed.payload.startswith(";CHESS    _")
    assert aprslib.parse(killed.raw)["alive"] is False


def test_stop_sends_nothing_if_the_object_never_went_out(object_conf):
    thread = beacon.ChessBeaconThread(None, (OBJ_LAT, LON), "CHESS")
    client = mock.Mock()
    with mock.patch("aprsd_chess_extension.beacon._client", return_value=client):
        thread.stop()
    client.send.assert_not_called()


def test_one_failure_does_not_stop_the_other(beacon_conf, object_conf):
    thread = beacon.ChessBeaconThread((LAT, LON), (OBJ_LAT, LON), "CHESS")
    sent = []

    def flaky(packet, direct=False):
        if type(packet).__name__ == "BeaconPacket":
            raise RuntimeError("no radio")
        sent.append(packet)

    with mock.patch("aprsd_chess_extension.beacon.tx.send", side_effect=flaky):
        assert thread.loop() is True
    assert [type(p).__name__ for p in sent] == ["ObjectReport"]
    thread._object_sent = False
    thread.stop()
