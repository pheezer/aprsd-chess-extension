"""Compact chess position code that fits inside a single APRS message.

An APRS message carries at most 67 characters of text, which is too small for
FEN (56 to ~90 characters) or a move list (about 5 characters per ply).  This
module packs everything needed to put the pieces back on the board -- and to
keep playing -- into at most 46 characters.

Bit layout (most significant bit first)::

    version         2   format version, currently 1
    result          2   0 ongoing, 1 white won, 2 black won, 3 draw
    draw offer      2   0 none, 1 offered by white, 2 offered by black
    occupancy      64   one bit per square, a1 = first bit ... h8 = last bit
    pieces       4*N   one 4-bit code per occupied square, in square order
    fullmove       12   fullmove number (1..4095)
    halfmove        8   halfmove clock (0..255)
    last move      12   from-square (6 bits) and to-square (6 bits); 0/0 = none

Piece codes 0-5 are white P N B R Q K and 6-11 are black p n b r q k.  Three
more codes fold the remaining FEN state into the board itself:

    12  a rook that still has castling rights (its color follows from its square)
    13  a pawn that can be captured en passant right now (color follows from rank)
    14  the black king, when it is black's turn to move

With at most 32 pieces this is at most 6 + 64 + 128 + 32 = 230 bits, which is
46 characters of Crockford base32 (5 bits per character).  The alphabet has
no I, L, O or U, so a code can never contain any of the words aprsd masks out
of outgoing messages, nor the characters APRS forbids in message text.
"""

from __future__ import annotations

import dataclasses
import re

import chess

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_CODE_LEN = 46

VERSION = 1

RESULT_ONGOING = 0
RESULT_WHITE = 1
RESULT_BLACK = 2
RESULT_DRAW = 3

DRAW_OFFER_NONE = 0
DRAW_OFFER_WHITE = 1
DRAW_OFFER_BLACK = 2

_CASTLING_ROOK = 12
_EP_PAWN = 13
_BLACK_KING_TO_MOVE = 14

_PIECE_SYMBOLS = "PNBRQKpnbrqk"
_CASTLING_SQUARES = (chess.A1, chess.H1, chess.A8, chess.H8)

_FULLMOVE_BITS = 12
_HALFMOVE_BITS = 8

# Crockford decoding is forgiving about characters people commonly confuse.
_DECODE_MAP = {c: i for i, c in enumerate(ALPHABET)}
_DECODE_MAP.update({"O": 0, "I": 1, "L": 1})

CODE_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{17,46}$", re.IGNORECASE)


class CodecError(ValueError):
    """Raised when a position code cannot be decoded."""


@dataclasses.dataclass
class Position:
    """A decoded position code."""

    board: chess.Board
    result: int = RESULT_ONGOING
    draw_offer: int = DRAW_OFFER_NONE
    last_move: chess.Move | None = None

    @property
    def ply(self) -> int:
        """Number of half-moves played since the standard start position."""
        return ply_of(self.board)


def ply_of(board: chess.Board) -> int:
    """Half-moves played to reach `board` from the standard start position."""
    return 2 * (board.fullmove_number - 1) + (board.turn == chess.BLACK)


class _BitWriter:
    def __init__(self):
        self.value = 0
        self.length = 0

    def write(self, value: int, bits: int) -> None:
        if value < 0 or value >= 1 << bits:
            raise CodecError(f"value {value} does not fit in {bits} bits")
        self.value = (self.value << bits) | value
        self.length += bits

    def to_code(self) -> str:
        pad = -self.length % 5
        value = self.value << pad
        chars = (self.length + pad) // 5
        return "".join(
            ALPHABET[(value >> (5 * (chars - 1 - i))) & 31] for i in range(chars)
        )


class _BitReader:
    def __init__(self, code: str):
        value = 0
        for ch in code.upper():
            if ch not in _DECODE_MAP:
                raise CodecError(f"invalid character {ch!r} in position code")
            value = (value << 5) | _DECODE_MAP[ch]
        self.value = value
        self.remaining = 5 * len(code)

    def read(self, bits: int) -> int:
        if bits > self.remaining:
            raise CodecError("position code is truncated")
        self.remaining -= bits
        return (self.value >> self.remaining) & ((1 << bits) - 1)


def _ep_pawn_square(board: chess.Board) -> int | None:
    """Square of the pawn that can legally be captured en passant, if any."""
    if board.ep_square is None or not board.has_legal_en_passant():
        return None
    # The capturable pawn sits one rank "behind" the ep target square.
    return board.ep_square + (-8 if board.turn == chess.WHITE else 8)


