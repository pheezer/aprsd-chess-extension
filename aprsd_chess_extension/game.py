"""Chess rules on top of python-chess: rebuilding games, parsing moves, results."""

from __future__ import annotations

import re

import chess

from aprsd_chess_extension import codec

RESULT_TEXT = {
    codec.RESULT_ONGOING: "*",
    codec.RESULT_WHITE: "1-0",
    codec.RESULT_BLACK: "0-1",
    codec.RESULT_DRAW: "1/2-1/2",
}

# Terminations that end the game on their own (no claim needed).
_AUTOMATIC_ENDINGS = {
    chess.Termination.CHECKMATE: "checkmate",
    chess.Termination.STALEMATE: "stalemate",
    chess.Termination.INSUFFICIENT_MATERIAL: "insufficient material",
    chess.Termination.SEVENTYFIVE_MOVES: "75-move rule",
    chess.Termination.FIVEFOLD_REPETITION: "fivefold repetition",
}

_ANNOTATION_RE = re.compile(r"[!?+#]+$")
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class MoveError(ValueError):
    """Raised when a move can't be understood or isn't legal."""

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


def board_from_moves(moves: list[str]) -> chess.Board:
    """Replay a list of UCI moves from the standard start position."""
    board = chess.Board()
    for uci in moves:
        board.push(chess.Move.from_uci(uci))
    return board


def parse_move(board: chess.Board, text: str) -> chess.Move:
    """Parse a move typed by a player, in SAN or UCI, as leniently as is safe.

    Accepts `Nf3`, `exd5`, `O-O`, `0-0`, `e8=Q`, `e8Q`, `g1f3`, `e7e8q`, with
    or without check/annotation marks, and falls back to a case-insensitive
    match when the exact text isn't recognized.  Raises MoveError, with the
    candidate UCI moves when the text is ambiguous.
    """
    raw = (text or "").strip()
    if not raw:
        raise MoveError("No move given")
    cleaned = _ANNOTATION_RE.sub("", raw)
    cleaned = cleaned.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    if cleaned.lower() in ("o-o", "o-o-o"):
        cleaned = cleaned.upper()

    lowered = cleaned.lower()
    if _UCI_RE.match(lowered):
        try:
            # Also normalizes castling typed king-takes-rook (e1h1 -> e1g1).
            return board.parse_uci(lowered)
        except ValueError:
            pass

    try:
        return board.parse_san(cleaned)
    except ValueError:  # includes chess.AmbiguousMoveError
        pass

    candidates = _matching_moves(board, cleaned)
    if len(candidates) == 1:
        return chess.Move.from_uci(candidates[0])
    if candidates:
        raise MoveError(f"Ambiguous move {raw}", candidates)
    raise MoveError(f"Illegal or unknown move {raw}")


_SAN_PARTS_RE = re.compile(r"^([nbrqk]?)([a-h]?)([1-8]?)x?([a-h][1-8])([nbrq]?)$")


def _matching_moves(board: chess.Board, text: str) -> list[str]:
    """Legal moves that `text` could mean, read case-insensitively.

    Used when exact SAN fails: `nf3`, `NF3`, `rd1` when two rooks can reach
    d1, or `e8` for a promotion.  A leading `b` is tried both as a bishop and
    as the b-file pawn.
    """
    parts = _SAN_PARTS_RE.match(text.lower().replace("=", ""))
    if not parts:
        return []
    piece, file, rank, dest, promotion = parts.groups()
    readings = [(piece, file)]
    if piece == "b" and not file:
        readings.append(("", "b"))
    to_square = chess.parse_square(dest)
    matches = set()
    for piece_letter, from_file in readings:
        piece_type = (
            chess.PIECE_SYMBOLS.index(piece_letter) if piece_letter else chess.PAWN
        )
        for move in board.legal_moves:
            if move.to_square != to_square:
                continue
            if board.piece_type_at(move.from_square) != piece_type:
                continue
            if from_file and chess.square_file(move.from_square) != "abcdefgh".index(
                from_file
            ):
                continue
            if rank and chess.square_rank(move.from_square) != int(rank) - 1:
                continue
            if promotion and move.promotion != chess.PIECE_SYMBOLS.index(promotion):
                continue
            matches.add(move.uci())
    return sorted(matches)


def automatic_result(board: chess.Board) -> tuple[int, str] | None:
    """The result if the game is over by rule (mate, stalemate, ...), else None."""
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return None
    reason = _AUTOMATIC_ENDINGS.get(
        outcome.termination, outcome.termination.name.lower()
    )
    if outcome.winner is None:
        return codec.RESULT_DRAW, reason
    return (codec.RESULT_WHITE if outcome.winner else codec.RESULT_BLACK), reason


def claimable_draw(board: chess.Board) -> str | None:
    """Reason a draw can be claimed right now (50 moves / threefold), else None."""
    if board.can_claim_fifty_moves():
        return "50-move rule"
    if board.can_claim_threefold_repetition():
        return "threefold repetition"
    return None
