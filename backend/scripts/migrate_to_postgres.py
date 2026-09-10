"""
Copy a local SQLite store into Postgres.

    export DATABASE_URL=postgres://...
    python scripts/migrate_to_postgres.py --source vimaan.db

Copies bronze, silver, gold and the weights, in dependency order, preserving
the bronze snapshot ids that silver rows point at — provenance that broke in
transit would defeat the purpose of having it.

Idempotent: every table is copied with the same conflict handling the
pipeline uses, so re-running tops up rather than duplicating.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vimaan import db                                       # noqa: E402
from vimaan.pgcompat import PgConnection, is_postgres_dsn   # noqa: E402

#: Copied in this order so foreign keys and id references stay valid.
TABLES: tuple[tuple[str, str], ...] = (
    ("route_weight", "INSERT OR REPLACE"),
    ("bronze_snapshot", "INSERT OR IGNORE"),
    ("silver_fare", "INSERT OR IGNORE"),
    ("gold_surface", "INSERT OR REPLACE"),
    ("gold_route_index", "INSERT OR REPLACE"),
    ("gold_index", "INSERT OR REPLACE"),
    ("gold_model", "INSERT OR REPLACE"),
)

BATCH = 500


def copy_table(src: sqlite3.Connection, dst, table: str, verb: str) -> int:
    cols = [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]
    if not cols:
        return 0
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0

    placeholders = ",".join("?" for _ in cols)
    sql = f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"

    written = 0
    for i in range(0, len(rows), BATCH):
        chunk = [tuple(r[c] for c in cols) for r in rows[i:i + BATCH]]
        dst.executemany(sql, chunk)
        dst.commit()
        written += len(chunk)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=db.DEFAULT_PATH)
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL", ""))
    args = ap.parse_args()

    if not is_postgres_dsn(args.target):
        raise SystemExit(
            "target must be a Postgres DSN; set DATABASE_URL or pass --target")
    if not os.path.exists(args.source):
        raise SystemExit(f"no SQLite database at {args.source}")

    src = sqlite3.connect(args.source)
    src.row_factory = sqlite3.Row

    dst = PgConnection(args.target)
    dst.executescript(db.SCHEMA)
    dst.commit()
    db._migrate(dst)
    dst.commit()

    total = 0
    for table, verb in TABLES:
        n = copy_table(src, dst, table, verb)
        total += n
        print(f"  {table:<20} {n:>8,} rows")

    # bronze ids are preserved above, but Postgres' sequence does not know
    # that; without this the next insert collides with a copied id
    dst.execute(
        """SELECT setval(pg_get_serial_sequence('bronze_snapshot','id'),
                         COALESCE((SELECT MAX(id) FROM bronze_snapshot), 1))""")
    dst.execute(
        """SELECT setval(pg_get_serial_sequence('silver_fare','id'),
                         COALESCE((SELECT MAX(id) FROM silver_fare), 1))""")
    dst.commit()

    print(f"\n{total:,} rows copied")
    print("target stats:", db.stats(dst))
    src.close()
    dst.close()


if __name__ == "__main__":
    main()
