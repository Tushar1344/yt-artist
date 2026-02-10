#!/bin/bash
# Block dangerous bash commands

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Block rm -rf on critical dirs
if echo "$COMMAND" | grep -qE 'rm\s+-rf\s+(/|~|\.git|src|tests)'; then
  echo "Blocked: destructive rm -rf on critical directory" >&2
  exit 2
fi

# Block DROP TABLE / DROP DATABASE
if echo "$COMMAND" | grep -iqE '\b(DROP\s+TABLE|DROP\s+DATABASE)\b'; then
  echo "Blocked: DROP commands not allowed" >&2
  exit 2
fi

# Block force push to main/master
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force.*(main|master)'; then
  echo "Blocked: force push to main/master" >&2
  exit 2
fi

exit 0
