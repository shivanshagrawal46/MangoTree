"""Outbound connections: use IPv4.

Measured on 2026-11-12 from the office laptop: Microsoft's sign-in and Graph
servers answer in under half a second over IPv4, and not at all over IPv6 — the
connection attempt hangs for about twenty seconds per address before the
library falls back. ``requests`` (which the Microsoft sign-in library and our
Graph client use) tries IPv6 first and sets no connect timeout during sign-in,
so "Put in Outlook Drafts" took minutes or appeared to hang.

Every service this system calls is reachable over IPv4, so preferring it costs
nothing where IPv6 works and removes the stall where it does not. Set
``MT_ALLOW_IPV6=1`` to leave the default address order alone.
"""
from __future__ import annotations

import os
import socket

_done = False


def prefer_ipv4() -> bool:
    """Make urllib3 (and so ``requests``) resolve hosts to IPv4 addresses only. Safe to call more than once."""
    global _done
    if _done or os.environ.get("MT_ALLOW_IPV6", "").strip() == "1":
        return _done
    try:
        import urllib3.util.connection as conn
        conn.allowed_gai_family = lambda: socket.AF_INET
        _done = True
    except Exception:
        pass
    return _done
