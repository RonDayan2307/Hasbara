"""Database migrations placeholder for future schema changes."""

from __future__ import annotations

import sqlite3

from .schema import SCHEMA_VERSION, get_schema_version, init_schema


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run any pending migrations."""
    current = get_schema_version(conn)
    if current == 0:
        init_schema(conn)
        return
    # Future migrations go here as elif blocks
    # if current < 2:
    #     migrate_v1_to_v2(conn)
