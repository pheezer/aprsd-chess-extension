from unittest import mock

from aprsd import packets

from aprsd_chess_extension import compat, protocol
from aprsd_chess_extension.plugin import ChessExpiryThread, ChessPlugin
from tests.conftest import BLACK, CONF, SERVER_CALL, WHITE, make_packet


def make_plugin():
    with mock.patch.object(ChessPlugin, "start_threads"):
        plugin = ChessPlugin()
    for thread in plugin.threads:
        thread.stop()
    return plugin


def run(plugin, text, from_call=WHITE):
    """Feed a message through aprsd's filter(); return (reply, sent notices)."""
    with mock.patch("aprsd_chess_extension.plugin.tx.send") as send:
        reply = plugin.filter(make_packet(text, from_call=from_call))
    return reply, [call.args[0] for call in send.call_args_list]


def test_plugin_enables_and_creates_expiry_thread(configured):
    plugin = make_plugin()
    assert plugin.enabled
    assert [type(t) for t in plugin.threads] == [ChessExpiryThread]


def test_full_exchange_through_the_plugin(configured):
    plugin = make_plugin()
    reply, sent = run(plugin, f"chess {WHITE} {BLACK}-7", from_call=f"{WHITE}-6")
    code = reply.split()[0]
    assert reply == protocol.fmt_new_game(code, BLACK)
    assert len(sent) == 1
    invite = sent[0]
    assert isinstance(invite, packets.MessagePacket)
    assert invite.from_call == SERVER_CALL
    assert invite.to_call == f"{BLACK}-7"
    assert invite.message_text == protocol.fmt_invite(code, WHITE)

    reply, sent = run(plugin, f"chess {code}", from_call=f"{BLACK}-7")
    assert reply.startswith(f"{code} You(B) vs {WHITE}")
    assert sent[0].to_call == f"{WHITE}-6"

    reply, sent = run(plugin, f"chess {code} e4", from_call=f"{WHITE}-6")
    assert reply == protocol.fmt_move_ok(code, 1, "e4", BLACK)
    assert sent[0].message_text == protocol.fmt_move_notice(code, 1, WHITE, "e4")


def test_multi_line_replies_are_a_list(configured):
    plugin = make_plugin()
    reply, _ = run(plugin, "chess help")
    assert reply == protocol.HELP_LINES


def test_other_messages_are_ignored(configured):
    plugin = make_plugin()
    reply, sent = run(plugin, "ping")
    assert reply is None and sent == []
    reply, _ = run(plugin, "chessboard")  # needs a word boundary
    assert reply is None


def test_messages_to_other_stations_are_ignored(configured):
    plugin = make_plugin()
    with mock.patch("aprsd_chess_extension.plugin.tx.send"):
        reply = plugin.filter(make_packet("chess help", to_call="W1AW-9"))
    assert reply is None


def test_games_persist_across_plugin_restarts(configured):
    plugin = make_plugin()
    reply, _ = run(plugin, f"chess {WHITE} {BLACK}")
    code = reply.split()[0]
    run(plugin, f"chess {code} d4")
    from aprsd_chess_extension.store import GameStore

    GameStore.reset()
    plugin = make_plugin()
    reply, _ = run(plugin, f"chess {code}", from_call=BLACK)
    assert "Last: #1 d4" in reply


def test_plugin_disables_itself_when_data_file_is_unwritable(configured, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    CONF.set_override("data_file", str(blocker / "games.json"), group="aprsd_chess")
    try:
        plugin = make_plugin()
        assert not plugin.enabled
        assert plugin.threads == []
    finally:
        CONF.set_override(
            "data_file", str(tmp_path / "games.json"), group="aprsd_chess"
        )


def test_help_lines_fit():
    for line in protocol.HELP_LINES:
        assert protocol.is_aprs_safe(line)


def test_expiry_thread_survives_errors(configured):
    plugin = make_plugin()
    thread = ChessExpiryThread(plugin.server)
    thread.period = 0
    with mock.patch.object(plugin.server, "expire", side_effect=RuntimeError("boom")):
        assert thread.loop() is True
    thread.stop()


class FakeClient:
    def __init__(self, transport, current=None):
        self._transport = transport
        self.filter = current

    def transport(self):
        return self._transport

    def get_filter(self):
        return self.filter

    def set_filter(self, value):
        self.filter = value


def test_aprsis_filter_is_added_and_kept():
    client = FakeClient("aprsis", "b/W1AW")
    assert compat.add_aprsis_filter("N0SRV-12", client) == "b/W1AW g/N0SRV-12"
    # Idempotent.
    assert compat.add_aprsis_filter("N0SRV-12", client) == "b/W1AW g/N0SRV-12"
    assert client.filter == "b/W1AW g/N0SRV-12"


def test_no_filter_for_kiss():
    client = FakeClient("tcpkiss")
    assert compat.add_aprsis_filter("N0SRV-12", client) is None
    assert client.filter is None


def test_transport_via_driver_attribute():
    class Driver:
        @staticmethod
        def transport():
            return "aprsis"

    class Client:
        driver = Driver()

    assert compat.get_transport(Client()) == "aprsis"
