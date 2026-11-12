import pytest

from aprsd_chess_extension import compat
from aprsd_chess_extension.client import runtime
from tests.conftest import CONF


@pytest.fixture
def client_conf(tmp_path):
    overrides = [
        ("callsign", "KK6SPK-6", None),
        ("save_location", str(tmp_path), None),
        ("server_callsign", "N0SRV-12", "aprsd_chess_client"),
        ("enabled", False, "aprs_network"),
    ]
    for name, value, group in overrides:
        CONF.set_override(name, value, group=group)
    yield CONF
    for name, _value, group in overrides:
        CONF.clear_override(name, group=group)
    for name in ("player_callsign", "cache_file"):
        CONF.clear_override(name, group="aprsd_chess_client")
    CONF.clear_override("password", group="aprs_network")
    if "login" in CONF.aprs_network:
        CONF.clear_override("login", group="aprs_network")


def test_settings_from_config(client_conf, tmp_path):
    settings = runtime.resolve_settings()
    assert settings.station == "KK6SPK-6"
    assert settings.player == "KK6SPK"
    assert settings.server_callsign == "N0SRV-12"
    assert settings.cache_file == str(tmp_path / runtime.CACHE_FILE_NAME)
    assert runtime.resolve_settings("w1aw-12").server_callsign == "W1AW-12"


def test_settings_problems_are_explained(client_conf):
    CONF.set_override("server_callsign", None, group="aprsd_chess_client")
    with pytest.raises(runtime.SetupError, match="server_callsign"):
        runtime.resolve_settings()
    with pytest.raises(runtime.SetupError, match="server's own callsign"):
        runtime.resolve_settings("KK6SPK-6")
    CONF.set_override("callsign", "NOCALL")
    with pytest.raises(runtime.SetupError, match="not a callsign"):
        runtime.resolve_settings("N0SRV-12")


def test_aprsis_needs_a_passcode(client_conf):
    CONF.set_override("enabled", True, group="aprs_network")
    CONF.set_override("password", None, group="aprs_network")
    with pytest.raises(runtime.SetupError, match="passcode"):
        runtime._check_aprsis_login("KK6SPK-6")


def test_aprsis_login_must_match_on_aprsd_4(client_conf):
    CONF.set_override("enabled", True, group="aprs_network")
    CONF.set_override("password", "12345", group="aprs_network")
    if "login" not in CONF.aprs_network:
        # aprsd 5.x logs in as [DEFAULT] callsign, so there's nothing to check.
        runtime._check_aprsis_login("KK6SPK-6")
        return
    CONF.set_override("login", "KK6SPK", group="aprs_network")
    with pytest.raises(runtime.SetupError, match="login = KK6SPK-6"):
        runtime._check_aprsis_login("KK6SPK-6")
    CONF.set_override("login", "kk6spk-6", group="aprs_network")
    runtime._check_aprsis_login("KK6SPK-6")


class PropertyClient:
    """aprsd 4.2.x: login_success / login_failure are properties."""

    login_success = True
    login_failure = None


class MethodClient:
    """aprsd 5.x: they're methods."""

    def login_success(self):
        return False

    def login_failure(self):
        return "bad passcode"


def test_login_status_on_either_aprsd():
    assert compat.login_success(PropertyClient()) is True
    assert compat.login_failure(PropertyClient()) is None
    assert compat.login_success(MethodClient()) is False
    assert compat.login_failure(MethodClient()) == "bad passcode"
