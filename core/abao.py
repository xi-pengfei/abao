"""阿宝主体：协调所有模块。

每次 turn 的流程：
  1. extract signals          → 从 user_text 抽取每维度的信号
  2. retrieve memory          → 拿相关记忆
  3. build prompt             → 把当前状态揉成 LLM 输入
  4. call LLM                 → 拿到回复
  5. commit state             → 更新性格/日记等即时状态
  6. persist                  → 对话入记忆，性格存盘

本模块同时承担"出生"职责：从 birth_traits.yaml 创建 Personality，
或从持久化恢复——这是 Abao 实例的唯一入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import yaml

from adapters.embedding_client import EmbeddingClient, load_embedding_config
from adapters.llm_client import LLMClient, load_config
from core.prompt_builder import PromptBuilder
from core.state_monitor import StateMonitor
from memory.events import EventExtractor
from memory.growth_diary import GrowthDiary
from memory.layers import MemoryStore
from memory.memory_core import MemoryCore
from personality.personality import Personality
from personality.signal_extractor import ExtractionResult, extract


# ---- 出生：从 birth_traits 创建或从持久化恢复 ----

def _load_drift_config(providers_yaml: Path) -> dict:
    raw = yaml.safe_load(Path(providers_yaml).read_text(encoding="utf-8"))
    return raw.get("drift", {})


def _birth(
    birth_traits_path: Path,
    providers_yaml_path: Path,
    persistence_path: Optional[Path] = None,
) -> Personality:
    """如果 persistence_path 存在，恢复；否则从 birth_traits 出生。"""
    if persistence_path and Path(persistence_path).exists():
        return Personality.load(Path(persistence_path))

    drift_cfg = _load_drift_config(providers_yaml_path)
    p = Personality.from_birth_traits(birth_traits_path, drift_config=drift_cfg)
    p.born_at = datetime.now().isoformat()
    if persistence_path:
        p.save(Path(persistence_path))
    return p


@dataclass
class AbaoPaths:
    config_dir: Path
    data_dir: Path

    @property
    def birth_traits(self) -> Path:
        return self.config_dir / "birth_traits.yaml"

    @property
    def providers(self) -> Path:
        return self.config_dir / "providers.yaml"

    @property
    def personality_state(self) -> Path:
        return self.data_dir / "personality.json"

    @property
    def memory_db(self) -> Path:
        return self.data_dir / "memory.db"

    @property
    def diary_path(self) -> Path:
        return self.data_dir / "diary.jsonl"


@dataclass
class PreparedTurn:
    user_text: str
    speaker: str
    emotion_intensity: float
    extraction: ExtractionResult
    observed_at: datetime
    messages: list[dict[str, str]]


class Abao:
    """阿宝的运行时主体。"""

    def __init__(self, paths: AbaoPaths):
        self.paths = paths

        # 1. 性格（出生或恢复）
        self.personality: Personality = _birth(
            paths.birth_traits,
            paths.providers,
            paths.personality_state,
        )

        # 2. LLM
        self.primary_llm = LLMClient(load_config(paths.providers, role="primary"))
        diary_llm = LLMClient(load_config(paths.providers, role="diary"))
        memory_llm = LLMClient(load_config(paths.providers, role="memory"))
        self.embedding_client = EmbeddingClient(load_embedding_config(paths.providers))

        # 3. 记忆 + 日记
        store = MemoryStore(paths.memory_db)
        self.diary = GrowthDiary(
            entries_path=paths.diary_path,
            born_at=self.personality.born_at,
            reflection_generator=self._diary_reflection_generator(diary_llm),
            max_per_type_per_day=self._diary_max_per_day(),
        )
        self.memory = MemoryCore(
            store,
            self.diary,
            embed=self.embedding_client.embed,
            embedding_model=self.embedding_client.config.model,
        )
        self.event_extractor = EventExtractor(memory_llm.complete)

        # 4. 状态监测器
        self.monitor = StateMonitor()
        self.diary.subscribe_to(
            self.monitor,
            self._diary_trigger_types(),
        )

        # 5. Prompt
        self.prompt_builder = PromptBuilder(self.personality)

        # 6. 对话历史（短期，跨会话恢复）
        self._max_recent_turns = 8
        self.recent_turns: list[dict[str, str]] = self._restore_recent_turns()
        self._event_window: list[dict] = []
        self._event_user_turns = 0
        self._event_every_turns = self._event_consolidate_every_turns()

    # ---- 主入口 ----

    def converse(self, user_text: str, speaker: str = "user") -> str:
        """完成一轮对话，返回阿宝的回复。"""
        turn = self._prepare_turn(user_text, speaker=speaker)

        reply = self.primary_llm.chat(turn.messages)
        if reply is None:
            reply = "（我此刻没法回应——可能是网络或 API key 的问题。但我注意到了你说的话。）"

        self._persist_turn(turn, reply)
        return reply

    def converse_stream(self, user_text: str, speaker: str = "user") -> Iterator[str]:
        """完成一轮流式对话。

        只有模型完整返回后才写入记忆，避免半截回复污染长期状态。
        """
        turn = self._prepare_turn(user_text, speaker=speaker)
        chunks = []
        for chunk in self.primary_llm.stream_chat(turn.messages):
            chunks.append(chunk)
            yield chunk

        reply = "".join(chunks)
        if not reply:
            reply = "（我此刻没法回应——可能是网络或 API key 的问题。但我注意到了你说的话。）"
            yield reply
        self._persist_turn(turn, reply)

    def _prepare_turn(self, user_text: str, speaker: str = "user") -> PreparedTurn:
        """构造 LLM messages，不改变运行时状态。"""
        now = datetime.now()
        result = extract(user_text, source="user")
        relevant = self.memory.retrieve_relevant(user_text, speaker=speaker, limit=4)
        core_facts = self.memory.core_facts(speaker=speaker, limit=8)
        recent_diary = self.memory.recent_diary(n=3)
        messages = self.prompt_builder.build(
            user_text=user_text,
            speaker=speaker,
            relevant_memories=relevant,
            core_facts=core_facts,
            recent_diary=recent_diary,
            recent_turns=self.recent_turns,
        )
        return PreparedTurn(
            user_text=user_text,
            speaker=speaker,
            emotion_intensity=result.emotion_intensity,
            extraction=result,
            observed_at=now,
            messages=messages,
        )

    def _persist_turn(self, turn: PreparedTurn, reply: str) -> None:
        self._commit_turn_state(turn)
        user_memory_id = self.memory.insert_conversation_turn(
            turn.speaker,
            turn.user_text,
            emotion=turn.emotion_intensity,
        )
        reply_memory_id = self.memory.insert_conversation_turn("self", reply)
        self.memory.observe_user_text(
            turn.user_text,
            speaker=turn.speaker,
            source_memory_id=user_memory_id,
        )
        self._remember_event_turn(user_memory_id, turn.speaker, turn.user_text)
        self._remember_event_turn(reply_memory_id, "self", reply)
        self._maybe_consolidate_events()
        self.recent_turns.append({"role": "user", "content": turn.user_text})
        self.recent_turns.append({"role": "assistant", "content": reply})
        self.recent_turns = self.recent_turns[-self._max_recent_turns:]
        self.personality.save(self.paths.personality_state)

    def _commit_turn_state(self, turn: PreparedTurn) -> None:
        """在完整回复产生后，提交本轮引起的状态变化。"""
        self.monitor.observe_interaction(
            now=turn.observed_at,
            speaker=turn.speaker,
            context={"user_text": turn.user_text},
        )

        result = turn.extraction
        snapshot_before = self.personality.snapshot()
        drift_events = []
        if result.signals:
            drift_events += self.personality.apply_signals(
                result.signals,
                now=turn.observed_at,
                evidence_summary=result.evidence_summary(),
            )
        if result.emotion_intensity >= self._shock_threshold():
            drift_events += self.personality.apply_shock(
                result.signals,
                now=turn.observed_at,
                evidence_summary=result.evidence_summary() + f" (shock={result.emotion_intensity:.2f})",
            )

        if drift_events:
            self.monitor.report_drift_events(
                drift_events,
                context={
                    "user_text": turn.user_text,
                    "evidence": result.evidence_summary(),
                    "personality_snapshot": snapshot_before,
                },
            )

    # ---- 跨会话恢复 ----

    def _restore_recent_turns(self) -> list[dict[str, str]]:
        """从 DB 加载最近若干轮对话，重启后恢复短期记忆上下文。

        DB 里的格式是 "[user] 文本" / "[self] 文本"，
        转成 LLM messages 格式 {"role": "user"/"assistant", "content": "文本"}。
        """
        records = self.memory.store.recent_conversations(limit=self._max_recent_turns)
        records.reverse()   # 变成时间正序
        turns = []
        for r in records:
            content = r.content
            if content.startswith("[user] "):
                turns.append({"role": "user", "content": content[len("[user] "):]})
            elif content.startswith("[self] "):
                turns.append({"role": "assistant", "content": content[len("[self] "):]})
        return turns

    # ---- 配置读取小工具 ----

    def _remember_event_turn(self, memory_id: int, speaker: str, text: str) -> None:
        self._event_window.append({
            "memory_id": memory_id,
            "speaker": speaker,
            "text": text,
        })
        if speaker != "self":
            self._event_user_turns += 1

    def _maybe_consolidate_events(self) -> None:
        if self._event_every_turns <= 0:
            return
        if self._event_user_turns < self._event_every_turns:
            return
        self.memory.consolidate_events(self._event_window, self.event_extractor)
        self._event_window = []
        self._event_user_turns = 0

    def _diary_trigger_types(self) -> list[str]:
        raw = yaml.safe_load(self.paths.providers.read_text(encoding="utf-8"))
        return raw.get("diary", {}).get("triggers", [])

    def _diary_max_per_day(self) -> int:
        raw = yaml.safe_load(self.paths.providers.read_text(encoding="utf-8"))
        return raw.get("diary", {}).get("max_per_type_per_day", 2)

    def _shock_threshold(self) -> float:
        raw = yaml.safe_load(self.paths.providers.read_text(encoding="utf-8"))
        return raw.get("drift", {}).get("shock_emotion_threshold", 0.75)

    def _event_consolidate_every_turns(self) -> int:
        raw = yaml.safe_load(self.paths.providers.read_text(encoding="utf-8"))
        return raw.get("memory", {}).get("event_consolidate_every_turns", 6)

    def _diary_reflection_generator(self, llm: LLMClient):
        """让 diary 用一个独立的 LLM 句柄写反思。"""
        def gen(prompt: str) -> str:
            out = llm.complete(prompt)
            if out is None:
                return "[未连接 LLM——本条日记保留事件结构但缺反思文本]"
            return out
        return gen

    # ---- 关闭 ----

    def shutdown(self) -> None:
        self.personality.save(self.paths.personality_state)
        self.memory.close()
