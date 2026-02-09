# ADR-0009: Guided onboarding with hints, quickstart, and --quiet

## Status

Accepted (2026-02-09).

## Context

New users face a steep learning curve: after running one command, they don't know what to do next. The CLI has a multi-step workflow (fetch → transcribe → summarize) but nothing guides users through it. Power users, meanwhile, want machine-parseable output without decorative hints.

## Decision

### Next-step hints after every command

After each command completes, print a contextual "Next:" hint to stderr (never stdout). Hints are command-specific:
- `fetch-channel` → suggests `transcribe --artist-id @X`
- `transcribe` (single) → suggests `summarize <video_id>`
- `transcribe` (bulk) → suggests `summarize --artist-id @X`
- `summarize` (single) → suggests `summarize --artist-id @X` for bulk
- `summarize` (bulk) → suggests `search-transcripts`
- `add-prompt` → suggests `set-default-prompt`
- `set-default-prompt` → suggests `summarize --artist-id @X`

Hints include real data from the just-completed operation (actual artist ID, video ID, video count).

### `--quiet` / `-q` flag

Suppresses all hints, first-run tips, and background suggestions. Output is clean for scripting and piping. Only primary command output goes to stdout.

### `quickstart` subcommand

Prints a guided 3-step walkthrough using @TED as a concrete example:
```
STEP 1: Fetch channel videos
STEP 2: Transcribe videos
STEP 3: Summarize with AI
SHORTCUT: summarize does everything automatically
```

### First-run detection

On first use (empty DB with no artists), print a "First time?" tip suggesting `quickstart`. Suppressed by `--quiet` and when running `quickstart` itself.

### Output channel discipline

All hints and tips go to stderr. Primary command output (data, tables, results) goes to stdout. This allows piping stdout to files or other tools without hint pollution.

## Consequences

- Zero-friction onboarding: users always know the next step.
- Power users opt out with `-q`.
- `quickstart` provides a copy-pasteable tutorial using a real channel.
- 22 new tests verify hint content, stderr routing, and quiet suppression.
