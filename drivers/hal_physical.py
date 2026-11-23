import time
import subprocess
from PIL import Image
from .hal_base import BaseRobotHAL

try:
    import pigpio
except ImportError:
    pigpio = None

try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7789
except ImportError:
    spi = None
    st7789 = None

try:
    import cv2
except ImportError:
    cv2 = None


class PhysicalRobotHAL(BaseRobotHAL):
    """
    树莓派 3B 物理真机驱动实现。
    使用 pigpio 硬件 DMA 定时器控制 SG90 舵机（防抖动），
    使用 luma.lcd / spidev 驱动 ST7789 2.4寸 SPI 显示屏，
    使用 fswebcam / v4l2 捕获 USB 摄像头画面。
    """

    def __init__(self, pan_pin=18, tilt_pin=13, spi_port=0, spi_device=0, dc_pin=25, rst_pin=22):
        if pigpio is None:
            raise ImportError(
                "未找到 pigpio 库，请在树莓派上运行: sudo apt install python3-pigpio 或 pip install pigpio"
            )

        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError(
                "pigpiod 守护进程未启动！请在终端执行: sudo systemctl start pigpiod"
            )

        self.pan_pin = pan_pin
        self.tilt_pin = tilt_pin
        self.cur_pan = 90.0
        self.cur_tilt = 90.0
        self._cam = None  # 常驻摄像头句柄 (cv2.VideoCapture)

        # 初始化 ST7789 2.4 寸 SPI 彩屏 (240x320)
        self.screen = None
        if spi is not None and st7789 is not None:
            try:
                serial_spi = spi(port=spi_port, device=spi_device, gpio_DC=dc_pin, gpio_RST=rst_pin)
                self.screen = st7789(serial_spi, width=240, height=320, rotate=0)
                print("📺 [HAL-Physical] ST7789 屏幕初始化成功")
            except Exception as e:
                print(f"⚠️ [HAL-Physical] ST7789 屏幕初始化失败: {e}")
        else:
            print("⚠️ [HAL-Physical] luma.lcd 库未安装，屏幕驱动已跳过")

        # 舵机回正 (90度居中)
        self.set_angles(90, 90, smooth=False)
        print("🤖 [HAL-Physical] 物理舵机与外设初始化完毕")

    def _angle_to_pulse(self, angle: float) -> int:
        """
        舵机角度转 PWM 脉宽 (SG90 通常 500us~2500us 对应 0°~180°)
        """
        angle = max(10.0, min(170.0, float(angle)))
        return int(500 + (angle / 180.0) * 2000)

    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        # 物理机械行程限位保护
        pan = max(20.0, min(160.0, float(pan)))
        tilt = max(60.0, min(120.0, float(tilt)))

        if not smooth:
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(pan))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(tilt))
            self.cur_pan, self.cur_tilt = pan, tilt
            return

        # 细分步长插值，消除瞬间加减速冲击
        steps = 15
        for s in range(1, steps + 1):
            p = self.cur_pan + (pan - self.cur_pan) * (s / steps)
            t = self.cur_tilt + (tilt - self.cur_tilt) * (s / steps)
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(p))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(t))
            time.sleep(0.015)

        self.cur_pan, self.cur_tilt = pan, tilt

    def display_face(self, pil_image: Image.Image):
        if self.screen is not None:
            try:
                self.screen.display(pil_image)
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 屏幕刷新错误: {e}")

    def capture_image(self) -> bytes:
        # 1. 优先使用常驻 OpenCV V4L2 句柄内存取帧 (零磁盘写入，耗时 < 15ms，避免反复创建进程)
        if cv2 is not None:
            try:
                if self._cam is None or not self._cam.isOpened():
                    self._cam = cv2.VideoCapture(0)
                    if self._cam.isOpened():
                        self._cam.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                        self._cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                if self._cam is not None and self._cam.isOpened():
                    ret, frame = self._cam.read()
                    if ret and frame is not None:
                        success, buf = cv2.imencode(".jpg", frame)
                        if success:
                            return buf.tobytes()
            except Exception:
                if self._cam is not None:
                    try:
                        self._cam.release()
                    except Exception:
                        pass
                self._cam = None

        # 2. 回退机制：若 OpenCV 句柄失败，降级为 fswebcam 临时抓拍
        tmp_img = "/tmp/capture.jpg"
        try:
            subprocess.run(
                ["fswebcam", "-r", "320x240", "--no-banner", tmp_img],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=3,
            )
            with open(tmp_img, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"⚠️ [HAL-Physical] 摄像头抓拍失败: {e}")
            return b""

    def close(self):
        # 释放摄像头句柄
        if self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None

        # 停用舵机信号输出，防止电机发热与持续蜂鸣
        self.pi.set_servo_pulsewidth(self.pan_pin, 0)
        self.pi.set_servo_pulsewidth(self.tilt_pin, 0)
        self.pi.stop()
        print("🛑 [HAL-Physical] 树莓派硬件资源已释放")
