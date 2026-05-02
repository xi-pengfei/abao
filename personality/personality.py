"""性格管理器：聚合所有维度，提供统一的"接收信号-观测漂移"接口。

性格 = 一组维度的集合。这一层只做编排：把信号字典分发到各维度，
收集所有触发的 DriftEvent 一起返回给 state_monitor。

不要在这一层做任何"漂移逻辑"——逻辑全在 dimension.py。
这一层应该尽量薄，方便后期替换持久化方式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .dimension import (
    DriftEvent,
    PersonalityDimension,
    DEFAULT_BASE_RATE,
    DEFAULT_SHOCK_RATE,
    DEFAULT_THRESHOLD,
    DEFAULT_BUFFER_DECAY,
    DEFAULT_MOMENTUM_SMOOTHING,
    DEFAULT_REVERSE_PENALTY,
    DEFAULT_VALUE_FLOOR,
    DEFAULT_VALUE_CEILING,
)


@dataclass
class Mood:
    """当下的瞬时情绪状态——和性格不同，性格是长期特质，情绪是此刻的能量。"""
    energy: float = 0.7
    openness: float = 0.8
    weight: float = 0.3
    focus: str = "open"

    def to_dict(self) -> dict:
        return {
            "energy": self.energy,
            "openness": self.openness,
            "weight": self.weight,
            "focus": self.focus,
        }


class Personality:
    """性格的运行时容器。"""

    def __init__(self, dimensions: dict[str, PersonalityDimension], self_seed: str = ""):
        self.dimensions = dimensions
        self.self_seed = self_seed
        self.mood = Mood()
        self.born_at: Optional[str] = None  # 由 birth.py 在创建时填入

    # ---- 接收信号 ----

    def apply_signals(
        self,
        signals: dict[str, float],
        now: Optional[datetime] = None,
        evidence_summary: Optional[str] = None,
    ) -> list[DriftEvent]:
        """把一次对话抽出的信号分发到各维度。

        signals: {dimension_name: signal_value} 不需要覆盖所有维度，
                 没出现的维度会自然衰减。
        """
        events: list[DriftEvent] = []
        for name, dim in self.dimensions.items():
            sig = signals.get(name, 0.0)
            ev = dim.apply_signal(sig, now=now, evidence_summary=evidence_summary)
            if ev:
                events.append(ev)
        # 让没收到信号的维度也走一遍衰减（保持时间一致性）
        for name, dim in self.dimensions.items():
            if name not in signals:
                dim._decay_buffer(now)
        return events

    def apply_shock(
        self,
        signals: dict[str, float],
        now: Optional[datetime] = None,
        evidence_summary: Optional[str] = None,
    ) -> list[DriftEvent]:
        """处理一次冲击事件，多个维度可能同时跳变。"""
        events: list[DriftEvent] = []
        for name, sig in signals.items():
            if name not in self.dimensions:
                continue
            ev = self.dimensions[name].apply_shock(
                sig, now=now, evidence_summary=evidence_summary
            )
            if ev:
                events.append(ev)
        return events

    # ---- 观测 ----

    def snapshot(self) -> dict[str, float]:
        return {name: dim.value for name, dim in self.dimensions.items()}

    def describe_for_prompt(self) -> str:
        """给 prompt_builder 用的简短性格自述。

        不暴露原始数字，而是把数值翻译成倾向描述（让 LLM 用得自然）。
        """
        lines = []
        for name, dim in self.dimensions.items():
            band = self._band(dim.value)
            lines.append(f"  {name}: {band} ({dim.value:.2f})")
        mood_line = (
            f"  当下：energy={self.mood.energy:.2f}, "
            f"openness={self.mood.openness:.2f}, "
            f"weight={self.mood.weight:.2f}, focus={self.mood.focus}"
        )
        return "\n".join(lines + [mood_line])

    @staticmethod
    def _band(v: float) -> str:
        if v >= 0.85: return "强"
        if v >= 0.65: return "偏强"
        if v >= 0.45: return "中性"
        if v >= 0.25: return "偏弱"
        return "弱"

    # ---- 持久化 ----

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "born_at": self.born_at,
            "self_seed": self.self_seed,
            "mood": self.mood.to_dict(),
            "dimensions": {n: d.to_dict() for n, d in self.dimensions.items()},
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Personality":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        dims = {
            n: PersonalityDimension.from_dict(d)
            for n, d in data["dimensions"].items()
        }
        p = cls(dims, self_seed=data.get("self_seed", ""))
        p.born_at = data.get("born_at")
        m = data.get("mood", {})
        p.mood = Mood(
            energy=m.get("energy", 0.7),
            openness=m.get("openness", 0.8),
            weight=m.get("weight", 0.3),
            focus=m.get("focus", "open"),
        )
        return p

    @classmethod
    def from_birth_traits(cls, birth_yaml: Path, drift_config: Optional[dict] = None) -> "Personality":
        """从 birth_traits.yaml 创建一个全新的性格——这是"出生"时刻。"""
        birth = yaml.safe_load(Path(birth_yaml).read_text(encoding="utf-8"))
        cfg = drift_config or {}

        defaults = dict(
            base_rate=cfg.get("base_rate", DEFAULT_BASE_RATE),
            shock_rate=cfg.get("shock_rate", DEFAULT_SHOCK_RATE),
            threshold=cfg.get("threshold", DEFAULT_THRESHOLD),
            buffer_decay=cfg.get("buffer_decay_per_day", DEFAULT_BUFFER_DECAY),
            momentum_smoothing=cfg.get("momentum_smoothing", DEFAULT_MOMENTUM_SMOOTHING),
            reverse_penalty=cfg.get("reverse_momentum_penalty", DEFAULT_REVERSE_PENALTY),
            value_floor=cfg.get("value_floor", DEFAULT_VALUE_FLOOR),
            value_ceiling=cfg.get("value_ceiling", DEFAULT_VALUE_CEILING),
        )

        dims = {}
        for name, init_value in birth["dimensions"].items():
            dims[name] = PersonalityDimension(
                name=name,
                value=float(init_value),
                **defaults,
            )

        p = cls(dims, self_seed=birth.get("self_seed", ""))
        p.born_at = datetime.now().isoformat()
        return p
