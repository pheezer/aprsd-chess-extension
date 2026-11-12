#!/bin/bash
# Install the APRS Chess web board on a DigiPi, beside WebChat.
#
#   ./install.sh --server KK6SPK-4 [--ssid 6] [--port 8056] [--transport kiss|aprsis]
#
# Run it on the DigiPi, as the pi user, from a checkout of this repository.
# It remounts the filesystem read-write; reboot afterwards to make it
# read-only again.
set -euo pipefail

SERVER=""
SSID=6
PORT=8056
TRANSPORT=kiss
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
VENV=/home/pi/.aprsd-venv
APRSD_CONF=/home/pi/config/aprsd/aprsd.conf
WWW=/var/www/html

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="${2^^}"; shift 2 ;;
    --ssid) SSID="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --transport) TRANSPORT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown option $1"; usage ;;
  esac
done
[ -n "$SERVER" ] || { echo "--server <chess server callsign> is required"; usage; }
case "$TRANSPORT" in kiss|aprsis) ;; *) echo "--transport must be kiss or aprsis"; exit 1 ;; esac
[[ "$SSID" =~ ^[0-9]{1,2}$ ]] && [ "$SSID" -le 15 ] || { echo "--ssid must be 0-15"; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "--port must be a number"; exit 1; }
case "$PORT" in 80|5901|6080|8000|8001|8055|8080|8088) echo "port $PORT is already used on a DigiPi"; exit 1 ;; esac
[ "$(id -un)" = pi ] || { echo "run this as the pi user"; exit 1; }
[ -x "$VENV/bin/aprsd" ] || { echo "no aprsd in $VENV (is this a DigiPi with WebChat?)"; exit 1; }
[ -f "$APRSD_CONF" ] || { echo "missing $APRSD_CONF; run DigiPi's Initialize first"; exit 1; }

conf_value() {  # conf_value <section> <key>
  sed -n "/^\[$1\]/,/^\[/p" "$APRSD_CONF" | sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//p" | head -1
}
BASECALL="$(conf_value DEFAULT callsign | tr '[:lower:]' '[:upper:]')"
BASECALL="${BASECALL%%-*}"
[ -n "$BASECALL" ] && [ "$BASECALL" != KX6XXX ] || { echo "set your callsign with DigiPi's Initialize first"; exit 1; }
CALLSIGN="$BASECALL-$SSID"
[ "$CALLSIGN" != "$SERVER" ] || { echo "the client can't use the server's callsign"; exit 1; }

echo "== Remounting the filesystem read-write"
sudo remount

echo "== Installing aprsd-chess-extension into $VENV"
# Constrain the install to exactly what's already in the venv, so WebChat's
# packages are never upgraded or replaced. This also matters for resolving:
# DigiPi's aprsd 5.0.0 comes from git and pins aprslib to a git commit, and
# uv only accepts such a URL dependency when it's listed explicitly, which a
# full freeze does. (DigiPi's venv is made by uv and has no pip, so use uv's
# freeze when uv is available.)
CONSTRAINTS="$(mktemp)"
trap 'rm -f "$CONSTRAINTS"' EXIT
UV=""
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [ -x /home/pi/.local/bin/uv ]; then
  UV=/home/pi/.local/bin/uv
fi
if [ -n "$UV" ]; then
  "$UV" pip freeze --python "$VENV/bin/python" > "$CONSTRAINTS"
  "$UV" pip install --python "$VENV/bin/python" -c "$CONSTRAINTS" "$REPO"
else
  "$VENV/bin/python" -m pip freeze > "$CONSTRAINTS"
  "$VENV/bin/python" -m pip install -c "$CONSTRAINTS" "$REPO"
fi
"$VENV/bin/aprsd" chess --help >/dev/null || { echo "aprsd chess didn't install correctly"; exit 1; }

echo "== Writing chess.conf for $CALLSIGN (server $SERVER, $TRANSPORT)"
if [ "$TRANSPORT" = kiss ]; then
  KISS_ENABLED=true; APRSIS_ENABLED=false; PASSWORD=""
else
  KISS_ENABLED=false; APRSIS_ENABLED=true; PASSWORD="$(conf_value aprs_network password)"
fi
mkdir -p /home/pi/config/aprsd /home/pi/.config/aprsd/chess
sed -e "s|@CALLSIGN@|$CALLSIGN|" -e "s|@BASECALL@|$BASECALL|" -e "s|@SERVER@|$SERVER|" \
    -e "s|@PORT@|$PORT|" -e "s|@KISS_ENABLED@|$KISS_ENABLED|" \
    -e "s|@APRSIS_ENABLED@|$APRSIS_ENABLED|" -e "s|@APRSIS_PASSWORD@|$PASSWORD|" \
    "$HERE/chess.conf.template" > /home/pi/config/aprsd/chess.conf
chmod 600 /home/pi/config/aprsd/chess.conf
cp /home/pi/config/aprsd/chess.conf /home/pi/.config/aprsd/chess.conf

echo "== Installing the chess service"
sed "s|@PORT@|$PORT|" "$HERE/chess.sh" > /home/pi/chess.sh
chmod 755 /home/pi/chess.sh
sudo install -m 644 "$HERE/chess.service" /etc/systemd/system/chess.service

echo "== Adding APRS Chess to the DigiPi page"
sudo python3 "$HERE/patch_index.py" --port "$PORT" "$WWW/index.php"
sudo chown www-data:www-data "$WWW/chess.php" 2>/dev/null || true

if ! sudo -n -u www-data sudo -n -l /usr/bin/systemctl start chess >/dev/null 2>&1 && \
   ! sudo -n -u www-data sudo -n -l systemctl start chess >/dev/null 2>&1; then
  echo "== Letting the web page start and stop chess"
  RULE="$(mktemp)"
  printf 'www-data ALL=(root) NOPASSWD: /usr/bin/systemctl start chess, /usr/bin/systemctl stop chess, /bin/systemctl start chess, /bin/systemctl stop chess\n' > "$RULE"
  sudo visudo -c -f "$RULE" >/dev/null
  sudo install -m 440 "$RULE" /etc/sudoers.d/aprsd-chess
  rm -f "$RULE"
fi

sudo systemctl daemon-reload

cat <<DONE

APRS Chess is installed.
  - Turn on "APRS Chess" on the DigiPi page, then click the Chess link.
  - The board is at http://<digipi>:$PORT/ and plays as $BASECALL (transmitting as $CALLSIGN).
  - Reboot when you're done to make the filesystem read-only again.
DONE
