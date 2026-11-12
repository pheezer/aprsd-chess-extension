"""The chess server: turns `chess ...` messages into game actions and replies.

This module knows nothing about aprsd's packet or thread machinery; the plugin
wraps it.  `handle()` returns the reply for the sender plus any notices for
the other player, and the caller is responsible for sending them.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Callable

import chess

from aprsd_chess_extension import codec, game, protocol
from aprsd_chess_extension.store import GameStore, new_game_record

LOG = logging.getLogger("APRSD")

DAY = 86400


@dataclasses.dataclass
class Outgoing:
    """A message to send to someone other than the sender."""

    to_call: str
    text: str


@dataclasses.dataclass
class Response:
    replies: list[str]
    notices: list[Outgoing] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Settings:
    expire_days: int = 10
    max_games_per_player: int = 5
    max_invites_per_day: int = 10


class ChessServer:
    def __init__(
        self,
        server_callsign: str,
        store: GameStore | None = None,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.server_callsign = protocol.normalize_call(server_callsign)
        self.store = store or GameStore()
        self.settings = settings or Settings()
        self.clock = clock
        # Serializes whole commands; the store's own lock guards its dict.
        self.lock = threading.RLock()

    @property
    def max_idle(self) -> float:
        return self.settings.expire_days * DAY

    # ------------------------------------------------------------------

    def handle(self, from_call: str, text: str) -> Response:
        """Process one `chess ...` message from `from_call`."""
        sender = protocol.normalize_call(from_call)
        player = protocol.base_call(sender)
        command = protocol.parse_command(text)
        with self.lock:
            now = self.clock()
            try:
                response = self._dispatch(command, sender, player, now)
            except Exception:
                LOG.exception(f"Chess: failed to handle {text!r} from {sender}")
                return Response([protocol.fmt_error("server error, try again")])
        return response

    def _dispatch(self, command, sender, player, now) -> Response:
        action = command.action
        if action == "help":
            return Response(list(protocol.HELP_LINES))
        if action == "invalid":
            return Response([protocol.fmt_error("unknown command. Send: chess help")])
        if action == "list":
            return self._list(player, now)
        if action == "new":
            return self._new(command, sender, player, now)

        record = self._find_game(command, player, now)
        if isinstance(record, Response):
            return record
        if action == "move":
            return self._move(record, command, sender, player, now)
        if action == "resign" and not record["joined"] and record["result"] is None:
            return self._cancel(record, player)

        # Any command on a game counts as activity and joins a pending invite.
        notices = self._touch(record, sender, player, now)
        if action == "status":
            response = Response([self._status_line(record, player)])
        elif action == "sync":
            response = Response([self._state_line(record, player)])
        elif action == "board":
            board = game.board_from_moves(record["moves"])
            response = Response(protocol.fmt_board(record["code"], board.fen()))
        elif action == "resign":
            response = self._resign(record, player, now)
        elif action == "draw":
            response = self._draw(record, player, now)
        else:  # pragma: no cover - parse_command only produces the above
            response = Response([protocol.fmt_error("unknown command")])
        response.notices = notices + response.notices
        self.store.save()
        return response

    # -- lookups -------------------------------------------------------

    def _find_game(self, command, player, now):
        code = command.code
        if (
            code is None
            and command.action == "move"
            and "-" in command.move
            and protocol.is_valid_callsign(command.move)
        ):
            return Response(
                [protocol.fmt_error("to start a game: chess <yourcall> <theircall>")]
            )
        if code is None:
            active = [
                g
                for g in self.store.active_games_for(player)
                if not self.store.is_expired(g, self.max_idle, now)
            ]
            if len(active) == 1 and command.action == "move":
                return active[0]
            if not active:
                return Response([protocol.LIST_EMPTY])
            if command.action == "move":
                return Response(
                    [protocol.fmt_error("include the game code: chess <code> <move>")]
                )
            return Response([protocol.fmt_error("include the game code")])
        record = self.store.get(code)
        if record is None or self.store.is_expired(record, self.max_idle, now):
            if record is not None:
                self.store.delete(code)
                self.store.save()
            return Response([protocol.fmt_error("no such game", code)])
        if player not in (record["white"], record["black"]):
            return Response([protocol.fmt_error("you are not in this game", code)])
        return record

    @staticmethod
    def _color_of(record, player) -> bool:
        return player == record["white"]

    @staticmethod
    def _opponent_of(record, player) -> str:
        return record["black"] if player == record["white"] else record["white"]

    def _address(self, record, player) -> str:
        return record["addr"].get(player, player)

    def _touch(self, record, sender, player, now) -> list[Outgoing]:
        """Mark activity; returns a notice if this joins a pending invite."""
        record["updated"] = int(now)
        record["addr"][player] = sender
        notices = []
        if player == record["black"] and not record["joined"]:
            record["joined"] = True
            notices.append(
                Outgoing(
                    self._address(record, record["white"]),
                    protocol.fmt_joined(record["code"], player),
                )
            )
        return notices

    # -- replies -------------------------------------------------------

    def _status_line(self, record, player) -> str:
        board = game.board_from_moves(record["moves"])
        is_white = self._color_of(record, player)
        last_san = None
        if board.move_stack:
            last = board.pop()
            last_san = board.san(last)
            board.push(last)
        return protocol.fmt_status(
            record["code"],
            protocol.color_letter(is_white),
            self._opponent_of(record, player),
            len(record["moves"]),
            last_san,
            board.turn == is_white,
            record["result"] or "*",
            record["termination"],
        )

    def _state_line(self, record, player) -> str:
        board = game.board_from_moves(record["moves"])
        last = board.move_stack[-1] if board.move_stack else None
        position = codec.encode(
            board,
            result=_RESULT_CODES[record["result"]],
            draw_offer=_offer_code(record),
            last_move=last,
        )
        return protocol.fmt_state(
            record["code"],
            self._opponent_of(record, player),
            protocol.color_letter(self._color_of(record, player)),
            position,
        )

    # -- commands ------------------------------------------------------

    def _list(self, player, now) -> Response:
        lines = []
        for record in self.store.active_games_for(player):
            if self.store.is_expired(record, self.max_idle, now):
                continue
            is_white = self._color_of(record, player)
            plies = len(record["moves"])
            my_turn = (plies % 2 == 0) == is_white
            lines.append(
                protocol.fmt_list_entry(
                    record["code"],
                    self._opponent_of(record, player),
                    protocol.color_letter(is_white),
                    plies,
                    my_turn,
                )
            )
        return Response(lines or [protocol.LIST_EMPTY])

    def _new(self, command, sender, player, now) -> Response:
        mycall, theircall = command.mycall, command.theircall
        if protocol.base_call(mycall) != player:
            return Response(
                [
                    protocol.fmt_error(
                        f"you are {player}, not {protocol.base_call(mycall)}"
                    )
                ]
            )
        opponent = protocol.base_call(theircall)
        if opponent == player:
            return Response([protocol.fmt_error("you can't play yourself")])
        if protocol.base_call(self.server_callsign) == opponent:
            return Response([protocol.fmt_error("challenge a person, not the server")])

        existing = self.store.active_game_between(player, opponent)
        if existing and not self.store.is_expired(existing, self.max_idle, now):
            # Re-sending a challenge (or answering one with a challenge of
            # your own) lands in the game the two already have.
            notices = self._touch(existing, sender, player, now)
            self.store.save()
            return Response([self._status_line(existing, player)], notices)

        active = [
            g
            for g in self.store.active_games_for(player)
            if not self.store.is_expired(g, self.max_idle, now)
        ]
        if len(active) >= self.settings.max_games_per_player:
            return Response(
                [
                    protocol.fmt_error(
                        f"limit is {self.settings.max_games_per_player} games"
                    )
                ]
            )
        if self.store.invites_today(player, now) >= self.settings.max_invites_per_day:
            return Response([protocol.fmt_error("too many new games today")])

        code = self.store.new_code()
        record = new_game_record(
            code, player, opponent, {player: sender, opponent: theircall}, now
        )
        self.store.add(record)
        self.store.record_invite(player, now)
        self.store.save()
        LOG.info(f"Chess: new game {code} {player} (W) vs {opponent} (B)")
        return Response(
            [protocol.fmt_new_game(code, opponent)],
            [Outgoing(theircall, protocol.fmt_invite(code, player))],
        )

    def _move(self, record, command, sender, player, now) -> Response:
        code = record["code"]
        if record["result"] is not None:
            return Response([self._status_line(record, player)])
        board = game.board_from_moves(record["moves"])
        is_white = self._color_of(record, player)
        plies = len(record["moves"])

        if command.ply is not None and command.ply != plies + 1:
            notices = self._touch(record, sender, player, now)
            self.store.save()
            # A retry of the sender's own last move gets its ok again.
            if command.ply == plies and plies and (plies % 2 == 1) == is_white:
                last = board.pop()
                try:
                    retry = game.parse_move(board, command.move)
                except game.MoveError:
                    retry = None
                last_san = board.san(last)
                if retry == last:
                    opponent = self._opponent_of(record, player)
                    reply = protocol.fmt_move_ok(code, plies, last_san, opponent)
                    return Response([reply], notices)
            # Otherwise the client is out of date: send it the real state.
            return Response([self._state_line(record, player)], notices)

        if board.turn != is_white:
            return Response(
                [
                    protocol.fmt_error(
                        f"{self._opponent_of(record, player)} to move", code
                    )
                ]
            )
        try:
            move = game.parse_move(board, command.move)
        except game.MoveError as ex:
            message = str(ex)
            if ex.candidates:
                message = f"{message}: {' '.join(ex.candidates)}"
            elif command.code is None and protocol.is_valid_callsign(command.move):
                message = "to start a game: chess <yourcall> <theircall>"
            return Response([protocol.fmt_error(message, code)])

        # The move notice tells the challenger the invitee has joined, so
        # the separate "joined" notice isn't needed here.
        self._touch(record, sender, player, now)
        notices = []
        san = board.san(move)
        board.push(move)
        record["moves"].append(move.uci())
        record["draw_offer"] = None
        ply = len(record["moves"])
        opponent = self._opponent_of(record, player)
        opponent_addr = self._address(record, opponent)

        ended = game.automatic_result(board)
        if ended:
            result_code, reason = ended
            self._finish(record, result_code, reason, now)
            winner = None
            if result_code == codec.RESULT_WHITE:
                winner = record["white"]
            elif result_code == codec.RESULT_BLACK:
                winner = record["black"]
            text = protocol.fmt_game_over(
                code, ply, san, reason, record["result"], winner
            )
            replies = [text]
            if record["joined"]:
                notices.append(Outgoing(opponent_addr, text))
        else:
            replies = [protocol.fmt_move_ok(code, ply, san, opponent)]
            if record["joined"]:
                notices.append(
                    Outgoing(
                        opponent_addr, protocol.fmt_move_notice(code, ply, player, san)
                    )
                )
        self.store.save()
        return Response(replies, notices)

    def _finish(self, record, result_code: int, reason: str, now) -> None:
        record["result"] = game.RESULT_TEXT[result_code]
        record["termination"] = reason
        record["draw_offer"] = None
        record["updated"] = int(now)
        LOG.info(f"Chess: game {record['code']} over {record['result']} ({reason})")

    def _cancel(self, record, player) -> Response:
        """Withdraw (challenger) or decline (invitee) a game Black never joined."""
        code = record["code"]
        opponent_addr = self._address(record, self._opponent_of(record, player))
        self.store.delete(code)
        self.store.save()
        LOG.info(f"Chess: game {code} cancelled by {player}")
        text = protocol.fmt_cancelled(code, player)
        return Response([text], [Outgoing(opponent_addr, text)])

    def _resign(self, record, player, now) -> Response:
        code = record["code"]
        if record["result"] is not None:
            return Response([self._status_line(record, player)])
        opponent = self._opponent_of(record, player)
        opponent_addr = self._address(record, opponent)
        is_white = self._color_of(record, player)
        self._finish(
            record,
            codec.RESULT_BLACK if is_white else codec.RESULT_WHITE,
            "resignation",
            now,
        )
        text = protocol.fmt_resigned(code, player, opponent, record["result"])
        return Response([text], [Outgoing(opponent_addr, text)])

    def _draw(self, record, player, now) -> Response:
        code = record["code"]
        if record["result"] is not None:
            return Response([self._status_line(record, player)])
        board = game.board_from_moves(record["moves"])
        opponent = self._opponent_of(record, player)
        opponent_addr = self._address(record, opponent)

        claim = game.claimable_draw(board)
        if claim:
            self._finish(record, codec.RESULT_DRAW, claim, now)
            text = protocol.fmt_draw_claimed(code, claim)
            return Response([text], [Outgoing(opponent_addr, text)])
        offered_by = record["draw_offer"]
        if offered_by == opponent:
            self._finish(record, codec.RESULT_DRAW, "agreement", now)
            text = protocol.fmt_draw_agreed(code)
            return Response([text], [Outgoing(opponent_addr, text)])
        record["draw_offer"] = player
        return Response(
            [protocol.fmt_draw_offered(code, opponent)],
            [Outgoing(opponent_addr, protocol.fmt_draw_offer(code, player))],
        )

    # ------------------------------------------------------------------

    def expire(self) -> list[str]:
        with self.lock:
            expired = self.store.expire(self.max_idle, self.clock())
            if expired:
                self.store.save()
                LOG.info(f"Chess: expired idle games {expired}")
        return expired


_RESULT_CODES = {
    None: codec.RESULT_ONGOING,
    "1-0": codec.RESULT_WHITE,
    "0-1": codec.RESULT_BLACK,
    protocol.DRAW: codec.RESULT_DRAW,
}


def _offer_code(record) -> int:
    offered_by = record.get("draw_offer")
    if offered_by is None:
        return codec.DRAW_OFFER_NONE
    if offered_by == record["white"]:
        return codec.DRAW_OFFER_WHITE
    return codec.DRAW_OFFER_BLACK


def position_from_state(position_code: str) -> chess.Board:
    """Board for a state line's position code (client side helper)."""
    return codec.decode(position_code).board
