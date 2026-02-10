---
globs:
  - "src/**/*.py"
---

# Python Source Rules

- Every module starts with a docstring
- `from __future__ import annotations` when using PEP 604 union syntax (X | Y)
- Logging: `log = logging.getLogger("yt_artist.<module_name>")`
- Storage methods: get _conn(), try/finally close
- Use ON CONFLICT DO UPDATE for upserts
- SystemExit with actionable message for user-facing errors
- ValueError or RuntimeError for internal/programmer errors
- Type hints on function signatures
- Private functions: underscore prefix
- No string interpolation in SQL — use ? placeholders
