import io

import chess
import pytest

from aprsd_chess_extension import protocol
from aprsd_chess_extension.client.terminal import (
    TerminalClient,
    is_move_like,
    move_list,
    parse_typed_move,
    render_board,
)
from tests.conftest import BLACK, SERVER_CALL, WHITE
from tests.test_client import Link


@pytest.fixture
def link(store, clock, tmp_path):
    return Link(store, clock, tmp_path)


def terminal(link, station, **kwargs):
    out = io.StringIO()
    kwargs.setdefault("use_unicode", False)
    kwargs.setdefault("use_color", False)
    return TerminalClient(link.clients[station], out=out, **kwargs), out


def take(out: io.StringIO) -> str:
    text = out.getvalue()
    out.seek(0)
    out.truncate()
    return text


def test_render_board_from_each_side():
    white = render_board(chess.STARTING_FEN, "W", use_unicode=False)
    lines = white.splitlines()
    assert lines[0].startswith(" 8 ") and " r  n  b  q  k  b  n  r " in lines[0]
    assert lines[-1].split() == list("abcdefgh")
    black = render_board(chess.STARTING_FEN, "B", use_unicode=False)
    assert black.splitlines()[0].startswith(" 1 ")
    assert black.splitlines()[-1].split() == list("hgfedcba")


def test_render_board_marks_the_last_move():
    board = chess.Board()
    board.push_uci("e2e4")
    text = render_board(board.fen(), "W", "e2e4", use_unicode=False)
    assert "[P]" in text  # e4
    assert "[.]" in text or "[:]" in text  # e2, now empty


def test_render_board_unicode_and_color():
    text = render_board(chess.STARTING_FEN, "W", use_unicode=True, use_color=True)
    assert "♚" in text and "\x1b[" in text


def test_move_shapes():
    for text in (
        "e4",
        "Nf3",
        "exd5",
        "O-O",
        "0-0-0",
        "e8=Q",
        "e8Q",
        "g1f3",
        "Qxf7#",
        "Nbd7",
    ):
        assert is_move_like(text), text
    for text in ("help", "new", "4821", "W1AW-7", "games", "e9"):
        assert not is_move_like(text), text


def test_parse_typed_move_messages():
    board = chess.Board("4k3/8/8/8/8/8/4K3/R6R w - - 0 1")
    with pytest.raises(ValueError, match="could be Rad1, Rhd1"):
        parse_typed_move(board, "Rd1")
    with pytest.raises(ValueError, match="isn't a legal move"):
        parse_typed_move(chess.Board(), "e5")
    assert parse_typed_move(chess.Board(), "nf3").uci() == "g1f3"


def test_move_list_numbering():
    game = {"san_moves": ["e4", "e5", "Nf3"], "base_ply": 0}
    assert move_list(game) == "1. e4 e5 2. Nf3"
    resumed = {"san_moves": ["Nc6", "Bb5"], "base_ply": 3}
    assert move_list(resumed) == "(from move 2) 2... Nc6 3. Bb5"


def test_play_a_game_from_two_terminals(link):
    white_term, white_out = terminal(link, f"{WHITE}-6")
    black_term, black_out = terminal(link, f"{BLACK}-6")

    white_term.handle(f"new {BLACK}-6")
    code = white_term.current
    assert protocol.is_game_code(code)
    text = take(white_out)
    assert f">> chess {WHITE} {BLACK}-6" in text
    assert f"<< {protocol.fmt_new_game(code, BLACK)}" in text
    assert "your move" in text

    black_term.drain_updates()
    text = take(black_out)
    assert (
        f"{WHITE} challenged you to game {code}" in text or black_term.current == code
    )
    black_term.handle(f"play {code}")
    assert "you (Black)" in take(black_out)

    moves = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7"]
    for i, san in enumerate(moves):
        mover = white_term if i % 2 == 0 else black_term
        mover.handle(san)
        white_term.drain_updates()
        black_term.drain_updates()

    white_text, black_text = take(white_out), take(black_out)
    assert "game over 1-0 (checkmate)" in white_text
    assert "game over 1-0 (checkmate)" in black_text
    assert link.server.store.get(code)["result"] == "1-0"
    white_term.handle("moves")
    assert take(white_out).strip() == "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7#"


