Manage git worktrees for parallel development: $ARGUMENTS

Commands:
- `list` — show all active worktrees
- `create <branch> [base]` — create worktree for branch (default base: main)
- `remove <branch>` — clean up worktree
- `status` — show status of all worktrees (branch, dirty files, test health)

Worktree location: ../yt-artist-worktrees/<branch-name>

Rules:
- Each worktree gets its own data/ dir and *.db
- Never share SQLite files between worktrees
- Run `pip install -e ".[dev]"` in new worktrees
- Show the user the `cd` path after creation
