"""
Postgres behind the sqlite3 interface the rest of this package speaks.

db.py's docstring has always claimed that moving off SQLite is "a change of
driver, not a redesign". This module is that claim being made good, and it is
worth being precise about why it is a shim rather than a rewrite: every call
site — pipeline, api, scripts, and 69 tests — takes a `sqlite3.Connection`
and calls `.execute(sql, params)` with `?` placeholders and `row["column"]`
access. Rewriting all of that to a second dialect would mean maintaining two
query languages and reverifying every test against both. Translating at the
boundary means one dialect in the source and one place where the differences
live.

What actually differs, and is handled here:

  ?                       ->  %s
  INSERT OR REPLACE       ->  INSERT ... ON CONFLICT (pk) DO UPDATE SET ...
  INSERT OR IGNORE        ->  INSERT ... ON CONFLICT DO NOTHING
  INTEGER PRIMARY KEY     ->  BIGSERIAL PRIMARY KEY
  BLOB                    ->  BYTEA
  cur.lastrowid           ->  RETURNING id
  PRAGMA table_info(t)    ->  information_schema.columns
  PRAGMA journal_mode/... ->  dropped, they mean nothing here

Not handled, deliberately: anything that would need the SQL itself to change
shape. If a query cannot be expressed in both, it belongs in db.py behind a
branch, not hidden in a regex here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence

#: Primary keys for the tables that rely on INSERT OR REPLACE. Postgres needs
#: to be told which columns the conflict is on; SQLite infers it.
CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "gold_surface": ("period", "route", "lead_bucket", "cabin"),
    "gold_route_index": ("period", "route"),
    "gold_index": ("period",),
    "route_weight": ("route", "base_year"),
    "gold_model": ("name", "period"),
}

_INSERT_TABLE = re.compile(
    r"^\s*INSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _to_pg(sql: str) -> str:
    """Rewrite one SQLite statement into its Postgres equivalent."""
    match = _INSERT_TABLE.search(sql)
    if match:
        mode, table, collist = match.group(1).upper(), match.group(2), match.group(3)
        columns = [c.strip() for c in collist.split(",") if c.strip()]
        body = sql[match.end(0):]
        head = f"INSERT INTO {table} ({collist})"

        if mode == "IGNORE":
            sql = f"{head}{body} ON CONFLICT DO NOTHING"
        else:
            keys = CONFLICT_KEYS.get(table)
            if not keys:
                raise ValueError(
                    f"INSERT OR REPLACE on {table!r} has no conflict key "
                    f"registered in pgcompat.CONFLICT_KEYS")
            updates = ", ".join(
                f"{c} = EXCLUDED.{c}" for c in columns if c not in keys)
            conflict = ", ".join(keys)
            sql = (f"{head}{body} ON CONFLICT ({conflict}) DO UPDATE SET "
                   f"{updates}") if updates else (
                   f"{head}{body} ON CONFLICT ({conflict}) DO NOTHING")

    # `window` is a reserved word in Postgres and a column name in gold_index
    sql = re.sub(r"(?<![\w.\"])window(?![\w(\"])", '"window"', sql)

    # placeholders last, so the rewrites above can match on the original text
    return sql.replace("?", "%s")


def _schema_to_pg(script: str) -> str:
    """Translate the DDL script. Types and pragmas, not queries."""
    # strip `--` comments before splitting: the schema's own comments contain
    # semicolons, and splitting on those cuts a statement in half
    script = re.sub(r"--[^\n]*", "", script)

    out = []
    for statement in script.split(";"):
        s = statement.strip()
        if not s:
            continue
        if s.upper().startswith("PRAGMA"):
            continue                      # journal modes and FK toggles: n/a
        s = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\b", "BIGSERIAL PRIMARY KEY",
                   s, flags=re.IGNORECASE)
        s = re.sub(r"\bBLOB\b", "BYTEA", s, flags=re.IGNORECASE)
        # "window" is reserved in Postgres; quote it wherever it is a column
        s = re.sub(r"(?<![\w.\"])window(?![\w(\"])", '"window"', s)
        out.append(s)
    return ";\n".join(out) + ";"


class HybridRow(dict):
    """A row that answers to both `row["col"]` and `row[0]`.

    sqlite3.Row does both, and the codebase uses both — `dict(r)` in the API,
    positional access in db.stats. A plain dict would break the second, so
    the shim hands back this instead and neither call site has to change.
    """

    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def keys(self):                       # sqlite3.Row exposes this
        return list(super().keys())


def _hybrid_row_factory(cursor):
    cols = [d.name for d in (cursor.description or [])]

    def make(values):
        return HybridRow(zip(cols, values))

    return make


class _Cursor:
    """The slice of sqlite3.Cursor the codebase actually uses."""

    def __init__(self, cur, rows: Optional[list] = None, rowcount: int = -1,
                 lastrowid: Optional[int] = None):
        self._cur = cur
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        if self._rows is not None:
            return self._rows[0] if self._rows else None
        return self._cur.fetchone()

    def fetchall(self):
        if self._rows is not None:
            return self._rows
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self.fetchall())


class PgConnection:
    """A psycopg connection wearing sqlite3.Connection's interface."""

    def __init__(self, dsn: str):
        import psycopg

        self._conn = psycopg.connect(dsn, row_factory=_hybrid_row_factory,
                                     autocommit=False)
        self.row_factory = None            # present because call sites set it

    # -- the sqlite3 surface -------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        upper = sql.strip().upper()

        if upper.startswith("PRAGMA TABLE_INFO"):
            table = sql.split("(", 1)[1].rsplit(")", 1)[0].strip().strip("'\"")
            cur = self._conn.cursor()
            cur.execute(
                """SELECT column_name AS name FROM information_schema.columns
                     WHERE table_name = %s""", (table,))
            return _Cursor(cur, rows=cur.fetchall())
        if upper.startswith("PRAGMA"):
            return _Cursor(None, rows=[])

        translated = _to_pg(sql)
        wants_id = (upper.startswith("INSERT")
                    and "RETURNING" not in upper
                    and " OR " not in upper.split("INTO")[0])
        if wants_id:
            translated += " RETURNING id"

        cur = self._conn.cursor()
        try:
            cur.execute(translated, tuple(params))
        except Exception:
            if wants_id:
                # the table has no id column; retry without the RETURNING
                self._conn.rollback()
                cur = self._conn.cursor()
                cur.execute(_to_pg(sql), tuple(params))
                return _Cursor(cur, rowcount=cur.rowcount)
            raise

        last = None
        if wants_id:
            row = cur.fetchone()
            last = row.get("id") if row else None
            return _Cursor(cur, rows=[], rowcount=cur.rowcount, lastrowid=last)

        rows = cur.fetchall() if cur.description else []
        return _Cursor(cur, rows=rows, rowcount=cur.rowcount)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> _Cursor:
        rows = [tuple(r) for r in seq]
        if not rows:
            return _Cursor(None, rows=[], rowcount=0)
        cur = self._conn.cursor()
        cur.executemany(_to_pg(sql), rows)
        return _Cursor(cur, rows=[], rowcount=cur.rowcount)

    def executescript(self, script: str) -> None:
        cur = self._conn.cursor()
        cur.execute(_schema_to_pg(script))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def is_postgres_dsn(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(("postgres://", "postgresql://"))
