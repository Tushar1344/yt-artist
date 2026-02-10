#!/bin/bash
# Auto-format Python files after Write/Edit using ruff

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only format Python files
[[ "$FILE_PATH" != *.py ]] && exit 0

# Only if file exists
[ ! -f "$FILE_PATH" ] && exit 0

# Use ruff if available, skip silently otherwise
if command -v ruff &>/dev/null; then
  ruff format --quiet "$FILE_PATH" 2>/dev/null
  ruff check --fix --quiet "$FILE_PATH" 2>/dev/null
fi

exit 0
