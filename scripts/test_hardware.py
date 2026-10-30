#!/usr/bin/env python3
"""
桌面具身 AI 伴侣机器人 - 硬件上电体检与外设接线自测工具 (test_hardware.py)
用于在树莓派真实机上电后，一键诊断：
1. pigpio 守护进程通信状态
2. SG90 双自由度云台舵机旋转测试 (Pin 18 / 13)
3. 2.4 寸 ST7789 SPI 液晶屏幕点亮与表情渲染测试
4. 声卡音频输出播放测试 (happy_chirp.wav)
5. USB 麦克风 16kHz 录音与人声能量检测测试
"""

import os
import sys
import time
import math
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

def print_header(title):
    print(f"\n{CYAN}{'='*60}{RESET}")
    print(f"{CYAN}🔍 {title}{RESET}")
    print(f"{CYAN}{'='*60}{RESET}")

def test_pigpio_and_servos(pan_pin=18, tilt_pin=13):
    print_header("1. 测试 pigpio DMA 守护进程与双轴舵机 (Pan:18, Tilt:13)")
    try:
        import pigpio
    except ImportError:
        print(f"{RED}[FAIL] 未安装 python3-pigpio 库！请运行: sudo apt install python3-pigpio{RESET}")
        return False

    pi = pigpio.pi()
    if not pi.connected:
        print(f"{RED}[FAIL] 无法连接到 pigpiod 守护服务！{RESET}")
        print(f"👉 修复方法: 请在终端运行: {YELLOW}sudo systemctl start pigpiod{RESET}")
        return False

    print(f"{GREEN}[PASS] pigpiod 守护进程通信正常 (本地端口 8888 就绪){RESET}")

    def angle_to_pulse(ang):
        return int(500 + (max(10, min(170, ang)) / 180.0) * 2000)

    print("🦾 正在测试舵机微幅转动 (90° -> 75° -> 105° -> 90°)...")
    try:
        # 平滑摆动 Pan
        for ang in [90, 75, 105, 90]:
            pi.set_servo_pulsewidth(pan_pin, angle_to_pulse(ang))
            time.sleep(0.35)
        # 平滑摆动 Tilt
        for ang in [90, 80, 100, 90]:
            pi.set_servo_pulsewidth(tilt_pin, angle_to_pulse(ang))
            time.sleep(0.35)
        print(f"{GREEN}[PASS] 舵机引脚控制测试成功！舵机已平滑回正 90°{RESET}")
        return True
    except Exception as e:
        print(f"{RED}[FAIL] 舵机控制异常: {e}{RESET}")
        return False
    finally:
        pi.set_servo_pulsewidth(pan_pin, 0)
        pi.set_servo_pulsewidth(tilt_pin, 0)
        pi.stop()

def test_st7789_screen():
    print_header("2. 测试 2.4 寸 ST7789 SPI 液晶屏显示 (DC:25, RST:22)")
    from drivers.eye_renderer import EyeRenderer
    eyes = EyeRenderer(240, 320)
    img_happy = eyes.render_eyes("HAPPY")
    img_love = eyes.render_eyes("LOVE")

    try:
        from luma.core.interface.serial import spi
        from luma.lcd.device import st7789
        serial_spi = spi(port=0, device=0, gpio_DC=25, gpio_RST=22)
        device = st7789(serial_spi, width=240, height=320, rotate=0)
        print("📺 正在向 SPI 屏幕推送开心与爱心表情 (维持 2 秒)...")
        device.display(img_happy)
        time.sleep(1.2)
        device.display(img_love)
        time.sleep(1.2)
        print(f"{GREEN}[PASS] ST7789 屏幕成功点亮并渲染眼部表情！{RESET}")
        return True
    except Exception as e:
        print(f"{YELLOW}[NOTICE] 硬件 SPI 屏幕未连通或未在树莓派真机上运行: {e}{RESET}")
        print("💡 PIL 矢量表情生成引擎自检通过 (生成 240x320 图像成功)")
        return False

