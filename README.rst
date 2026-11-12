=====================
aprsd-chess-extension
=====================

Play chess over APRS radio.

* **Server:** an `APRSD <https://github.com/craigerl/aprsd>`_ plugin that hosts
  games between callsigns. Each game has a 4-digit code that either player can
  use to reattach, and a game is deleted after 10 days with no activity.
* **Client:** ``aprsd chess``, a graphical web chessboard. On a
  `DigiPi <https://digipi.org>`_ it sits next to APRS WebChat as its own toggle.

The whole board fits in one APRS message, so a client that has lost
everything can resume a game from its code with one short exchange: about 2.3
seconds at 1200 baud on a direct path. See ``docs/board-state-encoding.pdf``
for how the position is packed.

.. contents::
   :local:

Playing from any APRS radio
===========================

Send messages to the server's callsign. Commands are not case-sensitive.

======================================  ============================================
Send                                    What happens
======================================  ============================================
``chess KK6SPK W1AW-7``                 Start a game against W1AW-7. You play White
                                        and can move straight away. W1AW-7 gets an
                                        invite with the game code.
``chess 4821 Nf3``                      Play a move: ``Nf3``, ``exd5``, ``O-O``,
                                        ``e8=Q`` or ``e8Q``, or UCI like ``g1f3``.
``chess Nf3``                           The same, when you have only one game.
``chess 4821``                          Status: whose move it is, and the last move.
                                        The invited player joins this way, or by
                                        moving.
``chess 4821 draw``                     Offer a draw, accept one, or claim a draw
                                        by repetition or the 50-move rule.
``chess 4821 resign``                   Resign, or cancel a game that was never
                                        joined.
``chess 4821 board``                    The piece layout as FEN, in at most 2
                                        messages.
``chess 4821 sync``                     The one-message state line the web client
                                        uses to restore a board.
``chess list``                          Your unfinished games.
``chess help``                          The command summary.
======================================  ============================================

Things to know:

* Use your opponent's **exact callsign and SSID** (``W1AW-7``). APRS delivers a
  message only to the exact SSID it's addressed to.
* Games belong to your base callsign. You can start a game from one SSID (the
  DigiPi's ``-6``) and keep playing from another (your HT's ``-7``). Notices
  go to the SSID you used last.
* Anyone can see a game code. The server only lets the game's two players
  act on it.
* Every reply starts with the game code, so you can keep several games going.

Running the server plugin
=========================

Install the package into the same environment as ``aprsd`` (it works with
aprsd 4.1.2 and newer), and enable the plugin::

    pip install git+https://github.com/pheezer/aprsd-chess-extension.git

.. code-block:: ini

    [DEFAULT]
    callsign = KK6SPK-12
    enabled_plugins = aprsd_chess_extension.plugin.ChessPlugin

    [aprsd_chess]
    # All optional; these are the defaults.
    game_expire_days = 10
    max_games_per_player = 5
    max_invites_per_day = 10
    # data_file = <save_location>/aprsd_chess_games.json
    aprsis_filter = true

The plugin answers messages that start with ``chess``. No built-in aprsd
plugin starts with "c", so they don't answer as well. On APRS-IS, the plugin
adds a ``g/<callsign>`` filter so every message addressed to the server
reaches it. Games are saved after every change, written atomically.

``radio-service/aprsd-chess`` in the vinnward-cluster repository runs this
plugin on k3s.

Running the web client
======================

``aprsd chess`` needs aprsd 4.2 or newer. Give it its own callsign or SSID,
not the server's, and point it at the server:

.. code-block:: ini

    [DEFAULT]
    callsign = KK6SPK-6

    [aprsd_chess_client]
    server_callsign = KK6SPK-12
    # player_callsign defaults to the base of [DEFAULT] callsign
    web_port = 8056

Then run ``aprsd chess -c chess.conf`` and open ``http://localhost:8056/``.

* **Start a game** with your callsign and your opponent's callsign-SSID.
* **Resume** a game by entering its code. The board comes back from a single
  state message.
* Moves you make show as "sent" until the server confirms them. If the server
  never answers, a **Retry** button appears. A retry is never played twice.

The board is `chessground <https://github.com/lichess-org/chessground>`_,
bundled so it works with no internet connection.

On a DigiPi
-----------

On the DigiPi, as the ``pi`` user, from a checkout of this repository::

    ./digipi/install.sh --server KK6SPK-12            # --ssid 6 --port 8056 --transport kiss

This installs into WebChat's venv without upgrading WebChat's packages. It
writes ``chess.conf`` using your DigiPi callsign with SSID ``-6``, adds a
``chess`` systemd service, and adds an **APRS Chess** toggle and a **Chess**
link to the DigiPi page. Reboot afterwards to make the filesystem read-only
again. ``./digipi/uninstall.sh`` removes everything.

With ``--transport kiss`` (the default), the client talks to DigiPi's direwolf,
just like WebChat. Messages go over RF and depend on igates in range to reach
the server and come back. ``--transport aprsis`` uses the internet instead.
The modes that take over the radio (Winlink, ARDOP, FT8, and so on) stop
chess, the same as they stop WebChat.

How a board fits in one packet
==============================

APRS message text is at most 67 characters. FEN takes 56 to about 90
characters, and a move list grows by about 5 characters per move. Neither
fits reliably.

The position code packs the board into at most 230 bits:

* a 64-bit map of the occupied squares
* 4 bits per piece
* the move number, halfmove clock and last move

Side to move, castling rights and en passant cost nothing extra. They are
stored as special piece codes: the black king when Black is to move, a rook
that can still castle, and a pawn that can be taken en passant.

The bits are written in Crockford base32, which gives at most 46 characters.
The alphabet has no I, L, O or U, so a code can never trip aprsd's word
filter. The resume reply::

    3135 S N0AAA B 83BWVC1049RQWPACHG010024G0CSM7CY36D6MV02R5WWR

is 60 characters and about 1 second of airtime at 1200 baud. It holds the
exact position: every piece, side to move, castling, en passant, both clocks,
the last move, the result and any draw offer. The server keeps the full move
history for repetition draws.

``docs/board-state-encoding.pdf`` decodes this example bit by bit.
``docs/samples/*.mp3`` is that exchange as real 1200-baud AFSK audio, which
direwolf's ``atest`` decodes.

Development
===========

::

    uv venv && uv pip install -e ".[dev]"
    .venv/bin/pytest
    tox -e aprsd412   # the plugin on aprsd 4.1.2 / Python 3.11 (the cluster image)

``tools/kiss_hub.py`` is a fake radio channel for end-to-end tests without
transmitting. Point several aprsd instances' ``[kiss_tcp]`` at it. With
``--baud 1200``, frames take real airtime and ``--record`` logs each one.

You can try the plugin without a radio by using aprsd's fake client
(``[fake_client] enabled = true``)::

    aprsd dev test-plugin -c test.conf --aprs-login KK6SPK-6 \
        -p aprsd_chess_extension.plugin.ChessPlugin "chess KK6SPK W1AW-7"

The command doesn't exit by itself after a move or a new game, because those
send a message to the other player, which starts aprsd's send-scheduler thread.
Press Ctrl+C once the reply is printed.

License
=======

GPL-3.0-or-later. python-chess and the bundled chessground are both GPL-3.0.
