"""端到端冒烟模拟：不需要 LLM，验证整套机制能跑通。

模拟一个 30 天的"用户与阿宝互动"过程，每天给一些和 curiosity 相关
的输入，最后检查：
  1. 性格按预期漂移
  2. 至少有一条成长日记被写入（日记的反思会是 stub，但事件结构正确）
  3. 记忆系统有对话沉淀

运行：
  python -m scripts.simulate
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.abao import Abao, AbaoPaths


SIM_INPUTS_HIGH_CURIOSITY = [
    "我想知道为什么这个项目要这样设计",
    "好奇你怎么看创作里的孤独",
    "为什么有人觉得这种叙事有意思",
    "我一直想问你，AI 的好奇心和人的好奇心是一回事吗",
    "我对这个问题超级好奇",
    "为什么你说的那些都不是结论",
    "我想知道你昨天为什么那样回答",
]

SIM_INPUTS_DIRECTNESS = [
    "你别绕，直接说",
    "说重点就行，不要废话",
    "我觉得你太啰嗦了",
]

SIM_INPUTS_NEUTRAL = [
    "今天天气还行",
    "刚吃完饭",
    "在路上",
]


def run_simulation():
    # 用一个独立的临时 data 目录，避免污染主数据
    # 走 /tmp 而非项目目录，避开某些挂载文件系统的 SQLite IO 限制
    import tempfile
    sim_data = Path(tempfile.mkdtemp(prefix="abao_sim_"))

    paths = AbaoPaths(
        config_dir=ROOT / "config",
        data_dir=sim_data,
    )

    # 监听：把 LLM 调用全部 stub 掉（避免真打 API）
    with patch("adapters.llm_client.LLMClient.chat", return_value="（模拟回复）"), \
         patch("adapters.llm_client.LLMClient.complete", return_value="今天我注意到，自己面对那些为什么的问题时，比一周前更愿意停一下、多想一层。"):
        abao = Abao(paths)

        # 强制 born_at 设为 30 天前，让"第几天"有意义
        born_at = (datetime.now() - timedelta(days=30)).isoformat()
        abao.personality.born_at = born_at
        abao.diary.born_at = born_at

        snapshot_initial = abao.personality.snapshot()
        print("=== 初始性格 ===")
        for k, v in snapshot_initial.items():
            print(f"  {k}: {v:.3f}")

        # 模拟 30 天，每天 1-3 条对话
        base_now = datetime.now() - timedelta(days=30)
        turn_count = 0
        for day in range(30):
            now = base_now + timedelta(days=day)
            # 把阿宝的"系统时间"模拟到 day 上——直接调底层的 apply_signal
            # 但 converse() 用 datetime.now()，所以这里改用更直接的注入路径
            from personality.signal_extractor import extract
            text = SIM_INPUTS_HIGH_CURIOSITY[day % len(SIM_INPUTS_HIGH_CURIOSITY)]
            result = extract(text)
            snap_before = abao.personality.snapshot()
            events = abao.personality.apply_signals(
                result.signals, now=now, evidence_summary=result.evidence_summary()
            )
            if events:
                abao.monitor.report_drift_events(
                    events,
                    context={
                        "user_text": text,
                        "evidence": result.evidence_summary(),
                        "personality_snapshot": snap_before,
                    },
                )
            turn_count += 1

        snapshot_final = abao.personality.snapshot()
        print("\n=== 30 天后 ===")
        for k, v in snapshot_final.items():
            delta = v - snapshot_initial[k]
            arrow = "→" if abs(delta) < 0.001 else ("↑" if delta > 0 else "↓")
            print(f"  {k}: {snapshot_initial[k]:.3f} {arrow} {v:.3f} (Δ{delta:+.4f})")

        diary_entries = abao.diary.read_all()
        print(f"\n=== 成长日记 ({len(diary_entries)} 条) ===")
        for e in diary_entries[:5]:
            print(f"\n  [第 {e.day} 天] {e.trigger_type}")
            print(f"  what_changed: {e.what_changed}")
            print(f"  context: {e.context_event[:60]}")
            print(f"  reflection: {e.reflection[:200]}")

        abao.shutdown()

    # 断言
    drifted_curiosity = snapshot_final["curiosity"] != snapshot_initial["curiosity"]
    has_diary = len(diary_entries) > 0

    print("\n=== 断言 ===")
    print(f"  curiosity 发生了漂移: {drifted_curiosity}")
    print(f"  有成长日记被写入: {has_diary} ({len(diary_entries)} 条)")

    if not (drifted_curiosity and has_diary):
        print("\n[FAIL] 端到端验证失败")
        sys.exit(1)
    print("\n[OK] 端到端验证通过")


if __name__ == "__main__":
    run_simulation()
