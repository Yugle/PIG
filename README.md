# 基于树莓派 3 的桌面具身 AI 伴侣机器人全栈落地工程指南

本项目专为 **树莓派 3 (1GB RAM)** 量身定制，严格遵循 **“端侧硬实时感知与物理执行、云端多模态推理与音色合成”** 的低耦合架构。同时提供 **仿真优先 (Sim-to-Real)** 设计，可在物理硬件到货前于 PC 端 (MuJoCo) 完成手眼闭环与交互逻辑的 100% 验证。

---

## 目录

1. [全局架构与数据流](#一全局架构与数据流)
2. [硬件物料清单 (BOM) 与采购精准参数](#二硬件物料清单-bom-与采购精准参数)
3. [电气走线与隔离供电拓扑](#三电气走线与隔离供电拓扑)
4. [3D 机械结构与装配工序](#四3d-机械结构与装配工序)
5. [软硬件解耦与统一抽象层 (HAL)](#五软硬件解耦与统一抽象层-hal)
6. [仿真环境管线搭建 (MuJoCo + URDF)](#六仿真环境管线搭建-mujoco--urdf)
7. [树莓派环境配置与排坑指南](#七树莓派环境配置与排坑指南)
8. [核心工程代码实现](#八核心工程代码实现)
   * 配置文件 (`config.json`)
   * 硬件驱动层 (`hal_physical.py` & `hal_simulation.py`)
   * 液晶屏表情渲染引擎 (`eye_renderer.py`)
   * 语音克隆与 TTS 合成适配器 (`tts_engine.py`)
   * 具身交互主中枢 (`main_agent.py`)
   * 局域网远程代播服务 (`remote_server.py`)
   * OTA 自动化静默升级 (`ota_updater.py`)
9. [系统守护与保活部署 (systemd)](#九系统守护与保活部署-systemd)

---

## 一、全局架构与数据流

```
 ┌────────────────────────────────────────────────────────┐
 │                      感知与输入层                       │
 │  • 本地 USB 麦克风 ──> openWakeWord 离线流式监听 ("小猪小猪")│
 │  • USB 免驱摄像头  ──> OpenCV/V4L2 场景快照抓拍        │
 │  • 局域网 HTTP API ──> FastAPI 接收手机/电脑下发播报文本 │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────v─────────────────────────────┐
 │                      大脑中枢调度                       │
 │  • 云端兼容 LLM/VLM (DeepSeek / Qwen-VL / OpenAI API)  │
 │  • 结构化契约输出:                                     │
 │    { reply: "语音文本", emotion: "...", action: "...",  │
 │      pan_angle: 90, tilt_angle: 80 }                  │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────v─────────────────────────────┐
 │                统一硬件抽象层 (HAL 调度)                │
 │          ┌───────────────┴───────────────┐             │
 │          ▼                               ▼             │
 │  [物理真实机 (树莓派 3)]           [PC 物理仿真 (MuJoCo)]   │
 │  • pigpio PWM 舵机驱动             • 关节力矩/位置控制回路 │
 │  • ST7789 SPI 液晶屏刷新           • 虚拟屏动态材质贴图     │
 │  • 3.5mm 音箱输出                 • 本地声卡播放          │
 └────────────────────────────────────────────────────────┘
```

---

## 二、硬件物料清单 (BOM) 与采购精准参数

| 模块分类 | 采购精准名称 / 搜索关键词 | 核心参数与规格要求 | 参考成本 |
| :--- | :--- | :--- | :--- |
| **主控板** | 树莓派 3B 或 3B+ (自备) | 运行官方 64-bit Raspberry Pi OS Lite，TF 卡 16GB 以上 (Class 10) | 0 元 |
| **彩色屏幕** | **2.4寸 ST7789 SPI TFT 显示屏模组** | **8-Pin 已焊接排针**，分辨率 $240 \times 320$，**不带触摸板**，驱动芯片认准 ST7789 | ~22 元 |
| **二轴脖子** | **SG90 二维云台套件** | 选**包含支架 + 2 个 SG90 9g 舵机**的整套包（自带固定螺丝和白色十字/一字舵盘） | ~16 元 |
| **视觉输入** | **免驱微型 USB 摄像头模组** | 720P 或 1080P，免驱 (UVC 协议)，镜头视角 70°~90° 无畸变，自带 USB-A 引线 (长 30~50cm) | ~20 元 |
| **声音输入** | **免驱迷你 USB 麦克风** | 超小型直插款（无线鼠标接收器大小），免驱即插即用，全向收音 | ~8 元 |
| **声音输出** | **便携式 3.5mm 有线小音箱** | 3.5mm 音频直插头，**必须选自带锂电池供电款**（避免直接抽取树莓派 USB 5V 导致瞬间欠压重启） | ~18 元 |
| **机构外壳** | **3D 打印 4 件套** | 对应交付的 STL：底座、机械颈、前脸、后脑壳。推荐材质：**PETG 哑光白 / 深灰撞色** 或 **光敏树脂** | ~25~35 元 |
| **接线耗材** | **杜邦线 20cm (母对母 40P + 公对母 40P)** | 屏幕需母对母，舵机延长需公对母 | ~6 元 |
| **舵机电源** | **废旧手机充电头 (5V 2A) + 剪线 USB 线** | 用于舵机独立供电，避免与树莓派共享 5V 电源轨 | 0 元 |

---

## 三、电气走线与隔离供电拓扑

舵机启动或堵转时产生的反电动势和浪涌电流极易导致树莓派 CPU 欠压复位（Brownout），**必须采用独立供电与共地设计**：

```text
               外部 5V 2A 独立供电头
               ┌───────────────┬───────────────┐
               │ +5V (红线)    │ GND (黑线)    │
               └───────┬───────┴───────┬───────┘
                       │               │
      ┌────────────────┼───────────────┤
      │                │               │
      ▼                ▼               ▼
[水平舵机 Pan]    [俯仰舵机 Tilt]         │
  红线 (VCC)       红线 (VCC)          │
  棕/黑 (GND)      棕/黑 (GND) ────────┘ (系统共地)
  黄/橙 (PWM)      黄/橙 (PWM)         │
      │                │               │
      │                │               ▼
┌─────┴────────────────┴───────────────┴───────────────────────┐
│                     树莓派 3B 40-Pin 拓扑                     │
├─────────────────────────────────────────────────────────────┤
│ • Pin 12 (GPIO 18 / PWM0)  ───> 水平舵机信号线 (Pan)         │
│ • Pin 33 (GPIO 13 / PWM1)  ───> 俯仰舵机信号线 (Tilt)        │
│ • Pin 6 / Pin 9 (GND)      ───> 共地输入 (GND)              │
│ • Pin 1  (3.3V PWR)        ───> ST7789 屏幕 VCC             │
│ • Pin 19 (GPIO 10 / MOSI)  ───> ST7789 屏幕 SDA / DIN       │
│ • Pin 23 (GPIO 11 / SCLK)  ───> ST7789 屏幕 SCL / CLK       │
│ • Pin 24 (GPIO 8  / CE0)   ───> ST7789 屏幕 CS              │
│ • Pin 22 (GPIO 25)         ───> ST7789 屏幕 DC (数据/命令)   │
│ • Pin 15 (GPIO 22)         ───> ST7789 屏幕 RES (复位)      │
│ • Pin 17 (3.3V) 或悬空     ───> ST7789 屏幕 BLK 背光引脚    │
│ • USB Port 1               ───> USB 摄像头模组              │
│ • USB Port 2               ───> USB 迷你麦克风              │
│ • 3.5mm 音频口             ───> 自带锂电小音箱              │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、3D 机械结构与装配工序

配套提供的 `full_desktop_robot_assembly_package.zip` 包含以下 4 个部件：
1. **底座 (`1_desktop_base_stand.stl`)**：
   * 水平舵机自上而下卡入底座顶部槽位，两颗沉头小螺丝紧固。
   * 电源引线与通信线自底座背部的防折线隧道穿出。
2. **机械颈 (`2_articulated_neck_joint.stl`)**：
   * 俯仰舵机横向装入球形铰链内部。
   * 机械颈底部的固定孔锁在底座舵机的单臂白色舵盘上。
3. **头部前面罩 (`3_robot_head_face.stl`)**：
   * 2.4 寸屏幕从后方贴紧 $37\text{mm} \times 49\text{mm}$ 的显示视窗，四周点少许纳米双面胶；摄像头对准额头 9.5mm 圆孔贴紧。
4. **后脑壳 (`4_robot_head_back.stl`)**：
   * 所有排线沿机械颈空腔引出；前后壳对齐，下端安装座与俯仰舵盘用中心螺丝咬合固紧。

---

## 五、软硬件解耦与统一抽象层 (HAL)

为了让同一套业务逻辑在 **PC 仿真环境** 与 **树莓派真机** 上无缝运行，采用抽象基类设计：

```python
# hal_base.py
from abc import ABC, abstractmethod

class BaseRobotHAL(ABC):
    @abstractmethod
    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        """设定脖子转角 (角度制: Pan 20~160, Tilt 60~120)"""
        pass

    @abstractmethod
    def display_face(self, pil_image):
        """在屏幕或视窗上渲染面部表情图像"""
        pass

    @abstractmethod
    def capture_image(self) -> bytes:
        """捕获当前视野的一帧 JPEG 图像字节流"""
        pass

    @abstractmethod
    def close(self):
        """释放外设资源"""
        pass
```

---

## 六、仿真环境管线搭建 (MuJoCo + URDF)

无需真实硬件，在开发机（Windows / macOS / Ubuntu）上即可完成全部验证。

### 1. 机器人 URDF 描述文件 (`robot.urdf`)

将生成的 STL 放置于同一目录，建立运动学拓扑：

```xml
<?xml version="1.0"?>
<robot name="desktop_robot">
  <!-- 底座固定于环境 -->
  <link name="base_link">
    <visual>
      <geometry><mesh filename="1_desktop_base_stand.stl" scale="0.001 0.001 0.001"/></geometry>
      <material name="white"><color rgba="0.9 0.9 0.9 1.0"/></material>
    </visual>
    <collision>
      <geometry><mesh filename="1_desktop_base_stand.stl" scale="0.001 0.001 0.001"/></geometry>
    </collision>
    <inertial>
      <mass value="0.15"/>
      <inertia ixx="0.0001" ixy="0" ixz="0" iyy="0.0001" iyz="0" izz="0.0001"/>
    </inertial>
  </link>

  <!-- 水平旋转关节 (Yaw) -->
  <joint name="neck_yaw_joint" type="revolute">
    <parent link="base_link"/>
    <child link="neck_link"/>
    <origin xyz="0 0 0.025" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="-1.22" upper="1.22" effort="0.25" velocity="3.0"/>
  </joint>

  <!-- 机械颈 -->
  <link name="neck_link">
    <visual>
      <geometry><mesh filename="2_articulated_neck_joint.stl" scale="0.001 0.001 0.001"/></geometry>
      <material name="dark_grey"><color rgba="0.25 0.25 0.25 1.0"/></material>
    </visual>
    <collision>
      <geometry><mesh filename="2_articulated_neck_joint.stl" scale="0.001 0.001 0.001"/></geometry>
    </collision>
    <inertial>
      <mass value="0.03"/>
      <inertia ixx="0.00003" ixy="0" ixz="0" iyy="0.00003" iyz="0" izz="0.00003"/>
    </inertial>
  </link>

  <!-- 俯仰旋转关节 (Pitch) -->
  <joint name="head_pitch_joint" type="revolute">
    <parent link="neck_link"/>
    <child link="head_link"/>
    <origin xyz="0 0 0.018" rpy="0 0 0"/>
    <axis xyz="1 0 0"/>
    <limit lower="-0.52" upper="0.52" effort="0.25" velocity="3.0"/>
  </joint>

  <!-- 机器人头部与传感器 -->
  <link name="head_link">
    <visual>
      <geometry><mesh filename="3_robot_head_face.stl" scale="0.001 0.001 0.001"/></geometry>
      <material name="white"/>
    </visual>
    <collision>
      <geometry><mesh filename="3_robot_head_face.stl" scale="0.001 0.001 0.001"/></geometry>
    </collision>
    <inertial>
      <mass value="0.08"/>
      <inertia ixx="0.00006" ixy="0" ixz="0" iyy="0.00006" iyz="0" izz="0.00006"/>
    </inertial>
  </link>
</robot>
```

### 2. PC 仿真实现适配器 (`hal_simulation.py`)

```python
import time
import math
import cv2
import numpy as np
import mujoco
import mujoco.viewer
from hal_base import BaseRobotHAL

class SimulationRobotHAL(BaseRobotHAL):
    def __init__(self, urdf_path="robot.urdf"):
        self.model = mujoco.MjModel.from_xml_path(urdf_path)
        self.data = mujoco.MjData(self.model)
        self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        self.cur_pan = 90.0
        self.cur_tilt = 90.0

    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        # 映射角度至弧度限位 (90度为中间零位)
        rad_pan = math.radians(pan - 90.0)
        rad_tilt = math.radians(tilt - 90.0)

        self.data.ctrl[0] = rad_pan
        self.data.ctrl[1] = rad_tilt

        for _ in range(20):
            mujoco.mj_step(self.model, self.data)
            self.viewer.sync()
            time.sleep(0.005)

        self.cur_pan, self.cur_tilt = pan, tilt

    def display_face(self, pil_image):
        # 在 PC 独立调试窗口中显示实时眼球渲染
        img_np = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        cv2.imshow("Robot Face Screen (Virtual)", img_np)
        cv2.waitKey(1)

    def capture_image(self) -> bytes:
        # 使用电脑自带摄像头捕获视觉，若无则返回合成画面
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if ret:
            _, buf = cv2.imencode(".jpg", frame)
            return buf.tobytes()
        return b""

    def close(self):
        cv2.destroyAllWindows()
        if self.viewer.is_running():
            self.viewer.close()
```

---

## 七、树莓派环境配置与排坑指南

### 1. 系统底层服务初始化
```bash
# 启用硬件接口并安装依赖
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

sudo apt update && sudo apt install -y \
    python3-pip python3-dev pigpio python3-pigpio \
    portaudio19-dev libasound2-dev alsa-utils fswebcam git

# 必须启用 pigpiod 精确 PWM 硬件控制（消除 SG90 抽搐现象）
sudo systemctl enable pigpiod && sudo systemctl start pigpiod

# 安装核心依赖
pip3 install openai openwakeword pyaudio edge-tts pygame pillow numpy fastapi uvicorn requests
```

### 2. 音频输入设备 (麦克风) 排坑排查
USB 麦克风插入后，需获取正确的 ALSA 硬件卡号：
```bash
arecord -l
```
若输出类似 `card 1: Device [USB PnP Sound Device], device 0`，需在当前用户主目录建立配置文件 `~/.asoundrc`，强制指定默认录音输入源为卡 1：
```text
pcm.!default {
    type asym
    playback.pcm "hw:0,0"
    capture.pcm "hw:1,0"
}
ctl.!default {
    type hw
    card 0
}
```

---

## 八、核心工程代码实现

### 1. 全局配置文件 (`config.json`)
```json
{
  "api_key": "YOUR_LLM_API_KEY",
  "base_url": "https://api.deepseek.com",
  "llm_model": "deepseek-chat",
  "tts_mode": "edge",
  "custom_voice_id": "",
  "custom_tts_api_key": "",
  "pan_pin": 18,
  "tilt_pin": 13,
  "wakeword_model": "hey_jarvis",
  "wakeword_threshold": 0.5
}
```

### 2. 真实物理机驱动实现 (`hal_physical.py`)
```python
import time
import subprocess
import pigpio
from PIL import Image
from hal_base import BaseRobotHAL

# 针对 ST7789 的底层推流驱动（通过 spidev 或 luma.lcd）
from luma.core.interface.serial import spi
from luma.lcd.device import st7789

class PhysicalRobotHAL(BaseRobotHAL):
    def __init__(self, pan_pin=18, tilt_pin=13):
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("pigpiod 未启动，请先运行: sudo systemctl start pigpiod")
        self.pan_pin = pan_pin
        self.tilt_pin = tilt_pin
        self.cur_pan = 90.0
        self.cur_tilt = 90.0

        # 初始化 ST7789 屏幕 (SPI 接口)
        serial_spi = spi(port=0, device=0, gpio_DC=25, gpio_RST=22)
        self.screen = st7789(serial_spi, width=240, height=320, rotate=0)

        # 舵机回正
        self.set_angles(90, 90, smooth=False)

    def _angle_to_pulse(self, angle: float) -> int:
        angle = max(10, min(170, angle))
        return int(500 + (angle / 180.0) * 2000)

    def set_angles(self, pan: float, tilt: float, smooth: bool = True):
        pan = max(20.0, min(160.0, pan))
        tilt = max(60.0, min(120.0, tilt))

        if not smooth:
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(pan))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(tilt))
            self.cur_pan, self.cur_tilt = pan, tilt
            return

        steps = 15
        for s in range(1, steps + 1):
            p = self.cur_pan + (pan - self.cur_pan) * (s / steps)
            t = self.cur_tilt + (tilt - self.cur_tilt) * (s / steps)
            self.pi.set_servo_pulsewidth(self.pan_pin, self._angle_to_pulse(p))
            self.pi.set_servo_pulsewidth(self.tilt_pin, self._angle_to_pulse(t))
            time.sleep(0.015)

        self.cur_pan, self.cur_tilt = pan, tilt

    def display_face(self, pil_image):
        self.screen.display(pil_image)

    def capture_image(self) -> bytes:
        tmp_img = "/tmp/capture.jpg"
        subprocess.run(["fswebcam", "-r", "640x480", "--no-banner", tmp_img],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(tmp_img, "rb") as f:
            return f.read()

    def close(self):
        self.pi.set_servo_pulsewidth(self.pan_pin, 0)
        self.pi.set_servo_pulsewidth(self.tilt_pin, 0)
        self.pi.stop()
```

### 3. 表情渲染引擎 (`eye_renderer.py`)
```python
from PIL import Image, ImageDraw

class EyeRenderer:
    def __init__(self, width=240, height=320):
        self.width = width
        self.height = height

    def render_eyes(self, emotion="NORMAL", look_x=0, look_y=0) -> Image.Image:
        # 深色背景 + 亮青色眼球
        img = Image.new("RGB", (self.width, self.height), (10, 14, 20))
        draw = ImageDraw.Draw(img)
        eye_color = (0, 220, 255)

        cy = self.height // 2 + look_y
        lx = self.width // 4 + look_x
        rx = self.width * 3 // 4 + look_x

        if emotion == "NORMAL":
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 36, cy - 50, cx + 36, cy + 50], radius=22, fill=eye_color)
                draw.ellipse([cx - 20, cy - 36, cx - 8, cy - 24], fill=(255, 255, 255))
        elif emotion == "BLINK":
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 36, cy - 4, cx + 36, cy + 4], radius=3, fill=eye_color)
        elif emotion == "HAPPY":
            for cx in [lx, rx]:
                draw.arc([cx - 40, cy - 40, cx + 40, cy + 30], start=195, end=345, fill=eye_color, width=12)
        elif emotion == "CONFUSED":
            draw.rounded_rectangle([lx - 36, cy - 50, lx + 36, cy + 50], radius=22, fill=eye_color)
            draw.rounded_rectangle([rx - 25, cy - 25, rx + 25, cy + 25], radius=16, fill=eye_color)

        return img
```

### 4. 语音克隆与 TTS 合成适配器 (`tts_engine.py`)
```python
import os
import asyncio
import pygame
import edge_tts
import requests

class TTSEngine:
    def __init__(self, mode="edge", custom_voice_id="", api_key=""):
        self.mode = mode
        self.custom_voice_id = custom_voice_id
        self.api_key = api_key
        pygame.mixer.init()

    def speak(self, text: str):
        out_file = "/tmp/speech_out.mp3"
        # 分支 1: 调用云端克隆音色 API (如 Fish Audio)
        if self.mode == "custom" and self.api_key and self.custom_voice_id:
            try:
                url = "https://api.fish.audio/v1/tts"
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                data = {"text": text, "reference_id": self.custom_voice_id, "format": "mp3"}
                res = requests.post(url, json=data, headers=headers, timeout=12)
                if res.status_code == 200:
                    with open(out_file, "wb") as f:
                        f.write(res.content)
                else:
                    self._edge_fallback(text, out_file)
            except Exception:
                self._edge_fallback(text, out_file)
        else:
            # 分支 2: Edge-TTS 高拟真免鉴权引擎
            self._edge_fallback(text, out_file)

        pygame.mixer.music.load(out_file)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

    def _edge_fallback(self, text, path):
        async def _run():
            c = edge_tts.Communicate(text, "zh-CN-XiaoyiNeural")
            await c.save(path)
        asyncio.run(_run())
```

### 5. 具身交互主中枢 (`main_agent.py`)
```python
import os
import sys
import json
import time
import wave
import pyaudio
import numpy as np
from openai import OpenAI
from openwakeword.model import Model

from eye_renderer import EyeRenderer
from tts_engine import TTSEngine

# 自动判定运行环境：PC 载入仿真，树莓派载入物理驱动
if os.path.exists("/proc/device-tree/model"):
    from hal_physical import PhysicalRobotHAL as RobotHAL
else:
    from hal_simulation import SimulationRobotHAL as RobotHAL

with open("config.json", "r") as f:
    cfg = json.load(f)

hal = RobotHAL()
eyes = EyeRenderer(240, 320)
tts = TTSEngine(mode=cfg.get("tts_mode", "edge"),
                custom_voice_id=cfg.get("custom_voice_id", ""),
                api_key=cfg.get("custom_tts_api_key", ""))

client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1280

p = pyaudio.PyAudio()
stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
oww_model = Model(wakeword_models=[cfg["wakeword_model"]], inference_framework="onnx")

def record_audio_clip(seconds=4) -> str:
    frames = []
    for _ in range(0, int(RATE / CHUNK * seconds)):
        frames.append(stream.read(CHUNK, exception_on_overflow=False))
    wav_path = "/tmp/user_input.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
    return wav_path

def run_loop():
    hal.display_face(eyes.render_eyes("NORMAL"))
    print("🤖 具身交互引擎已激活，等待唤醒词唤醒...")

    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        audio_np = np.frombuffer(data, dtype=np.int16)

        preds = oww_model.predict(audio_np)
        if any(v > cfg["wakeword_threshold"] for v in preds.values()):
            print("\n🔔 [唤醒成功] 触发具身注意力机制")
            oww_model.reset()

            # 具身反馈 1: 抬头 10° 并眨眼展示笑意
            hal.set_angles(90, 80)
            hal.display_face(eyes.render_eyes("BLINK"))
            time.sleep(0.12)
            hal.display_face(eyes.render_eyes("HAPPY"))

            # 录制用户语音指令
            clip_path = record_audio_clip(seconds=4)
            hal.display_face(eyes.render_eyes("CONFUSED"))

            try:
                with open(clip_path, "rb") as af:
                    trans = client.audio.transcriptions.create(model="whisper-1", file=af)
                user_text = trans.text
                print(f"👤 用户表达: {user_text}")

                sys_prompt = """
                你是桌面的具身实体机器人搭子。根据主人的话，输出符合情境的肢体语言与回答。
                严格输出 JSON 结构：
                {
                  "reply": "你说话的内容 (30字以内)",
                  "action": "NOD | SHAKE | NONE",
                  "emotion": "NORMAL | HAPPY | CONFUSED",
                  "pan": 90,
                  "tilt": 90
                }
                """
                resp = client.chat.completions.create(
                    model=cfg["llm_model"],
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    response_format={"type": "json_object"}
                )
                res = json.loads(resp.choices[0].message.content)

                # 具身执行
                hal.set_angles(res.get("pan", 90), res.get("tilt", 90))
                hal.display_face(eyes.render_eyes(res.get("emotion", "NORMAL")))

                if res.get("action") == "NOD":
                    hal.set_angles(res.get("pan", 90), 75)
                    hal.set_angles(res.get("pan", 90), 95)
                    hal.set_angles(res.get("pan", 90), 90)

                # 语音输出
                tts.speak(res.get("reply", "我在听。"))

            except Exception as e:
                print(f"交互故障: {e}")
                tts.speak("信号受干扰，我开小差了。")

            # 归位待机
            hal.display_face(eyes.render_eyes("NORMAL"))
            hal.set_angles(90, 90)

if __name__ == "__main__":
    try:
        run_loop()
    finally:
        stream.close()
        p.terminate()
        hal.close()
```

### 6. 局域网远程代播服务 (`remote_server.py`)
```python
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

# 动态加载 HAL
import os
if os.path.exists("/proc/device-tree/model"):
    from hal_physical import PhysicalRobotHAL as RobotHAL
else:
    from hal_simulation import SimulationRobotHAL as RobotHAL

from eye_renderer import EyeRenderer
from tts_engine import TTSEngine

app = FastAPI(title="Robot Remote Broadcaster")
hal = RobotHAL()
eyes = EyeRenderer(240, 320)
tts = TTSEngine()

class BroadcastMessage(BaseModel):
    text: str
    emotion: Optional[str] = "HAPPY"
    pan: Optional[int] = 90
    tilt: Optional[int] = 85

def handle_broadcast(msg: BroadcastMessage):
    hal.set_angles(msg.pan, msg.tilt)
    hal.display_face(eyes.render_eyes(msg.emotion))
    tts.speak(msg.text)
    hal.display_face(eyes.render_eyes("NORMAL"))
    hal.set_angles(90, 90)

@app.post("/speak")
async def trigger_speak(msg: BroadcastMessage, bg: BackgroundTasks):
    bg.add_task(handle_broadcast, msg)
    return {"status": "ok", "msg": f"正在代播: {msg.text}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 7. OTA 自动化静默升级 (`ota_updater.py`)
```python
import os
import subprocess

def auto_upgrade(repo_dir="/home/pi/desktop_robot"):
    os.chdir(repo_dir)
    print("📡 检查远端代码仓库更新...")
    subprocess.run(["git", "fetch", "origin", "main"], check=True)

    loc = subprocess.check_output(["git", "rev-parse", "HEAD"]).strip()
    rem = subprocess.check_output(["git", "rev-parse", "origin/main"]).strip()

    if loc != rem:
        print("📦 发现新版本代码，执行拉取...")
        subprocess.run(["git", "pull", "origin", "main"], check=True)
        if os.path.exists("requirements.txt"):
            subprocess.run(["pip3", "install", "-r", "requirements.txt", "--quiet"])

        print("🔁 重启 systemd 主服务...")
        subprocess.run(["sudo", "systemctl", "restart", "robot.service"])
        return True
    return False

if __name__ == "__main__":
    auto_upgrade()
```

---

## 九、系统守护与保活部署 (systemd)

在树莓派中注册守护服务，实现通电自启与进程崩溃重启：

1. 编辑配置文件：
   ```bash
   sudo nano /etc/systemd/system/robot.service
   ```
2. 粘贴以下内容：
   ```ini
   [Unit]
   Description=Desktop Embodied AI Robot Agent
   After=network.target pigpiod.service
   Wants=pigpiod.service

   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/desktop_robot
   ExecStart=/usr/bin/python3 /home/pi/desktop_robot/main_agent.py
   Restart=always
   RestartSec=5
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```
3. 激活服务：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable robot.service
   sudo systemctl start robot.service
   ```