# -*- coding: utf-8 -*-
"""TTP223 摸头杀动作编排（低头 -> 摇头 -> 害羞 -> 回正）与打断保护单元测试"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.hardware_agent import HardwareAgent


def _make_agent():
    fake_hal = MagicMock()
    fake_hal.cur_pan = 90.0
    fake_hal.cur_tilt = 90.0
    with patch.object(HardwareAgent, "_init_asr", return_value=None), \
         patch.object(HardwareAgent, "_init_tts", return_value=None), \
         patch.object(HardwareAgent, "_init_microphone", return_value=None):
        agent = HardwareAgent(
            config={"simulation": True},
            memory_manager=MagicMock(),
            skill_manager=MagicMock(),
            evolution_engine=MagicMock(),
            hal=fake_hal,
            face_tracker=MagicMock(),
        )
    return agent, fake_hal


def test_touch_lowers_shakes_and_turns_shy():
    agent, fake_hal = _make_agent()
    recorded_poses = []
    done = threading.Event()

    def _on_ev(ev):
        if ev.get("type") == "pose_sync":
            recorded_poses.append(ev)
            if ev.get("emotion") == "NORMAL":
                done.set()

    agent.register_event_listener(_on_ev)

    with patch("core.sound_effects.play_sound") as mock_play, \
         patch("core.hardware_agent.time.sleep", return_value=None):
        agent._on_touch_event()
        assert done.wait(timeout=2.0), f"timed out waiting for recovery, got: {recorded_poses}"

    mock_play.assert_called_once_with("purr", async_play=True)
    agent.evolution_engine.record_interaction.assert_called_once()
    seq = [(p["pan"], p["tilt"], p["emotion"]) for p in recorded_poses]
    assert seq == [
        (90, 108, "COZY"),
        (72, 108, "COZY"),
        (108, 108, "COZY"),
        (90, 106, "SHY"),
        (90, 90, "NORMAL"),
    ], f"unexpected pose sequence: {seq}"
    assert fake_hal.set_angles.call_count == 5


def test_touch_during_speaking_interrupts_and_runs_shy_choreography():
    agent, fake_hal = _make_agent()
    agent.state = agent.STATE_SPEAKING
    agent.tts_engine = MagicMock()
    recorded_poses = []
    done = threading.Event()

    def _on_ev(ev):
        if ev.get("type") == "pose_sync":
            recorded_poses.append(ev)
            if ev.get("emotion") == "NORMAL":
                done.set()

    agent.register_event_listener(_on_ev)

    with patch("core.sound_effects.play_sound"), \
         patch("core.hardware_agent.time.sleep", return_value=None):
        agent._on_touch_event()
        assert done.wait(timeout=2.0), f"timed out waiting for barge-in recovery, got: {recorded_poses}"

    agent.tts_engine.stop.assert_called_once()
    seq = [(p["pan"], p["tilt"], p["emotion"]) for p in recorded_poses]
    assert seq[:4] == [
        (90, 108, "COZY"),
        (72, 108, "COZY"),
        (108, 108, "COZY"),
        (90, 106, "SHY"),
    ], f"unexpected barge-in pose sequence: {seq}"
    assert seq[-1] == (90, 90, "NORMAL")
    assert agent.state == agent.STATE_IDLE


def test_touch_sequence_generation_cancels_stale_choreography():
    agent, fake_hal = _make_agent()
    # 用 _touch_seq 失效模拟新摸头打断旧编排
    agent._touch_seq = 2
    recorded = []
    agent.register_event_listener(lambda ev: recorded.append(ev))
    agent._play_touch_choreography(my_seq=1, hold_shy_sec=0.1)
    assert recorded == []
    fake_hal.set_angles.assert_not_called()


if __name__ == "__main__":
    test_touch_lowers_shakes_and_turns_shy()
    test_touch_during_speaking_interrupts_and_runs_shy_choreography()
    test_touch_sequence_generation_cancels_stale_choreography()
    print("✅ test_touch_choreography.py ALL PASSED")
