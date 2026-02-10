Create a git worktree for parallel development: $ARGUMENTS

Location: ../yt-artist-worktrees/<branch-name>

Steps:
1. Create the worktree: `git worktree add ../yt-artist-worktrees/<branch> -b <branch>`
2. Run `pip install -e ".[dev]"` in the new worktree
3. Show the full path so user can cd to it

Rules:
- Each worktree gets its own data/ dir and *.db
- Never share SQLite files between worktrees
