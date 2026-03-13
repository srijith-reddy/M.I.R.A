# amma/memory/sqlite_memory.py
import os
import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any
from mira.utils import logger

DATA_DIR = "./data"
DB_PATH = os.path.join(DATA_DIR, "amma_memory.sqlite")

class SQLiteMemory:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.con = sqlite3.connect(db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                role TEXT,
                text TEXT,
                tags TEXT,
                meta TEXT
            );
        """)
        self.con.commit()

    def add(
        self,
        role: str,
        text: str,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None
    ) -> int:
        try:
            tags_json = json.dumps(tags or [])
            meta_json = json.dumps(meta or {})
            cur = self.con.execute(
                "INSERT INTO memories(role, text, tags, meta) VALUES (?, ?, ?, ?)",
                (role, text, tags_json, meta_json)
            )
            self.con.commit()
            mem_id = cur.lastrowid
            logger.log_event("SQLiteMemory.add", f"id={mem_id} role={role} tags={tags}")
            return mem_id
        except Exception as e:
            logger.log_error(e, context="SQLiteMemory.add")
            return -1

    def query(
        self,
        keyword: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        tag: Optional[str] = None,
        limit: int = 50,
        order: str = "DESC",
    ) -> List[Dict[str, Any]]:
        try:
            clauses = []
            params: List[Any] = []

            if keyword:
                clauses.append("LOWER(text) LIKE ?")
                params.append(f"%{keyword.lower()}%")

            if since:
                clauses.append("ts >= ?")
                params.append(since)

            if until:
                clauses.append("ts <= ?")
                params.append(until)

            if tag:
                clauses.append("tags LIKE ?")
                params.append(f"%{tag}%")

            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            q = f"""
                SELECT id, ts, role, text, tags, meta
                FROM memories
                {where}
                ORDER BY ts {order}
                LIMIT {int(limit)}
            """
            rows = self.con.execute(q, params).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append({
                    "id": r["id"],
                    "ts": r["ts"],
                    "role": r["role"],
                    "text": r["text"],
                    "tags": json.loads(r["tags"] or "[]"),
                    "meta": json.loads(r["meta"] or "{}"),
                })
            return out
        except Exception as e:
            logger.log_error(e, context="SQLiteMemory.query")
            return []

    def delete(self, mem_id: int) -> bool:
        try:
            self.con.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            self.con.commit()
            logger.log_event("SQLiteMemory.delete", f"id={mem_id}")
            return True
        except Exception as e:
            logger.log_error(e, context="SQLiteMemory.delete")
            return False
