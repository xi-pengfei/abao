from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.abao import Abao, AbaoPaths
from memory.events import EventExtractor
from memory.growth_diary import GrowthDiary
from memory.layers import MemoryStore, MemoryType
from memory.memory_core import MemoryCore


def test_event_extractor_parses_schema_json():
    def complete(_prompt: str) -> str:
        return json.dumps({
            "events": [{
                "summary": "席朋飞因为图书馆进不去，最后在安静的食堂学习。",
                "event_type": "daily_context",
                "subjects": ["user"],
                "topics": ["图书馆", "食堂", "学习"],
                "emotion": "计划受阻后安定",
                "importance": 0.68,
                "confidence": 0.82,
                "source_memory_ids": [1, 2],
            }]
        }, ensure_ascii=False)

    drafts = EventExtractor(complete).extract([{
        "memory_id": 1,
        "speaker": "user",
        "text": "图书馆老师不在，我去食堂学习了",
    }])

    assert len(drafts) == 1
    assert drafts[0].event_type == "daily_context"
    assert "食堂" in drafts[0].topics


def test_memory_core_consolidates_event(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    memory = MemoryCore(store, GrowthDiary(tmp_path / "diary.jsonl"))
    extractor = EventExtractor(lambda _prompt: json.dumps({
        "events": [{
            "summary": "用户在食堂继续赶项目，觉得那里比预期安静。",
            "topics": ["食堂", "项目"],
            "importance": 0.7,
        }]
    }, ensure_ascii=False))

    try:
        inserted = memory.consolidate_events([{
            "memory_id": 1,
            "speaker": "user",
            "text": "我在食堂赶项目",
        }], extractor)
        events = store.list_by_type(MemoryType.EVENT.value)
    finally:
        memory.close()

    assert len(inserted) == 1
    assert events[0].content == "用户在食堂继续赶项目，觉得那里比预期安静。"
    assert events[0].tags == ["食堂", "项目"]


def test_event_memory_is_retrievable_without_spaces(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    memory = MemoryCore(store, GrowthDiary(tmp_path / "diary.jsonl"))
    try:
        memory.insert(
            content="席朋飞计划去图书馆学习受阻，后来转去食堂继续赶项目。",
            mem_type=MemoryType.EVENT.value,
            importance=0.72,
            related_persons=["user"],
            tags=["图书馆", "食堂", "项目"],
        )
        results = memory.retrieve_relevant("你还记得我去食堂赶项目那次吗", speaker="user")
    finally:
        memory.close()

    assert results
    assert results[0].mem_type == MemoryType.EVENT.value


def test_semantic_retrieval_can_find_event_without_shared_words(tmp_path):
    vectors = {
        "席朋飞计划去图书馆学习受阻，后来转去食堂继续赶项目。": [1.0, 0.0],
        "你还记得我上次计划被打乱那件事吗": [1.0, 0.0],
    }

    def embed(text: str):
        return vectors.get(text, [0.0, 1.0])

    store = MemoryStore(tmp_path / "memory.db")
    memory = MemoryCore(
        store,
        GrowthDiary(tmp_path / "diary.jsonl"),
        embed=embed,
        embedding_model="fake-embedding",
    )
    try:
        memory.insert(
            content="席朋飞计划去图书馆学习受阻，后来转去食堂继续赶项目。",
            mem_type=MemoryType.EVENT.value,
            importance=0.72,
            related_persons=["user"],
            tags=["图书馆", "食堂", "项目"],
        )
        results = memory.retrieve_relevant("你还记得我上次计划被打乱那件事吗", speaker="user")
    finally:
        memory.close()

    assert results
    assert results[0].mem_type == MemoryType.EVENT.value


def test_abao_writes_event_after_consolidation_window(tmp_path):
    config_dir = tmp_path / "config"
    shutil.copytree(ROOT / "config", config_dir)
    providers = config_dir / "providers.yaml"
    providers.write_text(
        providers.read_text(encoding="utf-8").replace(
            "event_consolidate_every_turns: 6",
            "event_consolidate_every_turns: 2",
        ),
        encoding="utf-8",
    )
    paths = AbaoPaths(config_dir=config_dir, data_dir=tmp_path / "data")

    def fake_chat(_self, messages, *args, **kwargs):
        content = messages[0]["content"]
        if "记忆整理器" in content:
            return json.dumps({
                "events": [{
                    "summary": "席朋飞计划去图书馆学习受阻，后来转去食堂继续赶项目。",
                    "event_type": "daily_context",
                    "subjects": ["user"],
                    "topics": ["图书馆", "食堂", "项目"],
                    "importance": 0.72,
                    "confidence": 0.86,
                }]
            }, ensure_ascii=False)
        return "我记住了。"

    with patch("adapters.llm_client.LLMClient.chat", new=fake_chat):
        abao = Abao(paths)
        abao.converse("图书馆老师不在，我进不去，只好去食堂学习", speaker="user")
        abao.converse("食堂一半不营业，很安静，我正好继续赶项目", speaker="user")
        events = abao.memory.store.list_by_type(MemoryType.EVENT.value)
        abao.shutdown()

    assert len(events) == 1
    assert "食堂" in events[0].content
