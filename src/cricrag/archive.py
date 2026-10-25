"""Read-only access to the cricbot DuckDB archive. DuckDB allows one writer, so we never write."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from cricrag.config import settings


@contextmanager
def connect(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect(str(path or settings().db_path), read_only=True)
    try:
        yield con
    finally:
        con.close()


STAGE_SQL = """
CASE
  WHEN lower(title) SIMILAR TO '.*(qualif|eliminat|semi|quarter|playoff).*' THEN 'playoff'
  WHEN lower(title) LIKE '%final%' THEN 'final'
  ELSE 'league'
END
"""
