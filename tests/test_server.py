import json

import chess
import pytest

from aprsd_chess_extension import codec, protocol
from aprsd_chess_extension.server import ChessServer, Settings
from aprsd_chess_extension.store import GameStore
from tests.conftest import BLACK, SERVER_CALL, WHITE

DAY = 86400


@pytest.fixture
def server(store, clock):
    return ChessServer(SERVER_CALL, store, Settings(), clock=clock)


def start_game(server, white=WHITE, black=BLACK, black_ssid="7"):
    response = server.handle(f"{white}-6", f"chess {white} {black}-{black_ssid}")
    code = response.replies[0].split()[0]
    assert protocol.is_game_code(code), response.replies
    return code, response


def all_texts(response):
    return response.replies + [n.text for n in response.notices]


def test_new_game_replies_and_invites(server):
    code, response = start_game(server)
    assert response.replies == [protocol.fmt_new_game(code, BLACK)]
    assert len(response.notices) == 1
    assert response.notices[0].to_call == f"{BLACK}-7"
    assert response.notices[0].text == protocol.fmt_invite(code, WHITE)
    game = server.store.get(code)
    assert game["white"] == WHITE and game["black"] == BLACK
    assert game["joined"] is False


def test_white_can_move_before_black_joins(server):
    code, _ = start_game(server)
    response = server.handle(f"{WHITE}-6", f"chess {code} e4")
    assert response.replies == [protocol.fmt_move_ok(code, 1, "e4", BLACK)]
    # The invitee hasn't joined, so they get no move notice yet.
    assert response.notices == []


def test_black_joins_with_status_and_sees_whites_move(server):
    code, _ = start_game(server)
    server.handle(f"{WHITE}-6", f"chess {code} e4")
    response = server.handle(f"{BLACK}-7", f"chess {code}")
    assert response.replies[0].startswith(f"{code} You(B) vs {WHITE}. Last: #1 e4.")
    assert response.replies[0].endswith("Your move")
    assert [n.text for n in response.notices] == [protocol.fmt_joined(code, BLACK)]
    assert response.notices[0].to_call == f"{WHITE}-6"
    assert server.store.get(code)["joined"] is True


def test_moves_notify_the_opponent_at_their_last_ssid(server):
    code, _ = start_game(server)
    server.handle(f"{BLACK}-7", f"chess {code}")  # join
    response = server.handle(f"{WHITE}-9", f"chess {code} Nf3")
    assert response.replies == [protocol.fmt_move_ok(code, 1, "Nf3", BLACK)]
    assert response.notices[0].to_call == f"{BLACK}-7"
    assert response.notices[0].text == protocol.fmt_move_notice(code, 1, WHITE, "Nf3")
    # Black now moves from a different SSID; White's notices follow White's
    # most recent SSID (-9).
    response = server.handle(f"{BLACK}-2", f"chess {code} d5")
    assert response.notices[0].to_call == f"{WHITE}-9"
    assert server.store.get(code)["addr"][BLACK] == f"{BLACK}-2"


def test_wrong_turn_and_illegal_moves_are_rejected(server):
    code, _ = start_game(server)
    response = server.handle(BLACK, f"chess {code} e5")
    assert response.replies == [protocol.fmt_error(f"{WHITE} to move", code)]
    response = server.handle(WHITE, f"chess {code} e5")
    assert "Illegal or unknown move e5" in response.replies[0]
    assert server.store.get(code)["moves"] == []


def test_move_without_code_uses_the_only_active_game(server):
    code, _ = start_game(server)
    response = server.handle(WHITE, "chess e4")
    assert response.replies == [protocol.fmt_move_ok(code, 1, "e4", BLACK)]


def test_move_without_code_needs_code_when_several_games(server):
    start_game(server)
    start_game(server, black="W1AW")
    response = server.handle(WHITE, "chess e4")
    assert "include the game code" in response.replies[0]


def test_promotion_without_equals_sign(server):
    code, _ = start_game(server)
    game = server.store.get(code)
    # Play to a position where White can promote: use a known line.
    line = ["e4", "d5", "exd5", "c6", "dxc6", "Nf6", "cxb7", "Nbd7"]
    for i, san in enumerate(line):
        mover = WHITE if i % 2 == 0 else BLACK
        response = server.handle(mover, f"chess {code} {san}")
        assert "ok" in response.replies[0], response.replies
    response = server.handle(WHITE, f"chess {code} bxa8Q")
    assert response.replies[0] == protocol.fmt_move_ok(code, 9, "bxa8=Q", BLACK)
    assert game["moves"][-1] == "b7a8q"


