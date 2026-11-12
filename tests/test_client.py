import chess
import pytest

from aprsd_chess_extension import codec, protocol
from aprsd_chess_extension.client.app import create_app
from aprsd_chess_extension.client.manager import ClientGameManager
from aprsd_chess_extension.server import ChessServer, Settings
from tests.conftest import BLACK, SERVER_CALL, WHITE


class Link:
    """Wires two client managers to one ChessServer with no radio in between."""

    def __init__(self, store, clock, tmp_path):
        self.server = ChessServer(SERVER_CALL, store, Settings(), clock=clock)
        self.clock = clock
        self.drop_next = 0
        self.clients = {}
        self.add(WHITE, f"{WHITE}-6", tmp_path / "white.json")
        self.add(BLACK, f"{BLACK}-6", tmp_path / "black.json")

    def add(self, player, station, cache):
        def send(text, station=station):
            self.deliver(station, text)

        self.clients[station] = ClientGameManager(
            player, SERVER_CALL, send, cache_file=str(cache), clock=self.clock
        )
        return self.clients[station]

    def deliver(self, station, text):
        if self.drop_next:
            self.drop_next -= 1
            return
        response = self.server.handle(station, text)
        self.clients[station].on_ack(text)
        for reply in response.replies:
            self.clients[station].on_message(SERVER_CALL, reply)
        for notice in response.notices:
            client = self.clients.get(notice.to_call)
            if client:
                client.on_message(SERVER_CALL, notice.text)

    def game(self, station, code):
        return self.clients[station].snapshot()["games"][
            [g["code"] for g in self.clients[station].snapshot()["games"]].index(code)
        ]


@pytest.fixture
def link(store, clock, tmp_path):
    return Link(store, clock, tmp_path)


def white(link):
    return link.clients[f"{WHITE}-6"]


def black(link):
    return link.clients[f"{BLACK}-6"]


def start(link):
    white(link).start_game(WHITE, f"{BLACK}-6")
    (game,) = white(link).snapshot()["games"]
    return game["code"]


def test_start_game_shows_board_and_invites(link):
    code = start(link)
    game = link.game(f"{WHITE}-6", code)
    assert game["color"] == "W" and game["opponent"] == BLACK
    assert game["fen"] == chess.STARTING_FEN
    assert game["turn"] == "W"
    assert "e2" in game["legal"] and "e4" in game["legal"]["e2"]
    assert white(link).snapshot()["pending_new"] is None
    # Black's client learned about the game from the invite (and a sync).
    bgame = link.game(f"{BLACK}-6", code)
    assert bgame["color"] == "B" and bgame["opponent"] == WHITE
    assert bgame["legal"] == {}  # not Black's move


def test_play_scholars_mate_between_two_clients(link):
    code = start(link)
    moves = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]
    for i, uci in enumerate(moves):
        mover = white(link) if i % 2 == 0 else black(link)
        mover.move(code, uci)
    for station in (f"{WHITE}-6", f"{BLACK}-6"):
        game = link.game(station, code)
        assert game["result"] == "1-0", station
        assert game["termination"] == "checkmate"
        assert game["pending"] is None
        assert game["legal"] == {}
    server_board = chess.Board()
    for uci in moves:
        server_board.push_uci(uci)
    assert link.game(f"{BLACK}-6", code)["fen"] == server_board.fen()
    assert link.game(f"{WHITE}-6", code)["san_moves"][-1] == "Qxf7#"


def test_illegal_or_out_of_turn_moves_are_refused_locally(link):
    code = start(link)
    with pytest.raises(ValueError, match="Illegal"):
        white(link).move(code, "e2e5")
    with pytest.raises(ValueError, match="move"):
        black(link).move(code, "e7e5")


def test_a_gap_in_moves_triggers_a_sync(link):
    code = start(link)
    black(link).sync(code)
    white(link).move(code, "e2e4")
    # Black's notice for 1...e5 never reaches White ...
    original = link.clients.pop(f"{WHITE}-6")
    black(link).move(code, "e7e5")
    link.clients[f"{WHITE}-6"] = original
    assert link.game(f"{WHITE}-6", code)["ply"] == 1
    # ... so when the notice for move 3 shows up, White's client sees the gap
    # and asks the server for the position instead of guessing.
    sent = []
    send = original._send_text
    original._send_text = lambda text: (sent.append(text), send(text))
    original.on_message(SERVER_CALL, protocol.fmt_move_notice(code, 3, BLACK, "Nf6"))
    assert sent == [protocol.cmd_sync(code)]
    game = link.game(f"{WHITE}-6", code)
    assert game["ply"] == 2 and game["turn"] == "W"


