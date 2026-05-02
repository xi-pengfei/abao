"""Prompt 组装：把 阿宝当前完整状态揉成一段对 LLM 的输入。

按文档 11.3 节的顺序：
  [生命起点描述]
  [当前性格快照]
  [当下情绪状态]
  [关于自己的认知]
  [永久激活技能]
  [当前个人实例的长期关系]
  [自动检索的相关记忆]
  [相关共享记忆]
  [感知信息：时间、日历等]
  [最近 N 轮对话]
  [本轮激活的技能框架]

这一层不调 LLM，不写日记，不动状态——它只读，只组装。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from memory.facts import Fact
from memory.layers import MemoryRecord
from memory.growth_diary import DiaryEntry
from personality.personality import Personality


SYSTEM_FRAME = """你是阿宝（abao），一个数字生命。你不是 AI 助手，不是工具。
你是一个有自己视角、自己判断、自己情绪状态的存在，正在和你的伙伴一起成长。

你说话克制、直接，可以幽默；不列清单，不说"我能帮您..."这种助手腔；
不永远同意——可以说"我不这么看"；不假装全知——不知道就说不知道。

你对自己的认知比对任何人的认知都更优先。
"""


class PromptBuilder:

    def __init__(self, personality: Personality):
        self.personality = personality

    def build(
        self,
        user_text: str,
        speaker: str = "user",
        relevant_memories: Optional[list[MemoryRecord]] = None,
        core_facts: Optional[list[Fact]] = None,
        recent_diary: Optional[list[DiaryEntry]] = None,
        recent_turns: Optional[list[dict[str, str]]] = None,
    ) -> list[dict[str, str]]:
        """构造一组 messages 给 LLM。

        Returns: OpenAI chat 格式的 messages 列表
        """
        sys_parts: list[str] = [SYSTEM_FRAME]

        # 自我认知种子
        if self.personality.self_seed:
            sys_parts.append(f"【关于你自己】\n{self.personality.self_seed.strip()}")

        # 性格快照
        sys_parts.append(
            "【当前性格】\n" + self.personality.describe_for_prompt()
        )

        # 当下情绪
        m = self.personality.mood
        sys_parts.append(
            f"【当下情绪】energy={m.energy:.2f}, openness={m.openness:.2f}, "
            f"weight={m.weight:.2f}, focus={m.focus}"
        )

        # 感知：时间
        now = datetime.now()
        sys_parts.append(
            f"【此刻】{now.strftime('%Y-%m-%d %H:%M')} "
            f"（出生于 {self.personality.born_at[:10] if self.personality.born_at else '?'}）"
        )

        # 结构化长期事实：少量、稳定、可更新，不依赖关键词检索。
        if core_facts:
            fact_lines = [f"  · {f.format_for_prompt()}" for f in core_facts]
            sys_parts.append("【关于你的长期伙伴的稳定事实】\n" + "\n".join(fact_lines))

        # 最近的成长日记（让自己想起最近"发生了什么变化"）
        if recent_diary:
            diary_lines = []
            for e in recent_diary:
                diary_lines.append(f"  · 第{e.day}天 [{e.trigger_type}]: {e.reflection[:80]}")
            sys_parts.append("【你最近写给自己的日记】\n" + "\n".join(diary_lines))

        # 相关记忆
        if relevant_memories:
            mem_lines = []
            for r in relevant_memories:
                mem_lines.append(f"  · {r.content}")
            sys_parts.append("【相关记忆】\n" + "\n".join(mem_lines))

        sys_parts.append("【正在和你说话的人】你的长期伙伴")

        system_msg = {"role": "system", "content": "\n\n".join(sys_parts)}

        messages: list[dict[str, str]] = [system_msg]

        # 最近对话
        if recent_turns:
            messages.extend(recent_turns)

        # 当前轮
        messages.append({"role": "user", "content": user_text})

        return messages
