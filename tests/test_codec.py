import random
import re

import chess
import pytest

from aprsd_chess_extension import codec

# aprsd masks these substrings in outgoing messages; a code must never contain one.
MASKED = re.compile("fuck|shit|cunt|piss|cock|bitch", re.IGNORECASE)


def assert_same_position(decoded: chess.Board, original: chess.Board):
    assert decoded.board_fen() == original.board_fen()
    assert decoded.turn == original.turn
    assert decoded.clean_castling_rights() == original.clean_castling_rights()
    # The code only records en passant when the capture is actually legal.
    expected_ep = original.ep_square if original.has_legal_en_passant() else None
    assert decoded.ep_square == expected_ep
    assert decoded.halfmove_clock == original.halfmove_clock
    assert decoded.fullmove_number == original.fullmove_number


def assert_code_is_aprs_safe(code: str):
    assert len(code) <= codec.MAX_CODE_LEN
    assert code.isascii() and code.isalnum()
    assert not any(c in code for c in "|~{ ")
    assert not MASKED.search(code)


@pytest.mark.parametrize(
    "fen",
    [
        chess.STARTING_FEN,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
        "r2q1rk1/pp2bppp/2n1bn2/2pp4/3P4/2NBPN2/PP3PPP/R1BQ1RK1 b - - 4 10",
        "r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1",
        "8/8/4k3/8/8/3K4/8/8 w - - 37 81",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",  # legal en passant e5xd6
        "4k3/8/8/8/3Pp3/8/8/4K3 b - d3 0 2",  # legal en passant e4xd3
    ],
)
def test_round_trip_known_positions(fen):
    board = chess.Board(fen)
    code = codec.encode(board)
    assert_code_is_aprs_safe(code)
    position = codec.decode(code)
    assert_same_position(position.board, board)
    assert position.result == codec.RESULT_ONGOING
    assert position.draw_offer == codec.DRAW_OFFER_NONE
    assert position.last_move is None


def test_full_board_fits_in_46_characters():
    # 32 pieces is the worst case: 6 + 64 + 128 + 32 = 230 bits -> 46 chars.
    code = codec.encode(chess.Board(), last_move=chess.Move.from_uci("h7h8"))
    assert len(code) == 46
    worst = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 100 4095")
    assert len(codec.encode(worst, codec.RESULT_DRAW, codec.DRAW_OFFER_BLACK)) <= 46


def test_status_fields_round_trip():
    board = chess.Board()
    board.push_san("e4")
    move = chess.Move.from_uci("e2e4")
    for result in (0, 1, 2, 3):
        for offer in (0, 1, 2):
            position = codec.decode(codec.encode(board, result, offer, move))
            assert position.result == result
            assert position.draw_offer == offer
            assert position.last_move == move
            assert position.ply == 1


def test_illegal_en_passant_target_is_dropped():
    # After 1.e4 the FEN has an ep target, but no black pawn can capture.
    board = chess.Board()
    board.push_san("e4")
    assert board.ep_square is not None
    decoded = codec.decode(codec.encode(board)).board
    assert decoded.ep_square is None
    assert decoded.fen() == board.fen(en_passant="legal")


def test_decode_is_case_insensitive():
    code = codec.encode(chess.Board())
    assert codec.decode(code.lower()).board.fen() == chess.STARTING_FEN


@pytest.mark.parametrize(
    "bad",
    ["", "U" * 10, "Z" * 47, "0" * 20, "*" * 20],
)
def test_decode_rejects_garbage(bad):
    with pytest.raises(codec.CodecError):
        codec.decode(bad)


def test_decode_rejects_truncated_code():
    code = codec.encode(chess.Board())
    with pytest.raises(codec.CodecError):
        codec.decode(code[:-4])


def test_random_games_round_trip_at_every_ply():
    rng = random.Random(1234)
    longest = 0
    for _game in range(300):
        board = chess.Board()
        for _ply in range(rng.randint(10, 160)):
            moves = list(board.legal_moves)
            if not moves:
                break
            move = rng.choice(moves)
            board.push(move)
            code = codec.encode(board, last_move=move)
            assert_code_is_aprs_safe(code)
            longest = max(longest, len(code))
            position = codec.decode(code)
            assert_same_position(position.board, board)
            # Only from/to are kept (for highlighting); the board shows promotions.
            assert position.last_move == chess.Move(move.from_square, move.to_square)
            assert position.ply == len(board.move_stack)
            if board.is_game_over():
                break
    assert longest <= 46
