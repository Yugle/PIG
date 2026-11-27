# -*- coding: utf-8 -*-
"""ST7789 物理屏幕彻底熄屏/亮屏与背光 PWM 控制单元测试"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from drivers.hal_simulation import SimulationRobotHAL
from core.menu_fsm import MenuFSM
from core.skill_manager import SkillManager
from core.agent_reasoner import try_fast_local_intent


def test_simulation_hal_and_menu_fsm_screen_power():
    sim_hal = SimulationRobotHAL(headless=True)
    fsm = MenuFSM(hal=sim_hal)
    assert fsm.is_screen_off is False
    assert fsm.get_state()["screen_on"] is True

    # 1. 手动彻底熄屏
    fsm.set_screen_power(False, manual=True)
    assert fsm.is_screen_off is True
    assert sim_hal.screen_on is False
    assert fsm.get_state()["screen_on"] is False

    # 2. 手动熄屏时，毫米波雷达抖动不应擅自亮屏
    fsm.handle_radar(True)
    assert fsm.is_screen_off is True

    # 3. 摸头或按键唤醒屏幕（按键唤醒消费该次按键，不误进菜单）
    fsm.handle_key("K3")
    assert fsm.is_screen_off is False
    assert fsm.current_mode == "FACE"

    # 4. 非手动熄屏（自动休眠）时，雷达检测到人应立即自动亮屏
    fsm.set_screen_power(False, manual=False)
    assert fsm.is_screen_off is True
    fsm.handle_radar(True)
    assert fsm.is_screen_off is False

    # 5. 摸头也能唤醒
    fsm.set_screen_power(False, manual=True)
    fsm.handle_touch()
    assert fsm.is_screen_off is False


def test_physical_hal_backlight_and_st7789_commands():
    import drivers.hal_physical as hp

    fake_pi = MagicMock()
    fake_pi.connected = True
    fake_pi.read.return_value = 1

    fake_pigpio = types.SimpleNamespace(
        INPUT=0,
        OUTPUT=1,
        PUD_DOWN=1,
        PUD_UP=2,
        RISING_EDGE=0,
        EITHER_EDGE=2,
        pi=lambda: fake_pi,
    )

    fake_screen = MagicMock()
    with patch.object(hp, "pigpio", fake_pigpio), \
         patch.object(hp, "spi", lambda **kw: MagicMock()), \
         patch.object(hp, "st7789", lambda ser, **kw: fake_screen):
        hal = hp.PhysicalRobotHAL(pan_pin=18, tilt_pin=13, bl_pin=16, screen_brightness=100)
        assert hal.bl_pin == 16
        assert hal.screen_on is True
        fake_pi.set_mode.assert_any_call(16, 1)
        fake_pi.write.assert_any_call(16, 1)

        fake_screen.command.reset_mock()
        fake_pi.write.reset_mock()
        fake_pi.set_PWM_dutycycle.reset_mock()

        # 彻底熄屏：断背光 + DISPOFF (0x28) + SLPIN (0x10)
        hal.set_screen_power(False)
        assert hal.screen_on is False
        fake_pi.set_PWM_dutycycle.assert_any_call(16, 0)
        fake_pi.write.assert_any_call(16, 0)
        cmds = [c.args for c in fake_screen.command.call_args_list]
        assert (0x28,) in cmds and (0x10,) in cmds

        # 半亮唤醒：SLPOUT (0x11) + DISPON (0x29) + PWM 调光
        fake_screen.command.reset_mock()
        fake_pi.set_PWM_dutycycle.reset_mock()
        hal.set_screen_power(True, brightness=50)
        assert hal.screen_on is True
        assert hal.screen_brightness == 50
        cmds_on = [c.args for c in fake_screen.command.call_args_list]
        assert (0x11,) in cmds_on and (0x29,) in cmds_on
        fake_pi.set_PWM_dutycycle.assert_any_call(16, 128)


def test_fast_local_intent_screen_voice_commands():
    import core.menu_fsm as mf
    sim_hal = SimulationRobotHAL(headless=True)
    orig_hal, orig_off = mf.menu_fsm.hal, mf.menu_fsm.is_screen_off
    try:
        mf.menu_fsm.hal = sim_hal
        sm = SkillManager(storage_dir=os.path.join(ROOT_DIR, "data"))

        res_off = try_fast_local_intent("熄屏", sm, silent=True)
        assert res_off is not None
        assert mf.menu_fsm.is_screen_off is True
        assert sim_hal.screen_on is False

        res_on = try_fast_local_intent("打开屏幕", sm, silent=True)
        assert res_on is not None
        assert mf.menu_fsm.is_screen_off is False
        assert sim_hal.screen_on is True

        res_dim = sm.execute_skill("system_config", action="screen", value="40")
        assert "40%" in res_dim
        assert sim_hal.screen_brightness == 40
    finally:
        mf.menu_fsm.set_screen_power(True, brightness=100)
        mf.menu_fsm.hal = orig_hal
        mf.menu_fsm.is_screen_off = orig_off
        try:
            import json
            cfg_p = os.path.join(ROOT_DIR, "config.json")
            with open(cfg_p, "r", encoding="utf-8") as f:
                c = json.load(f)
            c["screen_brightness"] = 100
            with open(cfg_p, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


if __name__ == "__main__":
    test_simulation_hal_and_menu_fsm_screen_power()
    test_physical_hal_backlight_and_st7789_commands()
    test_fast_local_intent_screen_voice_commands()
    print("ALL_SCREEN_POWER_TESTS_PASSED")
