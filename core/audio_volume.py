# -*- coding: utf-8 -*-
"""
core/audio_volume.py - 树莓派多层次鲁棒音量控制与声音反馈系统

提供现代 Linux + 树莓派全套音频输出架构的自适应音量调节：
1. Linux 声音服务器层 (PipeWire `wpctl` / PulseAudio `pactl`)
2. 物理硬件 ALSA 混音器层 (动态遍历 /proc/asound/cards，覆盖 Master, Headphone, PCM, Speaker, Playback, Digital 等控件及非 0 号 USB 声卡)
3. 软件应用层协同与持久化 (写入 config.json，联动 TTSEngine mpg123 -f 缩放因子 / pygame.mixer，即时播放试听提示音)
"""

import os
import re
import sys
import json
import shutil
import threading
import subprocess
from typing import Optional, List, Dict, Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

COMMON_ALSA_CONTROLS = [
    "Master",
    "Speaker",
    "Headphone",
    "Headphones",
    "PCM",
    "Playback",
    "Digital",
    "DAC",
    "Line Out"
]

def _read_config_volume() -> int:
    """读取 config.json 中的 volume 配置 (默认 80)"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return max(0, min(100, int(data.get("volume", 80))))
        except Exception:
            pass
    return 80

def _save_config_volume(vol: int) -> bool:
    """保存 volume 到 config.json"""
    vol = max(0, min(100, int(vol)))
    try:
        data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["volume"] = vol
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ [AudioVolume] 保存 volume 至 config.json 失败: {e}")
        return False

def get_system_volume() -> int:
    """获取当前系统设定的音量百分比 (0~100)"""
    return _read_config_volume()

def detect_sound_cards() -> List[int]:
    """探测系统中所有可用的 ALSA 物理声卡卡号"""
    cards: List[int] = []
    asound_cards_file = "/proc/asound/cards"
    if os.path.exists(asound_cards_file):
        try:
            with open(asound_cards_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # 匹配形如: " 0 [Headphones     ]: ..." 或 " 1 [Device         ]: ..."
            matches = re.findall(r"^\s*(\d+)\s*\[", content, re.MULTILINE)
            for m in matches:
                try:
                    cards.append(int(m))
                except ValueError:
                    pass
        except Exception:
            pass
    if not cards:
        cards = [0, 1]  # 默认保底尝试 0 和 1 (USB 通常在 card 1)
    return cards

def detect_card_controls(card: Optional[int] = None) -> List[str]:
    """探测指定声卡或默认声卡支持的所有 ALSA 简单混音器控件名称"""
    if not shutil.which("amixer"):
        return []
    cmd = ["amixer"]
    if card is not None:
        cmd.extend(["-c", str(card)])
    cmd.append("scontrols")
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
        if res.returncode == 0:
            ctrls = re.findall(r"Simple mixer control '([^']+)'", res.stdout)
            if ctrls:
                return ctrls
    except Exception:
        pass
    return []

def _apply_system_volume_async(vol: int):
    """
    在独立后台线程中执行系统音量命令，避免阻塞主循环或 UI 渲染。
    """
    vol = max(0, min(100, int(vol)))
    is_mute = (vol == 0)

    # 1. 尝试 PulseAudio (`pactl`)
    if shutil.which("pactl"):
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
            )
            subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if is_mute else "0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
            )
        except Exception:
            pass

    # 2. 尝试 PipeWire / WirePlumber (`wpctl`)
    if shutil.which("wpctl"):
        try:
            vol_float = f"{vol / 100.0:.2f}"
            subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", vol_float],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
            )
            subprocess.run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if is_mute else "0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
            )
        except Exception:
            pass

    # 3. 遍历所有 ALSA 声卡及混音器控件 (`amixer`)
    if shutil.which("amixer"):
        card_list = detect_sound_cards()
        # 包括默认 (card=None) 以及所有真实检测到的卡号
        targets: List[Optional[int]] = [None] + [c for c in card_list if c is not None]

        for card in targets:
            detected_ctrls = detect_card_controls(card)
            ctrls_to_set = detected_ctrls if detected_ctrls else COMMON_ALSA_CONTROLS

            for ctrl in ctrls_to_set:
                cmd = ["amixer", "-q"]
                if card is not None:
                    cmd.extend(["-c", str(card)])
                action = "mute" if is_mute else "unmute"
                cmd.extend(["sset", ctrl, f"{vol}%", action])
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.5)
                except Exception:
                    pass

def play_feedback_tone(vol: int):
    """播放音量调节反馈提示音 (轻快短啾或短嘟)"""
    if vol <= 0:
        return
    try:
        from core.sound_effects import play_sound
        play_sound("confused_boop", async_play=True)
    except Exception:
        pass

def set_system_volume(vol: int, play_feedback: bool = False) -> int:
    """
    设置系统音量 (0~100%)
    :param vol: 音量百分比
    :param play_feedback: 是否播放调节成功提示音
    :return: 修正后的音量值 (0~100)
    """
    vol = max(0, min(100, int(vol)))
    _save_config_volume(vol)

    # 异步下发底层音频系统命令
    threading.Thread(target=_apply_system_volume_async, args=(vol,), daemon=True).start()

    # 同步更新 pygame mixer (若已载入)
    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.music.set_volume(vol / 100.0)
    except Exception:
        pass

    if play_feedback:
        play_feedback_tone(vol)

    print(f"🔊 [AudioVolume] 系统音量已调整为: {vol}%")
    return vol

def adjust_volume(delta: int, play_feedback: bool = True) -> int:
    """
    按相对步长增减音量 (+10 或 -10 等)
    :param delta: 步长 (可正可负)
    :param play_feedback: 是否播放提示音
    :return: 调整后的新音量
    """
    cur_vol = get_system_volume()
    new_vol = max(0, min(100, cur_vol + delta))
    return set_system_volume(new_vol, play_feedback=play_feedback)

def get_audio_diagnostics() -> Dict[str, Any]:
    """获取完整的音频硬件与混音器诊断信息"""
    cards = detect_sound_cards()
    card_info = []
    for c in cards:
        ctrls = detect_card_controls(c)
        card_info.append({"card_id": c, "controls": ctrls})

    default_ctrls = detect_card_controls(None)
    has_amixer = bool(shutil.which("amixer"))
    has_pactl = bool(shutil.which("pactl"))
    has_wpctl = bool(shutil.which("wpctl"))

    return {
        "current_volume": get_system_volume(),
        "has_amixer": has_amixer,
        "has_pactl": has_pactl,
        "has_wpctl": has_wpctl,
        "cards": card_info,
        "default_controls": default_ctrls
    }
