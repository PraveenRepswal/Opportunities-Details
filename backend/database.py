import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.text_utils import is_english_content

logger = logging.getLogger(__name__)

DB_PATH = Path("opportunities_chat.db")

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


_schema_ensured_dbs: set[str] = set()


def _ensure_opportunities_schema(cursor: sqlite3.Cursor, force: bool = False) -> None:
    """Create the opportunities table if missing and migrate metadata columns."""
    db_name = ""
    try:
        row = cursor.connection.execute("PRAGMA database_list").fetchone()
        db_name = row[2] if row and len(row) > 2 else ""
    except Exception:
        pass

    if db_name and db_name != ":memory:" and db_name in _schema_ensured_dbs and not force:
        return
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            url TEXT,
            content_hash TEXT UNIQUE,
            created_at TEXT NOT NULL,
            metadata_json TEXT,
            deadline TEXT,
            profile_json TEXT,
            is_curated INTEGER DEFAULT 0
        )
        """
    )

    # Performance indexes for metadata filtering and ordering
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_deadline ON opportunities(deadline)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_source ON opportunities(source)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_curated ON opportunities(is_curated)")

    # SQLite FTS5 Full-Text Search Virtual Table and Automatic Triggers
    try:
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS opportunities_fts USING fts5(
                title,
                content,
                content='opportunities',
                content_rowid='id'
            );
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS opportunities_ai AFTER INSERT ON opportunities BEGIN
                INSERT INTO opportunities_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
            END;
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS opportunities_ad AFTER DELETE ON opportunities BEGIN
                INSERT INTO opportunities_fts(opportunities_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
            END;
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS opportunities_au AFTER UPDATE ON opportunities BEGIN
                INSERT INTO opportunities_fts(opportunities_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
                INSERT INTO opportunities_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
            END;
            """
        )
    except Exception:
        pass

    if db_name and db_name != ":memory:":
        _schema_ensured_dbs.add(db_name)


def init_db():
    """Initialize SQLite database tables for chat sessions and messages."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
            )
            """
        )
        _ensure_opportunities_schema(cursor)
        conn.commit()
    purged = purge_non_english_opportunities()
    if purged:
        print(f"[DB] Purged {purged} non-English opportunities from database.")
    print(f"[DB] Initialized SQLite database at {DB_PATH.resolve()}")


def purge_non_english_opportunities() -> int:
    """Purge all non-English opportunities currently stored in SQLite."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)
        cursor.execute("SELECT id, title, content FROM opportunities")
        rows = cursor.fetchall()
        to_delete = [
            r["id"] for r in rows
            if not is_english_content(r["title"] or "", r["content"] or "")
        ]
        if to_delete:
            cursor.executemany("DELETE FROM opportunities WHERE id = ?", [(i,) for i in to_delete])
            conn.commit()
            logger.info("Purged %d non-English opportunities from database.", len(to_delete))
        return len(to_delete)


def create_session(session_id: Optional[str] = None, title: str = "New Chat") -> str:
    """Create a new chat session."""
    sid = session_id or str(uuid4())
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO sessions (session_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (sid, title, now, now),
        )
        conn.commit()
    return sid


def list_sessions() -> List[Dict[str, Any]]:
    """List all chat sessions ordered by last update."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT session_id, title, created_at, updated_at
            FROM sessions
            ORDER BY updated_at DESC
            """
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    """Get all messages for a specific session."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, session_id, role, content, metadata_json, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
        rows = cursor.fetchall()
        messages = []
        for r in rows:
            d = dict(r)
            if d.get("metadata_json"):
                try:
                    d["metadata"] = json.loads(d["metadata_json"])
                except Exception:
                    d["metadata"] = None
            else:
                d["metadata"] = None
            del d["metadata_json"]
            messages.append(d)
        return messages


