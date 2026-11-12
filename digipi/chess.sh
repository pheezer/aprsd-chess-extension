#!/bin/bash
# Installed by aprsd-chess-extension.  Modeled on DigiPi's webchat.sh.

CONF=/home/pi/.config/aprsd/chess.conf
if [ ! -f "$CONF" ]; then
   # ~/.config is tmpfs on DigiPi; fall back to the saved copy.
   CONF=/home/pi/config/aprsd/chess.conf
fi

# The KISS transport needs an APRS-mode direwolf.  If none is running,
# start the 1200 baud TNC, the same as WebChat does.
if grep -q -E '^\s*enabled\s*=\s*true' <(sed -n '/^\[kiss_tcp\]/,/^\[/p' "$CONF"); then
   if ! ( systemctl is-active tnc300b --quiet || systemctl is-active tnc --quiet || systemctl is-active tracker --quiet || systemctl is-active digipeater --quiet ); then
      sudo systemctl start tnc
      sleep 2
   fi
fi

source ~/.aprsd-venv/bin/activate

# For longer loglines in journalctl -fu chess output
export COLUMNS=200

exec aprsd chess web -c "$CONF" --port @PORT@ --loglevel ERROR
