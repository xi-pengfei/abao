"""性格维度数学的核心测试。

这套测试守护的是文档 v2 + 2026-05-01 设计补完的所有数学约束。
任何让这套测试通不过的修改，都意味着性格漂移行为变了——必须有意识地决定。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personality.dimension import PersonalityDimension


# ---------- 基础不变量 ----------

def test_initial_state_has_zero_buffer():
    d = PersonalityDimension(name="curiosity", value=0.7)
    assert d.signal_buffer == 0.0
    assert d.momentum == 0.0


def test_zero_signal_does_nothing():
    d = PersonalityDimension(name="curiosity", value=0.7)
    ev = d.apply_signal(0.0)
    assert ev is None
    assert d.value == 0.7
    assert d.signal_buffer == 0.0


# ---------- 噪声不触发漂移 ----------

def test_single_small_signal_does_not_drift():
    d = PersonalityDimension(name="curiosity", value=0.7)
    ev = d.apply_signal(0.3)
    assert ev is None, "单个 0.3 的信号不应跨阈值"
    assert abs(d.signal_buffer - 0.3) < 1e-9
    assert d.value == 0.7


def test_signals_below_threshold_with_decay_never_drift():
    """小信号 + 时间间隔，应该被衰减抵消而不是积累。"""
    d = PersonalityDimension(name="curiosity", value=0.7)
    base_time = datetime(2026, 1, 1, 12, 0)
    for i in range(20):
        # 每天给一个 0.1 的小信号——不够强，且每天有 3% 衰减
        d.apply_signal(0.1, now=base_time + timedelta(days=i))
    # 0.1 信号与 0.97 衰减的稳态值约为 0.1 / (1 - 0.97) = 3.33
    # 所以 0.1 是会跨阈值的——这是预期：长期积累就该跨
    # 我们要测的是：更小的信号不会
    d2 = PersonalityDimension(name="curiosity", value=0.7)
    drifted = False
    for i in range(50):
        ev = d2.apply_signal(0.02, now=base_time + timedelta(days=i))
        if ev:
            drifted = True
    # 0.02 / 0.03 ≈ 0.67，远低于阈值 1.0
    assert not drifted, "极小信号即使持续 50 天也不应跨阈值"


# ---------- 持续信号跨阈值漂移 ----------

def test_sustained_signal_eventually_drifts():
    d = PersonalityDimension(name="curiosity", value=0.5)  # 中性位置最敏感
    base = datetime(2026, 1, 1, 12, 0)
    events = []
    for i in range(10):
        ev = d.apply_signal(0.4, now=base + timedelta(hours=i))  # 短时间内连续强信号
        if ev:
            events.append(ev)
    assert len(events) >= 1, "持续 0.4 信号应该跨阈值"
    assert events[0].direction == 1
    assert events[0].value_after > events[0].value_before


def test_drift_resets_buffer():
    d = PersonalityDimension(name="curiosity", value=0.5)
    base = datetime(2026, 1, 1, 12, 0)
    # 一次性塞一个超过阈值的信号
    ev = d.apply_signal(1.5, now=base)
    assert ev is not None
    assert d.signal_buffer == 0.0


# ---------- 距离极值的阻尼 ----------

def test_drift_slower_near_extremes():
    """同样的 buffer，靠近 0.5 时漂移大；靠近极值时漂移小。"""
    d_mid = PersonalityDimension(name="x", value=0.5)
    d_high = PersonalityDimension(name="x", value=0.92)

    base = datetime(2026, 1, 1, 12, 0)
    e_mid = d_mid.apply_signal(1.5, now=base)
    e_high = d_high.apply_signal(1.5, now=base)
    assert abs(e_mid.delta) > abs(e_high.delta), "中性位置漂移应大于极值位置"


def test_value_clamped_to_ceiling():
    d = PersonalityDimension(name="x", value=0.93)
    # 反复推 + 方向
    for _ in range(50):
        d.apply_signal(2.0)  # 强信号反复
    assert d.value <= 0.95
    assert d.value >= 0.05


def test_value_clamped_to_floor():
    d = PersonalityDimension(name="x", value=0.07)
    for _ in range(50):
        d.apply_signal(-2.0)
    assert d.value >= 0.05
    assert d.value <= 0.95


# ---------- 反向 momentum 打折 ----------

def test_reverse_direction_drift_is_dampened():
    """先建立正方向 momentum，再来一次反向漂移，反向 delta 应被打折。"""
    d_a = PersonalityDimension(name="x", value=0.5)
    base = datetime(2026, 1, 1, 12, 0)

    # 建立正向 momentum（多次正向漂移）
    for i in range(3):
        d_a.apply_signal(1.5, now=base + timedelta(hours=i))
    assert d_a.momentum > 0.5

    # 现在 d_a 大约在 0.515 左右，momentum 很正
    # 一次反向跨阈值
    ev_reverse = d_a.apply_signal(-1.5, now=base + timedelta(hours=4))
    assert ev_reverse is not None
    assert ev_reverse.direction == -1

    # 与无 momentum 的对照组比
    d_b = PersonalityDimension(name="x", value=d_a.value)  # 同样位置
    d_b.momentum = 0.0
    ev_b = d_b.apply_signal(-1.5, now=base)
    assert abs(ev_reverse.delta) < abs(ev_b.delta), \
        "反向 momentum 应让漂移幅度变小（防抖）"


# ---------- 时间衰减 ----------

def test_buffer_decays_over_time():
    d = PersonalityDimension(name="x", value=0.5)
    base = datetime(2026, 1, 1, 12, 0)
    d.apply_signal(0.5, now=base)
    assert abs(d.signal_buffer - 0.5) < 1e-9

    # 30 天后再来个零信号——只触发衰减
    d.apply_signal(0.0, now=base + timedelta(days=30))
    expected = 0.5 * (0.97 ** 30)  # ≈ 0.198
    assert abs(d.signal_buffer - expected) < 0.01


def test_signal_buffer_does_not_decay_when_no_time_passes():
    d = PersonalityDimension(name="x", value=0.5)
    base = datetime(2026, 1, 1, 12, 0)
    d.apply_signal(0.5, now=base)
    d.apply_signal(0.3, now=base)  # 同一时刻
    assert abs(d.signal_buffer - 0.8) < 0.01


# ---------- 冲击事件 ----------

def test_shock_causes_immediate_jump():
    d = PersonalityDimension(name="x", value=0.5)
    ev = d.apply_shock(1.0)
    assert ev is not None
    assert ev.event_type == "shock"
    # shock_rate=0.05, distance_penalty=1.0 (at 0.5), so delta ≈ 0.05
    assert abs(ev.delta - 0.05) < 0.001


def test_shock_does_not_consume_buffer():
    d = PersonalityDimension(name="x", value=0.5)
    base = datetime(2026, 1, 1, 12, 0)
    d.apply_signal(0.5, now=base)
    buffer_before = d.signal_buffer
    d.apply_shock(1.0, now=base)
    # 冲击不应清空 buffer——buffer 是普通累积通道，shock 走旁路
    assert d.signal_buffer == buffer_before


def test_shock_zero_signal_no_effect():
    d = PersonalityDimension(name="x", value=0.5)
    ev = d.apply_shock(0.0)
    assert ev is None
    assert d.value == 0.5


# ---------- 序列化 ----------

def test_round_trip_serialization():
    d = PersonalityDimension(name="curiosity", value=0.73, signal_buffer=0.42, momentum=-0.1)
    d.apply_signal(0.3)
    d2 = PersonalityDimension.from_dict(d.to_dict())
    assert d2.name == d.name
    assert d2.value == d.value
    assert d2.signal_buffer == d.signal_buffer
    assert d2.momentum == d.momentum


# ---------- 速度合理性（设计验证） ----------

def test_drift_speed_matches_design():
    """两周每天聊一个话题，预期相关维度漂移 +0.01 到 +0.03 量级。"""
    d = PersonalityDimension(name="curiosity", value=0.85)
    base = datetime(2026, 1, 1, 12, 0)
    # 每天一个 0.4 的相关信号（"为什么 / 想知道"等模式累积）
    for i in range(14):
        d.apply_signal(0.4, now=base + timedelta(days=i))
    delta = d.value - 0.85
    assert 0.001 < delta < 0.04, f"两周漂移应在合理范围，实际 {delta}"


def test_random_chatter_keeps_personality_stable():
    """话题分散——大部分维度信号都不连续——性格应稳定。"""
    import random
    random.seed(42)
    d = PersonalityDimension(name="curiosity", value=0.7)
    base = datetime(2026, 1, 1, 12, 0)
    initial_value = d.value
    for i in range(60):
        # 偶尔小信号
        sig = random.choice([0, 0, 0, 0.1, -0.05, 0])
        d.apply_signal(sig, now=base + timedelta(days=i))
    # 60 天分散信号，值不应大幅漂移
    assert abs(d.value - initial_value) < 0.02, \
        f"分散信号下性格应稳定，初始 {initial_value}, 现在 {d.value}"