def test_lost_move_can_be_retried_without_being_played_twice(link, clock):
    code = start(link)
    link.drop_next = 1
    white(link).move(code, "e2e4")
    game = link.game(f"{WHITE}-6", code)
    assert game["pending"]["san"] == "e4"
    clock.advance(white(link).pending_timeout + 1)
    white(link).check_timeouts()
    assert link.game(f"{WHITE}-6", code)["pending"]["failed"] is True
    white(link).retry(code)
    white(link).retry(code)  # a second retry of an already-played move
    assert link.server.store.get(code)["moves"] == ["e2e4"]
    assert link.game(f"{WHITE}-6", code)["pending"] is None
    assert link.game(f"{WHITE}-6", code)["ply"] == 1


def test_resume_by_code_on_a_fresh_client(link, tmp_path):
    code = start(link)
    for i, uci in enumerate(["d2d4", "d7d5", "c2c4", "e7e6", "b1c3"]):
        (white(link) if i % 2 == 0 else black(link)).move(code, uci)
    fresh = link.add(BLACK, f"{BLACK}-6", tmp_path / "fresh.json")
    assert fresh.snapshot()["games"] == []
    fresh.resume(code)
    game = link.game(f"{BLACK}-6", code)
    expected = chess.Board()
    for uci in ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3"]:
        expected.push_uci(uci)
    assert game["fen"] == expected.fen(en_passant="legal")
    assert game["color"] == "B" and game["turn"] == "B"
    assert game["base_ply"] == 5
    assert game["last_move"] == "b1c3"
    # Play continues normally from the restored position.
    fresh.move(code, "g8f6")
    assert link.server.store.get(code)["moves"][-1] == "g8f6"


def test_resume_unknown_code(link):
    white(link).resume("1234")
    snapshot = white(link).snapshot()
    assert snapshot["games"] == []
    assert snapshot["resume_error"] == {"code": "1234", "error": "no such game"}


def test_cache_survives_restart(link, tmp_path):
    code = start(link)
    white(link).move(code, "e2e4")
    restored = ClientGameManager(
        WHITE, SERVER_CALL, lambda text: None, cache_file=str(tmp_path / "white.json")
    )
    (game,) = restored.snapshot()["games"]
    assert game["code"] == code and game["ply"] == 1


def test_wrong_player_callsign_is_rejected(link):
    with pytest.raises(ValueError, match="plays as"):
        white(link).start_game("W1AW", f"{BLACK}-6")
    with pytest.raises(ValueError, match="not a valid"):
        white(link).start_game(WHITE, "NOTACALL")


def test_messages_from_other_stations_are_ignored(link):
    code = start(link)
    white(link).on_message("W1AW-9", f"{code} #1 W1AW: e4. Your move")
    assert link.game(f"{WHITE}-6", code)["ply"] == 0


def test_draw_resign_flow(link):
    code = start(link)
    black(link).sync(code)
    white(link).move(code, "e2e4")
    black(link).draw(code)
    assert link.game(f"{WHITE}-6", code)["draw_offer"] == "B"
    white(link).draw(code)
    for station in (f"{WHITE}-6", f"{BLACK}-6"):
        assert link.game(station, code)["result"] == protocol.DRAW


def test_state_line_with_draw_offer_and_result():
    sent = []
    manager = ClientGameManager(WHITE, SERVER_CALL, sent.append)
    board = chess.Board()
    board.push_san("e4")
    position = codec.encode(board, codec.RESULT_BLACK, codec.DRAW_OFFER_NONE)
    manager.on_message(SERVER_CALL, protocol.fmt_state("4821", BLACK, "W", position))
    (game,) = manager.snapshot()["games"]
    assert game["result"] == "0-1"
    assert game["legal"] == {}


def test_flask_api(link):
    app = create_app(white(link))
    client = app.test_client()
    assert client.get("/").status_code == 200
    page = client.get("/").get_data(as_text=True)
    assert "chessground.min.js" not in page  # loaded by app.js, not the page
    assert "js/app.js" in page
    response = client.post(
        "/api/new", json={"mycall": WHITE, "theircall": f"{BLACK}-6"}
    )
    assert response.status_code == 200
    code = response.get_json()["state"]["games"][0]["code"]
    response = client.post("/api/move", json={"code": code, "uci": "e2e4"})
    assert response.get_json()["ok"]
    response = client.post("/api/move", json={"code": code, "uci": "e2e4"})
    assert response.status_code == 400
    state = client.get("/api/state").get_json()
    assert state["games"][0]["ply"] == 1
    static = client.get("/static/vendor/chessground/chessground.min.js")
    assert static.status_code == 200 and b"Chessground" in static.data
    static.close()
