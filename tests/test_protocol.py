import chess
import pytest

from aprsd_chess_extension import codec, protocol

# Worst cases: 6-character base calls, 9-character call-SSIDs, long SAN.
LONG = "WB2XYZ"
CODE = "9999"
LONG_SAN = "exd8=Q#"
BIG_PLY = 99999


@pytest.mark.parametrize(
    "call,valid",
    [
        ("W1AW", True),
        ("KK6SPK", True),
        ("KK6SPK-12", True),
        ("kk6spk-7", True),
        ("2E0ABC", True),
        ("VE7ABC-15", True),
        ("N0CALL", False),
        ("NOCALL", False),
        ("NF3", False),
        ("E4", False),
        ("KK6SPK-123", False),
        ("ABCDEFG", False),
        ("1234", False),
        ("", False),
        (None, False),
    ],
)
def test_callsign_validation(call, valid):
    assert protocol.is_valid_callsign(call) is valid


def test_base_call():
    assert protocol.base_call("kk6spk-7") == "KK6SPK"
    assert protocol.base_call("W1AW") == "W1AW"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("chess", protocol.Command("help")),
        ("Chess help", protocol.Command("help")),
        ("chess ?", protocol.Command("help")),
        ("chess list", protocol.Command("list")),
        (
            "chess KK6SPK W1AW-7",
            protocol.Command("new", mycall="KK6SPK", theircall="W1AW-7"),
        ),
        (
            "CHESS kk6spk w1aw",
            protocol.Command("new", mycall="KK6SPK", theircall="W1AW"),
        ),
        ("chess 4821", protocol.Command("status", code="4821")),
        ("chess 4821 sync", protocol.Command("sync", code="4821")),
        ("chess 4821 SYNC", protocol.Command("sync", code="4821")),
        ("chess 4821 board", protocol.Command("board", code="4821")),
        ("chess 4821 resign", protocol.Command("resign", code="4821")),
        ("chess 4821 draw", protocol.Command("draw", code="4821")),
        ("chess 4821 Nf3", protocol.Command("move", code="4821", move="Nf3")),
        (
            "chess 4821 #12 g1f3",
            protocol.Command("move", code="4821", move="g1f3", ply=12),
        ),
        ("chess 4821 e2 e4", protocol.Command("move", code="4821", move="e2e4")),
        ("chess e4", protocol.Command("move", move="e4")),
        ("chess e8Q", protocol.Command("move", move="e8Q")),
        ("chess 4821 what is this", protocol.Command("invalid", code="4821")),
        ("hello", protocol.Command("invalid")),
    ],
)
def test_parse_command(text, expected):
    assert protocol.parse_command(text) == expected


def test_client_commands_round_trip():
    assert protocol.parse_command(
        protocol.cmd_new("KK6SPK-6", "w1aw-7")
    ) == protocol.Command("new", mycall="KK6SPK", theircall="W1AW-7")
    assert protocol.parse_command(
        protocol.cmd_move("4821", 3, "g1f3")
    ) == protocol.Command("move", code="4821", move="g1f3", ply=3)
    assert protocol.parse_command(protocol.cmd_sync("4821")).action == "sync"
    assert protocol.parse_command(protocol.cmd_list()).action == "list"


def _worst_position_code():
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 255 4095")
    return codec.encode(
        board, codec.RESULT_DRAW, codec.DRAW_OFFER_BLACK, chess.Move(0, 63)
    )


