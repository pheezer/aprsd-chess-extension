"""Small shims over the differences between aprsd 4.1.x and 5.x."""

from __future__ import annotations

import logging

LOG = logging.getLogger("APRSD")

TRANSPORT_APRSIS = "aprsis"


def get_client():
    """The connected aprsd client singleton, whichever aprsd this is."""
    try:
        from aprsd.client.client import APRSDClient  # aprsd >= 4.2
    except ImportError:
        from aprsd.client import client_factory  # aprsd 4.1.x

        return client_factory.create()
    return APRSDClient()


def get_transport(client) -> str | None:
    driver = getattr(client, "driver", None)  # aprsd >= 4.2
    source = driver if driver is not None else client
    transport = getattr(source, "transport", None)
    try:
        return transport() if callable(transport) else transport
    except Exception:
        return None


def get_filter(client) -> str | None:
    getter = getattr(client, "get_filter", None)
    try:
        return getter() if getter else getattr(client, "filter", None)
    except Exception:
        return None


def add_aprsis_filter(callsign: str, client=None) -> str | None:
    """Make APRS-IS deliver every message addressed to `callsign`.

    Adds `g/<callsign>` to the client's existing filter.  Does nothing for
    KISS transports.  Returns the filter that was set, or None.
    """
    client = client or get_client()
    if get_transport(client) != TRANSPORT_APRSIS:
        return None
    wanted = f"g/{callsign}"
    current = (get_filter(client) or "").strip()
    if wanted in current.split():
        return current
    new_filter = f"{current} {wanted}".strip()
    client.set_filter(new_filter)
    return new_filter