def test_opponent_moves_redraw_the_board(link):
    white_term, white_out = terminal(link, f"{WHITE}-6")
    black_term, black_out = terminal(link, f"{BLACK}-6")
    white_term.handle(f"new {BLACK}-6")
    code = white_term.current
    black_term.drain_updates()
    black_term.handle(f"play {code}")
    white_term.handle("d4")
    take(black_out)
    black_term.drain_updates()
    text = take(black_out)
    assert f"<< {code} #1 {WHITE}: d4. Your move" in text
    assert "you (Black)" in text and "your move" in text


def test_resume_by_code_in_a_fresh_terminal(link, tmp_path):
    white_term, _ = terminal(link, f"{WHITE}-6")
    black_term, _ = terminal(link, f"{BLACK}-6")
    white_term.handle(f"new {BLACK}-6")
    code = white_term.current
    black_term.drain_updates()
    black_term.handle(f"play {code}")
    for i, san in enumerate(["d4", "d5", "c4", "e6", "Nc3"]):
        (white_term if i % 2 == 0 else black_term).handle(san)
        white_term.drain_updates()
        black_term.drain_updates()

    link.add(BLACK, f"{BLACK}-6", tmp_path / "fresh.json")
    fresh, fresh_out = terminal(link, f"{BLACK}-6")
    assert fresh.current is None
    fresh.handle(f"load {code}")
    text = take(fresh_out)
    assert f">> chess {code} sync" in text
    assert "you (Black)" in text and "your move" in text
    fresh.handle("Nf6")
    assert link.server.store.get(code)["moves"][-1] == "g8f6"
    fresh.handle("moves")
    assert take(fresh_out).strip().endswith("(from move 3) 3... Nf6")


def test_errors_are_reported_not_raised(link):
    term, out = terminal(link, f"{WHITE}-6")
    term.handle("e4")
    assert "No game selected" in take(out)
    term.handle("frobnicate")
    assert "Unknown command 'frobnicate'" in take(out)
    term.handle("new")
    assert "Usage: new" in take(out)
    term.handle("load 1234")
    assert "Game 1234: no such game" in take(out)
    term.handle(f"new {BLACK}-6")
    take(out)
    term.handle("e5")
    assert "isn't a legal move" in take(out)


def test_quit_warns_about_an_unconfirmed_move(link):
    term, out = terminal(link, f"{WHITE}-6")
    term.handle(f"new {BLACK}-6")
    link.drop_next = 1
    term.handle("e4")
    take(out)
    term.handle("quit")
    assert term.running and "hasn't confirmed" in take(out)
    term.handle("quit")
    assert not term.running


def test_traffic_can_be_hidden(link):
    term, out = terminal(link, f"{WHITE}-6", show_traffic=False)
    term.handle(f"new {BLACK}-6")
    text = take(out)
    assert ">>" not in text and "<<" not in text
    assert "your move" in text


def test_run_loop_reads_until_quit(link):
    term, out = terminal(link, f"{WHITE}-6")
    lines = iter([f"new {BLACK}-6", "e4", "games", "quit"])
    term.run(read_line=lambda prompt: next(lines))
    text = out.getvalue()
    assert "Type help for commands" in text
    assert f"vs {BLACK}" in text and "move 1" in text
    code = term.current
    assert link.server.store.get(code)["moves"] == ["e2e4"]


def test_run_loop_stops_at_end_of_input(link):
    term, out = terminal(link, f"{WHITE}-6")
    lines = iter(["help"])

    def read(prompt):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    term.run(read_line=read)
    assert "new <theircall-SSID>" in out.getvalue()


def test_messages_from_other_stations_are_not_moves(link):
    term, out = terminal(link, f"{WHITE}-6")
    term.handle(f"new {BLACK}-6")
    code = term.current
    take(out)
    link.clients[f"{WHITE}-6"].on_message("W1AW-9", f"{code} #1 W1AW: e5. Your move")
    term.drain_updates()
    assert link.server.store.get(code)["moves"] == []
    assert "#1" not in take(out)


def test_server_callsign_shown(link):
    term, out = terminal(link, f"{WHITE}-6")
    term.run(read_line=lambda prompt: "quit")
    assert SERVER_CALL in out.getvalue()
