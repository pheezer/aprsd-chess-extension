import chess
import pytest

from aprsd_chess_extension import codec, game


@pytest.mark.parametrize(
    "text,uci",
    [
        ("e4", "e2e4"),
        ("e2e4", "e2e4"),
        ("E2E4", "e2e4"),
        ("Nf3", "g1f3"),
        ("nf3", "g1f3"),
        ("NF3", "g1f3"),
        ("Nf3!?", "g1f3"),
        ("g1f3", "g1f3"),
    ],
)
def test_parse_moves_from_the_start(text, uci):
    assert game.parse_move(chess.Board(), text).uci() == uci


def test_castling_spellings():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    for text in ("O-O", "0-0", "o-o", "e1g1", "e1h1"):
        assert game.parse_move(board, text).uci() == "e1g1", text
    for text in ("O-O-O", "0-0-0", "e1c1", "e1a1"):
        assert game.parse_move(board, text).uci() == "e1c1", text


def test_promotion_spellings():
    board = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
    for text in ("e8=Q", "e8Q", "e8q", "e7e8q", "E7E8Q", "e8=Q+"):
        assert game.parse_move(board, text).uci() == "e7e8q", text
    assert game.parse_move(board, "e8=N").uci() == "e7e8n"


def test_pawn_and_bishop_captures_on_the_b_file():
    # A b-pawn and a bishop can both capture on c3.
    board = chess.Board("4k3/8/8/8/8/2p5/1P1B4/4K3 w - - 0 1")
    assert game.parse_move(board, "bxc3").uci() == "b2c3"
    assert game.parse_move(board, "Bxc3").uci() == "d2c3"
    # All-caps can't tell them apart, so the player gets both choices.
    with pytest.raises(game.MoveError) as excinfo:
        game.parse_move(board, "BXC3")
    assert excinfo.value.candidates == ["b2c3", "d2c3"]


def test_ambiguous_and_illegal():
    board = chess.Board("4k3/8/8/8/8/8/4K3/R6R w - - 0 1")
    for text in ("Rd1", "rd1"):
        with pytest.raises(game.MoveError) as excinfo:
            game.parse_move(board, text)
        assert excinfo.value.candidates == ["a1d1", "h1d1"]
    assert game.parse_move(board, "Rad1").uci() == "a1d1"
    assert game.parse_move(board, "rhd1").uci() == "h1d1"
    promote = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
    with pytest.raises(game.MoveError) as excinfo:
        game.parse_move(promote, "e8")
    assert excinfo.value.candidates == ["e7e8b", "e7e8n", "e7e8q", "e7e8r"]
    with pytest.raises(game.MoveError):
        game.parse_move(chess.Board(), "e5")
    with pytest.raises(game.MoveError):
        game.parse_move(chess.Board(), "")


def test_automatic_result_and_claims():
    mate = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert game.automatic_result(mate) == (codec.RESULT_BLACK, "checkmate")
    stalemate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert game.automatic_result(stalemate) == (codec.RESULT_DRAW, "stalemate")
    assert game.automatic_result(chess.Board()) is None
    fifty = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 100 80")
    assert game.claimable_draw(fifty) == "50-move rule"


def test_board_from_moves():
    board = game.board_from_moves(["e2e4", "e7e5", "g1f3"])
    assert (
        board.fen() == "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    )
