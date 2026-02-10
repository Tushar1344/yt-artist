Create a knowledge checkpoint before major changes.

1. Summarize current session work so far
2. Sync CLAUDE.md with codebase:
   - Scan all modules in src/yt_artist/
   - Check CLI commands in cli.py
   - Check pyproject.toml deps
   - Check schema.sql tables
   - Propose specific updates to CLAUDE.md, apply after approval
3. Update docs/SESSION_SUMMARY.md with:
   - What was done this session
   - Key decisions made
   - Open questions or blockers
4. Stage and show a `git diff --stat` summary
5. Ask if user wants to commit the checkpoint
