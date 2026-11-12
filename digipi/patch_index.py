"""Add (or remove) the APRS Chess toggle and link on a DigiPi's index.php.

    sudo python3 patch_index.py [--port 8056] [/var/www/html/index.php]
    sudo python3 patch_index.py --revert [/var/www/html/index.php]

The edit is idempotent: every inserted block sits between BEGIN/END marker
comments, so running it twice changes nothing and --revert removes exactly
what was added.  A backup is written next to the file before the first patch.

Three things are inserted, anchored on DigiPi's existing WebChat entries:
  1. a POST handler (start/stop the `chess` systemd unit), after WebChat's;
  2. an "APRS Chess" toggle row, after WebChat's row.  It runs its own
     `systemctl is-active chess` so DigiPi's shared status list is untouched;
  3. an "APRS Chess" link, next to the WebChat link.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

DEFAULT_INDEX = "/var/www/html/index.php"
MARK = "aprsd-chess-extension"

HANDLER = f"""#-- BEGIN {MARK} handler

if (isset($_POST["chess"])) {{
  $submit = $_POST["chess"];
  if ( $submit == 'on' ) {{
      $output = shell_exec('sudo systemctl start chess');
  }}
  if ( $submit == 'off' ) {{
      $output = shell_exec('sudo systemctl stop chess');
  }}
}}
#-- END {MARK} handler
"""

ROW = f"""#-- BEGIN {MARK} row

echo "<tr>";
$output = trim(shell_exec('systemctl is-active chess'));
  if ($output == "active")
  {{
     echo '<td bgcolor="#33dd33">';
     $checked = "checked";
  }}
  elseif ($output == "failed")
  {{
     echo '<td bgcolor="red">';
     $checked = "";
  }}
  else
  {{
     echo '<td bgcolor="lightgrey">';
     $checked = "";
  }}
echo '</td>';
echo '<td nowrap bgcolor="#fafafa">';
echo '<font size=+1>APRS Chess</font></td>';
echo '<td nowrap bgcolor="#fafafa">';
echo '<form action="index.php" method="post">';
echo '<label class="switch switch-light">';
echo '  <input type="hidden" name="chess" value="off">';
echo "  <input onChange='this.form.submit()' class='switch-input' type='checkbox' name='chess' value='on'  $checked />";
echo '  <span class="switch-label" ></span> ';
echo '  <span class="switch-handle"></span> ';
echo '</label>';
echo '</form>';
echo '</font>';
echo '</td></tr>';
#-- END {MARK} row
"""

LINK = f"""<!-- BEGIN {MARK} link -->
     <a href="/chess.php" target="chess" title="APRS Chess board"><strong>Chess</strong></a>
<!-- END {MARK} link -->
"""

# Anchors in DigiPi's index.php (checked against DigiPi 2.2-1).
_HANDLER_ANCHOR = re.compile(
    r"if \(isset\(\$_POST\[\"webchat\"\]\)\) \{.*?\n\}\n", re.DOTALL
)
_ROW_ANCHOR = re.compile(r"#-- webchat -+\n.*?echo '</td></tr>';\n", re.DOTALL)
_LINK_ANCHOR = re.compile(r"[ \t]*<a href=\"/webchat\.php\"[^\n]*</a>\n")
_BLOCK_RE = re.compile(
    rf"(?:#-- BEGIN {MARK} (\w+)\n.*?#-- END {MARK} \1\n)"
    rf"|(?:<!-- BEGIN {MARK} (\w+) -->\n.*?<!-- END {MARK} \2 -->\n)",
    re.DOTALL,
)


class PatchError(RuntimeError):
    pass


def is_patched(text: str) -> bool:
    return f"BEGIN {MARK}" in text


def revert(text: str) -> str:
    return _BLOCK_RE.sub("", text)


def patch(text: str) -> str:
    """Return index.php with the chess blocks inserted (idempotent)."""
    text = revert(text)
    for name, anchor, block in (
        ("WebChat POST handler", _HANDLER_ANCHOR, HANDLER),
        ("WebChat status row", _ROW_ANCHOR, ROW),
        ("WebChat link", _LINK_ANCHOR, LINK),
    ):
        match = anchor.search(text)
        if not match:
            raise PatchError(f"couldn't find the {name} in index.php")
        if name == "WebChat link":
            # Put our link right after the webchat anchor tag, inside the same cell.
            insert_at = match.end()
            text = text[:insert_at] + block + text[insert_at:]
        else:
            text = text[: match.end()] + block + text[match.end() :]
    return text


def write_chess_php(directory: Path, port: int) -> Path:
    """The page the Chess link opens: waits for the board, then redirects."""
    template = Path(__file__).with_name("chess.php").read_text()
    path = directory / "chess.php"
    path.write_text(template.replace("8056", str(port)))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("index", nargs="?", default=DEFAULT_INDEX)
    parser.add_argument(
        "--revert", action="store_true", help="remove the chess entries"
    )
    parser.add_argument("--port", type=int, default=8056, help="chess web port")
    args = parser.parse_args(argv)

    index = Path(args.index)
    original = index.read_text()
    if args.revert:
        updated = revert(original)
        chess_php = index.parent / "chess.php"
        if chess_php.exists():
            chess_php.unlink()
    else:
        try:
            updated = patch(original)
        except PatchError as ex:
            print(f"patch_index: {ex}; leaving it unchanged", file=sys.stderr)
            return 1
        backup = index.with_name(index.name + ".pre-chess")
        if not backup.exists() and not is_patched(original):
            shutil.copy2(index, backup)
        write_chess_php(index.parent, args.port)
    if updated != original:
        index.write_text(updated)
        print(f"{'reverted' if args.revert else 'patched'} {index}")
    else:
        print(f"{index} already {'clean' if args.revert else 'patched'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
