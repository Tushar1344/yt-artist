# Portable Mac CLI & User Guide — Implementation Plan

> **For execution:** Use executing-plans or implement task-by-task.

**Goal:** Make yt-artist a portable Mac terminal utility with: bulk pull URLs per artist, search DB for transcripts, summarize by video URL or video ID (transcribe if needed), and one-line install. Add a user guide.

**Architecture:** Extend existing CLI with new subcommands; add an install script that creates a venv and places `yt-artist` on PATH; single USER_GUIDE.md for end users.

**Tech stack:** Existing Python 3.9+ stack; shell script for install (no Homebrew required); optional pipx path in docs.

---

## Task 1: Storage — list/search transcripts

**Files:** `src/yt_artist/storage.py`

- Add `list_transcripts(artist_id: Optional[str] = None) -> List[Dict]` returning rows with `video_id`, `artist_id` (from join), `title` (from videos), `length(raw_text)`, `created_at`. Join transcripts with videos (and optionally filter by artist_id).

**Steps:** Implement method; no new test file required if covered by CLI usage.

---

## Task 2: CLI — search-transcripts

**Files:** `src/yt_artist/cli.py`

- Add subcommand `search-transcripts` with optional `--artist-id` and optional `--video-id` (exact match). Output: table or lines of video_id, artist_id, title, transcript length, created_at.
- If no args: list all transcripts. If --artist-id: filter by artist. If --video-id: single row.

---

## Task 3: CLI — summarize-video (URL or video_id, transcribe if needed)

**Files:** `src/yt_artist/cli.py`

- Add subcommand `summarize-video`: one positional arg `video` (URL or video_id), required `--prompt`, optional `--intent`, `--audience`, `--max-preview`.
- Logic: extract video_id from video (URL or plain id). If no transcript in DB, call transcribe(video, storage, ...), then call summarize(video_id, prompt_id, storage, ...). Print summary as today's summarize command.

---

## Task 4: Portable install script (Mac)

**Files:** Create `scripts/install-mac.sh` (or `install.sh` at repo root).

- Script (idempotent): Ensure Python 3.9+ on PATH (`python3`). Create venv at `~/.local/yt-artist/venv` (or `~/.yt-artist/venv`). Pip install from repo dir (if run from repo) or `pip install yt-artist` / from git URL. Create wrapper script at `~/.local/bin/yt-artist` (or add to PATH) that activates venv and runs `python -m yt_artist.cli "$@"`. Ensure `~/.local/bin` in PATH (echo instruction or add to .zshrc/.bash_profile).
- Make script executable; document one-liner: clone repo then `./scripts/install-mac.sh` or `curl -sSL <raw_url> | bash` (if script is fetchable).

---

## Task 5: User guide

**Files:** Create `USER_GUIDE.md` at repo root.

- Sections: What is yt-artist; Prerequisites (Python 3.9+, yt-dlp, Ollama optional); Install (copy-paste for portable Mac); Quick start (fetch artist → list transcripts → summarize one video); Command reference (table: fetch-channel, transcribe, search-transcripts, summarize-video, summarize, add-prompt, list-prompts); Where data lives (DB path, urllist path); Environment (OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL for Ollama/OpenAI); Troubleshooting (no subtitles, Ollama connection).

---

## Task 6: README and skill update

**Files:** `README.md`, `.cursor/skills/yt-artist/SKILL.md`

- README: Add "Portable Mac install" section linking to USER_GUIDE and one-line install. Mention new commands search-transcripts, summarize-video.
- Skill: Add search-transcripts and summarize-video to commands table and example prompts.

---

## Execution summary

| Task | Deliverable |
|------|-------------|
| 1 | `storage.list_transcripts(artist_id=None)` |
| 2 | `yt-artist search-transcripts [--artist-id ID] [--video-id ID]` |
| 3 | `yt-artist summarize-video <url_or_id> --prompt ID [--intent] [--audience]` |
| 4 | `scripts/install-mac.sh` + PATH instructions |
| 5 | `USER_GUIDE.md` |
| 6 | README + skill updates |
