"""Job bookkeeping and freshness assertions.

v1's results fetcher ran green on schedule for five months, printed
"0 rows updated", exited 0, and wrote nothing - because its entry point read
gitignored CSVs that don't exist in CI. Nothing noticed. Every job here opens a
job_runs row, closes it ok/failed, and treats "wrote nothing when something was
expected" as an error rather than a default.
"""
from __future__ import annotations

import json
import traceback
from contextlib import contextmanager
from typing import Optional

import psycopg

from v2.lib.config import DATABASE_URL


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(DATABASE_URL, connect_timeout=30)


class JobResult:
    def __init__(self):
        self.rows = 0
        self.meta: dict = {}

    def add(self, n: int = 1):
        self.rows += n


@contextmanager
def job(name: str, *, expect_rows: bool = True):
    """Run a job, recording it. Raises if it wrote nothing when it should have.

        with job("ingest-fixtures") as r:
            r.add(len(fixtures))
    """
    result = JobResult()
    with connect() as conn:
        conn.autocommit = True
        run_id = conn.execute(
            "insert into job_runs (job, status) values (%s, 'running') returning id",
            (name,),
        ).fetchone()[0]
    try:
        yield result
        if expect_rows and result.rows == 0:
            raise RuntimeError(
                f"{name} completed without writing any rows. If that is a valid "
                f"outcome, pass expect_rows=False - silence must be deliberate."
            )
    except Exception as exc:
        with connect() as conn:
            conn.autocommit = True
            conn.execute(
                """update job_runs set status='failed', finished_at=now(),
                       rows_affected=%s, error=%s, meta=%s where id=%s""",
                (result.rows, f"{exc}\n{traceback.format_exc()[-2000:]}",
                 json.dumps(result.meta, default=str), run_id),
            )
        print(f"  [{name}] FAILED: {exc}")
        raise
    else:
        with connect() as conn:
            conn.autocommit = True
            conn.execute(
                """update job_runs set status='ok', finished_at=now(),
                       rows_affected=%s, meta=%s where id=%s""",
                (result.rows, json.dumps(result.meta, default=str), run_id),
            )
        print(f"  [{name}] ok, {result.rows} rows")


def assert_fresh(conn, table: str, column: str, max_age_days: int) -> None:
    """Fail loudly when a table has quietly stopped being updated."""
    row = conn.execute(
        f"select max({column}), now() - max({column}) from {table}"
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError(f"{table}.{column} is empty - nothing has ever loaded")
    age_days = row[1].total_seconds() / 86400
    if age_days > max_age_days:
        raise RuntimeError(
            f"{table}.{column} is {age_days:.1f} days old (limit {max_age_days}). "
            f"Newest row: {row[0]}"
        )


def assert_no_unresolved_aliases(conn) -> None:
    rows = conn.execute(
        "select alias, source, seen_count from unresolved_aliases where not reviewed"
    ).fetchall()
    if rows:
        raise RuntimeError(f"{len(rows)} unresolved team aliases: {rows[:10]}")