def add_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    auto_create_session: bool = True,
):
    """Add a message to a session."""
    now = datetime.now().isoformat()
    meta_json = json.dumps(metadata) if metadata else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if auto_create_session:
            cursor.execute("SELECT session_id FROM sessions WHERE session_id = ?", (session_id,))
            if not cursor.fetchone():
                # Derive title from user content preview
                title_preview = content[:40].strip() + ("..." if len(content) > 40 else "")
                cursor.execute(
                    "INSERT INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (session_id, title_preview or "New Chat", now, now),
                )

        cursor.execute(
            """
            INSERT INTO messages (session_id, role, content, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, meta_json, now),
        )

        # Update session title if user prompt and session title is default
        if role == "user":
            cursor.execute("SELECT title FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row["title"] == "New Chat":
                new_title = content[:35].strip() + ("..." if len(content) > 35 else "")
                cursor.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                    (new_title, now, session_id),
                )
            else:
                cursor.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
        else:
            cursor.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))

        conn.commit()


def delete_session(session_id: str):
    """Delete a session and all its messages."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()


def upsert_opportunities(items: List[Dict[str, Any]]) -> List[int]:
    """Upsert opportunity items into SQLite database based on SHA-256 content hash.

    Items may carry a ``metadata`` dict and ``deadline`` string produced by the
    metadata extractor. Returns database row IDs aligned with the input order.
    """
    import hashlib
    row_ids: List[int] = []
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)
        for item in items:
            title = (item.get("name") or item.get("title") or "Untitled Opportunity").strip()
            content = item.get("content") or ""
            if not is_english_content(title, content):
                logger.info("Skipping non-English opportunity during upsert: %s", title)
                continue
            source = item.get("source") or item.get("portal") or ""
            url = item.get("url") or item.get("link") or ""
            metadata = item.get("metadata")
            meta_json = json.dumps(metadata) if isinstance(metadata, dict) else None
            profile = item.get("profile")
            profile_json = json.dumps(profile) if isinstance(profile, dict) else None
            deadline = None
            if isinstance(metadata, dict):
                deadline = metadata.get("deadline")
            elif isinstance(profile, dict) and profile.get("deadline"):
                deadline = profile.get("deadline")

            is_curated = int(item.get("is_curated") or 0)
            if not is_curated:
                t_lower = title.lower()
                if "1#" in content or "10#" in content or t_lower.startswith("top 10") or t_lower.startswith("top scholarship"):
                    is_curated = 1

            # Compute content hash
            raw_str = f"{title}:{content[:200]}"
            content_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

            cursor.execute(
                """
                INSERT INTO opportunities (title, content, source, url, content_hash, created_at, metadata_json, deadline, profile_json, is_curated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    source=excluded.source,
                    url=excluded.url,
                    created_at=excluded.created_at,
                    metadata_json=COALESCE(excluded.metadata_json, opportunities.metadata_json),
                    deadline=COALESCE(excluded.deadline, opportunities.deadline),
                    profile_json=COALESCE(excluded.profile_json, opportunities.profile_json),
                    is_curated=CASE WHEN excluded.is_curated = 1 THEN 1 ELSE opportunities.is_curated END
                """,
                (title, content, source, url, content_hash, now, meta_json, deadline, profile_json, is_curated),
            )
            cursor.execute(
                "SELECT id FROM opportunities WHERE content_hash = ?",
                (content_hash,),
            )
            row = cursor.fetchone()
            row_ids.append(row["id"] if row else -1)
        conn.commit()
    return row_ids

def update_opportunity_profile_and_metadata(opp_id: int, profile: Dict[str, Any]) -> bool:
    """Atomically persist extracted OpportunityProfile and synchronize metadata fields."""
    if not isinstance(profile, dict):
        return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)

        cursor.execute("SELECT metadata_json FROM opportunities WHERE id = ?", (opp_id,))
        row = cursor.fetchone()
        existing_meta = {}
        if row and row["metadata_json"]:
            try:
                existing_meta = json.loads(row["metadata_json"]) or {}
            except Exception:
                existing_meta = {}

        for key in ("deadline", "organization", "location", "type"):
            val = profile.get(key)
            if val:
                existing_meta[key] = val

        deadline = profile.get("deadline") or existing_meta.get("deadline")

        cursor.execute(
            """
            UPDATE opportunities
            SET profile_json = ?, metadata_json = ?, deadline = COALESCE(?, deadline)
            WHERE id = ?
            """,
            (
                json.dumps(profile),
                json.dumps(existing_meta),
                deadline,
                opp_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def _parse_opportunity_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a DB row into a dict with parsed metadata and profile."""
    item = dict(row)
    item["is_curated"] = int(item.get("is_curated") or 0)
    meta_json = item.pop("metadata_json", None)
    if meta_json:
        try:
            item["metadata"] = json.loads(meta_json)
        except Exception:
            item["metadata"] = None
    else:
        item["metadata"] = None

    prof_json = item.pop("profile_json", None)
    if prof_json:
        try:
            item["profile"] = json.loads(prof_json)
        except Exception:
            item["profile"] = None
    else:
        item["profile"] = None

    return item


def list_opportunities(
    query: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    include_curated: bool = True,
    include_content: bool = True,
) -> Dict[str, Any]:
    """List opportunities with pagination, optional FTS5 full-text ranking, and content toggle."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)

        content_col = "content" if include_content else "'' as content"

        # Attempt high-performance SQLite FTS5 search if a search query is provided
        clean_tokens = [re.sub(r"[^\w]", "", t) for t in (query or "").split() if re.sub(r"[^\w]", "", t)]
        if clean_tokens:
            fts_query = " ".join(f'"{t}"*' for t in clean_tokens)
            try:
                base_from = "FROM opportunities o JOIN opportunities_fts fts ON o.id = fts.rowid"
                where_clauses = ["opportunities_fts MATCH ?"]
                params: List[Any] = [fts_query]

                if not include_curated:
                    where_clauses.append("(o.is_curated IS NULL OR o.is_curated = 0)")

                if source:
                    where_clauses.append("o.source = ?")
                    params.append(source)

                where_sql = " AND ".join(where_clauses)
                count_sql = f"SELECT COUNT(*) as total {base_from} WHERE {where_sql}"
                cursor.execute(count_sql, params)
                total = cursor.fetchone()["total"]

                data_sql = (
                    f"SELECT o.id, o.title, o.{content_col}, o.source, o.url, o.created_at, "
                    f"o.metadata_json, o.deadline, o.profile_json, o.is_curated "
                    f"{base_from} WHERE {where_sql} "
                    f"ORDER BY fts.rank ASC, o.id DESC LIMIT ? OFFSET ?"
                )
                cursor.execute(data_sql, params + [limit, offset])
                rows = [_parse_opportunity_row(r) for r in cursor.fetchall()]

                return {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "items": rows,
                }
            except Exception:
                # Graceful fallback to standard LIKE query if FTS query syntax fails
                pass

        # Standard non-FTS query or fallback
        sql = (
            f"SELECT id, title, {content_col}, source, url, created_at, "
            f"metadata_json, deadline, profile_json, is_curated FROM opportunities WHERE 1=1"
        )
        params = []

        if not include_curated:
            sql += " AND (is_curated IS NULL OR is_curated = 0)"

        if query:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])

        if source:
            sql += " AND source = ?"
            params.append(source)

        # Count query
        count_sql = f"SELECT COUNT(*) as total FROM ({sql})"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()["total"]

        # Pagination
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(sql, params)
        rows = [_parse_opportunity_row(r) for r in cursor.fetchall()]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": rows,
        }


