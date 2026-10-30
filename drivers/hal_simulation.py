import os
import time
import math
from .hal_base import BaseRobotHAL
from PIL import Image

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import mujoco
    import mujoco.viewer
except ImportError:
    mujoco = None


class SimulationRobotHAL(BaseRobotHAL):
    """
    PC 仿真硬件抽象层实现。
    使用 MuJoCo 进行二轴云台动力学与姿态控制，使用 OpenCV 渲染虚拟眼球屏幕。
    如果未安装 MuJoCo 或处于无头 (Headless) 环境，会自动退化为轻量模拟模式，方便逻辑调试。
    """

    def __init__(self, model_path="scene.xml", headless=False, urdf_path=None, **kwargs):
        self.cur_pan = 90.0
        self.cur_tilt = 90.0
        self.headless = headless or not bool(os.environ.get("DISPLAY"))
        self.has_mujoco = False
        self.model = None
        self.data = None
        self.viewer = None

        # 兼容 urdf_path 传参
        if urdf_path is not None:
            model_path = urdf_path

        # 优先选择自带几何体的 scene.xml，若不存在再回退到 urdf
        if not os.path.exists(model_path) and os.path.exists("robot.urdf"):
            model_path = "robot.urdf"

        if mujoco is not None and os.path.exists(model_path) and not self.headless:
            try:
                self.model = mujoco.MjModel.from_xml_path(model_path)
                self.data = mujoco.MjData(self.model)
                self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
                self.has_mujoco = True
                print(f"🎮 [HAL-Sim] MuJoCo 3D 动力学仿真视窗已启动 (模型: {model_path})")
            except Exception as e:
                print(f"⚠️ [HAL-Sim] MuJoCo 视窗初始化跳过 ({e})，将以控制台日志模式运行")
        else:
            if not self.has_mujoco:
                print("ℹ️ [HAL-Sim] 仿真模式以轻量/Headless模式运行 (无 3D 物理窗口)")

    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        pan = max(20.0, min(160.0, float(pan)))
        tilt = max(60.0, min(120.0, float(tilt)))

        if self.has_mujoco and self.model and self.data and self.viewer:
            # 映射角度至弧度 (90 度为中间零位)
            rad_pan = math.radians(pan - 90.0)
            rad_tilt = math.radians(tilt - 90.0)

            self.data.ctrl[0] = rad_pan
            self.data.ctrl[1] = rad_tilt

            steps = 20 if smooth else 1
            for _ in range(steps):
                mujoco.mj_step(self.model, self.data)
                if self.viewer.is_running():
                    self.viewer.sync()
                if smooth:
                    time.sleep(0.005)
        else:
            print(f"📐 [HAL-Sim] 姿态调整 -> Pan(水平): {pan:.1f}°, Tilt(俯仰): {tilt:.1f}°")

        self.cur_pan, self.cur_tilt = pan, tilt

    def display_face(self, pil_image: Image.Image):
        if cv2 is not None and np is not None and not self.headless:
            try:
                img_np = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
                cv2.imshow("Robot Face Screen (Virtual)", img_np)
                cv2.waitKey(1)
                return
            except Exception:
                pass
        # Headless 环境下保存至临时文件便于观察
        try:
            pil_image.save("/tmp/robot_face_latest.png")
        except Exception:
            pass

    def capture_image(self) -> bytes:
        if cv2 is not None:
            try:
                cap = cv2.VideoCapture(0)
                if cap.isOpened():
                    ret, frame = cap.read()
                    cap.release()
                    if ret:
                        _, buf = cv2.imencode(".jpg", frame)
                        return buf.tobytes()
            except Exception as e:
                print(f"⚠️ [HAL-Sim] 摄像头读取失败: {e}")

        # 若无摄像头则返回生成占位图
        return b""

    def close(self):
        if cv2 is not None:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        if self.viewer is not None and self.viewer.is_running():
            try:
                self.viewer.close()
            except Exception:
                pass
        print("🛑 [HAL-Sim] 仿真资源已释放")
