Add a new database table: $ARGUMENTS

This is a multi-step process. Do every step — missing one causes silent bugs.

**Step 1 — schema.sql**: Add the CREATE TABLE statement.
- TEXT PRIMARY KEY (not INTEGER) unless there's a reason for AUTOINCREMENT
- Foreign keys with ON DELETE CASCADE
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))` if temporal

**Step 2 — schema.sql**: Add CREATE INDEX for every foreign key column.
```sql
CREATE INDEX IF NOT EXISTS idx_<table>_<fk_col> ON <table>(<fk_col>);
```

**Step 3 — storage.py TypedDict**: Add a `<Name>Row(TypedDict)` class near the top, alongside ArtistRow, VideoRow, etc.

**Step 4 — storage.py CRUD methods**: Add at minimum:
- `upsert_<name>(self, ...)` — INSERT ON CONFLICT DO UPDATE
- `get_<name>(self, id)` — single row by PK
- `list_<names>(self, ...)` — filtered list
Pattern: `conn = self._conn()` / `try:` / `finally: conn.close()`

**Step 5 — storage.py migration**: Add `_migrate_<name>_table(conn)` method.
Call it from `ensure_schema()` following the existing pattern (conn.commit() after each migration).

**Step 6 — tests**: Add tests in `tests/test_storage.py`:
- Test upsert (insert + update-on-conflict)
- Test get (found + not found)
- Test list (empty + populated)
- Test FK cascade (delete parent, verify child deleted)
- Use `store` fixture from conftest.py

**Step 7 — Run tests**: `python -m pytest tests/test_storage.py -v`

**Step 8 — Update CLAUDE.md**: Add the new table to the "DB Schema (tables)" line.

Verify: all 8 steps done before reporting complete.
