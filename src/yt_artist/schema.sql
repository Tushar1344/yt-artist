-- yt-artist SQLite schema
-- One file; run on init or migration.

-- Artists = YouTube channels
CREATE TABLE IF NOT EXISTS artists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_url TEXT NOT NULL,
    urllist_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    default_prompt_id TEXT REFERENCES prompts(id),
    about TEXT
);

-- Videos belong to an artist
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    artist_id TEXT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(artist_id, id)
);

-- One transcript per video
CREATE TABLE IF NOT EXISTS transcripts (
    video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    format TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Prompt templates for summaries (placeholders: {artist}, {video}, {intent}, {audience})
CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    template TEXT NOT NULL,
    artist_component TEXT,
    video_component TEXT,
    intent_component TEXT,
    audience_component TEXT
);

-- Multiple summaries per video (one per prompt; re-run overwrites or add version by created_at)
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    prompt_id TEXT NOT NULL REFERENCES prompts(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(video_id, prompt_id)
);

-- Future: screenshots at transcript timestamps
CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    timestamp_sec REAL NOT NULL,
    transcript_snippet TEXT,
    file_path TEXT NOT NULL
);

-- Future: video stats and most replayed
CREATE TABLE IF NOT EXISTS video_stats (
    video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    view_count INTEGER,
    most_replayed TEXT
);

-- Background jobs for long-running bulk operations
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER NOT NULL,
    log_file TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

-- Rate-limit monitoring: log each yt-dlp request for request-rate visibility
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    request_type TEXT NOT NULL  -- 'subtitle_download', 'metadata', 'playlist'
);

CREATE INDEX IF NOT EXISTS idx_videos_artist_id ON videos(artist_id);
CREATE INDEX IF NOT EXISTS idx_summaries_video_id ON summaries(video_id);
CREATE INDEX IF NOT EXISTS idx_summaries_prompt_id ON summaries(prompt_id);
CREATE INDEX IF NOT EXISTS idx_screenshots_video_id ON screenshots(video_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log(timestamp);
