"""A terminal chess client: a text board and a command prompt.

It drives the same ClientGameManager as the web board, so it gets the same
behavior: safe retries, gap detection with automatic resync, and resuming a
game from its code alone.  Messages that arrive while you're at the prompt
(your opponent's moves) are printed as they come in, and the line you're
typing is redrawn underneath them.
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
from typing import Callable, TextIO

import chess

from aprsd_chess_extension import game as game_rules
from aprsd_chess_extension import protocol
from aprsd_chess_extension.client.manager import ClientGameManager

PROMPT = "chess> "

HELP = """\
Commands (leave out the game code to use the game you're looking at):
  new <theircall-SSID>   start a game; you play White and move first
  load <code>            resume a game from its 4-digit code
  games                  your games here            refresh: ask the server for them
  play <code>            switch to another game
  <move>                 play a move: e4  Nf3  O-O  exd5  e8=Q  g1f3
  board                  redraw the board           flip: view from the other side
  moves                  the moves since the board was loaded
  draw                   offer, accept or claim a draw
  resign                 resign (or cancel a game nobody has joined)
  sync                   ask the server for the position again
  retry                  resend a command the server never answered
  traffic                show or hide the APRS messages sent and received
  ascii                  switch between chess symbols and letters
  help, quit"""

# What a move looks like when typed at the prompt.
_MOVE_RE = re.compile(
    r"^(?:[KQRBNkqrbn]?[a-h]?[1-8]?x?[a-h][1-8](?:=?[QRBNqrbn])?"
    r"|[a-h][1-8][a-h][1-8][qrbnQRBN]?"
    r"|[O0o]-[O0o](?:-[O0o])?)[+#!?]*$"
)


def is_move_like(text: str) -> bool:
    return bool(_MOVE_RE.match(text or ""))


def describe(game: dict) -> str:
    """One line saying what's going on in a game (as the web board does)."""
    if not game.get("color"):
        return "loading from the server..." if game.get("syncing") else "not loaded"
    result = game.get("result", "*")
    if result != "*":
        why = f" ({game['termination']})" if game.get("termination") else ""
        if result == "-":
            return f"cancelled{why}"
        if result == "?":
            return "not found on the server"
        return f"game over {result}{why}"
    pending = game.get("pending")
    if pending:
        if pending.get("failed"):
            return "no answer from the server; type retry"
        what = pending.get("san") if pending["kind"] == "move" else pending["kind"]
        delivered = ", delivered" if pending.get("acked") else ""
        return f"{what} sent{delivered}, waiting for the server..."
    if game.get("error"):
        return f"error: {game['error']}"
    if game.get("syncing"):
        return "syncing with the server..."
    if game.get("draw_offer") and game["draw_offer"] != game["color"]:
        return f"{game['opponent']} offers a draw; type draw to accept"
    if game.get("turn") == game["color"]:
        return "check! your move" if game.get("check") else "your move"
    if game["color"] == "W" and not game.get("joined") and game.get("ply", 0) <= 1:
        return f"waiting for {game['opponent']} to join"
    return f"waiting for {game['opponent']}"


def render_board(
    fen: str,
    color: str = "W",
    last_move: str | None = None,
    use_unicode: bool = True,
    use_color: bool = False,
) -> str:
    """The board as text from `color`'s side, with the last move marked."""
    board = chess.Board(fen)
    marked = set()
    if last_move:
        marked = {chess.parse_square(last_move[:2]), chess.parse_square(last_move[2:4])}
    ranks = range(7, -1, -1) if color == "W" else range(8)
    files = range(8) if color == "W" else range(7, -1, -1)
    lines = []
    for rank in ranks:
        cells = []
        for file in files:
            square = chess.square(file, rank)
            light = (file + rank) % 2 == 1
            piece = board.piece_at(square)
            if piece is None:
                symbol = " " if use_color else ("." if light else ":")
            elif use_unicode:
                # Solid glyphs for both colors read better on a colored board;
                # the text color tells them apart.
                symbol = (
                    chess.UNICODE_PIECE_SYMBOLS[piece.symbol().lower()]
                    if use_color
                    else piece.unicode_symbol()
                )
            else:
                symbol = piece.symbol()
            if not use_color:
                cells.append(f"[{symbol}]" if square in marked else f" {symbol} ")
                continue
            if square in marked:
                background = "\x1b[48;5;143m" if light else "\x1b[48;5;100m"
            else:
                background = "\x1b[48;5;180m" if light else "\x1b[48;5;137m"
            foreground = "\x1b[38;5;231m" if piece and piece.color else "\x1b[38;5;16m"
            cells.append(f"{background}{foreground} {symbol} \x1b[0m")
        lines.append(f" {rank + 1} " + "".join(cells))
    lines.append("   " + "".join(f" {'abcdefgh'[f]} " for f in files))
    return "\n".join(lines)


def move_list(game: dict) -> str:
    moves = game.get("san_moves") or game.get("moves") or []
    base = game.get("base_ply", 0)
    parts = [f"(from move {base // 2 + 1})"] if base else []
    for i, san in enumerate(moves):
        ply = base + i + 1
        if ply % 2 == 1:
            parts.append(f"{(ply + 1) // 2}. {san}")
        elif i == 0:
            parts.append(f"{ply // 2}... {san}")
        else:
            parts.append(san)
    return " ".join(parts) if parts else "(no moves since the board was loaded)"


def parse_typed_move(board: chess.Board, text: str) -> chess.Move:
    """A legal move from what the player typed (as lenient as the server)."""
    try:
        return game_rules.parse_move(board, text)
    except game_rules.MoveError as ex:
        if ex.candidates:
            choices = ", ".join(
                board.san(chess.Move.from_uci(c)) for c in ex.candidates
            )
            raise ValueError(f"{text} could be {choices}; be more specific") from None
        raise ValueError(f"{text} isn't a legal move here") from None


def _readline_buffer() -> str:
    try:
        import readline

        return readline.get_line_buffer()
    except Exception:
        return ""


class TerminalClient:
    def __init__(
        self,
        manager: ClientGameManager,
        out: TextIO | None = None,
        use_unicode: bool | None = None,
        use_color: bool | None = None,
        show_traffic: bool = True,
    ):
        self.manager = manager
        self.out = out or sys.stdout
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        encoding = (getattr(self.out, "encoding", None) or "").lower()
        self.use_unicode = use_unicode if use_unicode is not None else "utf" in encoding
        if use_color is None:
            use_color = self.tty and not os.environ.get("NO_COLOR")
        self.use_color = use_color
        self.show_traffic = show_traffic
        self.flipped = False
        self.running = True
        self.current: str | None = None
        self._quit_warned = False
        self._at_prompt = False
        self._last_line = ""
        self._awaiting_opponent: str | None = None
        self._out_lock = threading.Lock()
        self._ui_lock = threading.RLock()
        self._updates = manager.subscribe()

        snapshot = manager.snapshot()
        playable = [g for g in snapshot["games"] if g.get("color")]
        if playable:
            self.current = playable[0]["code"]
        # Remember what's already on screen, so only changes get printed.
        self._seen_seq = manager.log[-1]["seq"] if manager.log else 0
        self._known = {g["code"] for g in snapshot["games"]}
        self._fen = {g["code"]: g.get("fen") for g in snapshot["games"]}
        self._status = {g["code"]: describe(g) for g in snapshot["games"]}
        self._shown_errors: set[str] = set()

    # -- output -----------------------------------------------------------

    def say(self, text: str = "") -> None:
        with self._out_lock:
            if self._at_prompt and self.tty:
                # Print above the prompt, then put the half-typed line back.
                # libedit (macOS) keeps the last line in its buffer until the
                # next keystroke, so don't echo that back as if retyped.
                typed = _readline_buffer().rstrip("\r\n")
                if typed == self._last_line:
                    typed = ""
                self.out.write(f"\r\x1b[2K{text}\n{PROMPT}{typed}")
            else:
                self.out.write(text + "\n")
            self.out.flush()

    def _game(self, code: str | None = None) -> dict | None:
        code = code or self.current
        return next(
            (g for g in self.manager.snapshot()["games"] if g["code"] == code), None
        )

    def show_board(self, code: str | None = None) -> None:
        game = self._game(code)
        if game is None:
            self.say(
                "No game selected. Start one with new <theircall-SSID>, or load <code>."
            )
            return
        if not game.get("color") or not game.get("fen") or game.get("syncing"):
            self.say(f"Game {game['code']}: {describe(game)}")
            return
        side = game["color"]
        if self.flipped:
            side = "B" if side == "W" else "W"
        color_name = "White" if game["color"] == "W" else "Black"
        board = render_board(
            game["fen"], side, game.get("last_move"), self.use_unicode, self.use_color
        )
        # One write, so a redraw above the prompt happens all at once.
        self.say(
            f"\nGame {game['code']}: you ({color_name}) vs {game['opponent']}\n"
            f"{board}\n  {describe(game)}"
        )

    # -- live updates -----------------------------------------------------

    def drain_updates(self) -> None:
        """Print whatever changed since the last look."""
        with self._ui_lock:
            while True:
                try:
                    self._updates.get_nowait()
                except queue.Empty:
                    break
            # Traffic first, then state, so every printed message is reflected.
            self._print_traffic()
            snapshot = self.manager.snapshot()
            for game in snapshot["games"]:
                self._game_changed(game)
            self._print_errors(snapshot)

    def _print_traffic(self) -> None:
        with self.manager.lock:
            entries = [e for e in self.manager.log if e["seq"] > self._seen_seq]
        if entries:
            self._seen_seq = entries[-1]["seq"]
        if not self.show_traffic:
            return
        for entry in entries:
            if entry["dir"] == "tx":
                self.say(f"  >> {entry['text']}")
            elif entry["dir"] == "rx":
                self.say(f"  << {entry['text']}")

    def _game_changed(self, game: dict) -> None:
        code = game["code"]
        if code not in self._known and game.get("color"):
            self._known.add(code)
            if game["color"] == "W" and game.get("opponent") == self._awaiting_opponent:
                self._awaiting_opponent = None
                self.current = code  # the game we just started
            elif self.current is None:
                self.current = code
            if game.get("invited"):
                hint = (
                    "You play Black." if code == self.current else f"Type: play {code}"
                )
                self.say(f"** {game['opponent']} challenged you to game {code}. {hint}")
        status = describe(game)
        fen = game.get("fen") if not game.get("syncing") else None
        moved = fen is not None and fen != self._fen.get(code)
        changed = status != self._status.get(code)
        self._status[code] = status
        if fen is not None:
            self._fen[code] = fen
        if code == self.current:
            if moved:
                self.show_board(code)
            elif changed:
                self.say(f"  {status}")
        elif changed and status in ("your move", "check! your move"):
            self.say(
                f"** Game {code}: your move against {game['opponent']} (play {code})"
            )
        elif changed and status.startswith("game over"):
            self.say(f"** Game {code} against {game['opponent']}: {status}")

    def _print_errors(self, snapshot: dict) -> None:
        pending_new = snapshot.get("pending_new") or {}
        if pending_new.get("error"):
            key = f"new:{pending_new.get('since')}"
            if key not in self._shown_errors:
                self._shown_errors.add(key)
                self._awaiting_opponent = None
                self.say(f"!! {pending_new['error']}")
        resume_error = snapshot.get("resume_error")
        if resume_error:
            key = f"resume:{resume_error['code']}:{resume_error['error']}"
            if key not in self._shown_errors:
                self._shown_errors.add(key)
                self.say(f"!! Game {resume_error['code']}: {resume_error['error']}")
                if self.current == resume_error["code"]:
                    self.current = None

    # -- commands ---------------------------------------------------------

    def handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        words = line.split()
        with self._ui_lock:
            try:
                self._dispatch(words[0].lower(), words[1:], words)
            except ValueError as ex:
                self.say(f"!! {ex}")
        if not self.running:
            return
        self.drain_updates()

    def _code(self, args: list[str]) -> str:
        if args and protocol.is_game_code(args[0]):
            return args[0]
        if self.current:
            return self.current
        raise ValueError("Which game? Add its code, or pick one with play <code>")

    def _dispatch(self, command: str, args: list[str], words: list[str]) -> None:
        manager = self.manager
        if command in ("quit", "exit", "bye"):
            self._quit()
        elif command in ("help", "?"):
            self.say(HELP)
        elif command == "new":
            if len(args) == 1:
                mycall, theircall = manager.player, args[0]
            elif len(args) == 2:
                mycall, theircall = args
            else:
                raise ValueError("Usage: new <theircall-SSID>, for example new W1AW-7")
            manager.start_game(mycall, theircall)
            self._awaiting_opponent = protocol.base_call(theircall)
        elif command in ("load", "resume"):
            if not args:
                raise ValueError("Usage: load <code>")
            manager.resume(args[0])
            self.current = args[0]
            self._fen.pop(args[0], None)
        elif command in ("play", "switch"):
            code = self._code(args)
            game = self._game(code)
            if not game or not game.get("color"):
                raise ValueError(f"Game {code} isn't loaded here; try load {code}")
            self.current = code
            self.show_board()
        elif command in ("games", "list"):
            self._list_games()
        elif command == "refresh":
            manager.refresh_list()
        elif command in ("board", "show"):
            self.show_board(self._code(args))
        elif command == "flip":
            self.flipped = not self.flipped
            self.show_board()
        elif command == "moves":
            game = self._game(self._code(args))
            self.say(move_list(game) if game else "No such game")
        elif command in ("sync", "resync"):
            manager.sync(self._code(args))
        elif command == "retry":
            manager.retry(self._code(args))
        elif command == "draw":
            manager.draw(self._code(args))
        elif command == "resign":
            manager.resign(self._code(args))
        elif command == "forget":
            code = self._code(args)
            manager.forget(code)
            if self.current == code:
                self.current = None
        elif command == "traffic":
            self.show_traffic = not self.show_traffic
            self.say(f"APRS traffic display {'on' if self.show_traffic else 'off'}")
        elif command == "log":
            for entry in manager.log[-15:]:
                arrow = ">>" if entry["dir"] == "tx" else "<<"
                self.say(f"  {arrow} {entry['text']}")
        elif command == "ascii":
            self.use_unicode = not self.use_unicode
            self.show_board()
        elif command == "move" and args:
            self._move("".join(args))
        elif is_move_like("".join(words)):
            # Keep the case as typed: Bxc3 is a bishop, bxc3 a pawn.
            self._move("".join(words))
        else:
            raise ValueError(f"Unknown command {words[0]!r}; type help")

    def _move(self, text: str) -> None:
        game = self._game()
        if not game or not game.get("color"):
            raise ValueError("No game selected; start one with new, or play <code>")
        if not game.get("fen") or game.get("syncing"):
            raise ValueError("That game is still loading from the server")
        move = parse_typed_move(chess.Board(game["fen"]), text)
        self.manager.move(game["code"], move.uci())

    def _list_games(self) -> None:
        games = [g for g in self.manager.snapshot()["games"] if g.get("color")]
        if not games:
            self.say("No games here. Start one with new <theircall-SSID>, or refresh.")
            return
        for game in games:
            marker = ">" if game["code"] == self.current else " "
            self.say(
                f"{marker} {game['code']}  vs {game['opponent']:<7} you {game['color']}"
                f"  move {game.get('ply', 0) // 2 + 1:<4} {describe(game)}"
            )

    def _quit(self) -> None:
        waiting = [
            g["code"] for g in self.manager.snapshot()["games"] if g.get("pending")
        ]
        if waiting and not self._quit_warned:
            self._quit_warned = True
            self.say(
                f"Game {waiting[0]} has a command the server hasn't confirmed yet; "
                "aprsd stops resending it if you leave. Type quit again to leave anyway."
            )
            return
        self.running = False

    # -- main loop --------------------------------------------------------

    def run(self, read_line: Callable[[str], str] = input) -> None:
        """Read commands until quit, printing incoming updates as they arrive."""
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                try:
                    self._updates.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self.drain_updates()
                except Exception as ex:  # never let a display bug end the game
                    self.say(f"!! display error: {ex}")

        threading.Thread(target=watch, name="ChessTerminal", daemon=True).start()
        self.say(
            f"APRS chess as {self.manager.player}, server "
            f"{self.manager.server_callsign}. Type help for commands."
        )
        if self.current:
            self.show_board()
        try:
            while self.running:
                self._at_prompt = True
                try:
                    line = read_line(PROMPT)
                except EOFError:
                    break
                finally:
                    self._at_prompt = False
                self._last_line = line
                self.handle(line)
        finally:
            stop.set()
            self.manager.unsubscribe(self._updates)
