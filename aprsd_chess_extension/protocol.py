"""The APRS message formats spoken between players, clients and the server.

Everything here is plain text that must fit in one APRS message: at most 67
characters, printable ASCII, and none of the characters ``| ~ {`` that APRS
reserves.  The server's reply formatters and the web client's parsers live
side by side so the two can't drift apart.
"""

from __future__ import annotations

import dataclasses
import re

MAX_MSG_LEN = 67

# Characters APRS does not allow in message text.
FORBIDDEN_CHARS = "|~{"

DRAW = "1/2-1/2"

HELP_LINES = [
    "Chess: send chess <yourcall> <theircall-SSID> to start a game",
    "Then: chess <code> <move> (e.g. chess 4821 Nf3) or chess <code>",
    "Also: chess <code> resign/draw/board/sync, chess list",
]

USAGE = "Send: chess help"

# --------------------------------------------------------------------------
# Callsigns
# --------------------------------------------------------------------------

# A base callsign: prefix, a digit, and a suffix that ends in a letter
# (W1AW, KK6SPK, 2E0ABC, 3DA0AB).  Requiring the final letter keeps most
# chess moves (Nf3, exd5) from looking like callsigns.
_BASE_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z]$")
_SSID_RE = re.compile(r"^[A-Z0-9]{1,2}$")
_INVALID_CALLS = {"NOCALL", "N0CALL"}

GAME_CODE_RE = re.compile(r"^\d{4}$")


def normalize_call(call: str | None) -> str:
    return (call or "").strip().upper()


def base_call(call: str | None) -> str:
    """The callsign without its SSID: KK6SPK-7 -> KK6SPK."""
    return normalize_call(call).split("-", 1)[0]


def is_valid_callsign(call: str | None) -> bool:
    """True for a plausible amateur callsign with an optional -SSID."""
    call = normalize_call(call)
    base, sep, ssid = call.partition("-")
    if not 3 <= len(base) <= 6 or not _BASE_RE.match(base):
        return False
    if sep and not _SSID_RE.match(ssid):
        return False
    return base not in _INVALID_CALLS


def is_game_code(text: str | None) -> bool:
    return bool(GAME_CODE_RE.match(text or ""))


def is_aprs_safe(text: str) -> bool:
    """True if `text` can be sent unchanged as APRS message text."""
    return (
        len(text) <= MAX_MSG_LEN
        and text.isascii()
        and text.isprintable()
        and not any(c in text for c in FORBIDDEN_CHARS)
    )


def wrap_lines(text: str, width: int = MAX_MSG_LEN) -> list[str]:
    """Split text into <= width chunks at word boundaries, losing nothing."""
    text = re.sub(r"\s+", " ", text or "").strip()
    chunks: list[str] = []
    current = ""
    for word in text.split(" ") if text else []:
        while len(word) > width:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


def _fit(text: str) -> str:
    """Make text safe to send as one APRS message.

    Replies can echo what a player typed, so anything APRS can't carry is
    replaced (a non-ASCII character would make aprsd's KISS driver fail the
    send and retry forever) and the text is clamped to one message.
    """
    text = "".join(
        "/"
        if ch in FORBIDDEN_CHARS
        else ch
        if ch.isascii() and ch.isprintable()
        else "?"
        for ch in text
    )
    return text[:MAX_MSG_LEN]


# --------------------------------------------------------------------------
# Commands: what players and clients send to the server
# --------------------------------------------------------------------------

_GAME_ACTIONS = {"sync", "board", "resign", "draw", "status", "help"}
_PLY_RE = re.compile(r"^#(\d{1,5})$")
_SQUARE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)
_SQUARE_PROMO_RE = re.compile(r"^[a-h][1-8][qrbn]?$", re.IGNORECASE)


@dataclasses.dataclass
class Command:
    """A parsed `chess ...` message."""

    action: str  # help list new status sync board move resign draw invalid
    code: str | None = None
    move: str | None = None
    ply: int | None = None
    mycall: str | None = None
    theircall: str | None = None


def parse_command(text: str) -> Command:
    """Parse the text of a message that starts with `chess`."""
    tokens = (text or "").split()
    if not tokens or tokens[0].lower() != "chess":
        return Command("invalid")
    args = tokens[1:]
    if not args or args[0].lower() in ("help", "?"):
        return Command("help")
    if len(args) == 1 and args[0].lower() == "list":
        return Command("list")

    code = None
    if is_game_code(args[0]):
        code = args[0]
        args = args[1:]
        if not args:
            return Command("status", code=code)
    elif len(args) == 2 and is_valid_callsign(args[0]) and is_valid_callsign(args[1]):
        return Command(
            "new", mycall=normalize_call(args[0]), theircall=normalize_call(args[1])
        )

    word = args[0].lower()
    if len(args) == 1 and word in _GAME_ACTIONS:
        if word == "help":
            return Command("help")
        return Command(word, code=code)

    ply = None
    match = _PLY_RE.match(args[0])
    if match:
        ply = int(match.group(1))
        args = args[1:]

    if len(args) == 1:
        return Command("move", code=code, move=args[0], ply=ply)
    # "e2 e4" -> "e2e4"
    if len(args) == 2 and _SQUARE_RE.match(args[0]) and _SQUARE_PROMO_RE.match(args[1]):
        return Command("move", code=code, move=(args[0] + args[1]).lower(), ply=ply)
    return Command("invalid", code=code)


