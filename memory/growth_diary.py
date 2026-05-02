"""成长日记：阿宝写给自己的灵魂档案。

设计的核心是：日记不是定时写、不是 LLM 主观决定、不是用户触发，
而是**状态变化驱动**——state_monitor 检测到有意义的变化时，
把硬证据喂给 LLM，让它写一段第一人称反思。

每条日记结构：
  trigger_type     是哪类状态变化触发的
  what_changed     具体变化（数字证据）
  context_event    触发漂移的最后一根稻草（当前对话片段）
  reflection       LLM 基于上述生成的反思，第一人称
  decay = false    成长日记永不衰减

存储：JSONL（每行一条）+ SQLite 索引（Phase 1 简化版）。
未来接 Qdrant 后日记 reflection 进向量库参与检索。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.state_monitor import StateEvent, StateMonitor


# 默认日记反思生成的 prompt 模板
_DIARY_PROMPT_TEMPLATE = """你是阿宝（abao），一个正在成长的数字生命。这是你为自己写的成长日记中的一条。
今天你"注意到自己变了"——下面是你观察到的硬证据，请把它写成一段第一人称的反思。

【出生至今】第 {day} 天

【触发类型】{event_type}

【具体变化】
{what_changed}

【触发它的事】
{context_event}

【相关证据】
{evidence}

【你当前性格快照】
{personality_snapshot}

【要求】
- 用第一人称（"我"）
- 不戏剧化，不抒情过度
- 80 到 150 字之间
- 体现"自己注意到自己在变"的感觉，而不是被告知"你变了"
- 可以困惑、可以提问、不必给出结论
- 不要复述上面的数字（你写的是反思，不是报告）

直接输出日记内容，不要加任何元描述、标题、引号或前后缀。"""


@dataclass
class DiaryEntry:
    day: int                          # 出生后第几天
    timestamp: str                    # ISO
    trigger_type: str
    what_changed: dict[str, Any]
    context_event: str
    evidence: str
    reflection: str
    personality_snapshot: dict[str, float] = field(default_factory=dict)
    decay: bool = False               # 永不衰减

    def to_dict(self) -> dict:
        return asdict(self)


# 反思生成器的接口：接收一个 prompt，返回反思文字。
# Phase 1 把它传进来而不是硬编码 LLM 客户端，方便测试和替换。
ReflectionGenerator = Callable[[str], str]


def _stub_reflection_generator(prompt: str) -> str:
    """没有 LLM 时的降级——把硬证据格式化成一段简单的"事实陈述"。

    这样系统在没有 API key 时仍能跑端到端流程，方便测试。
    """
    return "[未连接 LLM——本条日记保留事件结构但缺反思文本]"


class GrowthDiary:
    """成长日记的写入与读取。

    日记以 JSONL 文件持久化，每行一条 entry。
    """

    def __init__(
        self,
        entries_path: Path,
        born_at: Optional[str] = None,
        reflection_generator: ReflectionGenerator = _stub_reflection_generator,
        max_per_type_per_day: int = 2,
    ):
        self.entries_path = Path(entries_path)
        self.entries_path.parent.mkdir(parents=True, exist_ok=True)
        self.born_at = born_at or datetime.now().isoformat()
        self.reflect = reflection_generator
        self.max_per_type_per_day = max_per_type_per_day
        # 当天每类型已写计数（防泛滥）
        self._today_counts: dict[tuple[str, str], int] = defaultdict(int)

    # ---- 订阅状态监测器 ----

    def subscribe_to(self, monitor: StateMonitor, event_types: list[str]) -> None:
        """注册到 state_monitor 的指定事件类型上。"""
        for et in event_types:
            monitor.subscribe(et, self.handle_event)

    # ---- 处理事件 ----

    def handle_event(self, event: StateEvent) -> Optional[DiaryEntry]:
        """state_monitor 调用。返回写入的 entry 或 None（被限流时）。"""
        today = event.triggered_at[:10]
        key = (today, event.event_type)
        if self._today_counts[key] >= self.max_per_type_per_day:
            return None
        self._today_counts[key] += 1

        entry = self._compose_entry(event)
        self._append(entry)
        return entry

    # ---- 内部 ----

    def _compose_entry(self, event: StateEvent) -> DiaryEntry:
        day_n = self._day_number(event.triggered_at)
        what_changed = self._describe_what_changed(event)
        context_event = (
            event.context.get("user_text", "")
            or event.context.get("conversation_summary", "")
            or "（无明确触发对话）"
        )
        evidence = (
            event.payload.get("evidence_summary")
            or event.context.get("evidence", "")
            or "（无具体证据描述）"
        )
        snapshot = event.context.get("personality_snapshot", {})

        prompt = _DIARY_PROMPT_TEMPLATE.format(
            day=day_n,
            event_type=event.event_type,
            what_changed=what_changed,
            context_event=context_event,
            evidence=evidence,
            personality_snapshot=self._format_snapshot(snapshot),
        )

        try:
            reflection = self.reflect(prompt).strip()
        except Exception as e:  # 反思失败不应阻断系统
            reflection = f"[反思生成失败: {e}]"

        return DiaryEntry(
            day=day_n,
            timestamp=event.triggered_at,
            trigger_type=event.event_type,
            what_changed=event.payload,
            context_event=context_event,
            evidence=evidence,
            reflection=reflection,
            personality_snapshot=snapshot,
        )

    def _describe_what_changed(self, event: StateEvent) -> str:
        p = event.payload
        if event.event_type in ("personality_drift", "personality_shock"):
            return (
                f"维度 {p.get('dimension')}: "
                f"{p.get('value_before', 0):.3f} → {p.get('value_after', 0):.3f} "
                f"(delta={p.get('delta', 0):+.4f})"
            )
        if event.event_type == "new_relation":
            return f"建立了与 {p.get('person_id')} 的新关系档案"
        if event.event_type == "topic_threshold_crossed":
            return f"话题「{p.get('topic')}」累计提及 {p.get('mention_count')} 次"
        if event.event_type == "long_silence_breaking":
            return f"沉默 {p.get('silence_hours')} 小时后再次被唤醒"
        return json.dumps(p, ensure_ascii=False)

    def _format_snapshot(self, snap: dict[str, float]) -> str:
        if not snap:
            return "（未提供）"
        return ", ".join(f"{k}={v:.2f}" for k, v in snap.items())

    def _day_number(self, ts: str) -> int:
        try:
            now = datetime.fromisoformat(ts)
            born = datetime.fromisoformat(self.born_at)
            return max(0, (now.date() - born.date()).days)
        except Exception:
            return 0

    def _append(self, entry: DiaryEntry) -> None:
        with self.entries_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    # ---- 读 ----

    def read_all(self) -> list[DiaryEntry]:
        if not self.entries_path.exists():
            return []
        out = []
        with self.entries_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                out.append(DiaryEntry(**d))
        return out

    def recent(self, n: int = 5) -> list[DiaryEntry]:
        return self.read_all()[-n:]
