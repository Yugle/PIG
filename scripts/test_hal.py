import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drivers.hal_simulation import SimulationRobotHAL
from drivers.eye_renderer import EyeRenderer

def test_simulation_hal():
    print("🤖 测试仿真 HAL 运动与表情调度...")
    hal = SimulationRobotHAL(headless=True)
    eyes = EyeRenderer()

    # 测试常态显示
    img_normal = eyes.render_eyes("NORMAL")
    hal.display_face(img_normal)
    print("  ✅ 屏幕显示 NORMAL 表情")

    # 测试脖子左右转动
    print("  🔄 舵机转向测试: 左转 Pan=45°")
    hal.set_angles(pan=45, tilt=90, smooth=False)
    assert hal.cur_pan == 45.0

    print("  🔄 舵机转向测试: 右转 Pan=135°")
    hal.set_angles(pan=135, tilt=90, smooth=False)
    assert hal.cur_pan == 135.0

    print("  🔄 舵机转向测试: 抬头 Tilt=75°")
    hal.set_angles(pan=90, tilt=75, smooth=False)
    assert hal.cur_tilt == 75.0

    print("  🔄 舵机转向测试: 回正")
    hal.set_angles(pan=90, tilt=90, smooth=False)
    assert hal.cur_pan == 90.0
    assert hal.cur_tilt == 90.0

    hal.close()
    print("✨ 仿真 HAL 接口测试全部通过！")

if __name__ == "__main__":
    test_simulation_hal()
