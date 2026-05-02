"""性格维度的核心数学。

每个维度（curiosity, warmth, directness, ...）都是一个 PersonalityDimension。
它不是简单的一个 float，而是一个有"惯性"的小系统：

    value           当前值，[0.05, 0.95]
    signal_buffer   累积证据。每天衰减 3%，跨阈值才触发漂移
    momentum        最近几次漂移的方向（防止抖动）
    last_update     上次更新的日期（用于计算时间衰减）

设计目标：
  1. 噪声不会推动漂移（小信号被衰减抵消）
  2. 持续证据会跨阈值产生小幅跳变（"经历塑造人"）
  3. 极值附近漂移变慢（自然阻尼，不会撞天花板）
  4. 反向 momentum 打折（防抖动）
  5. 高情绪强度事件走旁路（"冲击触发"）

详见 abao_design_v2.md 第 6.2 节，以及 2026-05-01 的设计补完。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ---- 常量（从 providers.yaml 加载到这里，模块级保持简单） ----

DEFAULT_BASE_RATE = 0.005
DEFAULT_SHOCK_RATE = 0.05
DEFAULT_THRESHOLD = 1.0
DEFAULT_BUFFER_DECAY = 0.97
DEFAULT_MOMENTUM_SMOOTHING = 0.7
DEFAULT_REVERSE_PENALTY = 0.5
DEFAULT_VALUE_FLOOR = 0.05
DEFAULT_VALUE_CEILING = 0.95


@dataclass
class DriftEvent:
    """一次漂移发生了。这个对象会被 state_monitor 接住并转给成长日记。

    重要：reflection 字段在这一层是空的——日记的反思文字由 LLM 在 growth_diary
    那一层基于这个事件的事实数据生成。这里只负责"发生了什么"的硬证据。
    """
    dimension: str
    event_type: str            # "drift" | "shock"
    value_before: float
    value_after: float
    delta: float
    direction: int             # +1 或 -1
    buffer_before: float       # 触发时的 buffer 值（漂移走 buffer 路径时有用）
    triggered_at: str          # ISO 时间戳
    evidence_summary: Optional[str] = None  # 由 signal_extractor 给出的"是什么累积出来的"


@dataclass
class PersonalityDimension:
    """一个性格维度的完整状态机。"""
    name: str
    value: float
    signal_buffer: float = 0.0
    momentum: float = 0.0
    last_update: str = field(default_factory=lambda: datetime.now().isoformat())
    last_drift_day: Optional[str] = None

    # 调参（覆盖默认值用）
    base_rate: float = DEFAULT_BASE_RATE
    shock_rate: float = DEFAULT_SHOCK_RATE
    threshold: float = DEFAULT_THRESHOLD
    buffer_decay: float = DEFAULT_BUFFER_DECAY
    momentum_smoothing: float = DEFAULT_MOMENTUM_SMOOTHING
    reverse_penalty: float = DEFAULT_REVERSE_PENALTY
    value_floor: float = DEFAULT_VALUE_FLOOR
    value_ceiling: float = DEFAULT_VALUE_CEILING

    # ---- 公开 API ----

    def apply_signal(
        self,
        signal: float,
        now: Optional[datetime] = None,
        evidence_summary: Optional[str] = None,
    ) -> Optional[DriftEvent]:
        """处理一次普通信号。

        Args:
            signal: [-1, 1] 范围的信号强度。0 表示该维度与本次对话无关。
            now: 当前时间（测试时可注入）。
            evidence_summary: 这次信号"是什么"的简短描述，用于日记反思。

        Returns:
            如果 buffer 跨阈值产生了漂移，返回 DriftEvent；否则 None。
        """
        if signal == 0:
            self._decay_buffer(now)
            return None

        self._decay_buffer(now)
        self.signal_buffer += signal
        self.last_update = (now or datetime.now()).isoformat()

        if abs(self.signal_buffer) < self.threshold:
            return None

        return self._fire_drift(evidence_summary, now)

    def apply_shock(
        self,
        signal: float,
        now: Optional[datetime] = None,
        evidence_summary: Optional[str] = None,
    ) -> Optional[DriftEvent]:
        """处理冲击事件——高情绪强度时走的旁路。

        不经过 buffer 累积，直接按 shock_rate 跳变。
        delta = signal * shock_rate * distance_penalty
        """
        if signal == 0:
            return None

        before = self.value
        distance_penalty = self._distance_penalty()
        delta = signal * self.shock_rate * distance_penalty
        self.value = self._clip(self.value + delta)

        # 冲击事件也更新 momentum，但更剧烈
        direction = 1 if delta > 0 else -1
        self.momentum = (
            self.momentum_smoothing * self.momentum
            + (1 - self.momentum_smoothing) * direction
        )

        ts = (now or datetime.now()).isoformat()
        self.last_update = ts

        return DriftEvent(
            dimension=self.name,
            event_type="shock",
            value_before=before,
            value_after=self.value,
            delta=self.value - before,
            direction=direction,
            buffer_before=self.signal_buffer,  # 冲击不消耗 buffer
            triggered_at=ts,
            evidence_summary=evidence_summary,
        )

    # ---- 内部 ----

    def _decay_buffer(self, now: Optional[datetime]) -> None:
        """按距离上次更新的天数对 buffer 做指数衰减。

        buffer *= decay^days
        days 是浮点数（小数部分按比例衰减），允许频繁更新。
        """
        if self.signal_buffer == 0:
            return
        try:
            last = datetime.fromisoformat(self.last_update)
        except (ValueError, TypeError):
            return
        cur = now or datetime.now()
        # 用秒计算更平滑，避免一天内多次对话被错误地"积累"成"间隔了 0 天"
        seconds = max(0.0, (cur - last).total_seconds())
        days = seconds / 86400.0
        if days <= 0:
            return
        self.signal_buffer *= self.buffer_decay ** days

    def _fire_drift(
        self,
        evidence_summary: Optional[str],
        now: Optional[datetime],
    ) -> DriftEvent:
        """buffer 跨阈值，触发一次漂移。"""
        before = self.value
        buffer_before = self.signal_buffer
        direction = 1 if self.signal_buffer > 0 else -1

        delta = direction * self.base_rate * self._distance_penalty()

        # 反向 momentum 打折：性格不喜欢突然反转方向
        if direction != self._momentum_sign() and self.momentum != 0:
            delta *= self.reverse_penalty

        self.value = self._clip(self.value + delta)
        self.momentum = (
            self.momentum_smoothing * self.momentum
            + (1 - self.momentum_smoothing) * direction
        )
        self.signal_buffer = 0.0  # 清零，重新积累

        ts = (now or datetime.now()).isoformat()
        self.last_update = ts
        self.last_drift_day = ts[:10]

        return DriftEvent(
            dimension=self.name,
            event_type="drift",
            value_before=before,
            value_after=self.value,
            delta=self.value - before,
            direction=direction,
            buffer_before=buffer_before,
            triggered_at=ts,
            evidence_summary=evidence_summary,
        )

    def _distance_penalty(self) -> float:
        """离 0.5 越远，漂移越慢。在 [0.05, 0.95] 范围内是 [0.1, 1.0]。

        在 0.5 时为 1.0（满速），在 0/1 时为 0（撞墙）。
        实际由 value_floor/ceiling 阻止真正贴边。
        """
        return max(0.0, 1 - 2 * abs(self.value - 0.5))

    def _momentum_sign(self) -> int:
        if self.momentum > 0:
            return 1
        if self.momentum < 0:
            return -1
        return 0

    def _clip(self, v: float) -> float:
        return max(self.value_floor, min(self.value_ceiling, v))

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PersonalityDimension":
        return cls(**d)
