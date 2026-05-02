"""四层记忆的数据结构。

文档 v2 4.1：
  成长记忆      → 见 growth_diary.py，独立存储，永不衰减
  身份记忆      → 关于自己 + 关于重要的人。永久稳定，但可更新
  项目记忆      → 当前在做什么。动态权重，活跃时高，结束后降权
  对话记忆      → 最近若干轮原文。超过阈值压缩成摘要降入项目记忆

这一层只定义数据结构和持久化接口，不做检索智能。
检索由 memory_core.py 调度。
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .facts import Fact, SCALAR_PREDICATES


class MemoryType(str, Enum):
    IDENTITY = "identity"
    PROJECT = "project"
    CONVERSATION = "conversation"
    EVENT = "event"
    PREFERENCE = "preference"   # 偏好类（"不喜欢吃香菜"）走身份层


@dataclass
class MemoryRecord:
    id: Optional[int]              # 由 SQLite 分配
    mem_type: str
    content: str
    importance: float = 0.5        # 初始重要性
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now().isoformat())
    access_count: int = 0
    related_persons: list[str] = field(default_factory=list)   # ["self", "pengfei", ...]
    tags: list[str] = field(default_factory=list)
    emotion: float = 0.0           # 当时情绪强度，影响重要性
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> tuple:
        return (
            self.mem_type,
            self.content,
            self.importance,
            self.created_at,
            self.last_accessed,
            self.access_count,
            json.dumps(self.related_persons, ensure_ascii=False),
            json.dumps(self.tags, ensure_ascii=False),
            self.emotion,
            json.dumps(self.extra, ensure_ascii=False),
        )

    @classmethod
    def from_row(cls, row: tuple) -> "MemoryRecord":
        return cls(
            id=row[0],
            mem_type=row[1],
            content=row[2],
            importance=row[3],
            created_at=row[4],
            last_accessed=row[5],
            access_count=row[6],
            related_persons=json.loads(row[7]) if row[7] else [],
            tags=json.loads(row[8]) if row[8] else [],
            emotion=row[9] or 0.0,
            extra=json.loads(row[10]) if row[10] else {},
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mem_type TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    related_persons TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    emotion REAL NOT NULL DEFAULT 0.0,
    extra TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(mem_type);
CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.85,
    status TEXT NOT NULL DEFAULT 'active',
    source_memory_id INTEGER,
    source_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class MemoryStore:
    """记忆的 SQLite 存储。

    FastAPI streaming may run the conversation generator in a worker thread; the
    server guards Abao with a lock, so sharing one connection across threads is
    acceptable for this single-instance app.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def insert(self, record: MemoryRecord) -> int:
        cur = self._conn.execute(
            """INSERT INTO memories
               (mem_type, content, importance, created_at, last_accessed,
                access_count, related_persons, tags, emotion, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            record.to_row(),
        )
        self._conn.commit()
        record.id = cur.lastrowid
        return record.id

    def get(self, id_: int) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            "SELECT id, mem_type, content, importance, created_at, last_accessed, "
            "access_count, related_persons, tags, emotion, extra FROM memories WHERE id = ?",
            (id_,),
        ).fetchone()
        return MemoryRecord.from_row(row) if row else None

    def list_by_type(self, mem_type: str, limit: int = 50) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT id, mem_type, content, importance, created_at, last_accessed, "
            "access_count, related_persons, tags, emotion, extra FROM memories "
            "WHERE mem_type = ? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (mem_type, limit),
        ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def search_text(self, keyword: str, limit: int = 10) -> list[MemoryRecord]:
        """Phase 1 简单 LIKE 检索。Phase 2 接 Qdrant 后这里换成向量检索。"""
        rows = self._conn.execute(
            "SELECT id, mem_type, content, importance, created_at, last_accessed, "
            "access_count, related_persons, tags, emotion, extra FROM memories "
            "WHERE content LIKE ? ORDER BY importance DESC LIMIT ?",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def recent_conversations(self, limit: int = 16) -> list[MemoryRecord]:
        """按时间倒序取最近的对话记录，调用方 reverse() 后得到正序。"""
        rows = self._conn.execute(
            "SELECT id, mem_type, content, importance, created_at, last_accessed, "
            "access_count, related_persons, tags, emotion, extra FROM memories "
            "WHERE mem_type = ? ORDER BY created_at DESC LIMIT ?",
            (MemoryType.CONVERSATION.value, limit),
        ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def touch(self, id_: int) -> None:
        """记忆被访问时更新 access_count 和 last_accessed。"""
        self._conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (datetime.now().isoformat(), id_),
        )
        self._conn.commit()

    # ---- 结构化事实 ----

    def upsert_fact(self, fact: Fact) -> int:
        """Insert or update a stable fact.

        Scalar predicates keep one active value per subject. Multi-value
        predicates deduplicate exact active matches and otherwise append.
        """
        now = datetime.now().isoformat()
        existing = self._conn.execute(
            "SELECT id, subject, predicate, value, confidence, status, source_memory_id, "
            "source_text, created_at, updated_at, extra FROM facts "
            "WHERE subject = ? AND predicate = ? AND value = ? AND status = 'active'",
            (fact.subject, fact.predicate, fact.value),
        ).fetchone()
        if existing:
            row_id = existing[0]
            self._conn.execute(
                "UPDATE facts SET confidence = MAX(confidence, ?), updated_at = ?, "
                "source_memory_id = COALESCE(?, source_memory_id), source_text = COALESCE(NULLIF(?, ''), source_text) "
                "WHERE id = ?",
                (fact.confidence, now, fact.source_memory_id, fact.source_text, row_id),
            )
            self._conn.commit()
            return row_id

        if fact.predicate in SCALAR_PREDICATES:
            self._conn.execute(
                "UPDATE facts SET status = 'superseded', updated_at = ? "
                "WHERE subject = ? AND predicate = ? AND status = 'active'",
                (now, fact.subject, fact.predicate),
            )

        if fact.predicate == "likes":
            self._supersede_opposite_fact(fact.subject, "dislikes", fact.value, now)
        elif fact.predicate == "dislikes":
            self._supersede_opposite_fact(fact.subject, "likes", fact.value, now)

        cur = self._conn.execute(
            """INSERT INTO facts
               (subject, predicate, value, confidence, status, source_memory_id,
                source_text, created_at, updated_at, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fact.subject,
                fact.predicate,
                fact.value,
                fact.confidence,
                fact.status,
                fact.source_memory_id,
                fact.source_text,
                fact.created_at,
                now,
                json.dumps(fact.extra, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def _supersede_opposite_fact(
        self,
        subject: str,
        predicate: str,
        value: str,
        now: str,
    ) -> None:
        self._conn.execute(
            "UPDATE facts SET status = 'superseded', updated_at = ? "
            "WHERE subject = ? AND predicate = ? AND value = ? AND status = 'active'",
            (now, subject, predicate, value),
        )

    def active_facts(
        self,
        subject: str = "user",
        predicates: Optional[list[str]] = None,
        limit: int = 12,
    ) -> list[Fact]:
        params: list[Any] = [subject]
        pred_sql = ""
        if predicates:
            placeholders = ",".join("?" * len(predicates))
            pred_sql = f" AND predicate IN ({placeholders})"
            params.extend(predicates)
        params.append(limit)
        rows = self._conn.execute(
            "SELECT id, subject, predicate, value, confidence, status, source_memory_id, "
            "source_text, created_at, updated_at, extra FROM facts "
            f"WHERE subject = ? AND status = 'active'{pred_sql} "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._fact_from_row(r) for r in rows]

    def clear_facts(self) -> None:
        self._conn.execute("DELETE FROM facts")
        self._conn.commit()

    # ---- 语义索引 ----

    def upsert_embedding(
        self,
        memory_id: int,
        *,
        model: str,
        vector: list[float],
    ) -> None:
        if not vector:
            return
        self._conn.execute(
            """INSERT INTO memory_embeddings (memory_id, model, dim, vector, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(memory_id) DO UPDATE SET
                   model = excluded.model,
                   dim = excluded.dim,
                   vector = excluded.vector,
                   updated_at = excluded.updated_at""",
            (
                memory_id,
                model,
                len(vector),
                json.dumps(vector),
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def semantic_search(
        self,
        query_vector: list[float],
        *,
        limit: int = 8,
        mem_types: Optional[list[str]] = None,
    ) -> list[tuple[MemoryRecord, float]]:
        if not query_vector:
            return []
        params: list[Any] = []
        type_sql = ""
        if mem_types:
            placeholders = ",".join("?" * len(mem_types))
            type_sql = f" AND m.mem_type IN ({placeholders})"
            params.extend(mem_types)
        rows = self._conn.execute(
            "SELECT m.id, m.mem_type, m.content, m.importance, m.created_at, "
            "m.last_accessed, m.access_count, m.related_persons, m.tags, m.emotion, m.extra, "
            "e.vector FROM memories m JOIN memory_embeddings e ON m.id = e.memory_id "
            f"WHERE 1=1{type_sql}",
            params,
        ).fetchall()
        scored = []
        for row in rows:
            vector = json.loads(row[11])
            score = _cosine(query_vector, vector)
            if score > 0:
                scored.append((MemoryRecord.from_row(row[:11]), score))
        scored.sort(key=lambda item: (item[1], item[0].importance), reverse=True)
        return scored[:limit]

    def unembedded_memories(
        self,
        *,
        mem_types: Optional[list[str]] = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        params: list[Any] = []
        type_sql = ""
        if mem_types:
            placeholders = ",".join("?" * len(mem_types))
            type_sql = f" AND m.mem_type IN ({placeholders})"
            params.extend(mem_types)
        params.append(limit)
        rows = self._conn.execute(
            "SELECT m.id, m.mem_type, m.content, m.importance, m.created_at, "
            "m.last_accessed, m.access_count, m.related_persons, m.tags, m.emotion, m.extra "
            "FROM memories m LEFT JOIN memory_embeddings e ON m.id = e.memory_id "
            f"WHERE e.memory_id IS NULL{type_sql} ORDER BY m.id ASC LIMIT ?",
            params,
        ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def _fact_from_row(self, row: tuple) -> Fact:
        return Fact(
            id=row[0],
            subject=row[1],
            predicate=row[2],
            value=row[3],
            confidence=row[4],
            status=row[5],
            source_memory_id=row[6],
            source_text=row[7],
            created_at=row[8],
            updated_at=row[9],
            extra=json.loads(row[10]) if row[10] else {},
        )

    def close(self) -> None:
        self._conn.close()


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
