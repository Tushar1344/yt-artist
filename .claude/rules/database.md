---
globs:
  - "src/yt_artist/schema.sql"
  - "src/yt_artist/storage.py"
  - "src/yt_artist/init_db.py"
---

# Database Rules

- Schema changes in schema.sql need corresponding migration in storage.py
- TEXT PRIMARY KEY on most tables (not INTEGER)
- Foreign keys with ON DELETE CASCADE
- PRAGMA foreign_keys = ON in every connection
- PRAGMA journal_mode = WAL for concurrent reads
- Parameterized queries (? placeholders) — never string interpolation
- Batch operations inside storage.transaction() context manager
- New tables need CREATE INDEX for FK columns