def list_curated_opportunities(limit: int = 100) -> List[Dict[str, Any]]:
    """List curated roundup / Top-N directory opportunities."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)
        cursor.execute(
            """
            SELECT id, title, content, source, url, created_at,
                   metadata_json, deadline, profile_json, is_curated
            FROM opportunities
            WHERE is_curated = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [_parse_opportunity_row(r) for r in cursor.fetchall()]
        return [r for r in rows if is_english_content(r.get("title", ""), r.get("content", ""))]


def get_opportunity_by_id(opp_id: int) -> Optional[Dict[str, Any]]:
    """Get single opportunity by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)
        cursor.execute(
            (
                "SELECT id, title, content, source, url, created_at, "
                "metadata_json, deadline, profile_json, is_curated FROM opportunities WHERE id = ?"
            ),
            (opp_id,),
        )
        row = cursor.fetchone()
        return _parse_opportunity_row(row) if row else None



def get_opportunity_by_title_or_query(query_or_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full opportunity row by ID or fuzzy title search."""
    if not query_or_id or not str(query_or_id).strip():
        return None

    clean_query = str(query_or_id).strip()
    if clean_query.isdigit():
        return get_opportunity_by_id(int(clean_query))

    import re
    cleaned = re.sub(
        r"^(?:can\s+you\s+)?(?:please\s+)?(?:give\s+me|tell\s+me|show\s+me|find|get)\s+(?:all\s+|full\s+|more\s+)?(?:details|information|info)?\s*(?:about|on|for|regarding)?\s*",
        "",
        clean_query,
        flags=re.IGNORECASE,
    ).strip()
    search_query = cleaned if cleaned else clean_query

    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)

        # 1. Exact or broad substring match on title
        cursor.execute(
            """
            SELECT id, title, content, source, url, created_at,
                   metadata_json, deadline, profile_json, is_curated
            FROM opportunities
            WHERE lower(title) LIKE ?
            ORDER BY id DESC LIMIT 1
            """,
            (f"%{search_query.lower()}%",),
        )
        row = cursor.fetchone()
        if row:
            return _parse_opportunity_row(row)

        # 2. Token-based fallback match (filtering stopwords and noise tokens)
        stopwords = {
            "the", "for", "and", "in", "at", "to", "of", "a", "an", "on",
            "scholarship", "internship", "fellowship", "program", "programs",
            "give", "tell", "show", "details", "detail", "about", "full", "all",
            "info", "information", "with", "what", "is", "are", "how", "apply", "me", "you",
            "options", "option", "latest", "available", "can", "good", "best", "some", "any", "find",
            "looking", "want", "need",
        }
        words = [w for w in re.split(r"\W+", search_query.lower()) if len(w) > 2 and w not in stopwords]
        if words:
            like_clauses = " AND ".join(["lower(title) LIKE ?" for _ in words[:3]])
            params = [f"%{w}%" for w in words[:3]]
            cursor.execute(
                f"""
                SELECT id, title, content, source, url, created_at,
                       metadata_json, deadline, profile_json, is_curated
                FROM opportunities
                WHERE {like_clauses}
                ORDER BY LENGTH(title) ASC, id DESC LIMIT 1
                """,
                tuple(params),
            )
            row = cursor.fetchone()
            if row:
                return _parse_opportunity_row(row)

    return None

