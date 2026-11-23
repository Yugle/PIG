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
        self.touch_listeners = []
        self.button_listeners = []
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
        # 1. 优先获取数字孪生/Web 仿真端传入的真实摄像头画面 (/tmp/pig_latest_frame.jpg)
        tmp_path = "/tmp/pig_latest_frame.jpg"
        if os.path.exists(tmp_path):
            try:
                with open(tmp_path, "rb") as f:
                    content = f.read()
                    if len(content) > 100:
                        return content
            except Exception:
                pass

        main_mod = sys.modules.get("__main__")
        if main_mod:
            if hasattr(main_mod, "get_latest_vision_frame"):
                try:
                    f = main_mod.get_latest_vision_frame()
                    if f and len(f) > 100:
                        return f
                except Exception:
                    pass
            if hasattr(main_mod, "_latest_webcam_frame") and main_mod._latest_webcam_frame:
                return main_mod._latest_webcam_frame

        if cv2 is not None:
            # Linux 环境下若不存在 /dev/video0，直接返回，避免触发 OpenCV V4L2 报警
            if sys.platform.startswith("linux") and not os.path.exists("/dev/video0"):
                return b""

            now = time.time()
            # 仿真环境下避免高频轮询不存在的宿主机摄像头
            if getattr(self, "_cam_unavailable", False) and (now - getattr(self, "_last_cam_check", 0) < 10.0):
                return b""
            try:
                self._last_cam_check = now
                if not hasattr(self, "_cam") or self._cam is None or not self._cam.isOpened():
                    self._cam = cv2.VideoCapture(0)
                if self._cam is not None and self._cam.isOpened():
                    self._cam_unavailable = False
                    ret, frame = self._cam.read()
                    if ret and frame is not None:
                        success, buf = cv2.imencode(".jpg", frame)
                        if success:
                            return buf.tobytes()
                else:
                    self._cam_unavailable = True
            except Exception as e:
                self._cam = None
                self._cam_unavailable = True

        # 若无物理摄像头则返回空字节
        return b""

    def add_touch_listener(self, callback):
        """注册仿真摸头事件回调"""
        if callback not in self.touch_listeners:
            self.touch_listeners.append(callback)

    def trigger_touch(self):
        """仿真触发摸头事件"""
        for cb in list(self.touch_listeners):
            try:
                cb()
            except Exception as e:
                print(f"⚠️ [HAL-Sim] 触摸回调执行异常: {e}")

    def add_button_listener(self, callback):
        """注册仿真按键事件回调"""
        if callback not in self.button_listeners:
            self.button_listeners.append(callback)

    def trigger_button(self, button_name: str, event_type: str = "click"):
        """仿真触发实体按键事件 (K1, K2, K3, K4)"""
        for cb in list(self.button_listeners):
            try:
                cb(button_name, event_type)
            except Exception as e:
                print(f"⚠️ [HAL-Sim] 按键回调执行异常: {e}")

    def close(self):
        if hasattr(self, "_cam") and self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None
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