def test_scholars_mate_ends_the_game(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    line = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"]
    for i, san in enumerate(line):
        server.handle(WHITE if i % 2 == 0 else BLACK, f"chess {code} {san}")
    response = server.handle(WHITE, f"chess {code} Qxf7")
    expected = f"{code} #7 Qxf7# Checkmate. {WHITE} wins 1-0"
    assert response.replies == [expected]
    assert [n.text for n in response.notices] == [expected]
    game = server.store.get(code)
    assert game["result"] == "1-0" and game["termination"] == "checkmate"
    # Further moves just report the final status.
    response = server.handle(BLACK, f"chess {code} Ke7")
    assert "Game over 1-0" in response.replies[0]


def test_sync_reply_restores_the_exact_position(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    line = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]
    board = chess.Board()
    for i, san in enumerate(line):
        server.handle(WHITE if i % 2 == 0 else BLACK, f"chess {code} {san}")
        board.push_san(san)
    response = server.handle(BLACK, f"chess {code} sync")
    reply = protocol.parse_reply(response.replies[0])
    assert reply.kind == "state"
    assert reply.fields["opponent"] == WHITE and reply.fields["color"] == "B"
    assert len(response.replies[0]) <= protocol.MAX_MSG_LEN
    position = codec.decode(reply.fields["position"])
    assert position.board.fen() == board.fen(en_passant="legal")
    assert position.ply == 10
    assert position.last_move == chess.Move.from_uci("a7a6")


def test_ply_guard_makes_retries_safe(server):
    code, _ = start_game(server)
    first = server.handle(WHITE, f"chess {code} #1 e2e4")
    assert first.replies == [protocol.fmt_move_ok(code, 1, "e4", BLACK)]
    # The same message arriving again (lost ack, retry) is not played twice.
    again = server.handle(WHITE, f"chess {code} #1 e2e4")
    assert again.replies == first.replies
    assert server.store.get(code)["moves"] == ["e2e4"]
    # A client that's behind gets the state line instead.
    stale = server.handle(BLACK, f"chess {code} #1 e7e5")
    assert protocol.parse_reply(stale.replies[0]).kind == "state"
    assert server.store.get(code)["moves"] == ["e2e4"]


def test_non_player_cannot_touch_a_game(server):
    code, _ = start_game(server)
    response = server.handle("W1AW", f"chess {code} e4")
    assert response.replies == [protocol.fmt_error("you are not in this game", code)]


def test_unknown_code(server):
    response = server.handle(WHITE, "chess 4242")
    assert response.replies == [protocol.fmt_error("no such game", "4242")]


def test_cannot_start_as_someone_else_or_play_yourself(server):
    response = server.handle(WHITE, f"chess W1AW {BLACK}")
    assert response.replies == [protocol.fmt_error(f"you are {WHITE}, not W1AW")]
    response = server.handle(WHITE, f"chess {WHITE} {WHITE}-5")
    assert response.replies == [protocol.fmt_error("you can't play yourself")]


def test_rechallenge_returns_the_existing_game(server):
    code, _ = start_game(server)
    response = server.handle(f"{WHITE}-6", f"chess {WHITE} {BLACK}-7")
    assert response.replies[0].startswith(f"{code} You(W) vs {BLACK}")
    # The invitee challenging back lands in the same game and joins it.
    response = server.handle(f"{BLACK}-7", f"chess {BLACK} {WHITE}-6")
    assert response.replies[0].startswith(f"{code} You(B) vs {WHITE}")
    assert server.store.get(code)["joined"] is True
    assert len(server.store) == 1


def test_game_limit(server):
    for i in range(5):
        start_game(server, black=f"W{i}AW")
    response = server.handle(WHITE, f"chess {WHITE} K9XYZ")
    assert response.replies == [protocol.fmt_error("limit is 5 games")]


def test_invite_rate_limit(store, clock):
    server = ChessServer(
        SERVER_CALL, store, Settings(max_invites_per_day=2), clock=clock
    )
    start_game(server, black="W1AW")
    code, _ = start_game(server, black="W2AW")
    server.handle(WHITE, f"chess {code} resign")  # cancels; frees a game slot
    response = server.handle(WHITE, f"chess {WHITE} W3AW")
    assert response.replies == [protocol.fmt_error("too many new games today")]
    clock.advance(DAY + 1)
    _, response = start_game(server, black="W3AW")
    assert response.replies[0].endswith("You are White, your move")


def test_resign_before_join_cancels_the_game(server):
    code, _ = start_game(server)
    response = server.handle(WHITE, f"chess {code} resign")
    assert response.replies == [protocol.fmt_cancelled(code, WHITE)]
    assert response.notices[0].to_call == f"{BLACK}-7"
    assert server.store.get(code) is None


def test_resign_after_join(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    response = server.handle(BLACK, f"chess {code} resign")
    expected = protocol.fmt_resigned(code, BLACK, WHITE, "1-0")
    assert response.replies == [expected]
    assert response.notices[0].text == expected
    assert server.store.get(code)["result"] == "1-0"


def test_draw_offer_and_accept(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    response = server.handle(WHITE, f"chess {code} draw")
    assert response.replies == [protocol.fmt_draw_offered(code, BLACK)]
    assert response.notices[0].text == protocol.fmt_draw_offer(code, WHITE)
    # The offer shows up in the state code.
    state = protocol.parse_reply(server.handle(BLACK, f"chess {code} sync").replies[0])
    assert codec.decode(state.fields["position"]).draw_offer == codec.DRAW_OFFER_WHITE
    response = server.handle(BLACK, f"chess {code} draw")
    assert response.replies == [protocol.fmt_draw_agreed(code)]
    assert server.store.get(code)["result"] == protocol.DRAW


def test_a_move_withdraws_a_draw_offer(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    server.handle(WHITE, f"chess {code} e4")
    server.handle(WHITE, f"chess {code} draw")
    assert server.store.get(code)["draw_offer"] == WHITE
    # Black declines by playing on.
    server.handle(BLACK, f"chess {code} e5")
    assert server.store.get(code)["draw_offer"] is None


def test_threefold_repetition_can_be_claimed(server):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    line = ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8"]
    for i, san in enumerate(line):
        server.handle(WHITE if i % 2 == 0 else BLACK, f"chess {code} {san}")
    response = server.handle(WHITE, f"chess {code} draw")
    assert response.replies == [protocol.fmt_draw_claimed(code, "threefold repetition")]


def test_list(server):
    code1, _ = start_game(server)
    code2, _ = start_game(server, black="W1AW")
    server.handle(WHITE, f"chess {code1} e4")
    response = server.handle(WHITE, "chess list")
    assert response.replies == [
        protocol.fmt_list_entry(code1, BLACK, "W", 1, False),
        protocol.fmt_list_entry(code2, "W1AW", "W", 0, True),
    ]
    assert server.handle("N0NE", "chess list").replies == [protocol.LIST_EMPTY]


def test_games_expire_after_ten_idle_days(server, clock):
    code, _ = start_game(server)
    clock.advance(9 * DAY)
    assert server.handle(WHITE, f"chess {code}").replies[0].startswith(code)
    clock.advance(9 * DAY)  # activity above reset the idle timer
    assert server.expire() == []
    clock.advance(DAY + 1)
    assert server.expire() == [code]
    assert server.handle(WHITE, f"chess {code}").replies == [
        protocol.fmt_error("no such game", code)
    ]


def test_expired_game_is_gone_even_before_the_sweep(server, clock):
    code, _ = start_game(server)
    clock.advance(10 * DAY + 1)
    response = server.handle(WHITE, f"chess {code} e4")
    assert response.replies == [protocol.fmt_error("no such game", code)]
    assert server.store.get(code) is None


def test_finished_games_also_expire(server, clock):
    code, _ = start_game(server)
    server.handle(BLACK, f"chess {code}")
    server.handle(WHITE, f"chess {code} resign")
    clock.advance(10 * DAY + 1)
    assert server.expire() == [code]


def test_games_survive_a_restart(server, store, tmp_path):
    code, _ = start_game(server)
    server.handle(WHITE, f"chess {code} e4")
    path = store.path
    GameStore.reset()
    reloaded = GameStore()
    reloaded.load(path)
    assert reloaded.get(code)["moves"] == ["e2e4"]
    with open(path) as fp:
        assert json.load(fp)["version"] == 1


def test_corrupt_store_is_moved_aside(tmp_path):
    path = tmp_path / "games.json"
    path.write_text("{not json")
    GameStore.reset()
    store = GameStore()
    store.load(str(path))
    assert len(store) == 0
    assert not path.exists()
    assert list(tmp_path.glob("games.json.corrupt-*"))
    GameStore.reset()


def test_help_and_unknown(server):
    assert server.handle(WHITE, "chess").replies == protocol.HELP_LINES
    assert server.handle(WHITE, "chess help").replies == protocol.HELP_LINES
    response = server.handle(WHITE, "chess do a barrel roll")
    assert response.replies == [protocol.fmt_error("unknown command. Send: chess help")]


def test_callsign_typed_as_a_move_gets_a_hint(server):
    start_game(server)
    response = server.handle(WHITE, "chess W1AW-7")
    assert "to start a game" in response.replies[0]


def test_every_server_message_is_aprs_safe(server):
    """Drive a game with worst-case callsigns and check every message sent."""
    white, black = "KK6SPK", "WB2XYZ"  # 6-character base calls are the longest
    sent = []

    def run(sender, text):
        response = server.handle(sender, text)
        sent.extend(all_texts(response))
        return response

    code = run(f"{white}-15", f"chess {white} {black}-15").replies[0].split()[0]
    run(f"{black}-15", f"chess {code}")
    for i, san in enumerate(["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"]):
        run(f"{white}-15" if i % 2 == 0 else f"{black}-15", f"chess {code} {san}")
    run(f"{black}-15", f"chess {code} draw")
    run(f"{black}-15", f"chess {code} sync")
    run(f"{black}-15", f"chess {code} board")
    run(f"{black}-15", "chess list")
    run(f"{black}-15", f"chess {code} Nf6xe4!!")
    run(f"{white}-15", f"chess {code} Qxf7")
    run(f"{black}-15", f"chess {code}")
    run(f"{black}-15", "chess help")
    for text in sent:
        assert protocol.is_aprs_safe(text), text