def cmd_new(mycall: str, theircall: str) -> str:
    return f"chess {base_call(mycall)} {normalize_call(theircall)}"


def cmd_sync(code: str) -> str:
    return f"chess {code} sync"


def cmd_move(code: str, ply: int, uci: str) -> str:
    return f"chess {code} #{ply} {uci}"


def cmd_resign(code: str) -> str:
    return f"chess {code} resign"


def cmd_draw(code: str) -> str:
    return f"chess {code} draw"


def cmd_list() -> str:
    return "chess list"


# --------------------------------------------------------------------------
# Replies: what the server sends back
# --------------------------------------------------------------------------


def color_letter(is_white: bool) -> str:
    return "W" if is_white else "B"


def fmt_new_game(code: str, opponent: str) -> str:
    return f"{code} New game vs {opponent}. You are White, your move"


def fmt_invite(code: str, challenger: str) -> str:
    return f"{code} {challenger} challenges you to chess! Reply: chess {code}"


def fmt_move_ok(code: str, ply: int, san: str, opponent: str) -> str:
    return f"{code} #{ply} {san} ok. {opponent} to move"


def fmt_move_notice(code: str, ply: int, mover: str, san: str) -> str:
    return f"{code} #{ply} {mover}: {san}. Your move"


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def fmt_game_over(
    code: str, ply: int, san: str, reason: str, result: str, winner: str | None
) -> str:
    outcome = f"{winner} wins {result}" if winner else f"Draw {result}"
    return f"{code} #{ply} {san} {_capitalize(reason)}. {outcome}"


def fmt_resigned(code: str, loser: str, winner: str, result: str) -> str:
    return f"{code} {loser} resigned. {winner} wins {result}"


def fmt_draw_agreed(code: str) -> str:
    return f"{code} Draw agreed {DRAW}"


def fmt_draw_claimed(code: str, reason: str) -> str:
    return f"{code} Draw by {reason} {DRAW}"


def fmt_draw_offer(code: str, offerer: str) -> str:
    return f"{code} {offerer} offers a draw. Reply: chess {code} draw"


def fmt_draw_offered(code: str, opponent: str) -> str:
    return f"{code} Draw offered to {opponent}"


def fmt_joined(code: str, player: str) -> str:
    return f"{code} {player} joined the game"


def fmt_cancelled(code: str, by: str) -> str:
    return f"{code} Game cancelled by {by}"


def fmt_status(
    code: str,
    color: str,
    opponent: str,
    ply: int,
    last_san: str | None,
    my_turn: bool,
    result: str = "*",
    termination: str | None = None,
) -> str:
    head = f"{code} You({color}) vs {opponent}."
    if result != "*":
        return _fit(f"{head} Game over {result} ({termination or 'ended'})")
    turn = "Your move" if my_turn else f"{opponent} to move"
    if ply and last_san:
        return f"{head} Last: #{ply} {last_san}. {turn}"
    return f"{head} No moves yet. {turn}"


def fmt_state(code: str, opponent: str, color: str, position_code: str) -> str:
    return f"{code} S {opponent} {color} {position_code}"


def fmt_list_entry(
    code: str, opponent: str, color: str, ply: int, my_turn: bool
) -> str:
    turn = "your move" if my_turn else "their move"
    return f"{code} vs {opponent}, you {color}, #{ply}, {turn}"


LIST_EMPTY = "No active games. Start one: chess <yourcall> <theircall>"


def fmt_board(code: str, fen: str) -> list[str]:
    """The piece placement (FEN ranks 8..1) and side to move, in <= 2 messages."""
    placement, turn = fen.split()[:2]
    lines: list[str] = []
    current = f"{code} "
    for i, rank in enumerate(placement.split("/")):
        piece = rank if i == 0 else f"/{rank}"
        if len(current) + len(piece) > MAX_MSG_LEN:
            lines.append(current)
            current = rank if i else ""
        else:
            current += piece
    if len(current) + 2 > MAX_MSG_LEN:
        lines.append(current)
        current = turn
    else:
        current += f" {turn}"
    lines.append(current)
    return lines


def fmt_error(message: str, code: str | None = None) -> str:
    prefix = f"{code} Error: " if code else "Error: "
    return _fit(prefix + message)


