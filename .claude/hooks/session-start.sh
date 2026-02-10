#!/bin/bash
# Session start: show brief project status

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
TEST_COUNT=$(find tests -name 'test_*.py' 2>/dev/null | wc -l | tr -d ' ')

echo "Branch: $BRANCH | Uncommitted: $DIRTY | Test files: $TEST_COUNT"

# Check dev dependencies
if python -c "import pytest" 2>/dev/null; then
  echo "pytest: ok"
else
  echo "pytest: missing — run: pip install -e '.[dev]'"
fi

if command -v ruff &>/dev/null; then
  echo "ruff: ok"
else
  echo "ruff: missing — run: pip install -e '.[dev]'"
fi
