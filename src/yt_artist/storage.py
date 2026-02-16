"""Storage layer: SQLite CRUD for artists, videos, transcripts, prompts, summaries."""
from contextlib import contextmanager
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Union

from yt_artist.init_db import get_schema_sql

log = logging.getLogger("yt_artist.storage")

# ---------------------------------------------------------------------------
# TypedDict row types — give callers type-safe access to dict keys.
# Using total=False only where columns may be absent on older DBs.
# ---------------------------------------------------------------------------
try:
    from typing import TypedDict
except ImportError:  # Python 3.7
    from typing_extensions import TypedDict


class ArtistRow(TypedDict, total=False):
    id: str
    name: str
    channel_url: str
    urllist_path: str
    created_at: str
    default_prompt_id: Optional[str]
    about: Optional[str]


class VideoRow(TypedDict):
    id: str
    artist_id: str
    url: str
    title: str
    fetched_at: str


class TranscriptRow(TypedDict):
    video_id: str
    raw_text: str
    format: str
    created_at: str


class PromptRow(TypedDict):
    id: str
    name: str
    template: str
    artist_component: str
    video_component: str
    intent_component: str
    audience_component: str


class SummaryRow(TypedDict):
    id: int
    video_id: str
    prompt_id: str
    content: str
    created_at: str


class TranscriptListRow(TypedDict, total=False):
    """Row returned by list_transcripts (join)."""
    video_id: str
    format: str
    created_at: str
    transcript_len: int
    artist_id: str
    title: str


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------