def encode(
    board: chess.Board,
    result: int = RESULT_ONGOING,
    draw_offer: int = DRAW_OFFER_NONE,
    last_move: chess.Move | None = None,
) -> str:
    """Encode a standard-chess board (plus game status) as a position code."""
    castling_rooks = set(chess.SquareSet(board.clean_castling_rights()))
    ep_pawn = _ep_pawn_square(board)

    writer = _BitWriter()
    writer.write(VERSION, 2)
    writer.write(result, 2)
    writer.write(draw_offer, 2)

    occupied = int(board.occupied)
    for square in chess.SQUARES:
        writer.write((occupied >> square) & 1, 1)

    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        if square in castling_rooks:
            code = _CASTLING_ROOK
        elif square == ep_pawn:
            code = _EP_PAWN
        elif (
            piece == chess.Piece(chess.KING, chess.BLACK) and board.turn == chess.BLACK
        ):
            code = _BLACK_KING_TO_MOVE
        else:
            code = _PIECE_SYMBOLS.index(piece.symbol())
        writer.write(code, 4)

    writer.write(min(board.fullmove_number, (1 << _FULLMOVE_BITS) - 1), _FULLMOVE_BITS)
    writer.write(min(board.halfmove_clock, (1 << _HALFMOVE_BITS) - 1), _HALFMOVE_BITS)
    if last_move is None:
        writer.write(0, 12)
    else:
        writer.write(last_move.from_square, 6)
        writer.write(last_move.to_square, 6)
    return writer.to_code()


def decode(code: str) -> Position:
    """Decode a position code produced by :func:`encode`.

    Raises CodecError for malformed codes and for codes that decode to an
    impossible position.
    """
    code = (code or "").strip()
    if not code or len(code) > MAX_CODE_LEN:
        raise CodecError("position code has the wrong length")
    reader = _BitReader(code)

    version = reader.read(2)
    if version != VERSION:
        raise CodecError(f"unsupported position code version {version}")
    result = reader.read(2)
    draw_offer = reader.read(2)
    if draw_offer > DRAW_OFFER_BLACK:
        raise CodecError("invalid draw offer")

    occupied = [square for square in chess.SQUARES if reader.read(1)]

    board = chess.Board(None)
    board.turn = chess.WHITE
    castling = 0
    ep_square = None
    for square in occupied:
        code_value = reader.read(4)
        if code_value == _CASTLING_ROOK:
            if square not in _CASTLING_SQUARES:
                raise CodecError("castling rook on a non-corner square")
            color = chess.WHITE if chess.square_rank(square) == 0 else chess.BLACK
            board.set_piece_at(square, chess.Piece(chess.ROOK, color))
            castling |= chess.BB_SQUARES[square]
        elif code_value == _EP_PAWN:
            rank = chess.square_rank(square)
            if rank == 3:  # white pawn that just moved two squares
                board.set_piece_at(square, chess.Piece(chess.PAWN, chess.WHITE))
                ep_square = square - 8
            elif rank == 4:  # black pawn that just moved two squares
                board.set_piece_at(square, chess.Piece(chess.PAWN, chess.BLACK))
                ep_square = square + 8
            else:
                raise CodecError("en passant pawn on the wrong rank")
        elif code_value == _BLACK_KING_TO_MOVE:
            board.set_piece_at(square, chess.Piece(chess.KING, chess.BLACK))
            board.turn = chess.BLACK
        elif code_value < len(_PIECE_SYMBOLS):
            board.set_piece_at(
                square, chess.Piece.from_symbol(_PIECE_SYMBOLS[code_value])
            )
        else:
            raise CodecError(f"invalid piece code {code_value}")

    board.castling_rights = castling
    board.ep_square = ep_square
    board.fullmove_number = reader.read(_FULLMOVE_BITS)
    board.halfmove_clock = reader.read(_HALFMOVE_BITS)
    from_square, to_square = reader.read(6), reader.read(6)
    if reader.remaining >= 5:
        raise CodecError("position code has trailing data")

    if board.fullmove_number < 1:
        raise CodecError("invalid move number")
    if ep_square is not None and (
        (board.turn == chess.WHITE) != (chess.square_rank(ep_square) == 5)
    ):
        raise CodecError("en passant square does not match side to move")
    if not board.is_valid():
        raise CodecError(f"position is not valid: {board.status()!r}")

    last_move = None
    if from_square != to_square:
        last_move = chess.Move(from_square, to_square)
    return Position(
        board=board, result=result, draw_offer=draw_offer, last_move=last_move
    )


def is_code(text: str) -> bool:
    """Cheap syntactic check for something that looks like a position code."""
    return bool(CODE_RE.match(text or ""))