WORST_CASE_MESSAGES = [
    *protocol.HELP_LINES,
    protocol.USAGE,
    protocol.LIST_EMPTY,
    protocol.fmt_new_game(CODE, LONG),
    protocol.fmt_invite(CODE, LONG),
    protocol.fmt_move_ok(CODE, BIG_PLY, LONG_SAN, LONG),
    protocol.fmt_move_notice(CODE, BIG_PLY, LONG, LONG_SAN),
    protocol.fmt_game_over(CODE, BIG_PLY, LONG_SAN, "checkmate", "1-0", LONG),
    protocol.fmt_game_over(
        CODE, BIG_PLY, LONG_SAN, "insufficient material", protocol.DRAW, None
    ),
    protocol.fmt_game_over(
        CODE, BIG_PLY, LONG_SAN, "fivefold repetition", protocol.DRAW, None
    ),
    protocol.fmt_resigned(CODE, LONG, LONG, "0-1"),
    protocol.fmt_draw_agreed(CODE),
    protocol.fmt_draw_claimed(CODE, "threefold repetition"),
    protocol.fmt_draw_offer(CODE, LONG),
    protocol.fmt_draw_offered(CODE, LONG),
    protocol.fmt_joined(CODE, LONG),
    protocol.fmt_cancelled(CODE, LONG),
    protocol.fmt_status(CODE, "B", LONG, BIG_PLY, LONG_SAN, False),
    protocol.fmt_status(CODE, "W", LONG, 0, None, True),
    protocol.fmt_status(
        CODE, "W", LONG, 9, "e4", True, protocol.DRAW, "insufficient material"
    ),
    protocol.fmt_state(CODE, LONG, "W", _worst_position_code()),
    protocol.fmt_list_entry(CODE, LONG, "B", BIG_PLY, False),
    protocol.fmt_error("you are not in this game", CODE),
    protocol.fmt_error("x" * 100, CODE),
    *protocol.fmt_board(
        CODE, "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 2 2"
    ),
    *protocol.fmt_board(
        CODE, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
]


@pytest.mark.parametrize("text", WORST_CASE_MESSAGES)
def test_every_message_fits_in_one_aprs_message(text):
    assert protocol.is_aprs_safe(text), (len(text), text)


def test_error_text_from_players_is_sanitized():
    text = protocol.fmt_error("Illegal or unknown move é|~{", "4821")
    assert protocol.is_aprs_safe(text)


def test_board_fits_in_two_messages():
    lines = protocol.fmt_board(
        CODE, "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 2 2"
    )
    assert len(lines) <= 2
    assert (
        "".join(lines).replace(f"{CODE} ", "")
        == "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b"
    )


def test_parse_every_reply_kind():
    position = codec.encode(chess.Board())
    cases = {
        protocol.fmt_state(CODE, LONG, "B", position): (
            "state",
            {"opponent": LONG, "color": "B", "position": position},
        ),
        protocol.fmt_new_game(CODE, LONG): ("new_game", {"call": LONG}),
        protocol.fmt_invite(CODE, LONG): ("invite", {"call": LONG}),
        protocol.fmt_move_ok(CODE, 12, "Nf3", LONG): (
            "move_ok",
            {"ply": 12, "san": "Nf3", "opponent": LONG},
        ),
        protocol.fmt_move_notice(CODE, 12, LONG, "O-O-O+"): (
            "move_notice",
            {"ply": 12, "mover": LONG, "san": "O-O-O+"},
        ),
        protocol.fmt_game_over(CODE, 37, "Qxf7#", "checkmate", "1-0", LONG): (
            "game_over",
            {
                "ply": 37,
                "san": "Qxf7#",
                "reason": "checkmate",
                "winner": LONG,
                "result": "1-0",
            },
        ),
        protocol.fmt_game_over(CODE, 40, "Kb3", "stalemate", protocol.DRAW, None): (
            "game_over",
            {
                "ply": 40,
                "san": "Kb3",
                "reason": "stalemate",
                "winner": None,
                "result": protocol.DRAW,
            },
        ),
        protocol.fmt_list_entry(CODE, LONG, "W", 5, True): (
            "list_entry",
            {"opponent": LONG, "color": "W", "ply": 5, "my_turn": True},
        ),
        protocol.fmt_draw_offer(CODE, LONG): ("draw_offer", {"call": LONG}),
        protocol.fmt_draw_offered(CODE, LONG): ("draw_offered", {"call": LONG}),
        protocol.fmt_joined(CODE, LONG): ("joined", {"call": LONG}),
        protocol.fmt_resigned(CODE, LONG, "W1AW", "0-1"): (
            "resigned",
            {"loser": LONG, "winner": "W1AW", "result": "0-1"},
        ),
        protocol.fmt_draw_agreed(CODE): ("drawn", {"how": "agreed"}),
        protocol.fmt_draw_claimed(CODE, "50-move rule"): (
            "drawn",
            {"how": "by 50-move rule"},
        ),
        protocol.fmt_cancelled(CODE, LONG): ("cancelled", {"by": LONG}),
        protocol.fmt_error("no such game", CODE): (
            "error",
            {"message": "no such game"},
        ),
    }
    for text, (kind, fields) in cases.items():
        reply = protocol.parse_reply(text)
        assert reply.kind == kind, text
        assert reply.code == CODE, text
        assert reply.fields == fields, text


def test_parse_status_replies():
    reply = protocol.parse_reply(
        protocol.fmt_status(CODE, "B", LONG, 7, "Qxf7#", False)
    )
    assert reply.kind == "status"
    assert reply.fields == {
        "color": "B",
        "opponent": LONG,
        "ply": 7,
        "san": "Qxf7#",
        "my_turn": False,
        "result": "*",
    }
    reply = protocol.parse_reply(protocol.fmt_status(CODE, "W", LONG, 0, None, True))
    assert reply.fields["ply"] == 0 and reply.fields["my_turn"] is True
    reply = protocol.parse_reply(
        protocol.fmt_status(CODE, "W", LONG, 9, "e4", True, "0-1", "resignation")
    )
    assert reply.fields["result"] == "0-1"
    assert reply.fields["termination"] == "resignation"


def test_parse_errors_and_other():
    assert (
        protocol.parse_reply("Error: unknown command. Send: chess help").kind == "error"
    )
    assert protocol.parse_reply(protocol.LIST_EMPTY).kind == "list_empty"
    assert protocol.parse_reply("Unknown command!").kind == "other"


def test_wrap_lines_loses_nothing():
    text = "word " * 40 + "x" * 80
    lines = protocol.wrap_lines(text, 20)
    assert all(len(line) <= 20 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