def _dict_row(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


class Storage:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        if not self.db_path or str(self.db_path).strip() in ("", "."):
            raise ValueError(
                "Database path is empty. Set --db to a file path (e.g. ./yt_artist.db) or set the DB environment variable."
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = _dict_row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a connection for batch operations; commit on success, rollback on error."""
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Built-in default prompt shipped with the package — zero-config summarize.
    _DEFAULT_PROMPT_ID = "default"
    _DEFAULT_PROMPT_NAME = "Default Summary"
    _DEFAULT_PROMPT_TEMPLATE = (
        "You are a helpful assistant that summarizes YouTube video transcripts.\n"
        "Artist/channel context: {artist}\n"
        "Video title: {video}\n"
        "{intent}\n{audience}\n\n"
        "Provide a clear, concise summary of the key points discussed in the transcript."
    )

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            conn.executescript(get_schema_sql())
            conn.commit()
            self._migrate_artists_columns(conn)
            conn.commit()
            self._migrate_jobs_table(conn)
            conn.commit()
            self._migrate_request_log_table(conn)
            conn.commit()
            self._ensure_default_prompt(conn)
            conn.commit()
        finally:
            conn.close()

    def _ensure_default_prompt(self, conn: sqlite3.Connection) -> None:
        """Create the built-in default prompt if no prompts exist yet."""
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM prompts")
        row = cur.fetchone()
        count = row["cnt"] if isinstance(row, dict) else row[0]
        if count == 0:
            conn.execute(
                "INSERT INTO prompts (id, name, template, artist_component, video_component, intent_component, audience_component) "
                "VALUES (?, ?, ?, '', '', '', '')",
                (self._DEFAULT_PROMPT_ID, self._DEFAULT_PROMPT_NAME, self._DEFAULT_PROMPT_TEMPLATE),
            )

    def _migrate_artists_columns(self, conn: sqlite3.Connection) -> None:
        """Add default_prompt_id and about to artists if missing (existing DBs)."""
        cur = conn.execute("PRAGMA table_info(artists)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "default_prompt_id" not in names:
            conn.execute("ALTER TABLE artists ADD COLUMN default_prompt_id TEXT REFERENCES prompts(id)")
        if "about" not in names:
            conn.execute("ALTER TABLE artists ADD COLUMN about TEXT")

    def _migrate_jobs_table(self, conn: sqlite3.Connection) -> None:
        """Create jobs table if missing (existing DBs created before background-jobs feature)."""
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
        if not cur.fetchone():
            conn.execute("""
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
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    def _migrate_request_log_table(self, conn: sqlite3.Connection) -> None:
        """Create request_log table if missing (existing DBs created before rate-limit monitoring)."""
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'")
        if not cur.fetchone():
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    request_type TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log(timestamp)")

    # ------ Artists ------

    def upsert_artist(
        self,
        *,
        artist_id: str,
        name: str,
        channel_url: str,
        urllist_path: str,
        default_prompt_id: Optional[str] = None,
        about: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO artists (id, name, channel_url, urllist_path, default_prompt_id, about)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    channel_url = excluded.channel_url,
                    urllist_path = excluded.urllist_path,
                    default_prompt_id = COALESCE(excluded.default_prompt_id, artists.default_prompt_id),
                    about = COALESCE(NULLIF(trim(excluded.about), ''), artists.about)
                """,
                (artist_id, name, channel_url, urllist_path, default_prompt_id, about),
            )
            conn.commit()
        finally:
            conn.close()

    def get_artist(self, artist_id: str) -> Optional[ArtistRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            return cur.fetchone()  # type: ignore[return-value]
        finally:
            conn.close()

    def get_artist_default_prompt_id(self, artist_id: str) -> Optional[str]:
        artist = self.get_artist(artist_id)
        if not artist:
            return None
        pid = artist.get("default_prompt_id")
        return pid if pid else None

    def set_artist_default_prompt(self, artist_id: str, prompt_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE artists SET default_prompt_id = ? WHERE id = ?",
                (prompt_id, artist_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_artist_about(self, artist_id: str, about: Optional[str]) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE artists SET about = ? WHERE id = ?",
                (about or "", artist_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_artists(self) -> List[ArtistRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM artists ORDER BY name")
            return cur.fetchall()  # type: ignore[return-value]
        finally:
            conn.close()

    # ------ Videos ------

    def list_videos(self, artist_id: Optional[str] = None) -> List[VideoRow]:
        conn = self._conn()
        try:
            if artist_id:
                cur = conn.execute(
                    "SELECT * FROM videos WHERE artist_id = ? ORDER BY fetched_at DESC",
                    (artist_id,),
                )
            else:
                cur = conn.execute("SELECT * FROM videos ORDER BY fetched_at DESC")
            return cur.fetchall()  # type: ignore[return-value]
        finally:
            conn.close()

    def upsert_video(
        self,
        *,
        video_id: str,
        artist_id: str,
        url: str,
        title: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO videos (id, artist_id, url, title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    url = excluded.url,
                    title = excluded.title,
                    fetched_at = datetime('now')
                """,
                (video_id, artist_id, url, title or ""),
            )
            conn.commit()
        finally:
            conn.close()

    def get_video(self, video_id: str) -> Optional[VideoRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
            return cur.fetchone()  # type: ignore[return-value]
        finally:
            conn.close()

    # ------ Transcripts ------

    def save_transcript(
        self,
        *,
        video_id: str,
        raw_text: str,
        format: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO transcripts (video_id, raw_text, format)
                VALUES (?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    raw_text = excluded.raw_text,
                    format = excluded.format,
                    created_at = datetime('now')
                """,
                (video_id, raw_text, format or ""),
            )
            conn.commit()
        finally:
            conn.close()

    def get_transcript(self, video_id: str) -> Optional[TranscriptRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,))
            return cur.fetchone()  # type: ignore[return-value]
        finally:
            conn.close()

    def list_transcripts(
        self,
        artist_id: Optional[str] = None,
        video_id: Optional[str] = None,
    ) -> List[TranscriptListRow]:
        """List transcripts with video/artist info. Filter by artist_id and/or video_id (exact)."""
        conn = self._conn()
        try:
            sql = """
                SELECT t.video_id, t.format, t.created_at,
                       length(t.raw_text) AS transcript_len,
                       v.artist_id, v.title
                FROM transcripts t
                LEFT JOIN videos v ON v.id = t.video_id
                WHERE 1=1
            """
            params: list = []
            if artist_id:
                sql += " AND v.artist_id = ?"
                params.append(artist_id)
            if video_id:
                sql += " AND t.video_id = ?"
                params.append(video_id)
            sql += " ORDER BY t.created_at DESC"
            cur = conn.execute(sql, params)
            return cur.fetchall()  # type: ignore[return-value]
        finally:
            conn.close()

    # ------ Prompts ------

    def upsert_prompt(
        self,
        *,
        prompt_id: str,
        name: str,
        template: str,
        artist_component: Optional[str] = None,
        video_component: Optional[str] = None,
        intent_component: Optional[str] = None,
        audience_component: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO prompts (id, name, template, artist_component, video_component, intent_component, audience_component)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    template = excluded.template,
                    artist_component = excluded.artist_component,
                    video_component = excluded.video_component,
                    intent_component = excluded.intent_component,
                    audience_component = excluded.audience_component
                """,
                (
                    prompt_id,
                    name,
                    template,
                    artist_component or "",
                    video_component or "",
                    intent_component or "",
                    audience_component or "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_prompt(self, prompt_id: str) -> Optional[PromptRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
            return cur.fetchone()  # type: ignore[return-value]
        finally:
            conn.close()

    def list_prompts(self) -> List[PromptRow]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT id, name, template FROM prompts ORDER BY id")
            return cur.fetchall()  # type: ignore[return-value]
        finally:
            conn.close()

    # ------ Summaries ------

    def upsert_summary(
        self,
        *,
        video_id: str,
        prompt_id: str,
        content: str,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO summaries (video_id, prompt_id, content, created_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(video_id, prompt_id) DO UPDATE SET
                    content = excluded.content,
                    created_at = datetime('now')
                """,
                (video_id, prompt_id, content),
            )
            conn.commit()
        finally:
            conn.close()

    def get_summaries_for_video(self, video_id: str) -> List[SummaryRow]:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT * FROM summaries WHERE video_id = ? ORDER BY created_at",
                (video_id,),
            )
            return cur.fetchall()  # type: ignore[return-value]
        finally:
            conn.close()

    def video_ids_with_transcripts(self, video_ids: List[str]) -> set:
        """Return the subset of video_ids that already have transcripts (single query)."""
        if not video_ids:
            return set()
        conn = self._conn()
        try:
            placeholders = ",".join("?" for _ in video_ids)
            cur = conn.execute(
                f"SELECT video_id FROM transcripts WHERE video_id IN ({placeholders})",
                video_ids,
            )
            return {row["video_id"] if isinstance(row, dict) else row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    def video_ids_with_summary(self, video_ids: List[str], prompt_id: str) -> set:
        """Return the subset of video_ids that already have a summary for the given prompt_id."""
        if not video_ids:
            return set()
        conn = self._conn()
        try:
            placeholders = ",".join("?" for _ in video_ids)
            cur = conn.execute(
                f"SELECT video_id FROM summaries WHERE prompt_id = ? AND video_id IN ({placeholders})",
                [prompt_id] + video_ids,
            )
            return {row["video_id"] if isinstance(row, dict) else row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    # ------ Counts (for status command) ------

    def count_artists(self) -> int:
        """Return total number of artists."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM artists")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    def count_videos(self) -> int:
        """Return total number of videos."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM videos")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    def count_transcribed_videos(self) -> int:
        """Return number of videos that have transcripts."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM transcripts")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    def count_summarized_videos(self) -> int:
        """Return number of distinct videos that have at least one summary."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(DISTINCT video_id) AS cnt FROM summaries")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    def count_prompts(self) -> int:
        """Return total number of prompts."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM prompts")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    # ------ Helpers ------

    def urllist_path(self, artist_id: str, artist_name: str) -> str:
        """Compute path for artist urllist file: artist{id}{sanitized_name}-urllist.md"""
        safe_name = re.sub(r"[^\w\-]", "_", artist_name).strip("_") or "channel"
        return f"data/artists/{artist_id}/artist{artist_id}{safe_name}-urllist.md"
