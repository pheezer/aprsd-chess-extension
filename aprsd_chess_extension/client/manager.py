"""The web client's view of its games, rebuilt from the server's messages.

The server is the authority.  This keeps one python-chess board per game,
applies the moves the server reports, and asks for a `sync` (the one-message
state code) whenever it might have missed something.  Updates are pushed to
any number of browsers through per-subscriber queues (Server-Sent Events).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import tempfile
import threading
import time
from typing import Callable

import chess

from aprsd_chess_extension import codec, protocol

LOG = logging.getLogger("APRSD")

MAX_LOG = 200


class ClientGameManager:
    def __init__(
        self,
        player: str,
        server_callsign: str,
        send: Callable[[str], None],
        cache_file: str | None = None,
        pending_timeout: float = 200,
        clock: Callable[[], float] = time.time,
    ):
        self.player = protocol.base_call(player)
        self.server_callsign = protocol.normalize_call(server_callsign)
        self._send_text = send
        self.cache_file = cache_file
        self.pending_timeout = pending_timeout
        self.clock = clock
        self.lock = threading.RLock()
        self.games: dict[str, dict] = {}
        # A pending `chess <me> <them>` we're waiting to hear back about.
        self.pending_new: dict | None = None
        # The last failed resume-by-code, shown next to the Resume box.
        self.resume_error: dict | None = None
        self.log: list[dict] = []
        self._subscribers: list[queue.Queue] = []
        self._load_cache()

    # -- sending -------------------------------------------------------

    def _send(self, text: str) -> None:
        self._log("tx", text)
        self._send_text(text)

    def start_game(self, mycall: str, theircall: str) -> None:
        mycall = protocol.normalize_call(mycall)
        theircall = protocol.normalize_call(theircall)
        if protocol.base_call(mycall) != self.player:
            raise ValueError(
                f"This station plays as {self.player}; to play as "
                f"{protocol.base_call(mycall)}, change player_callsign"
            )
        if not protocol.is_valid_callsign(theircall):
            raise ValueError(f"{theircall} is not a valid callsign")
        if protocol.base_call(theircall) == self.player:
            raise ValueError("You can't play yourself")
        with self.lock:
            self.pending_new = {
                "opponent": protocol.base_call(theircall),
                "since": self.clock(),
            }
        self._send(protocol.cmd_new(self.player, theircall))
        self._notify()

    def resume(self, code: str) -> None:
        code = (code or "").strip()
        if not protocol.is_game_code(code):
            raise ValueError("A game code is 4 digits")
        with self.lock:
            self.resume_error = None
            game = self.games.setdefault(code, self._blank_game(code))
            game["syncing"] = True
            game["updated"] = int(self.clock())
        self._send(protocol.cmd_sync(code))
        self._notify()

    def sync(self, code: str) -> None:
        self.resume(code)

    def refresh_list(self) -> None:
        self._send(protocol.cmd_list())

    def move(self, code: str, uci: str) -> None:
        with self.lock:
            game = self._require(code)
            if game["result"] != "*":
                raise ValueError("The game is over")
            if game["pending"]:
                raise ValueError(
                    "Still waiting for the server to confirm your last move"
                )
            board = self._board(game)
            if board.turn != (game["color"] == "W"):
                raise ValueError(f"It's {game['opponent']}'s move")
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                raise ValueError(f"Bad move {uci}") from None
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move {uci}")
            ply = self._ply(game) + 1
            game["pending"] = {
                "kind": "move",
                "uci": move.uci(),
                "ply": ply,
                "san": board.san(move),
                "since": self.clock(),
                "acked": False,
                "failed": False,
            }
        self._send(protocol.cmd_move(code, ply, move.uci()))
        self._notify()

    def retry(self, code: str) -> None:
        with self.lock:
            game = self._require(code)
            pending = game["pending"]
            if not pending:
                self.sync(code)
                return
            pending.update(since=self.clock(), failed=False)
            text = (
                protocol.cmd_move(code, pending["ply"], pending["uci"])
                if pending["kind"] == "move"
                else protocol.cmd_resign(code)
                if pending["kind"] == "resign"
                else protocol.cmd_draw(code)
            )
        self._send(text)
        self._notify()

    def cancel_pending(self, code: str) -> None:
        with self.lock:
            self._require(code)["pending"] = None
        self.sync(code)

    def resign(self, code: str) -> None:
        self._simple_action(code, "resign", protocol.cmd_resign(code))

    def draw(self, code: str) -> None:
        self._simple_action(code, "draw", protocol.cmd_draw(code))

    def _simple_action(self, code: str, kind: str, text: str) -> None:
        with self.lock:
            game = self._require(code)
            if game["result"] != "*":
                raise ValueError("The game is over")
            if not game["pending"]:
                game["pending"] = {
                    "kind": kind,
                    "since": self.clock(),
                    "acked": False,
                    "failed": False,
                }
        self._send(text)
        self._notify()

    def forget(self, code: str) -> None:
        with self.lock:
            self.games.pop(code, None)
            self._save_cache()
        self._notify()

    # -- receiving -----------------------------------------------------

    def on_ack(self, text: str | None) -> None:
        """aprsd confirmed the server received one of our messages."""
        if not text:
            return
        command = protocol.parse_command(text)
        with self.lock:
            game = self.games.get(command.code or "")
            if game and game["pending"]:
                game["pending"]["acked"] = True
        self._notify()

    def on_message(self, from_call: str, text: str) -> None:
        """A message addressed to this station."""
        if protocol.normalize_call(from_call) != self.server_callsign:
            self._log("rx-other", f"{from_call}: {text}")
            return
        self._log("rx", text)
        reply = protocol.parse_reply(text)
        need_sync = []
        with self.lock:
            handler = getattr(self, f"_on_{reply.kind}", None)
            if handler:
                need_sync = handler(reply) or []
            self._save_cache()
        self._auto_sync(need_sync)
        self._notify()

    def _auto_sync(self, codes) -> None:
        """Ask for the state of games we may have missed messages for."""
        now = self.clock()
        to_send = []
        with self.lock:
            for code in dict.fromkeys(codes):
                game = self.games.get(code)
                if game is not None:
                    if game.get("sync_sent") and now - game["sync_sent"] < 60:
                        continue  # one is already on its way
                    game["syncing"] = True
                    game["sync_sent"] = now
                to_send.append(code)
        for code in to_send:
            self._send(protocol.cmd_sync(code))

    def _on_new_game(self, reply):
        known = reply.code in self.games
        game = self._ensure(reply.code, reply.fields["call"], "W")
        if not known:
            game["joined"] = False
        self.pending_new = None
        return []

    def _on_invite(self, reply):
        game = self._ensure(reply.code, reply.fields["call"], "B")
        game["invited"] = True
        return [reply.code]  # learn White's opening move, if any

    def _on_state(self, reply):
        try:
            position = codec.decode(reply.fields["position"])
        except codec.CodecError as ex:
            LOG.warning(f"Chess: bad state code from server: {ex}")
            return []
        game = self._ensure(reply.code, reply.fields["opponent"], reply.fields["color"])
        game["fen"] = position.board.fen()
        game["ply"] = position.ply
        game["result"] = {
            codec.RESULT_ONGOING: "*",
            codec.RESULT_WHITE: "1-0",
            codec.RESULT_BLACK: "0-1",
            codec.RESULT_DRAW: protocol.DRAW,
        }[position.result]
        game["draw_offer"] = {
            codec.DRAW_OFFER_NONE: None,
            codec.DRAW_OFFER_WHITE: "W",
            codec.DRAW_OFFER_BLACK: "B",
        }[position.draw_offer]
        game["last_move"] = position.last_move.uci() if position.last_move else None
        # The state code replaces the move list; history before it is unknown.
        game["moves"] = []
        game["base_fen"] = game["fen"]
        game["base_ply"] = position.ply
        game["syncing"] = False
        game["sync_sent"] = 0
        pending = game["pending"]
        if pending and pending["kind"] == "move" and pending["ply"] <= position.ply:
            game["pending"] = None  # it was played (or superseded)
        if self.pending_new and self.pending_new["opponent"] == game["opponent"]:
            self.pending_new = None
        return []

    def _on_move_ok(self, reply):
        game = self.games.get(reply.code)
        if not game:
            return [reply.code]
        return self._apply_san(
            game, reply.fields["ply"], reply.fields["san"], mine=True
        )

    def _on_move_notice(self, reply):
        game = self._ensure(reply.code, reply.fields["mover"], None)
        game["joined"] = True
        return self._apply_san(
            game, reply.fields["ply"], reply.fields["san"], mine=False
        )

    def _on_game_over(self, reply):
        game = self.games.get(reply.code)
        if not game:
            return [reply.code]
        need = self._apply_san(
            game, reply.fields["ply"], reply.fields["san"], mine=None
        )
        game["result"] = reply.fields["result"]
        game["termination"] = reply.fields["reason"]
        game["pending"] = None
        return need

    def _on_status(self, reply):
        known = reply.code in self.games
        game = self._ensure(reply.code, reply.fields["opponent"], reply.fields["color"])
        fields = reply.fields
        if self.pending_new and self.pending_new["opponent"] == game["opponent"]:
            # A re-challenge answered with the game the two already have.
            self.pending_new = None
        if fields.get("result", "*") != "*":
            game["result"] = fields["result"]
            game["termination"] = fields.get("termination")
            return [] if known else [reply.code]
        # The server's ply only grows, so a lower one is a late retry.
        if not known or fields.get("ply", 0) > self._ply(game):
            return [reply.code]
        return []

    def _on_list_entry(self, reply):
        known = reply.code in self.games
        game = self._ensure(reply.code, reply.fields["opponent"], reply.fields["color"])
        if not known or reply.fields["ply"] > self._ply(game):
            return [reply.code]
        return []

    def _on_list_empty(self, reply):
        # The server has no unfinished games for us, so any we think are
        # still running were finished or expired while we weren't looking.
        return [code for code, game in self.games.items() if game["result"] == "*"]

    def _on_draw_offer(self, reply):
        game = self.games.get(reply.code)
        if game:
            game["draw_offer"] = "B" if game["color"] == "W" else "W"
        return [] if game else [reply.code]

    def _on_draw_offered(self, reply):
        game = self.games.get(reply.code)
        if game:
            game["draw_offer"] = game["color"]
            game["pending"] = None
        return [] if game else [reply.code]

    def _on_joined(self, reply):
        game = self.games.get(reply.code)
        if game:
            game["joined"] = True
        return []

    def _on_resigned(self, reply):
        return self._finish(reply, reply.fields["result"], "resignation")

    def _on_drawn(self, reply):
        how = reply.fields["how"]
        reason = "agreement" if how == "agreed" else how.removeprefix("by ")
        return self._finish(reply, protocol.DRAW, reason)

    def _on_cancelled(self, reply):
        return self._finish(reply, "-", f"cancelled by {reply.fields['by']}")

    def _finish(self, reply, result, reason):
        game = self.games.get(reply.code)
        if game:
            game["result"] = result
            game["termination"] = reason
            game["pending"] = None
            game["draw_offer"] = None
        return []

    def _on_error(self, reply):
        game = self.games.get(reply.code or "")
        if game:
            game["error"] = reply.fields["message"]
            game["pending"] = None
            game["syncing"] = False
            if reply.fields["message"] in ("no such game", "you are not in this game"):
                game["result"] = "?"
                game["termination"] = "not on server"
                if game["color"] is None:
                    # A resume of a code that doesn't exist (or isn't ours).
                    del self.games[reply.code]
                    self.resume_error = {
                        "code": reply.code,
                        "error": reply.fields["message"],
                    }
            return []
        if self.pending_new and reply.code is None:
            self.pending_new = {**self.pending_new, "error": reply.fields["message"]}
        return []

    # -- game bookkeeping ----------------------------------------------

    @staticmethod
    def _blank_game(code: str) -> dict:
        return {
            "code": code,
            "opponent": None,
            "color": None,
            "base_fen": chess.STARTING_FEN,
            "base_ply": 0,
            "moves": [],  # SAN moves after base_fen
            "fen": None,
            "ply": 0,
            "last_move": None,
            "result": "*",
            "termination": None,
            "draw_offer": None,
            "pending": None,
            "joined": True,
            "invited": False,
            "syncing": False,
            "error": None,
            "updated": 0,
        }

    def _ensure(self, code: str, opponent: str | None, color: str | None) -> dict:
        game = self.games.get(code)
        if game is None:
            game = self._blank_game(code)
            game["fen"] = chess.STARTING_FEN
            self.games[code] = game
        if opponent:
            game["opponent"] = opponent
        if color:
            game["color"] = color
        game["error"] = None
        game["updated"] = int(self.clock())
        return game

    def _require(self, code: str) -> dict:
        game = self.games.get(code)
        if not game or game["color"] is None or game["fen"] is None:
            raise ValueError("Unknown game; resume it with its code first")
        return game

    @staticmethod
    def _ply(game: dict) -> int:
        return game["base_ply"] + len(game["moves"])

    @staticmethod
    def _board(game: dict) -> chess.Board:
        board = chess.Board(game["base_fen"])
        for san in game["moves"]:
            board.push_san(san)
        return board

    def _apply_san(
        self, game: dict, ply: int, san: str, mine: bool | None
    ) -> list[str]:
        """Apply a move the server reported; returns codes needing a sync."""
        current = self._ply(game)
        pending = game["pending"]
        if mine and pending and pending["kind"] == "move" and pending["ply"] == ply:
            game["pending"] = None
        if ply <= current:
            return []  # already have it (duplicate message)
        if ply != current + 1 or game["fen"] is None:
            return [game["code"]]  # missed something
        board = self._board(game)
        try:
            move = board.parse_san(san)
        except ValueError:
            return [game["code"]]
        board.push(move)
        game["moves"].append(san)
        game["fen"] = board.fen()
        game["ply"] = ply
        game["last_move"] = move.uci()
        game["draw_offer"] = None
        game["error"] = None
        return []

    # -- timeouts --------------------------------------------------------

    def check_timeouts(self) -> None:
        changed = False
        now = self.clock()
        with self.lock:
            for game in self.games.values():
                pending = game["pending"]
                if pending and not pending["failed"]:
                    if now - pending["since"] > self.pending_timeout:
                        pending["failed"] = True
                        changed = True
            if self.pending_new and not self.pending_new.get("error"):
                if now - self.pending_new["since"] > self.pending_timeout:
                    self.pending_new = {
                        **self.pending_new,
                        "error": "No answer from the chess server",
                    }
                    changed = True
        if changed:
            self._notify()

    # -- state for the browser -----------------------------------------

    def snapshot(self) -> dict:
        with self.lock:
            games = []
            for game in sorted(self.games.values(), key=lambda g: -g["updated"]):
                if game["color"] is None:
                    games.append({**game, "legal": {}, "turn": None})
                    continue
                legal: dict[str, list[str]] = {}
                turn = None
                if game["fen"]:
                    board = chess.Board(game["fen"])
                    turn = "W" if board.turn else "B"
                    if (
                        game["result"] == "*"
                        and turn == game["color"]
                        and not game["pending"]
                    ):
                        for move in board.legal_moves:
                            legal.setdefault(
                                chess.square_name(move.from_square), []
                            ).append(chess.square_name(move.to_square))
                    in_check = board.is_check()
                else:
                    in_check = False
                games.append(
                    {
                        **game,
                        "legal": legal,
                        "turn": turn,
                        "check": in_check,
                        "san_moves": list(game["moves"]),
                    }
                )
            return {
                "player": self.player,
                "server": self.server_callsign,
                "games": games,
                "pending_new": self.pending_new,
                "resume_error": self.resume_error,
                "log": self.log[-50:],
            }

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=20)
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _notify(self) -> None:
        with self.lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait("update")
            except queue.Full:
                pass  # that browser will catch up from the next snapshot

    def _log(self, direction: str, text: str) -> None:
        with self.lock:
            self.log.append({"t": int(self.clock()), "dir": direction, "text": text})
            del self.log[:-MAX_LOG]

    # -- cache -----------------------------------------------------------

    def _load_cache(self) -> None:
        if not self.cache_file or not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("player") == self.player:
                for code, game in data.get("games", {}).items():
                    merged = self._blank_game(code)
                    merged.update(game)
                    merged["pending"] = None
                    merged["syncing"] = False
                    self.games[code] = merged
        except Exception:
            LOG.warning(
                f"Chess: ignoring unreadable cache {self.cache_file}", exc_info=True
            )

    def _save_cache(self) -> None:
        if not self.cache_file:
            return
        try:
            directory = os.path.dirname(os.path.abspath(self.cache_file))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=directory, prefix=".chess-client.", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump({"player": self.player, "games": self.games}, fp)
            os.replace(tmp, self.cache_file)
        except Exception:
            LOG.warning("Chess: could not write the client cache", exc_info=True)

    def startup_sync(self) -> None:
        """After a restart, refresh every cached unfinished game."""
        self.refresh_list()
