Review project status. Run these and summarize as a brief dashboard:

1. `git status` and `git log --oneline -5`
2. `python -m pytest tests/ -q --tb=no`
3. Count source files in src/yt_artist/ and test files in tests/
4. Check if ruff is installed
5. Read docs/PARKING_LOT.md for pending work items

Format: branch, last 5 commits (one line each), test pass/fail, file counts, top 3 parking lot items.
