import time
import threading
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

    def __init__(self, pan_pin=18, tilt_pin=13, touch_pin=17, spi_port=0, spi_device=0, dc_pin=25, rst_pin=22, **kwargs):
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
        self.touch_pin = touch_pin
        self.cur_pan = 90.0
        self.cur_tilt = 90.0
        self._cam = None  # 常驻摄像头句柄 (cv2.VideoCapture)

        # 舵机深度运动学参数
        self.pan_trim = float(kwargs.get("pan_trim", 0.0))
        self.tilt_trim = float(kwargs.get("tilt_trim", 0.0))
        self.pan_invert = bool(kwargs.get("pan_invert", False))
        self.tilt_invert = bool(kwargs.get("tilt_invert", False))
        self.pan_min = float(kwargs.get("pan_min", 20.0))
        self.pan_max = float(kwargs.get("pan_max", 160.0))
        self.tilt_min = float(kwargs.get("tilt_min", 60.0))
        self.tilt_max = float(kwargs.get("tilt_max", 120.0))
        self.servo_speed = max(2, int(kwargs.get("servo_speed", 15)))
        self.auto_detach_delay = max(0.1, float(kwargs.get("servo_auto_detach_delay", 0.8)))

        # 舵机防抖与防发热自动卸力定时器
        self._detach_timer = None
        self._detach_lock = threading.Lock()

        # 触摸监听回调列表
        self.touch_listeners = []
        self._touch_cb_handle = None

        # 初始化 TTP223 摸头传感器 (默认点动高电平有效)
        if self.touch_pin is not None:
            try:
                self.pi.set_mode(self.touch_pin, pigpio.INPUT)
                self.pi.set_pull_up_down(self.touch_pin, pigpio.PUD_DOWN)
                self._touch_cb_handle = self.pi.callback(self.touch_pin, pigpio.RISING_EDGE, self._on_gpio_touch)
                print(f"👆 [HAL-Physical] TTP223 摸头传感器已就绪 (GPIO {self.touch_pin})")
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 摸头传感器初始化跳过: {e}")

        # 初始化 4 独立按键 (K1~K4): 低电平触发标准逻辑 (内部弱上拉)
        # K1: GPIO 23 (音量+ / 菜单上), K2: GPIO 24 (音量- / 菜单下)
        # K3: GPIO 27 (确认 / 进入菜单), K4: GPIO 4 (表情页直接唤醒 / 菜单返回 / 长按舵机休眠)
        self.button_pins = {
            "K1": 23,
            "K2": 24,
            "K3": 27,
            "K4": 4
        }
        self.button_listeners = []
        self._btn_cb_handles = []
        self._btn_down_times = {}

        for b_name, b_pin in self.button_pins.items():
            try:
                self.pi.set_mode(b_pin, pigpio.INPUT)
                self.pi.set_pull_up_down(b_pin, pigpio.PUD_UP)
                self.pi.set_glitch_filter(b_pin, 30000)  # 30ms 硬件硬件消抖
                cb = self.pi.callback(b_pin, pigpio.EITHER_EDGE, lambda g, l, t, name=b_name: self._on_gpio_button(name, l, t))
                self._btn_cb_handles.append(cb)
                print(f"🔘 [HAL-Physical] 独立按键 {b_name} 就绪 (GPIO {b_pin})")
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 按键 {b_name} (GPIO {b_pin}) 初始化跳过: {e}")

        # 初始化 HLK-LD2402 24GHz 毫米波人体存在感应模块 (Pin 37 / GPIO 26)
        # OUT 默认高电平=有人/微动，低电平=无人
        self.radar_pin = kwargs.get("radar_pin", 26)
        self.radar_listeners = []
        self._radar_cb_handle = None
        self.radar_detected = True

        if self.radar_pin is not None:
            try:
                self.pi.set_mode(self.radar_pin, pigpio.INPUT)
                self.pi.set_pull_up_down(self.radar_pin, pigpio.PUD_DOWN)
                self.pi.set_glitch_filter(self.radar_pin, 50000)  # 50ms 硬件消抖
                self.radar_detected = bool(self.pi.read(self.radar_pin))
                self._radar_cb_handle = self.pi.callback(
                    self.radar_pin,
                    pigpio.EITHER_EDGE,
                    self._on_gpio_radar
                )
                print(f"📡 [HAL-Physical] HLK-LD2402 毫米波人体雷达已就绪 (GPIO {self.radar_pin}, 初始状态: {'有人' if self.radar_detected else '无人'})")
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 毫米波雷达初始化跳过: {e}")


        # 初始化 ST7789 2.4 寸 SPI 彩屏 (240x320)
        self.screen = None
        if spi is not None and st7789 is not None:
            try:
                serial_spi = spi(port=spi_port, device=spi_device, gpio_DC=dc_pin, gpio_RST=rst_pin, bus_speed_hz=32000000)
                self.screen = st7789(serial_spi, width=240, height=320, rotate=0)
                # 覆盖 luma.lcd 默认横屏 MADCTL(0x70) 与反色 INVON(0x21)，适配 GMT024 竖屏 240x320 正色显示
                self.screen.command(0x36, 0x00)
                self.screen.command(0x20)
                print("📺 [HAL-Physical] ST7789 屏幕初始化成功")
            except Exception as e:
                print(f"⚠️ [HAL-Physical] ST7789 屏幕初始化失败: {e}")
        else:
            print("⚠️ [HAL-Physical] luma.lcd 库未安装，屏幕驱动已跳过")

        # 舵机回正 (90度居中)
        self.set_angles(90, 90, smooth=False)
        print("🤖 [HAL-Physical] 物理舵机与外设初始化完毕")

    def _on_gpio_touch(self, gpio, level, tick):
        """GPIO 边缘中断回调"""
        self.trigger_touch()

    def add_touch_listener(self, callback):
        """注册摸头事件回调"""
        if callback not in self.touch_listeners:
            self.touch_listeners.append(callback)

    def trigger_touch(self):
        """触发触摸事件广播"""
        for cb in list(self.touch_listeners):
            try:
                cb()
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 触摸回调执行异常: {e}")

    def _on_gpio_button(self, name: str, level: int, tick: int):
        """处理 4 独立按键电平跳变 (低电平按下，高电平释放)"""
        now = time.time()
        if level == 0:  # 下降沿按下
            self._btn_down_times[name] = now
        elif level == 1:  # 上升沿松开
            down_t = self._btn_down_times.pop(name, None)
            if down_t:
                press_dur = now - down_t
                event_type = "long_press" if press_dur >= 2.5 else "click"
                self.trigger_button(name, event_type)

    def add_button_listener(self, callback):
        """注册独立按键事件回调"""
        if callback not in self.button_listeners:
            self.button_listeners.append(callback)

    def trigger_button(self, button_name: str, event_type: str = "click"):
        """触发按键事件广播"""
        for cb in list(self.button_listeners):
            try:
                cb(button_name, event_type)
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 按键回调执行异常: {e}")

    def _on_gpio_radar(self, gpio, level, tick):
        """GPIO 毫米波雷达中断回调 (1=有人靠近/在场, 0=无人离席)"""
        detected = (level == 1)
        self.radar_detected = detected
        self.trigger_radar(detected)

    def add_radar_listener(self, callback):
        """注册毫米波雷达人体存在事件回调"""
        if callback not in self.radar_listeners:
            self.radar_listeners.append(callback)

    def trigger_radar(self, detected: bool):
        """触发毫米波雷达人体事件广播"""
        self.radar_detected = bool(detected)
        for cb in list(self.radar_listeners):
            try:
                cb(self.radar_detected)
            except Exception as e:
                print(f"⚠️ [HAL-Physical] 毫米波雷达回调执行异常: {e}")

    def is_radar_detected(self) -> bool:
        """获取当前雷达感知状态"""
        return bool(self.radar_detected)


    def _angle_to_pulse(self, angle: float) -> int:
        """
        舵机角度转 PWM 脉宽 (SG90 通常 500us~2500us 对应 0°~180°)
        """
        angle = max(10.0, min(170.0, float(angle)))
        return int(500 + (angle / 180.0) * 2000)

    def _cancel_detach(self):
        """取消待执行的卸力计划"""
        with self._detach_lock:
            if self._detach_timer:
                self._detach_timer.cancel()
                self._detach_timer = None

    def _schedule_servo_detach(self, delay: float = 0.8):
        """延迟自动释放 PWM 脉冲，杜绝 SG90 持续啸叫、发热与抖动"""
        with self._detach_lock:
            if self._detach_timer:
                self._detach_timer.cancel()
            self._detach_timer = threading.Timer(delay, self._detach_servos)
            self._detach_timer.daemon = True
            self._detach_timer.start()

    def _detach_servos(self):
        """真正切断舵机 PWM 脉宽输出"""
        try:
            if self.pi and self.pi.connected:
                self.pi.set_servo_pulsewidth(self.pan_pin, 0)
                self.pi.set_servo_pulsewidth(self.tilt_pin, 0)
        except Exception:
            pass

    def detach_servos(self):
        """主动立即切断舵机 PWM 脉宽输出 (卸力/防发热/静音休眠)"""
        self._cancel_detach()
        self._detach_servos()


    def update_servo_config(self, cfg: dict):
        """动态更新舵机微调与动力学参数"""
        if "pan_trim" in cfg: self.pan_trim = float(cfg["pan_trim"])
        if "tilt_trim" in cfg: self.tilt_trim = float(cfg["tilt_trim"])
        if "pan_invert" in cfg: self.pan_invert = bool(cfg["pan_invert"])
        if "tilt_invert" in cfg: self.tilt_invert = bool(cfg["tilt_invert"])
        if "pan_min" in cfg: self.pan_min = float(cfg["pan_min"])
        if "pan_max" in cfg: self.pan_max = float(cfg["pan_max"])
        if "tilt_min" in cfg: self.tilt_min = float(cfg["tilt_min"])
        if "tilt_max" in cfg: self.tilt_max = float(cfg["tilt_max"])
        if "servo_speed" in cfg: self.servo_speed = max(2, int(cfg["servo_speed"]))
        if "servo_auto_detach_delay" in cfg: self.auto_detach_delay = max(0.1, float(cfg["servo_auto_detach_delay"]))

    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        # 唤醒/重置卸力定时器
        self._cancel_detach()

        # 软件零点微调 (Trim Offset)
        target_pan = pan + self.pan_trim
        target_tilt = tilt + self.tilt_trim

        # 软件反向 (Invert)
        if self.pan_invert:
            target_pan = 180.0 - target_pan
        if self.tilt_invert:
            target_tilt = 180.0 - target_tilt

        # 物理机械行程限位保护 (Min/Max Limits)
        target_pan = max(self.pan_min, min(self.pan_max, float(target_pan)))
        target_tilt = max(self.tilt_min, min(self.tilt_max, float(target_tilt)))

        # 硬件防抖滤波死区 (若目标角度变化小于 0.8°，忽略微小抖动，避免电机频繁唤醒与发热)
        if abs(target_pan - self.cur_pan) < 0.8 and abs(target_tilt - self.cur_tilt) < 0.8:
            return

        if not smooth:
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(target_pan))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(target_tilt))
            self.cur_pan, self.cur_tilt = target_pan, target_tilt
            self._schedule_servo_detach(self.auto_detach_delay)
            return

        # 细分步长插值，消除瞬间加减速冲击 (受平滑步长 servo_speed 控制)
        steps = self.servo_speed
        for s in range(1, steps + 1):
            p = self.cur_pan + (target_pan - self.cur_pan) * (s / steps)
            t = self.cur_tilt + (target_tilt - self.cur_tilt) * (s / steps)
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(p))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(t))
            time.sleep(0.015)

        self.cur_pan, self.cur_tilt = target_pan, target_tilt
        # 到位后自动卸力休眠 (受 auto_detach_delay 控制)
        self._schedule_servo_detach(self.auto_detach_delay)

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
                        self._cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        self._cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                if self._cam is not None and self._cam.isOpened():
                    # 关键抗陈旧机制：V4L2 硬件驱动常有 3~5 帧积压缓存，抓拍前连续丢弃 3 帧旧缓冲，获取最新曝光
                    for _ in range(3):
                        self._cam.grab()
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
        # 抓拍前务必先删除陈旧的历史临时文件，杜绝失败时误读旧图！
        if os.path.exists(tmp_img):
            try:
                os.remove(tmp_img)
            except Exception:
                pass
        try:
            res = subprocess.run(
                ["fswebcam", "-r", "640x480", "--no-banner", tmp_img],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=3,
            )
            if res.returncode == 0 and os.path.exists(tmp_img):
                with open(tmp_img, "rb") as f:
                    data = f.read()
                # 读完后立即清理临时文件，杜绝残存
                try:
                    os.remove(tmp_img)
                except Exception:
                    pass
                return data
        except Exception as e:
            print(f"⚠️ [HAL-Physical] 摄像头抓拍失败: {e}")
            if os.path.exists(tmp_img):
                try:
                    os.remove(tmp_img)
                except Exception:
                    pass
            return b""

    def close(self):
        # 释放摸头传感器中断
        if self._touch_cb_handle is not None:
            try:
                self._touch_cb_handle.cancel()
            except Exception:
                pass
            self._touch_cb_handle = None

        # 取消卸力定时器
        self._cancel_detach()

        # 释放摄像头句柄
        if self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None

        # 停用舵机信号输出，防止电机发热与持续蜂鸣
        try:
            self.pi.set_servo_pulsewidth(self.pan_pin, 0)
            self.pi.set_servo_pulsewidth(self.tilt_pin, 0)
            self.pi.stop()
        except Exception:
            pass
        print("🛑 [HAL-Physical] 树莓派硬件资源已释放")
