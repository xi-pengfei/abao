"""状态监测器：所有"值得记一笔"的事件在这里汇总。

它是阿宝的事件枢纽。性格漂移、长沉默被打破——这些状态层的变化，
都流向同一个总线，让 growth_diary 决定要不要写日记。

为什么要单独一层而不直接耦合 personality → diary：
  1. diary 不只来自性格漂移（未来还有技能融合、记忆矛盾等）
  2. 集中做"防泛滥"（每天每类型最多 N 条）
  3. 调试时可以把所有事件 dump 出来看

这一层不写日记，只产事件。日记由 growth_diary 订阅。

新事件类型按需添加，不预留占位（极简原则）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Callable

from personality.dimension import DriftEvent


# ---- 事件类型常量（只列当前真正会触发的） ----

EVENT_PERSONALITY_DRIFT = "personality_drift"
EVENT_PERSONALITY_SHOCK = "personality_shock"
EVENT_LONG_SILENCE_BREAKING = "long_silence_breaking"


@dataclass
class StateEvent:
    """通用事件结构。payload 装事件特定的数据。"""
    event_type: str
    triggered_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    """context 装的是和事件无关但日记可能用得上的环境信息：
        当前对话片段、说话人、最近几条相关记忆等。"""

    def to_dict(self) -> dict:
        return asdict(self)


Subscriber = Callable[[StateEvent], None]


class StateMonitor:
    """事件总线 + 派生事件检测器。"""

    def __init__(self):
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self._last_interaction_time: datetime | None = None
        self._silence_threshold_hours: float = 48.0

    def subscribe(self, event_type: str, callback: Subscriber) -> None:
        self._subscribers[event_type].append(callback)

    def publish(self, event: StateEvent) -> None:
        for cb in self._subscribers.get(event.event_type, []):
            cb(event)

    # ---- 来自性格层的桥接 ----

    def report_drift_events(
        self,
        drift_events: list[DriftEvent],
        context: dict[str, Any] | None = None,
    ) -> None:
        """把性格层产生的 DriftEvent 转成 StateEvent 发出去。"""
        for e in drift_events:
            event_type = (
                EVENT_PERSONALITY_SHOCK
                if e.event_type == "shock"
                else EVENT_PERSONALITY_DRIFT
            )
            self.publish(StateEvent(
                event_type=event_type,
                triggered_at=e.triggered_at,
                payload={
                    "dimension": e.dimension,
                    "value_before": e.value_before,
                    "value_after": e.value_after,
                    "delta": e.delta,
                    "direction": e.direction,
                    "buffer_before": e.buffer_before,
                    "evidence_summary": e.evidence_summary,
                },
                context=context or {},
            ))

    # ---- 派生事件检测 ----

    def observe_interaction(
        self,
        now: datetime | None = None,
        speaker: str = "user",
        context: dict[str, Any] | None = None,
    ) -> None:
        """每次有交互发生时调用。检测"长时间沉默被打破"这类时间维度的事件。"""
        cur = now or datetime.now()
        if self._last_interaction_time is not None:
            hours = (cur - self._last_interaction_time).total_seconds() / 3600
            if hours >= self._silence_threshold_hours:
                self.publish(StateEvent(
                    event_type=EVENT_LONG_SILENCE_BREAKING,
                    triggered_at=cur.isoformat(),
                    payload={
                        "silence_hours": round(hours, 2),
                        "speaker": speaker,
                    },
                    context=context or {},
                ))
        self._last_interaction_time = cur
