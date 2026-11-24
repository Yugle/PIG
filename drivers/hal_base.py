from abc import ABC, abstractmethod
from PIL import Image

class BaseRobotHAL(ABC):
    """
    机器人硬件抽象层 (Hardware Abstraction Layer) 基类。
    统一树莓派物理机 (Physical) 与 PC 仿真环境 (Simulation) 的接口契约。
    """

    @abstractmethod
    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        """
        设定脖子二轴舵机角度。
        :param pan: 水平航向角 (Yaw)，角度范围通常限制在 [20.0, 160.0]，90 为正中。
        :param tilt: 俯仰角 (Pitch)，角度范围通常限制在 [60.0, 120.0]，90 为平视，80 为轻微抬头。
        :param smooth: 是否平滑插值过渡。
        """
        pass

    @abstractmethod
    def display_face(self, pil_image: Image.Image):
        """
        在显示屏或虚拟视窗上渲染面部/眼球图像。
        :param pil_image: 240x320 尺寸的 PIL Image 对象。
        """
        pass

    @abstractmethod
    def capture_image(self) -> bytes:
        """
        捕获当前摄像头视野的一帧 JPEG 图像字节流。
        :return: bytes 格式的 JPEG 图像数据。
        """
        pass

    @abstractmethod
    def close(self):
        """
        安全释放所有硬件/外设资源。
        """
        pass

    def add_touch_listener(self, callback):
        """
        注册摸头传感器 (TTP223) 触摸事件监听回调。
        :param callback: 无参函数 Callable[[], None]
        """
        pass

    def trigger_touch(self):
        """
        手动或仿真触发一次触摸事件。
        """
        pass

    def add_button_listener(self, callback):
        """
        注册实体独立按键 (K1~K4) 事件监听回调。
        :param callback: 接受 (button_name: str, event_type: str) 的回调函数
        button_name: 'K1' | 'K2' | 'K3' | 'K4'
        event_type: 'click' | 'long_press'
        """
        pass

    def trigger_button(self, button_name: str, event_type: str = "click"):
        """
        手动或仿真触发一次按键事件。
        """
        pass

    def add_radar_listener(self, callback):
        """
        注册 HLK-LD2402 24GHz 毫米波人体存在雷达事件监听回调。
        :param callback: 接受 (detected: bool) 的回调函数
        detected: True (有人靠近/在场), False (人走/无人离席)
        """
        pass

    def trigger_radar(self, detected: bool):
        """
        手动或仿真触发一次毫米波雷达人体状态变化事件。
        """
        pass

    def is_radar_detected(self) -> bool:
        """
        获取当前毫米波雷达感知的人体存在状态。
        """
        return True

    def detach_servos(self):
        """
        切断舵机 PWM 脉冲以释放力矩 (防发热/防啸叫/低功耗卸力)。
        """
        pass


