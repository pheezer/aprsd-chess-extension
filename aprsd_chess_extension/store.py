"""Persistent store of chess games, keyed by 4-digit game code.

This deliberately does not use aprsd's ObjectStoreMixin: through aprsd 4.x it
pickles without an atomic write (a crash mid-save can lose every game), and in
5.x its JSON decoder rewrites keys such as `timestamp`.  This store writes
plain JSON atomically and behaves the same on every aprsd version.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import threading
import time

LOG = logging.getLogger("APRSD")

CODE_MIN = 1000
CODE_MAX = 9999

_SCHEMA_VERSION = 1


def new_game_record(code: str, white: str, black: str, addr: dict, now: float) -> dict:
    return {
        "code": code,
        "white": white,
        "black": black,
        "addr": dict(addr),
        "moves": [],
        "result": None,
        "termination": None,
        "draw_offer": None,
        "joined": False,
        "created": int(now),
        "updated": int(now),
    }


class GameStore:
    """Thread-safe singleton holding every game, saved to one JSON file."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.lock = threading.RLock()
                instance.games = {}
                instance.invites = {}
                instance.path = None
                cls._instance = instance
        return cls._instance

    @classmethod
    def reset(cls):
        """Forget the singleton (tests)."""
        with cls._instance_lock:
            cls._instance = None

    # -- persistence ---------------------------------------------------

    def load(self, path: str) -> None:
        """Load games from `path`.  A missing file starts an empty store."""
        with self.lock:
            self.path = path
            self.games = {}
            self.invites = {}
            if not os.path.exists(path):
                LOG.info(f"Chess: no saved games at {path}, starting fresh")
                return
            try:
                with open(path, encoding="utf-8") as fp:
                    data = json.load(fp)
                games = data.get("games", {})
                if not isinstance(games, dict):
                    raise ValueError("'games' is not an object")
                self.games = {str(k): v for k, v in games.items()}
                self.invites = dict(data.get("invites", {}))
                LOG.info(f"Chess: loaded {len(self.games)} games from {path}")
            except Exception as ex:
                # Keep the unreadable file for inspection instead of
                # overwriting it on the next save.
                aside = f"{path}.corrupt-{int(time.time())}"
                LOG.error(f"Chess: could not read {path} ({ex}); moved to {aside}")
                try:
                    os.replace(path, aside)
                except OSError:
                    LOG.exception("Chess: could not move unreadable game file aside")
                self.games = {}
                self.invites = {}

    def save(self) -> None:
        """Atomically write all games to disk."""
        with self.lock:
            if not self.path:
                return
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            payload = {
                "version": _SCHEMA_VERSION,
                "games": self.games,
                "invites": self.invites,
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=directory, prefix=".chess-games.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fp:
                    json.dump(payload, fp, indent=1, sort_keys=True)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_path, self.path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    # -- queries -------------------------------------------------------

    def get(self, code: str) -> dict | None:
        with self.lock:
            return self.games.get(code)

    def games_for(self, player: str) -> list[dict]:
        """All games `player` (a base callsign) is in, oldest first."""
        with self.lock:
            games = [
                g for g in self.games.values() if player in (g["white"], g["black"])
            ]
        return sorted(games, key=lambda g: g["created"])

    def active_games_for(self, player: str) -> list[dict]:
        return [g for g in self.games_for(player) if g["result"] is None]

    def active_game_between(self, a: str, b: str) -> dict | None:
        for game in self.active_games_for(a):
            if {game["white"], game["black"]} == {a, b}:
                return game
        return None

    def __len__(self):
        with self.lock:
            return len(self.games)

    # -- mutation ------------------------------------------------------

    def new_code(self, rng: random.Random | None = None) -> str:
        """A random unused game code."""
        rng = rng or random.SystemRandom()
        with self.lock:
            used = set(self.games)
            if len(used) >= CODE_MAX - CODE_MIN + 1:
                raise RuntimeError("no free game codes")
            while True:
                code = str(rng.randint(CODE_MIN, CODE_MAX))
                if code not in used:
                    return code

    def add(self, game: dict) -> None:
        with self.lock:
            self.games[game["code"]] = game

    def delete(self, code: str) -> None:
        with self.lock:
            self.games.pop(code, None)

    def record_invite(self, player: str, now: float) -> None:
        with self.lock:
            stamps = [t for t in self.invites.get(player, []) if now - t < 86400]
            stamps.append(int(now))
            self.invites[player] = stamps

    def invites_today(self, player: str, now: float) -> int:
        with self.lock:
            return sum(1 for t in self.invites.get(player, []) if now - t < 86400)

    def expire(self, max_idle_seconds: float, now: float | None = None) -> list[str]:
        """Delete games idle for longer than `max_idle_seconds`.  Returns codes."""
        now = time.time() if now is None else now
        with self.lock:
            expired = [
                code
                for code, game in self.games.items()
                if now - game.get("updated", 0) > max_idle_seconds
            ]
            for code in expired:
                del self.games[code]
            for player in list(self.invites):
                stamps = [t for t in self.invites[player] if now - t < 86400]
                if stamps:
                    self.invites[player] = stamps
                else:
                    del self.invites[player]
        return expired

    def is_expired(self, game: dict, max_idle_seconds: float, now: float) -> bool:
        return now - game.get("updated", 0) > max_idle_seconds