def test_audio_output():
    print_header("3. 测试声卡与音频输出播放")
    wav_path = os.path.join(PROJECT_ROOT, "assets", "sounds", "happy_chirp.wav")
    if not os.path.exists(wav_path):
        import core.sound_effects as se
        se.ensure_sound_assets()

    print(f"🔊 正在通过声卡播放非语言萌趣音效: {os.path.basename(wav_path)}...")
    try:
        res = subprocess.run(["aplay", "-q", wav_path], timeout=4)
        if res.returncode == 0:
            print(f"{GREEN}[PASS] aplay 声卡播放测试成功，能清晰听到轻快双音啾声！{RESET}")
            return True
        else:
            print(f"{YELLOW}⚠️ aplay 退出码 {res.returncode}，尝试使用 pygame 回放...{RESET}")
    except Exception:
        pass

    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(wav_path)
        pygame.mixer.music.play()
        time.sleep(0.8)
        print(f"{GREEN}[PASS] pygame 音频输出成功！{RESET}")
        return True
    except Exception as e:
        print(f"{RED}[FAIL] 音频播放失败: {e}{RESET}")
        print(f"👉 修复方法: 运行 {YELLOW}alsamixer{RESET} 检查音量是否为 0 或静音 (MM)")
        return False

def test_microphone_input():
    print_header("4. 测试 USB 麦克风 16kHz 录音与声音能量采集")
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        RATE = 16000
        CHUNK = 1024
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True, frames_per_buffer=CHUNK)
        print("🎙️ 正在进行 2 秒环境音拾取，请试着对麦克风说话或吹气...")

        energy_list = []
        for _ in range(int(RATE / CHUNK * 2.0)):
            data = stream.read(CHUNK, exception_on_overflow=False)
            import struct
            shorts = struct.unpack(f"{CHUNK}h", data)
            rms = math.sqrt(sum(s*s for s in shorts) / CHUNK)
            energy_list.append(rms)

        stream.stop_stream()
        stream.close()
        p.terminate()

        max_energy = max(energy_list) if energy_list else 0
        avg_energy = sum(energy_list) / len(energy_list) if energy_list else 0
        print(f"📊 麦克风能量检测完成: 平均能量={avg_energy:.1f}, 峰值能量={max_energy:.1f}")

        if max_energy > 80:
            print(f"{GREEN}[PASS] USB 麦克风拾音正常，音量峰值响应清晰！{RESET}")
            return True
        else:
            print(f"{YELLOW}⚠️ 拾取到的声音能量偏低 (峰值仅 {max_energy:.1f})，请在 alsamixer 中调大麦克风增益 (Mic Capture){RESET}")
            return True
    except Exception as e:
        print(f"{RED}[FAIL] 麦克风初始化异常: {e}{RESET}")
        print(f"👉 修复方法: 检查 USB 麦克风是否插紧，运行 {YELLOW}arecord -l{RESET} 查看是否列出麦克风硬件")
        return False

def main():
    print(f"\n{CYAN}============================================================{RESET}")
    print(f"{GREEN}🤖 桌面具身 AI 伴侣 - 树莓派 3 硬件体检自测程序{RESET}")
    print(f"{CYAN}============================================================{RESET}")

    results = {}
    results["舵机与 pigpio 驱动"] = test_pigpio_and_servos()
    results["ST7789 2.4寸屏幕"] = test_st7789_screen()
    results["声卡扬声器输出"] = test_audio_output()
    results["USB 麦克风录音"] = test_microphone_input()

    print_header("体检自测诊断汇总报告")
    for item, status in results.items():
        st_text = f"{GREEN}通过 [PASS]{RESET}" if status else f"{YELLOW}需留意 [WARN/FAIL]{RESET}"
        print(f"• {item:<24} : {st_text}")
    print(f"\n{CYAN}============================================================{RESET}\n")

if __name__ == "__main__":
    main()
