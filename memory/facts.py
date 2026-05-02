"""Structured long-term facts extracted from conversation.

This module stays deliberately small: it only handles stable facts that are
safe to promote out of raw conversation logs. Broader semantic memory can plug
in later without changing the storage contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


SCALAR_PREDICATES = {"name", "preferred_name"}


@dataclass
class Fact:
    id: Optional[int]
    subject: str
    predicate: str
    value: str
    confidence: float = 0.85
    status: str = "active"
    source_memory_id: Optional[int] = None
    source_text: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extra: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        labels = {
            "name": "名字",
            "preferred_name": "希望被称呼为",
            "likes": "喜欢",
            "dislikes": "不喜欢",
            "current_project": "正在做",
            "goal": "目标",
        }
        return labels.get(self.predicate, self.predicate)

    def format_for_prompt(self) -> str:
        return f"{self.subject} {self.label()}：{self.value}"


_NAME_PATTERNS = [
    re.compile(r"我叫([^\s，。！？,.!?\d]{1,12})"),
    re.compile(r"我的名字(?:是|叫)([^\s，。！？,.!?\d]{1,12})"),
    re.compile(r"[Mm]y name is ([A-Za-z][A-Za-z\s]{0,30})"),
]

_PREFERRED_NAME_PATTERNS = [
    re.compile(r"(?:以后)?(?:你)?(?:可以)?叫我([^\s，。！？,.!?\d]{1,12})"),
]

_LIKES_PATTERNS = [
    re.compile(r"我(?:很|挺|特别|非常)?喜欢([^，。！？,.!?]{1,30})"),
    re.compile(r"我(?:很|挺|特别|非常)?爱([^，。！？,.!?]{1,30})"),
]

_DISLIKES_PATTERNS = [
    re.compile(r"我(?:很|挺|特别|非常)?不喜欢([^，。！？,.!?]{1,30})"),
    re.compile(r"我(?:很|挺|特别|非常)?讨厌([^，。！？,.!?]{1,30})"),
]

_PROJECT_PATTERNS = [
    re.compile(r"我(?:最近|现在|目前)?(?:正在|在)做([^，。！？,.!?]{2,40})"),
    re.compile(r"我的项目(?:是|叫)([^，。！？,.!?]{2,40})"),
]

_GOAL_PATTERNS = [
    re.compile(r"我(?:想|打算|计划|准备)去([^，。！？,.!?]{2,40})"),
]


def _clean_value(value: str) -> str:
    value = value.strip(" ：:，。,.!?！？ \t\n")
    stop_words = ("啊", "呀", "吧", "呢", "了")
    while value and value[-1] in stop_words:
        value = value[:-1]
    return value.strip()


def _looks_like_question_value(value: str) -> bool:
    return value.startswith(("什么", "啥", "谁", "哪", "多少", "几"))


def _append_fact(
    facts: list[Fact],
    *,
    subject: str,
    predicate: str,
    value: str,
    confidence: float,
    source_memory_id: Optional[int],
    source_text: str,
) -> None:
    cleaned = _clean_value(value)
    if not cleaned:
        return
    if predicate in {"name", "preferred_name"} and _looks_like_question_value(cleaned):
        return
    facts.append(Fact(
        id=None,
        subject=subject,
        predicate=predicate,
        value=cleaned,
        confidence=confidence,
        source_memory_id=source_memory_id,
        source_text=source_text,
    ))


def extract_facts(
    text: str,
    *,
    subject: str = "user",
    source_memory_id: Optional[int] = None,
) -> list[Fact]:
    """Extract stable facts from a single user utterance.

    The first pass is rule-based on purpose. It is predictable, cheap, and the
    interface can later be backed by an LLM schema extractor.
    """
    if not text or not text.strip():
        return []

    facts: list[Fact] = []
    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            _append_fact(
                facts,
                subject=subject,
                predicate="name",
                value=m.group(1),
                confidence=0.96,
                source_memory_id=source_memory_id,
                source_text=text,
            )
            break

    for pat in _PREFERRED_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            _append_fact(
                facts,
                subject=subject,
                predicate="preferred_name",
                value=m.group(1),
                confidence=0.9,
                source_memory_id=source_memory_id,
                source_text=text,
            )
            break

    for predicate, patterns, confidence in (
        ("dislikes", _DISLIKES_PATTERNS, 0.86),
        ("likes", _LIKES_PATTERNS, 0.82),
        ("current_project", _PROJECT_PATTERNS, 0.78),
        ("goal", _GOAL_PATTERNS, 0.72),
    ):
        for pat in patterns:
            for m in pat.finditer(text):
                _append_fact(
                    facts,
                    subject=subject,
                    predicate=predicate,
                    value=m.group(1),
                    confidence=confidence,
                    source_memory_id=source_memory_id,
                    source_text=text,
                )

    return facts
