"""Initialize or ensure SQLite schema exists."""

from pathlib import Path


def get_schema_sql() -> str:
    path = Path(__file__).parent / "schema.sql"
    return path.read_text()
