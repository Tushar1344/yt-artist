---
globs:
  - "tests/**/*.py"
---

# Testing Rules

- File naming: test_<module>.py
- Use conftest.py fixtures: db_path, store
- Mock ALL external calls: yt-dlp (subprocess.run), LLM (yt_artist.llm.complete), web search
- Use tmp_path for any file I/O
- Arrange-act-assert pattern
- CLI tests: patch sys.argv + call main() or use _run_cli() helper
- Prefer specific assertions: `assert x["id"] == "foo"` not `assert x is not None`
- Test both happy path and error cases
- Never hit real YouTube or LLM APIs
- DB seeding in tests: use `with store.transaction() as conn:` for raw SQL, never `store._conn()`
