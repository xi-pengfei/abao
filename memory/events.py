"""Event memory extraction.

Events are compact summaries of what happened across a small conversation
window. They are different from facts: facts say what is currently believed;
events preserve lived context.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


CompleteFn = Callable[[str], Optional[str]]


@dataclass
class EventDraft:
    summary: str
    event_type: str = "conversation_event"
    subjects: list[str] = field(default_factory=lambda: ["user"])
    topics: list[str] = field(default_factory=list)
    emotion: str = ""
    importance: float = 0.55
    confidence: float = 0.75
    source_memory_ids: list[int] = field(default_factory=list)


_EVENT_PROMPT = """你是阿宝的记忆整理器。请从最近一小段对话中提取值得长期记住的"事件记忆"。

事件记忆记录发生了什么、为什么值得记住、涉及谁、情绪或语境是什么。
不要提取稳定事实（名字、偏好等），这些由 facts 系统处理。
如果这段对话只是寒暄、重复、没有长期意义，返回空 events。

只输出 JSON，不要解释，不要 markdown。
格式：
{{
  "events": [
    {{
      "summary": "80字以内的中文事件摘要",
      "event_type": "daily_context|project_progress|relationship_moment|reflection|conversation_event",
      "subjects": ["user"],
      "topics": ["项目", "学习"],
      "emotion": "简短情绪/语境",
      "importance": 0.0到1.0,
      "confidence": 0.0到1.0,
      "source_memory_ids": [1, 2]
    }}
  ]
}}

最近对话：
{turns}
"""


class EventExtractor:
    """Pluggable event extractor backed by an LLM completion function."""

    def __init__(self, complete: CompleteFn):
        self.complete = complete

    def extract(self, turns: list[dict[str, Any]]) -> list[EventDraft]:
        if not turns:
            return []
        out = self.complete(_EVENT_PROMPT.format(turns=_format_turns(turns)))
        if not out:
            return []
        data = _parse_json_object(out)
        if not isinstance(data, dict):
            return []
        drafts = []
        for item in data.get("events", [])[:2]:
            draft = _draft_from_item(item)
            if draft:
                drafts.append(draft)
        return drafts


def _format_turns(turns: list[dict[str, Any]]) -> str:
    lines = []
    for t in turns:
        mid = t.get("memory_id")
        speaker = t.get("speaker", "unknown")
        text = str(t.get("text", "")).strip()
        if text:
            lines.append(f"- #{mid} [{speaker}] {text}")
    return "\n".join(lines)


def _parse_json_object(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _draft_from_item(item: Any) -> Optional[EventDraft]:
    if not isinstance(item, dict):
        return None
    summary = str(item.get("summary", "")).strip()
    if len(summary) < 8:
        return None
    return EventDraft(
        summary=summary[:120],
        event_type=str(item.get("event_type") or "conversation_event")[:40],
        subjects=_clean_list(item.get("subjects")) or ["user"],
        topics=_clean_list(item.get("topics"))[:8],
        emotion=str(item.get("emotion") or "")[:40],
        importance=_clamp_float(item.get("importance"), 0.55),
        confidence=_clamp_float(item.get("confidence"), 0.75),
        source_memory_ids=[
            int(x) for x in item.get("source_memory_ids", [])
            if isinstance(x, int) or (isinstance(x, str) and x.isdigit())
        ][:12],
    )


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s[:24])
    return out


def _clamp_float(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))
