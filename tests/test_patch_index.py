import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "patch_index", ROOT / "digipi" / "patch_index.py"
)
patch_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_index)

# A cut-down DigiPi index.php with the same WebChat structure as DigiPi 2.2-1.
FIXTURE = """<?php include 'header.php' ?>
<?php
if (isset($_POST["webchat"])) {
  $submit = $_POST["webchat"];
  if ( $submit == 'on' ) {
      $output = shell_exec('sudo systemctl start webchat');
  }
  if ( $submit == 'off' ) {
      $output = shell_exec('sudo systemctl stop webchat');
  }
}

if (isset($_POST["tnc300b"])) {
  $submit = $_POST["tnc300b"];
}

echo '<table width="400px">';
$output = shell_exec('sudo systemctl is-active tnc tnc300b digipeater tracker webchat');
$status_list = preg_split("#[\\r\\n]+#", $output);

#-- webchat ----------------------------------------

echo "<tr>";
$output = $status_list[4];
echo '<font size=+1>APRS WebChat</font></td>';
echo "  <input onChange='this.form.submit()' class='switch-input' type='checkbox' name='webchat' value='on'  $checked />";
echo '</td></tr>';

#-- tnc300b ----------------------------------------

echo "<tr>";
echo '</td></tr>';
?>
</table>
<table width=400>
<tr>
  <td colspan="1">
     <a href="/webchat.php" target="webchat" title="APRS Messaging"><strong>Webchat</strong></a>
  </td>
</tr>
</table>
"""


def test_patch_inserts_three_blocks_in_order():
    patched = patch_index.patch(FIXTURE)
    assert patched.count(f"BEGIN {patch_index.MARK}") == 3
    # Handler right after WebChat's handler, before tnc300b's.
    assert patched.index(f"BEGIN {patch_index.MARK} handler") < patched.index(
        '$_POST["tnc300b"]'
    )
    assert patched.index('$_POST["webchat"]') < patched.index('$_POST["chess"]')
    # Row right after WebChat's row, before tnc300b's row.
    row = patched.index("APRS Chess</font>")
    assert patched.index("APRS WebChat") < row < patched.index("#-- tnc300b")
    # Our row checks its own status; the shared status list is untouched.
    assert "systemctl is-active chess" in patched
    assert "is-active tnc tnc300b digipeater tracker webchat')" in patched
    # Link sits in the WebChat link's cell.
    link = patched.index('href="/chess.php"')
    assert (
        patched.index('href="/webchat.php"') < link < patched.index("</table>\n", link)
    )


def test_patch_is_idempotent_and_revertible():
    once = patch_index.patch(FIXTURE)
    assert patch_index.patch(once) == once
    assert patch_index.revert(once) == FIXTURE
    assert patch_index.revert(FIXTURE) == FIXTURE


def test_patch_refuses_an_unknown_layout():
    with pytest.raises(patch_index.PatchError):
        patch_index.patch("<?php echo 'not a digipi'; ?>")


def test_cli_backs_up_writes_chess_php_and_reverts(tmp_path):
    index = tmp_path / "index.php"
    index.write_text(FIXTURE)
    assert patch_index.main([str(index), "--port", "8099"]) == 0
    assert (tmp_path / "index.php.pre-chess").read_text() == FIXTURE
    chess_php = (tmp_path / "chess.php").read_text()
    assert ":8099" in chess_php and ":8056" not in chess_php
    assert patch_index.main([str(index)]) == 0  # second run: no change
    assert patch_index.main([str(index), "--revert"]) == 0
    assert index.read_text() == FIXTURE
    assert not (tmp_path / "chess.php").exists()
