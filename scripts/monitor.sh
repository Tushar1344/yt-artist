#!/usr/bin/env bash
# yt-artist dashboard — per-artist progress + live processes + jobs + rate limits
# Usage: ./scripts/monitor.sh [interval_seconds]
# Default refresh: 5s. Ctrl-C to stop.

INTERVAL="${1:-5}"
DB="${YT_ARTIST_DB:-data/yt_artist.db}"

bold=$(tput bold 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)

while true; do
  clear

  echo "${bold}═══════════════════════════════════════════════════════════════${reset}"
  echo "${bold}  yt-artist dashboard                        $(date '+%H:%M:%S')${reset}"
  echo "${bold}═══════════════════════════════════════════════════════════════${reset}"
  echo ""

  # ── Per-artist progress ──
  echo "${bold}▸ Per-artist progress${reset}"
  sqlite3 "$DB" "
    SELECT
      v.artist_id                                                          AS artist,
      COUNT(*)                                                             AS videos,
      SUM(CASE WHEN t.video_id IS NOT NULL THEN 1 ELSE 0 END)             AS transcribed,
      SUM(CASE WHEN s.video_id IS NOT NULL THEN 1 ELSE 0 END)             AS summarized,
      SUM(CASE WHEN s.quality_score IS NOT NULL THEN 1 ELSE 0 END)        AS scored,
      ROUND(AVG(s.quality_score), 2)                                      AS avg_quality
    FROM videos v
    LEFT JOIN transcripts t ON t.video_id = v.id
    LEFT JOIN summaries  s  ON s.video_id = v.id
    GROUP BY v.artist_id
    ORDER BY v.artist_id;
  " -header -column
  echo ""

  # ── Jobs (from DB) ──
  echo "${bold}▸ Jobs${reset}"
  JOBS=$(sqlite3 "$DB" "
    SELECT
      substr(id, 1, 8) AS job_id,
      status,
      done || '/' || total AS progress,
      errors AS errs,
      substr(started_at, 12, 8) AS started,
      command
    FROM jobs
    ORDER BY started_at DESC
    LIMIT 8;
  " -header -column 2>/dev/null)
  if [ -z "$JOBS" ]; then
    echo "  (no jobs)"
  else
    echo "$JOBS"
  fi
  echo ""

  # ── Live OS processes (yt-dlp + yt_artist workers) ──
  echo "${bold}▸ Live processes${reset}"
  YT_PROCS=$(ps -eo pid,etime,command 2>/dev/null \
    | grep -E "yt.dlp|yt_artist" \
    | grep -v grep \
    | grep -v "monitor.sh" \
    | grep -v "yt_artist.cli -q status\|yt_artist.cli -q jobs\|yt_artist.cli status\|yt_artist.cli jobs")

  if [ -z "$YT_PROCS" ]; then
    echo "  (none)"
    YT_COUNT=0
  else
    # Summarise by type
    DLP_COUNT=$(echo "$YT_PROCS" | grep -c "yt.dlp" || true)
    WORKER_COUNT=$(echo "$YT_PROCS" | grep -c "_bg-worker\|yt_artist.cli.*summarize\|yt_artist.cli.*transcribe\|yt_artist.cli.*score" || true)
    OLLAMA_COUNT=$(echo "$YT_PROCS" | grep -c "ollama.*runner" || true)
    echo "  yt-dlp downloads : $DLP_COUNT"
    echo "  yt-artist workers: $WORKER_COUNT"
    echo ""
    echo "  PID    ELAPSED  COMMAND"
    echo "$YT_PROCS" | while IFS= read -r line; do
      # Trim command to 70 chars
      pid=$(echo "$line" | awk '{print $1}')
      elapsed=$(echo "$line" | awk '{print $2}')
      cmd=$(echo "$line" | awk '{$1=""; $2=""; print}' | sed 's/^ *//' | cut -c1-70)
      printf "  %-6s %-8s %s\n" "$pid" "$elapsed" "$cmd"
    done
    YT_COUNT=$((DLP_COUNT + WORKER_COUNT))
  fi
  echo ""

  # ── YouTube rate limiting ──
  echo "${bold}▸ YouTube requests${reset}"
  sqlite3 "$DB" "
    SELECT
      (SELECT COUNT(*) FROM request_log
       WHERE timestamp > datetime('now', '-5 minutes'))  AS last_5min,
      (SELECT COUNT(*) FROM request_log
       WHERE timestamp > datetime('now', '-1 hour'))     AS last_1hr,
      (SELECT COUNT(*) FROM request_log)                 AS total;
  " -header -column 2>/dev/null
  echo ""

  # ── Summary line ──
  TOTAL_TRANSCRIBED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM transcripts;" 2>/dev/null)
  TOTAL_SUMMARIZED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM summaries;" 2>/dev/null)
  TOTAL_SCORED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM summaries WHERE quality_score IS NOT NULL;" 2>/dev/null)
  DB_SIZE=$(du -h "$DB" 2>/dev/null | awk '{print $1}')
  echo "${dim}Totals: ${TOTAL_TRANSCRIBED} transcribed | ${TOTAL_SUMMARIZED} summarized | ${TOTAL_SCORED} scored | DB: ${DB_SIZE}${reset}"
  echo "${dim}Active processes sending YT requests: ${YT_COUNT:-0}${reset}"
  echo "${dim}(refreshing every ${INTERVAL}s — Ctrl-C to stop)${reset}"

  sleep "$INTERVAL"
done
