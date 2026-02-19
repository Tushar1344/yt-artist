"""Storage layer: SQLite CRUD for artists, videos, transcripts, prompts, summaries."""

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from yt_artist.init_db import get_schema_sql

log = logging.getLogger("yt_artist.storage")

# SQLite has a compile-time limit (SQLITE_MAX_VARIABLE_NUMBER, default 999) on
# the number of ?-placeholders per query.  Channels with 1000+ videos would
# exceed this in WHERE ... IN (?, ?, ...) clauses.  We batch at 500 to stay
# well under the limit.
_IN_BATCH_SIZE = 500

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


class TranscriptRow(TypedDict, total=False):
    video_id: str
    raw_text: str
    format: str
    quality_score: Optional[float]
    raw_vtt: Optional[str]
    created_at: str


class PromptRow(TypedDict):
    id: str
    name: str
    template: str
    artist_component: str
    video_component: str
    intent_component: str
    audience_component: str


class SummaryRow(TypedDict, total=False):
    id: int
    video_id: str
    prompt_id: str
    content: str
    created_at: str
    quality_score: Optional[float]
    heuristic_score: Optional[float]
    llm_score: Optional[float]
    faithfulness_score: Optional[float]
    verification_score: Optional[float]
    model: Optional[str]
    strategy: Optional[str]
    prompt_hash: Optional[str]
    transcript_hash: Optional[str]


class TranscriptListRow(TypedDict, total=False):
    """Row returned by list_transcripts (join)."""

    video_id: str
    format: str
    created_at: str
    transcript_len: int
    quality_score: Optional[float]
    artist_id: str
    title: str


class WorkLedgerRow(TypedDict, total=False):
    """Row from the work_ledger table."""

    id: int
    video_id: str
    operation: str
    model: Optional[str]
    prompt_id: Optional[str]
    strategy: Optional[str]
    status: str
    started_at: str
    finished_at: str
    duration_ms: Optional[int]
    error_message: Optional[str]


