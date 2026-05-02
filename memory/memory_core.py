"""记忆核心：对外的统一接口。

这一层封装四层存储（store + growth_diary），处理：
  - insert：判断该入哪一层，自动加默认 tag/persons
  - retrieve：综合检索，按当前对话上下文返回相关记忆
  - decay：周期性更新重要性

Phase 1 的检索是简单的关键词 + 重要性排序。
Phase 2 接 Qdrant 后，retrieve 内部换成向量检索，但接口不变。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .events import EventDraft, EventExtractor
from .facts import Fact, extract_facts
from .growth_diary import GrowthDiary, DiaryEntry
from .layers import MemoryRecord, MemoryStore, MemoryType


class MemoryCore:
    """对外提供统一的记忆 API。"""

    def __init__(
        self,
        store: MemoryStore,
        diary: GrowthDiary,
        embed: Optional[Callable[[str], Optional[list[float]]]] = None,
        embedding_model: str = "",
    ):
        self.store = store
        self.diary = diary
        self.embed = embed
        self.embedding_model = embedding_model

    # ---- 写入 ----

    def insert(
        self,
        content: str,
        mem_type: str = MemoryType.CONVERSATION.value,
        importance: float = 0.5,
        related_persons: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        emotion: float = 0.0,
        extra: Optional[dict] = None,
    ) -> int:
        record = MemoryRecord(
            id=None,
            mem_type=mem_type,
            content=content,
            importance=importance,
            related_persons=related_persons or ["self"],
            tags=tags or [],
            emotion=emotion,
            extra=extra or {},
        )
        memory_id = self.store.insert(record)
        self.index_memory(memory_id, content)
        return memory_id

    def insert_conversation_turn(
        self,
        speaker: str,
        text: str,
        emotion: float = 0.0,
    ) -> int:
        """便捷接口：记录一轮对话。"""
        return self.insert(
            content=f"[{speaker}] {text}",
            mem_type=MemoryType.CONVERSATION.value,
            importance=0.3 + 0.4 * min(1.0, emotion),  # 情绪强→重要性高
            related_persons=[speaker, "self"] if speaker != "self" else ["self"],
            emotion=emotion,
        )

    def observe_user_text(
        self,
        text: str,
        *,
        speaker: str = "user",
        source_memory_id: Optional[int] = None,
    ) -> list[Fact]:
        """Promote stable facts from a user utterance into long-term memory."""
        facts = extract_facts(
            text,
            subject=speaker,
            source_memory_id=source_memory_id,
        )
        for fact in facts:
            self.store.upsert_fact(fact)
        return facts

    def insert_event(self, draft: EventDraft) -> Optional[int]:
        """Persist one event summary if it is not an exact duplicate."""
        recent_events = self.store.list_by_type(MemoryType.EVENT.value, limit=50)
        if any(r.content == draft.summary for r in recent_events):
            return None
        return self.insert(
            content=draft.summary,
            mem_type=MemoryType.EVENT.value,
            importance=max(0.2, min(1.0, draft.importance)),
            related_persons=draft.subjects or ["user"],
            tags=draft.topics,
            extra={
                "event_type": draft.event_type,
                "emotion": draft.emotion,
                "confidence": draft.confidence,
                "source_memory_ids": draft.source_memory_ids,
                "extractor": "llm",
            },
        )

    def index_memory(self, memory_id: int, text: str) -> bool:
        if not self.embed or not self.embedding_model:
            return False
        vector = self.embed(text)
        if vector is None:
            return False
        self.store.upsert_embedding(
            memory_id,
            model=self.embedding_model,
            vector=vector,
        )
        return True

    def index_unembedded(self, limit: int = 100) -> int:
        count = 0
        for record in self.store.unembedded_memories(
            mem_types=[MemoryType.CONVERSATION.value, MemoryType.EVENT.value, MemoryType.PROJECT.value],
            limit=limit,
        ):
            if record.id is not None and self.index_memory(record.id, record.content):
                count += 1
        return count

    def consolidate_events(
        self,
        turns: list[dict],
        extractor: EventExtractor,
    ) -> list[int]:
        """Ask the extractor for event drafts and persist accepted events."""
        inserted = []
        for draft in extractor.extract(turns):
            event_id = self.insert_event(draft)
            if event_id is not None:
                inserted.append(event_id)
        return inserted

    # ---- 检索 ----

    def retrieve_relevant(
        self,
        cue: str,
        speaker: Optional[str] = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """根据当前对话片段检索相关记忆。

        Phase 1: LIKE 检索 + 按重要性排序。
        Phase 2: 更强的向量/FTS 检索 + 多维加权（重要性、时间、人物关联、情绪）。
        """
        keywords = _query_terms(cue)[:8]
        results: dict[int, MemoryRecord] = {}
        scores: dict[int, float] = {}
        if self.embed:
            query_vector = self.embed(cue)
            if query_vector:
                semantic = self.store.semantic_search(
                    query_vector,
                    limit=limit * 2,
                    mem_types=[MemoryType.EVENT.value, MemoryType.CONVERSATION.value, MemoryType.PROJECT.value],
                )
                for r, score in semantic:
                    if r.id is not None:
                        results[r.id] = r
                        scores[r.id] = scores.get(r.id, 0.0) + 3.0 * score
        for kw in keywords:
            for r in self.store.search_text(kw, limit=limit):
                if r.id is not None:
                    results[r.id] = r
                    scores[r.id] = scores.get(r.id, 0.0) + 1.0
        # 如果按 speaker 过滤
        if speaker:
            results = {
                rid: r for rid, r in results.items()
                if speaker in r.related_persons or "self" in r.related_persons
            }
        # 触摸被检索到的记忆，提升其重要性
        for rid in results:
            self.store.touch(rid)
        ranked = sorted(
            results.values(),
            key=lambda r: (
                scores.get(r.id or -1, 0.0),
                0.3 if r.mem_type == MemoryType.EVENT.value else 0.0,
                r.importance,
                r.created_at,
            ),
            reverse=True,
        )
        return ranked[:limit]

    def core_facts(self, speaker: str = "user", limit: int = 8) -> list[Fact]:
        """Small set of stable facts that can safely follow the speaker across turns."""
        return self.store.active_facts(
            subject=speaker,
            predicates=["name", "preferred_name", "likes", "dislikes", "current_project", "goal"],
            limit=limit,
        )

    def recent_diary(self, n: int = 3) -> list[DiaryEntry]:
        return self.diary.recent(n)

    # ---- 关闭 ----

    def close(self) -> None:
        self.store.close()


def _query_terms(text: str) -> list[str]:
    """Small local query tokenizer for Phase 1.

    It keeps ASCII words and adds Chinese bigrams/trigrams so retrieval no
    longer depends on spaces in Chinese input.
    """
    terms: list[str] = []
    terms.extend(w for w in re.findall(r"[A-Za-z0-9_]{2,}", text))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", text))
    for n in (3, 2):
        for i in range(max(0, len(chinese) - n + 1)):
            terms.append(chinese[i:i + n])
    out = []
    seen = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out
