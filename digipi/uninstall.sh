#!/bin/bash
# Remove the APRS Chess web board from a DigiPi.  Run as the pi user.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo remount
sudo systemctl stop chess 2>/dev/null || true
sudo rm -f /etc/systemd/system/chess.service /etc/sudoers.d/aprsd-chess
sudo systemctl daemon-reload
sudo python3 "$HERE/patch_index.py" --revert /var/www/html/index.php
rm -f /home/pi/chess.sh /home/pi/config/aprsd/chess.conf /home/pi/.config/aprsd/chess.conf
/home/pi/.aprsd-venv/bin/python -m pip uninstall -y aprsd-chess-extension 2>/dev/null || true
echo "APRS Chess removed. Reboot to make the filesystem read-only again."