# --------------------------------------------------------------------------
# Reply parsing (used by the web client)
# --------------------------------------------------------------------------

_C = r"(\d{4})"
_CALL = r"([A-Z0-9]{3,6})"
_RES = r"(1-0|0-1|1/2-1/2)"

_REPLY_PATTERNS = [
    ("error", re.compile(r"^(?:(\d{4}) )?Error: (?P<message>.+)$")),
    ("state", re.compile(rf"^{_C} S {_CALL} ([WB]) (?P<position>[0-9A-Z]+)$")),
    ("new_game", re.compile(rf"^{_C} New game vs {_CALL}\. You are White, your move$")),
    (
        "invite",
        re.compile(rf"^{_C} {_CALL} challenges you to chess! Reply: chess \d{{4}}$"),
    ),
    ("move_ok", re.compile(rf"^{_C} #(\d+) (\S+) ok\. {_CALL} to move$")),
    ("move_notice", re.compile(rf"^{_C} #(\d+) {_CALL}: (\S+)\. Your move$")),
    (
        "game_over",
        re.compile(
            rf"^{_C} #(\d+) (\S+) (?P<reason>.+?)\. (?:{_CALL} wins|Draw) {_RES}$"
        ),
    ),
    ("status", re.compile(rf"^{_C} You\(([WB])\) vs {_CALL}\. (?P<rest>.+)$")),
    (
        "list_entry",
        re.compile(rf"^{_C} vs {_CALL}, you ([WB]), #(\d+), (your|their) move$"),
    ),
    (
        "draw_offer",
        re.compile(rf"^{_C} {_CALL} offers a draw\. Reply: chess \d{{4}} draw$"),
    ),
    ("draw_offered", re.compile(rf"^{_C} Draw offered to {_CALL}$")),
    ("joined", re.compile(rf"^{_C} {_CALL} joined the game$")),
    ("resigned", re.compile(rf"^{_C} {_CALL} resigned\. {_CALL} wins {_RES}$")),
    ("drawn", re.compile(rf"^{_C} Draw (?P<how>agreed|by .+) 1/2-1/2$")),
    ("cancelled", re.compile(rf"^{_C} Game cancelled by {_CALL}$")),
    ("list_empty", re.compile(r"^(No) active games\. Start one: .+$")),
]

_STATUS_LAST_RE = re.compile(r"^Last: #(\d+) (\S+)\. (.+)$")
_STATUS_OVER_RE = re.compile(rf"^Game over {_RES} \((.+)\)$")


@dataclasses.dataclass
class Reply:
    kind: str
    code: str | None = None
    fields: dict = dataclasses.field(default_factory=dict)
    text: str = ""


def parse_reply(text: str) -> Reply:
    """Classify a message received from the server."""
    text = (text or "").strip()
    for kind, pattern in _REPLY_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        code = groups[0]
        fields: dict = {}
        if kind == "error":
            fields = {"message": match.group("message")}
        elif kind == "state":
            fields = {"opponent": groups[1], "color": groups[2], "position": groups[3]}
        elif kind in ("new_game", "invite", "draw_offered", "joined", "draw_offer"):
            fields = {"call": groups[1]}
        elif kind == "move_ok":
            fields = {"ply": int(groups[1]), "san": groups[2], "opponent": groups[3]}
        elif kind == "move_notice":
            fields = {"ply": int(groups[1]), "mover": groups[2], "san": groups[3]}
        elif kind == "game_over":
            fields = {
                "ply": int(groups[1]),
                "san": groups[2],
                "reason": match.group("reason").lower(),
                "winner": groups[4],
                "result": groups[5],
            }
        elif kind == "status":
            fields = {"color": groups[1], "opponent": groups[2]}
            rest = match.group("rest")
            last = _STATUS_LAST_RE.match(rest)
            over = _STATUS_OVER_RE.match(rest)
            if over:
                fields.update(result=over.group(1), termination=over.group(2), ply=None)
            elif last:
                fields.update(
                    ply=int(last.group(1)),
                    san=last.group(2),
                    my_turn=last.group(3) == "Your move",
                    result="*",
                )
            else:
                fields.update(ply=0, my_turn=rest.endswith("Your move"), result="*")
        elif kind == "list_entry":
            fields = {
                "opponent": groups[1],
                "color": groups[2],
                "ply": int(groups[3]),
                "my_turn": groups[4] == "your",
            }
        elif kind == "resigned":
            fields = {"loser": groups[1], "winner": groups[2], "result": groups[3]}
        elif kind == "drawn":
            fields = {"how": match.group("how")}
        elif kind == "cancelled":
            fields = {"by": groups[1]}
        elif kind == "list_empty":
            code = None
        return Reply(kind=kind, code=code, fields=fields, text=text)
    return Reply(kind="other", text=text)
