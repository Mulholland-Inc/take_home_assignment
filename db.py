"""Local Postgres helper.

Spawns an embedded Postgres on first connect (via `pgserver`, which
downloads a Postgres binary into your user cache on first run).
Data lives in `data/.pg/`, so it survives between runs — delete that
directory to reset.
"""

from __future__ import annotations

from pathlib import Path

import pgserver
import psycopg

_PG_DIR = Path(__file__).resolve().parent / "data" / ".pg"
_server: pgserver.PostgresServer | None = None


def _get_server() -> pgserver.PostgresServer:
    global _server
    if _server is None:
        _PG_DIR.mkdir(parents=True, exist_ok=True)
        _server = pgserver.get_server(str(_PG_DIR))
    return _server


def connect() -> psycopg.Connection:
    """Open a fresh psycopg connection to the local Postgres."""
    return psycopg.connect(_get_server().get_uri())
