Review current uncommitted changes:

1. `git diff` for unstaged, `git diff --cached` for staged
2. For each changed file check:
   - Follows existing patterns?
   - Missing docstrings or type hints on new functions?
   - Error messages actionable for non-technical users?
   - New external calls that need mocking in tests?
   - Secrets, hardcoded paths, debug leftovers?
3. Run `python -m pytest tests/ -x -q`
4. Present findings as: **Critical** / **Warning** / **Suggestion**

One line per finding unless it needs explanation.
