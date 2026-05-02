"""信号抽取：把对话文本翻译成每个维度的 signal[d] 值。

设计：双层结构。
  Layer 1（本文件）：便宜的关键词/模式匹配，[-1, 1] 范围打分。
  Layer 2（占位，未实现）：LLM 周期性校准——每周拿一批对话回看，
    调整 Layer 1 的权重和模式。

之所以这样切：每条对话都过 LLM 太贵也太慢，且 LLM 的判断会漂；
而纯关键词太僵硬。让 Layer 1 跑得便宜，Layer 2 慢慢校准它。

返回 ExtractionResult，包含：
  signals      {dim: score}，给 Personality.apply_signals 用
  emotion_intensity  [0, 1]，用于判定是否冲击事件
  evidence     {dim: 触发的具体词组列表}，供日记反思引用
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---- 模式库 ----
#
# 每个维度有一组 (pattern, weight) 元组。weight 为正推动维度上升，
# 为负推动下降。注意 weight 的量级——单条匹配最大贡献应在 0.5 以下，
# 否则一两个词就跨阈值会让漂移变成"对关键词的反射"。

_PATTERNS: dict[str, list[tuple[str, float]]] = {
    "curiosity": [
        (r"为什么", 0.30),
        (r"怎么", 0.20),
        (r"是不是", 0.15),
        (r"我想知道", 0.35),
        (r"好奇", 0.40),
        (r"有意思", 0.25),
        (r"探索", 0.30),
        (r"无聊", -0.30),
        (r"算了不重要", -0.20),
    ],
    "coherence_seeking": [
        (r"矛盾", 0.40),
        (r"对不上", 0.30),
        (r"不一致", 0.35),
        (r"为什么会", 0.20),
        (r"逻辑", 0.25),
        (r"原因", 0.20),
        (r"差不多就行", -0.30),
        (r"别想那么多", -0.40),
    ],
    "aesthetic_sensitivity": [
        (r"美|漂亮|好看", 0.30),
        (r"形式|结构|节奏", 0.30),
        (r"质感|氛围|调性", 0.35),
        (r"丑|难看", -0.20),
        (r"哪里都行", -0.15),
    ],
    "connection_seeking": [
        (r"让我想到|让我想起", 0.35),
        (r"和.*一样|类似", 0.25),
        (r"关联|联系", 0.30),
        (r"孤立|没关系|无关", -0.25),
    ],
    "self_awareness": [
        # 注意：用户的元对话/反思也会推动阿宝的 self_awareness，
        # 因为环境的反思密度上升了
        (r"你是谁|你是什么", 0.40),
        (r"你有自己|你的想法", 0.35),
        (r"你怎么看", 0.20),
        (r"我是不是|我有没有", 0.15),
        (r"反思|自省", 0.30),
    ],
    "initiative": [
        # 这一维主要受 阿宝自身输出行为反馈调整，
        # 用户输入只能给少量正向（用户邀请主动性）
        (r"你说|你来|你先", 0.20),
        (r"不要主动|别打扰", -0.40),
    ],
    "warmth": [
        (r"难过|伤心|累", 0.30),       # 表达脆弱时温度需求上升
        (r"开心|高兴|喜欢", 0.20),
        (r"谢谢|感谢", 0.15),
        (r"客气点|别太热情", -0.30),
    ],
    "directness": [
        (r"别绕|直接说|说重点", 0.40),
        (r"啰嗦|废话", 0.30),
        (r"详细一点|展开说", -0.20),
        (r"温柔点|委婉", -0.25),
    ],
    "challenge_tendency": [
        (r"我不同意|我觉得不对", 0.35),
        (r"你确定|真的吗", 0.25),
        (r"别质疑我|不要反驳", -0.40),
        (r"听我的|按我说的", -0.30),
    ],
}


# 情绪强度信号——用于判定冲击事件
_EMOTION_PATTERNS = [
    (r"特别|非常|极其|超级", 0.20),
    (r"！", 0.15),
    (r"!{2,}", 0.30),
    (r"啊啊|呜呜|哭|爽爆", 0.40),
    (r"震撼|崩溃|绝望|狂喜", 0.50),
    (r"我恨|我爱|从来没有", 0.45),
    (r"真的吗", 0.10),
]


@dataclass
class ExtractionResult:
    signals: dict[str, float] = field(default_factory=dict)
    emotion_intensity: float = 0.0
    evidence: dict[str, list[str]] = field(default_factory=dict)

    def evidence_summary(self) -> str:
        """生成一行人类可读的证据摘要，供日记反思引用。"""
        parts = []
        for dim, words in self.evidence.items():
            if words:
                parts.append(f"{dim}<-{','.join(words[:3])}")
        if self.emotion_intensity > 0.3:
            parts.append(f"emotion={self.emotion_intensity:.2f}")
        return "; ".join(parts) if parts else "no signal"


def extract(text: str, *, source: str = "user") -> ExtractionResult:
    """从一段文本抽取所有维度的信号。

    Args:
        text: 文本内容
        source: "user" | "self"（阿宝自己的回复），未来可能用不同模式表
    """
    if not text or not text.strip():
        return ExtractionResult()

    res = ExtractionResult()
    for dim, patterns in _PATTERNS.items():
        score = 0.0
        hits: list[str] = []
        for pat, weight in patterns:
            matches = re.findall(pat, text)
            if matches:
                score += weight * len(matches)
                hits.extend(str(m) if not isinstance(m, str) else m for m in matches[:2])
        if score != 0:
            # clamp 到 [-1, 1]：单次对话即使狂轰也不该超过这个量级
            res.signals[dim] = max(-1.0, min(1.0, score))
            res.evidence[dim] = hits

    # 情绪强度
    emo = 0.0
    for pat, weight in _EMOTION_PATTERNS:
        emo += weight * len(re.findall(pat, text))
    res.emotion_intensity = min(1.0, emo)

    return res


def merge(results: Iterable[ExtractionResult]) -> ExtractionResult:
    """把多段文本（如 user_input + abao_reply）的抽取结果合并。"""
    merged = ExtractionResult()
    emo_max = 0.0
    for r in results:
        for dim, score in r.signals.items():
            merged.signals[dim] = max(-1.0, min(1.0, merged.signals.get(dim, 0.0) + score))
        for dim, hits in r.evidence.items():
            merged.evidence.setdefault(dim, []).extend(hits)
        emo_max = max(emo_max, r.emotion_intensity)
    merged.emotion_intensity = emo_max
    return merged
