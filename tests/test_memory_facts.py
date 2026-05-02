from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.abao import Abao, AbaoPaths
from memory.facts import extract_facts
from memory.growth_diary import GrowthDiary
from memory.layers import MemoryStore
from memory.memory_core import MemoryCore


def _memory_core(tmp_path: Path) -> MemoryCore:
    store = MemoryStore(tmp_path / "memory.db")
    diary = GrowthDiary(tmp_path / "diary.jsonl")
    return MemoryCore(store, diary)


def test_extracts_name_and_preference_facts():
    facts = extract_facts("我叫席朋飞，我不喜欢香菜", subject="user", source_memory_id=3)
    pairs = {(f.predicate, f.value) for f in facts}
    assert ("name", "席朋飞") in pairs
    assert ("dislikes", "香菜") in pairs


def test_name_question_is_not_extracted_as_name():
    facts = extract_facts("你知道我叫什么名字吗", subject="user")
    assert all(f.predicate != "name" for f in facts)


def test_scalar_fact_supersedes_old_value(tmp_path):
    memory = _memory_core(tmp_path)
    try:
        first_id = memory.insert_conversation_turn("user", "我叫席朋飞")
        memory.observe_user_text("我叫席朋飞", source_memory_id=first_id)
        second_id = memory.insert_conversation_turn("user", "我的名字叫Peter")
        memory.observe_user_text("我的名字叫Peter", source_memory_id=second_id)

        facts = memory.core_facts("user")
        names = [f.value for f in facts if f.predicate == "name"]
        assert names == ["Peter"]
    finally:
        memory.close()


def test_abao_restores_long_term_name_after_restart(tmp_path):
    paths = AbaoPaths(config_dir=ROOT / "config", data_dir=tmp_path)
    with patch("adapters.llm_client.LLMClient.chat", return_value="记住了。"):
        abao = Abao(paths)
        abao.converse("我叫席朋飞", speaker="user")
        abao.shutdown()

    with patch("adapters.llm_client.LLMClient.chat", return_value="你叫席朋飞。") as chat:
        abao = Abao(paths)
        abao.converse("你知道我叫什么吗", speaker="user")
        messages = chat.call_args.args[0]
        system_prompt = messages[0]["content"]
        abao.shutdown()

    assert "user 名字：席朋飞" in system_prompt