class JobRow(TypedDict, total=False):
    """Row from the jobs table."""

    id: str
    command: str
    status: str
    pid: int
    log_file: str
    started_at: Optional[str]
    finished_at: Optional[str]
    total: int
    done: int
    errors: int
    error_message: Optional[str]


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

    @contextmanager
    def _read_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for read-only DB operations; auto-closes."""
        conn = self._conn()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for single writes; auto-commits and closes."""
        conn = self._conn()
        try:
            yield conn
            conn.commit()
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
        "Provide a clear, concise summary of the key points discussed in the transcript.\n\n"
        "IMPORTANT: Only state facts, names, quotes, and claims that appear in the transcript. "
        "Do not invent or assume anything not explicitly stated. "
        "If you are uncertain about a detail, omit it rather than guess."
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
            self._migrate_summary_score_columns(conn)
            conn.commit()
            self._migrate_faithfulness_score_column(conn)
            conn.commit()
            self._migrate_verification_score_column(conn)
            conn.commit()
            self._migrate_provenance_columns(conn)
            conn.commit()
            self._migrate_transcript_quality_column(conn)
            conn.commit()
            self._migrate_transcript_raw_vtt_column(conn)
            conn.commit()
            self._migrate_hash_columns(conn)
            conn.commit()
            self._migrate_work_ledger_table(conn)
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

    def _migrate_summary_score_columns(self, conn: sqlite3.Connection) -> None:
        """Add quality_score, heuristic_score, llm_score columns to summaries if missing."""
        cur = conn.execute("PRAGMA table_info(summaries)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "quality_score" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN quality_score REAL")
        if "heuristic_score" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN heuristic_score REAL")
        if "llm_score" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN llm_score REAL")

    def _migrate_faithfulness_score_column(self, conn: sqlite3.Connection) -> None:
        """Add faithfulness_score column to summaries if missing."""
        cur = conn.execute("PRAGMA table_info(summaries)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "faithfulness_score" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN faithfulness_score REAL")

    def _migrate_verification_score_column(self, conn: sqlite3.Connection) -> None:
        """Add verification_score column to summaries if missing."""
        cur = conn.execute("PRAGMA table_info(summaries)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "verification_score" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN verification_score REAL")

    def _migrate_provenance_columns(self, conn: sqlite3.Connection) -> None:
        """Add model and strategy columns to summaries if missing."""
        cur = conn.execute("PRAGMA table_info(summaries)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "model" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN model TEXT")
        if "strategy" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN strategy TEXT")

    def _migrate_transcript_quality_column(self, conn: sqlite3.Connection) -> None:
        """Add quality_score column to transcripts if missing."""
        cur = conn.execute("PRAGMA table_info(transcripts)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "quality_score" not in names:
            conn.execute("ALTER TABLE transcripts ADD COLUMN quality_score REAL")

    def _migrate_transcript_raw_vtt_column(self, conn: sqlite3.Connection) -> None:
        """Add raw_vtt column to transcripts if missing."""
        cur = conn.execute("PRAGMA table_info(transcripts)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "raw_vtt" not in names:
            conn.execute("ALTER TABLE transcripts ADD COLUMN raw_vtt TEXT")

    def _migrate_hash_columns(self, conn: sqlite3.Connection) -> None:
        """Add prompt_hash and transcript_hash columns to summaries if missing."""
        cur = conn.execute("PRAGMA table_info(summaries)")
        rows = cur.fetchall()
        names = {row["name"] if isinstance(row, dict) else row[1] for row in rows}
        if "prompt_hash" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN prompt_hash TEXT")
        if "transcript_hash" not in names:
            conn.execute("ALTER TABLE summaries ADD COLUMN transcript_hash TEXT")

    def _migrate_work_ledger_table(self, conn: sqlite3.Connection) -> None:
        """Create work_ledger table if missing (existing DBs created before work-ledger feature)."""
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='work_ledger'")
        if not cur.fetchone():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS work_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    operation TEXT NOT NULL,
                    model TEXT,
                    prompt_id TEXT,
                    strategy TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER,
                    error_message TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_work_ledger_video_id ON work_ledger(video_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_work_ledger_operation ON work_ledger(operation)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_work_ledger_started_at ON work_ledger(started_at)")

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
        with self._read_conn() as conn:
            cur = conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            return cur.fetchone()  # type: ignore[return-value]

    def get_artist_default_prompt_id(self, artist_id: str) -> Optional[str]:
        artist = self.get_artist(artist_id)
        if not artist:
            return None
        pid = artist.get("default_prompt_id")
        return pid if pid else None

    def set_artist_default_prompt(self, artist_id: str, prompt_id: str) -> None:
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE artists SET default_prompt_id = ? WHERE id = ?",
                (prompt_id, artist_id),
            )

    def set_artist_about(self, artist_id: str, about: Optional[str]) -> None:
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE artists SET about = ? WHERE id = ?",
                (about or "", artist_id),
            )

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
        with self._read_conn() as conn:
            cur = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
            return cur.fetchone()  # type: ignore[return-value]

    # ------ Transcripts ------

    def save_transcript(
        self,
        *,
        video_id: str,
        raw_text: str,
        format: Optional[str] = None,
        quality_score: Optional[float] = None,
        raw_vtt: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO transcripts (video_id, raw_text, format, quality_score, raw_vtt)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    raw_text = excluded.raw_text,
                    format = excluded.format,
                    quality_score = excluded.quality_score,
                    raw_vtt = excluded.raw_vtt,
                    created_at = datetime('now')
                """,
                (video_id, raw_text, format or "", quality_score, raw_vtt),
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
                       t.quality_score,
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

    def update_transcript_quality_score(self, video_id: str, quality_score: float) -> None:
        """Update quality_score on an existing transcript row (for backfill)."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE transcripts SET quality_score = ? WHERE video_id = ?",
                (quality_score, video_id),
            )
            conn.commit()
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
        model: Optional[str] = None,
        strategy: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        transcript_hash: Optional[str] = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO summaries (video_id, prompt_id, content, created_at,
                                       model, strategy, prompt_hash, transcript_hash)
                VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)
                ON CONFLICT(video_id, prompt_id) DO UPDATE SET
                    content = excluded.content,
                    created_at = datetime('now'),
                    model = excluded.model,
                    strategy = excluded.strategy,
                    prompt_hash = excluded.prompt_hash,
                    transcript_hash = excluded.transcript_hash
                """,
                (video_id, prompt_id, content, model, strategy, prompt_hash, transcript_hash),
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

    def list_summaries(self, artist_id: Optional[str] = None) -> List[SummaryRow]:
        """Return all summaries, optionally filtered to an artist's videos."""
        with self._read_conn() as conn:
            if artist_id:
                cur = conn.execute(
                    "SELECT s.* FROM summaries s "
                    "JOIN videos v ON v.id = s.video_id "
                    "WHERE v.artist_id = ? "
                    "ORDER BY s.created_at DESC",
                    (artist_id,),
                )
            else:
                cur = conn.execute("SELECT * FROM summaries ORDER BY created_at DESC")
            return cur.fetchall()  # type: ignore[return-value]

    @staticmethod
    def _execute_chunked_in(
        conn: sqlite3.Connection,
        query_template: str,
        id_list: List[str],
        extra_params: Optional[List] = None,
    ) -> List:
        """Execute a query with IN clause in batches to stay under SQLite param limit.

        *query_template* must contain ``{placeholders}`` where the IN list goes.
        *extra_params* are prepended to each batch's parameter list (e.g. prompt_id).
        Returns all rows from all batches concatenated.
        """
        results: List = []
        prefix = extra_params or []
        for i in range(0, len(id_list), _IN_BATCH_SIZE):
            batch = id_list[i : i + _IN_BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            sql = query_template.format(placeholders=placeholders)
            cur = conn.execute(sql, prefix + batch)
            results.extend(cur.fetchall())
        return results

    def video_ids_with_transcripts(self, video_ids: List[str]) -> set:
        """Return the subset of video_ids that already have transcripts."""
        if not video_ids:
            return set()
        conn = self._conn()
        try:
            rows = self._execute_chunked_in(
                conn,
                "SELECT video_id FROM transcripts WHERE video_id IN ({placeholders})",
                video_ids,
            )
            return {row["video_id"] if isinstance(row, dict) else row[0] for row in rows}
        finally:
            conn.close()

    def video_ids_with_summary(self, video_ids: List[str], prompt_id: str) -> set:
        """Return the subset of video_ids that already have a summary for the given prompt_id."""
        if not video_ids:
            return set()
        conn = self._conn()
        try:
            rows = self._execute_chunked_in(
                conn,
                "SELECT video_id FROM summaries WHERE prompt_id = ? AND video_id IN ({placeholders})",
                video_ids,
                extra_params=[prompt_id],
            )
            return {row["video_id"] if isinstance(row, dict) else row[0] for row in rows}
        finally:
            conn.close()

    # ------ Staleness detection ------

    def get_stale_summary_counts(self) -> Dict[str, int]:
        """Count summaries whose prompt or transcript hash differs from current data.

        Returns dict with keys: stale_prompt, stale_transcript, stale_unknown, total_stale.
        NULL hashes count as stale_unknown (legacy rows without provenance).
        """
        from yt_artist.hashing import content_hash

        with self._read_conn() as conn:
            cur = conn.execute(
                "SELECT s.prompt_hash, s.transcript_hash, p.template, t.raw_text "
                "FROM summaries s "
                "LEFT JOIN prompts p ON p.id = s.prompt_id "
                "LEFT JOIN transcripts t ON t.video_id = s.video_id"
            )
            rows = cur.fetchall()

        stale_prompt = 0
        stale_transcript = 0
        stale_unknown = 0

        for row in rows:
            s_ph = row["prompt_hash"]
            s_th = row["transcript_hash"]
            if s_ph is None or s_th is None:
                stale_unknown += 1
                continue
            current_ph = content_hash(row["template"]) if row["template"] else None
            current_th = content_hash(row["raw_text"]) if row["raw_text"] else None
            if current_ph and s_ph != current_ph:
                stale_prompt += 1
            elif current_th and s_th != current_th:
                stale_transcript += 1

        return {
            "stale_prompt": stale_prompt,
            "stale_transcript": stale_transcript,
            "stale_unknown": stale_unknown,
            "total_stale": stale_prompt + stale_transcript + stale_unknown,
        }

    def get_stale_video_ids(self, video_ids: List[str], prompt_id: str) -> Dict[str, List[str]]:
        """Return video_ids whose summary is stale for *prompt_id*.

        Returns dict: stale_prompt, stale_transcript, stale_unknown — each a list of video_ids.
        """
        from yt_artist.hashing import content_hash

        empty: Dict[str, List[str]] = {"stale_prompt": [], "stale_transcript": [], "stale_unknown": []}
        if not video_ids:
            return empty

        conn = self._conn()
        try:
            rows = self._execute_chunked_in(
                conn,
                "SELECT s.video_id, s.prompt_hash, s.transcript_hash, "
                "p.template, t.raw_text "
                "FROM summaries s "
                "LEFT JOIN prompts p ON p.id = s.prompt_id "
                "LEFT JOIN transcripts t ON t.video_id = s.video_id "
                "WHERE s.prompt_id = ? AND s.video_id IN ({placeholders})",
                video_ids,
                extra_params=[prompt_id],
            )
        finally:
            conn.close()

        stale_prompt: List[str] = []
        stale_transcript: List[str] = []
        stale_unknown: List[str] = []

        for row in rows:
            vid = row["video_id"]
            s_ph = row["prompt_hash"]
            s_th = row["transcript_hash"]
            if s_ph is None or s_th is None:
                stale_unknown.append(vid)
                continue
            current_ph = content_hash(row["template"]) if row["template"] else None
            current_th = content_hash(row["raw_text"]) if row["raw_text"] else None
            if current_ph and s_ph != current_ph:
                stale_prompt.append(vid)
            elif current_th and s_th != current_th:
                stale_transcript.append(vid)

        return {
            "stale_prompt": stale_prompt,
            "stale_transcript": stale_transcript,
            "stale_unknown": stale_unknown,
        }

    # ------ Scoring ------

    def update_summary_scores(
        self,
        *,
        video_id: str,
        prompt_id: str,
        quality_score: Optional[float],
        heuristic_score: Optional[float],
        llm_score: Optional[float],
        faithfulness_score: Optional[float] = None,
        verification_score: Optional[float] = None,
    ) -> None:
        """Write quality scores to an existing summary row."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE summaries SET quality_score = ?, heuristic_score = ?, llm_score = ?, "
                "faithfulness_score = ?, verification_score = ? "
                "WHERE video_id = ? AND prompt_id = ?",
                (
                    quality_score,
                    heuristic_score,
                    llm_score,
                    faithfulness_score,
                    verification_score,
                    video_id,
                    prompt_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_unscored_summaries(self, prompt_id: str, video_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return summaries that have no quality_score yet for the given prompt.

        If *video_ids* is provided, restrict to that set.
        Returns list of dicts with at least video_id, prompt_id.
        """
        conn = self._conn()
        try:
            if video_ids:
                return self._execute_chunked_in(
                    conn,
                    "SELECT video_id, prompt_id FROM summaries "
                    "WHERE prompt_id = ? AND quality_score IS NULL AND video_id IN ({placeholders})",
                    video_ids,
                    extra_params=[prompt_id],
                )
            else:
                cur = conn.execute(
                    "SELECT video_id, prompt_id FROM summaries WHERE prompt_id = ? AND quality_score IS NULL",
                    (prompt_id,),
                )
                return cur.fetchall()
        finally:
            conn.close()

    def count_scored_summaries(self) -> int:
        """Return number of summaries that have a quality_score."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM summaries WHERE quality_score IS NOT NULL")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]
        finally:
            conn.close()

    def avg_quality_score(self) -> Optional[float]:
        """Return average quality_score across all scored summaries, or None if none scored."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT AVG(quality_score) AS avg_score FROM summaries WHERE quality_score IS NOT NULL")
            row = cur.fetchone()
            val = row["avg_score"] if isinstance(row, dict) else row[0]
            return round(val, 2) if val is not None else None
        finally:
            conn.close()

    # ------ Counts (for status command) ------

    def count_artists(self) -> int:
        """Return total number of artists."""
        with self._read_conn() as conn:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM artists")
            row = cur.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]

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

    # ------ Work Ledger ------

    def log_work(
        self,
        *,
        video_id: str,
        operation: str,
        status: str,
        started_at: str,
        finished_at: str,
        duration_ms: Optional[int] = None,
        model: Optional[str] = None,
        prompt_id: Optional[str] = None,
        strategy: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> int:
        """Append a work ledger entry. Returns the row id."""
        with self._write_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO work_ledger
                    (video_id, operation, model, prompt_id, strategy,
                     status, started_at, finished_at, duration_ms, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    operation,
                    model,
                    prompt_id,
                    strategy,
                    status,
                    started_at,
                    finished_at,
                    duration_ms,
                    (error_message or "")[:1000] if error_message else None,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_work_history(
        self,
        *,
        video_id: Optional[str] = None,
        artist_id: Optional[str] = None,
        operation: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query work ledger entries with optional filters.

        When *artist_id* is provided, joins through videos table.
        Returns newest-first.
        """
        with self._read_conn() as conn:
            if artist_id:
                sql = (
                    "SELECT wl.*, v.title AS video_title "
                    "FROM work_ledger wl "
                    "JOIN videos v ON v.id = wl.video_id "
                    "WHERE v.artist_id = ?"
                )
                params: list = [artist_id]
            else:
                sql = (
                    "SELECT wl.*, v.title AS video_title "
                    "FROM work_ledger wl "
                    "LEFT JOIN videos v ON v.id = wl.video_id "
                    "WHERE 1=1"
                )
                params = []

            if video_id:
                sql += " AND wl.video_id = ?"
                params.append(video_id)
            if operation:
                sql += " AND wl.operation = ?"
                params.append(operation)
            sql += " ORDER BY wl.started_at DESC LIMIT ?"
            params.append(limit)
            cur = conn.execute(sql, params)
            return cur.fetchall()

    def count_work_ledger(self) -> Dict[str, int]:
        """Return ledger counts by operation and status for the status command."""
        with self._read_conn() as conn:
            cur = conn.execute("SELECT operation, status, COUNT(*) AS cnt FROM work_ledger GROUP BY operation, status")
            rows = cur.fetchall()
        result: Dict[str, int] = {"total": 0}
        for row in rows:
            key = f"{row['operation']}_{row['status']}"
            result[key] = row["cnt"]
            result["total"] += row["cnt"]
        return result

    # ------ Jobs ------

    def create_job(self, *, job_id: str, command: str, log_file: str) -> None:
        """Insert a new job row with status='running'."""
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, command, status, pid, log_file) VALUES (?, ?, 'running', -1, ?)",
                (job_id, command, log_file),
            )

    def update_job_pid(self, job_id: str, pid: int) -> None:
        """Set the actual PID after subprocess launch."""
        with self._write_conn() as conn:
            conn.execute("UPDATE jobs SET pid = ? WHERE id = ?", (pid, job_id))

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a job by ID (supports prefix match for short IDs)."""
        with self._read_conn() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if row:
                return row
            cur = conn.execute(
                "SELECT * FROM jobs WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (job_id + "%",),
            )
            return cur.fetchone()

    def update_job_progress(
        self,
        job_id: str,
        *,
        done: Optional[int] = None,
        errors: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        """Update progress fields on a job row."""
        parts: List[str] = []
        params: List[Any] = []
        if total is not None:
            parts.append("total = ?")
            params.append(total)
        if done is not None:
            parts.append("done = ?")
            params.append(done)
        if errors is not None:
            parts.append("errors = ?")
            params.append(errors)
        if not parts:
            return
        params.append(job_id)
        with self._write_conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(parts)} WHERE id = ?", params)

    def finalize_job(self, job_id: str, status: str = "completed", error_message: Optional[str] = None) -> None:
        """Mark a job as finished (completed, failed, or stopped)."""
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = datetime('now'), error_message = ? WHERE id = ?",
                (status, error_message, job_id),
            )

    def mark_job_stale(self, job_id: str) -> None:
        """Mark a running job whose process died as failed."""
        with self._write_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', finished_at = datetime('now'), "
                "error_message = 'Process died unexpectedly' WHERE id = ? AND status = 'running'",
                (job_id,),
            )

    def list_recent_jobs(self, status_filter: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent jobs, optionally filtered by status."""
        with self._read_conn() as conn:
            if status_filter:
                cur = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY started_at DESC LIMIT ?",
                    (status_filter, limit),
                )
            else:
                cur = conn.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,))
            return cur.fetchall()

    def delete_old_jobs(self, max_age_days: int = 7) -> List[Dict[str, Any]]:
        """Delete finished jobs older than *max_age_days*.  Returns deleted rows."""
        with self.transaction() as conn:
            cur = conn.execute(
                "SELECT id, log_file FROM jobs WHERE status != 'running' AND finished_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            rows = cur.fetchall()
            if rows:
                conn.execute(
                    "DELETE FROM jobs WHERE status != 'running' AND finished_at < datetime('now', ?)",
                    (f"-{max_age_days} days",),
                )
        return rows

    # ------ Rate limiting ------

    def log_rate_request(self, request_type: str, cleanup_age_hours: int = 24) -> None:
        """Log a yt-dlp request and clean up old entries."""
        with self._write_conn() as conn:
            conn.execute(
                "INSERT INTO request_log (request_type) VALUES (?)",
                (request_type,),
            )
            conn.execute(
                "DELETE FROM request_log WHERE timestamp < datetime('now', ?)",
                (f"-{cleanup_age_hours} hours",),
            )

    def count_rate_requests(self, hours: int = 1) -> int:
        """Count yt-dlp requests in the last *hours* hours."""
        with self._read_conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) AS cnt FROM request_log WHERE timestamp > datetime('now', ?)",
                (f"-{hours} hours",),
            )
            row = cur.fetchone()
        return row["cnt"] if isinstance(row, dict) else row[0]

    # ------ Doctor helpers ------

    def get_unscored_transcripts(self) -> List[Dict[str, Any]]:
        """Return transcripts where quality_score is NULL."""
        with self._read_conn() as conn:
            cur = conn.execute("SELECT video_id, raw_text FROM transcripts WHERE quality_score IS NULL")
            return cur.fetchall()

    # ------ Helpers ------

    def urllist_path(self, artist_id: str, artist_name: str) -> str:
        """Compute path for artist urllist file: artist{id}{sanitized_name}-urllist.md"""
        from yt_artist.paths import urllist_rel_path

        return urllist_rel_path(artist_id, artist_name)
