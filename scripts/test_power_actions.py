# -*- coding: utf-8 -*-
"""安全关机 / 重启服务全链路单元测试（权限预检、实机断电防误触发、语音直通）"""

import os
import sys
import time
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from drivers.hal_simulation import SimulationRobotHAL
from core.menu_fsm import MenuFSM
from core.skill_manager import SkillManager
from core.agent_reasoner import try_fast_local_intent


def test_shutdown_permission_guard_on_pi():
    sim_hal = SimulationRobotHAL(headless=True)
    fsm = MenuFSM(hal=sim_hal)

    # 1. 实机缺失免密 sudo 权限：必须明确报错，绝不盲目黑屏锁死
    with patch.object(MenuFSM, "_is_raspberry_pi_host", return_value=True), \
         patch.object(MenuFSM, "_can_passwordless_poweroff", return_value=(False, "sudo: a password is required")):
        res = fsm._execute_power_action("shutdown", async_exec=False)
        assert res["ok"] is False
        assert "免密" in res["message"]
        assert fsm._shutting_down is False
        assert fsm.is_screen_off is False


def test_shutdown_success_on_pi_mocked():
    fake_hal = MagicMock()
    fsm = MenuFSM(hal=fake_hal)

    with patch.object(MenuFSM, "_is_raspberry_pi_host", return_value=True), \
         patch.object(MenuFSM, "_can_passwordless_poweroff", return_value=(True, "sudo -n")), \
         patch.object(MenuFSM, "_run_os_shutdown", return_value=(True, "sudo -n /sbin/shutdown -h now")) as mock_os_off, \
         patch("core.menu_fsm.time.sleep", return_value=None):
        res = fsm._execute_power_action("shutdown", async_exec=False)
        assert res["ok"] is True
        assert res["status"] == "shutting_down"
        assert fsm._shutting_down is True
        assert fsm.is_screen_off is True
        fake_hal.set_screen_power.assert_called_with(False, 100)
        fake_hal.close.assert_called_once()
        mock_os_off.assert_called_once()


def test_shutdown_simulation_safe_and_voice_intent():
    import core.menu_fsm as mf
    sim_hal = SimulationRobotHAL(headless=True)
    orig_hal, orig_off, orig_shut = mf.menu_fsm.hal, mf.menu_fsm.is_screen_off, mf.menu_fsm._shutting_down
    try:
        mf.menu_fsm.hal = sim_hal
        sm = SkillManager(storage_dir=os.path.join(ROOT_DIR, "data"))

        # 非树莓派环境语音喊“关机”：走 fast_local_intents 并提取干净文本，不调用 OS 关机且不锁死 _shutting_down
        with patch.object(MenuFSM, "_is_raspberry_pi_host", return_value=False), \
             patch.object(MenuFSM, "_run_os_shutdown") as mock_os_off:
            res = try_fast_local_intent("关机", sm, silent=True)
            assert res is not None
            assert not res["reply"].startswith("{")
            assert "关机" in res["reply"]
            time.sleep(0.05)
            mock_os_off.assert_not_called()
            assert mf.menu_fsm._shutting_down is False
    finally:
        mf.menu_fsm.set_screen_power(True)
        mf.menu_fsm.hal = orig_hal
        mf.menu_fsm.is_screen_off = orig_off
        mf.menu_fsm._shutting_down = orig_shut


if __name__ == "__main__":
    test_shutdown_permission_guard_on_pi()
    test_shutdown_success_on_pi_mocked()
    test_shutdown_simulation_safe_and_voice_intent()
    print("ALL_POWER_ACTION_TESTS_PASSED")
