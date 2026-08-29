import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

DB_PATH = Path("opportunities_chat.db")


from contextlib import contextmanager

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ensure_opportunities_schema(cursor: sqlite3.Cursor) -> None:
    """Create the opportunities table if missing and migrate metadata columns."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            url TEXT,
            content_hash TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(opportunities)").fetchall()}
    if "metadata_json" not in columns:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN metadata_json TEXT")
    if "deadline" not in columns:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN deadline TEXT")


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
    print(f"[DB] Initialized SQLite database at {DB_PATH.resolve()}")


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
            title = item.get("name") or item.get("title") or "Untitled Opportunity"
            content = item.get("content") or ""
            source = item.get("source") or item.get("portal") or ""
            url = item.get("url") or item.get("link") or ""
            metadata = item.get("metadata")
            meta_json = json.dumps(metadata) if isinstance(metadata, dict) else None
            deadline = None
            if isinstance(metadata, dict):
                deadline = metadata.get("deadline")

            # Compute content hash
            raw_str = f"{title}:{content[:200]}"
            content_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

            cursor.execute(
                """
                INSERT INTO opportunities (title, content, source, url, content_hash, created_at, metadata_json, deadline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    source=excluded.source,
                    url=excluded.url,
                    created_at=excluded.created_at,
                    metadata_json=COALESCE(excluded.metadata_json, opportunities.metadata_json),
                    deadline=COALESCE(excluded.deadline, opportunities.deadline)
                """,
                (title, content, source, url, content_hash, now, meta_json, deadline),
            )
            cursor.execute(
                "SELECT id FROM opportunities WHERE content_hash = ?",
                (content_hash,),
            )
            row = cursor.fetchone()
            row_ids.append(row["id"] if row else -1)
        conn.commit()
    return row_ids


def update_opportunity_metadata(opp_id: int, metadata: Dict[str, Any]) -> bool:
    """Persist enriched metadata for an opportunity row."""
    if not isinstance(metadata, dict):
        return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_opportunities_schema(cursor)
        cursor.execute(
            """
            UPDATE opportunities
            SET metadata_json = ?, deadline = COALESCE(?, deadline)
            WHERE id = ?
            """,
            (
                json.dumps(metadata),
                metadata.get("deadline"),
                opp_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def _parse_opportunity_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a DB row into a dict with parsed metadata."""
    item = dict(row)
    meta_json = item.pop("metadata_json", None)
    if meta_json:
        try:
            item["metadata"] = json.loads(meta_json)
        except Exception:
            item["metadata"] = None
    else:
        item["metadata"] = None
    return item


def list_opportunities(
    query: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """List opportunities with pagination and optional search filter."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        sql = (
            "SELECT id, title, content, source, url, created_at, "
            "metadata_json, deadline FROM opportunities WHERE 1=1"
        )
        params: List[Any] = []

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


def get_opportunity_by_id(opp_id: int) -> Optional[Dict[str, Any]]:
    """Get single opportunity by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            (
                "SELECT id, title, content, source, url, created_at, "
                "metadata_json, deadline FROM opportunities WHERE id = ?"
            ),
            (opp_id,),
        )
        row = cursor.fetchone()
        return _parse_opportunity_row(row) if row else None

