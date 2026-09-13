"""Apply SQL migrations in order, once each.

    python -m v2.scripts.migrate [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from v2.lib.config import DATABASE_URL, V2

MIGRATIONS = V2 / "db" / "migrations"


def applied(conn) -> set:
    conn.execute("""
        create table if not exists schema_migrations (
            filename   text primary key,
            applied_at timestamptz not null default now()
        )""")
    return {r[0] for r in conn.execute("select filename from schema_migrations")}


def main() -> int:
    if not DATABASE_URL:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    dry = "--dry-run" in sys.argv
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print("no migrations found", file=sys.stderr)
        return 1

    with psycopg.connect(DATABASE_URL, connect_timeout=30) as conn:
        conn.autocommit = False
        done = applied(conn)
        conn.commit()
        pending = [f for f in files if f.name not in done]
        if not pending:
            print(f"up to date ({len(done)} applied)")
            return 0
        for path in pending:
            print(f"{'would apply' if dry else 'applying'} {path.name} ...", end=" ")
            if dry:
                print("skipped")
                continue
            try:
                conn.execute(path.read_text())
                conn.execute(
                    "insert into schema_migrations (filename) values (%s)", (path.name,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                print("FAILED")
                raise
            print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
