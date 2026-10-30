"""
桌面具身 AI 伴侣机器人 - 非语言萌趣音效库 (Non-verbal Emotional Audio)
无需外部网络资源，基于纯数学物理波形程序化生成（Procedural Audio）。
包含：欢快啁啾、困惑咕噜、惊奇唧声、思考蜂鸣、清脆唤醒三和弦。
"""

import os
import math
import wave
import struct
import threading

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "sounds")

def ensure_sound_assets():
    """自动生成 5 款高品质 16kHz WAV 萌趣音频文件"""
    os.makedirs(SOUNDS_DIR, exist_ok=True)

    def _write_wav(filename, freq_func, duration=0.35, sample_rate=16000, max_vol=0.45):
        filepath = os.path.join(SOUNDS_DIR, filename)
        if os.path.exists(filepath):
            return
        n_samples = int(duration * sample_rate)
        with wave.open(filepath, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            phase = 0.0
            for i in range(n_samples):
                t = i / sample_rate
                freq = freq_func(t, duration)
                phase += 2 * math.pi * freq / sample_rate
                # ADSR 平滑包络，避免音频爆音 (Clicking)
                attack = min(1.0, i / (0.04 * sample_rate))
                decay = max(0.0, 1.0 - (i / n_samples))
                env = attack * decay
                sample = int(32767 * max_vol * env * math.sin(phase))
                wf.writeframes(struct.pack("<h", sample))

    # 1. 欢快双音啁啾 (Happy Chirp: 650Hz -> 1800Hz 优美爬升)
    _write_wav("happy_chirp.wav", lambda t, d: 650 + 1200 * (t / d)**1.5, duration=0.32)

    # 2. 疑惑变调咕噜 (Confused Boop: 550Hz ~ 320Hz 摆动反问)
    _write_wav("confused_boop.wav", lambda t, d: 480 - 180 * math.sin(t * 16), duration=0.28)

    # 3. 惊奇升调唧声 (Surprise Squeak: 800Hz -> 2400Hz 极速冲刺)
    _write_wav("surprise_squeak.wav", lambda t, d: 800 + 1600 * (t / d), duration=0.22)

    # 4. 思考轻柔蜂鸣 (Thinking Hum: 440Hz A4 温润正弦基波)
    _write_wav("thinking_hum.wav", lambda t, d: 430 + 30 * math.sin(t * 18), duration=0.40)

    # 5. 唤醒清脆三和弦 (Wake Chime: C5 -> E5 -> G5 琶音)
    def _chime_freq(t, d):
        if t < d * 0.33: return 523.25
        elif t < d * 0.66: return 659.25
        return 783.99
    _write_wav("wake_chime.wav", _chime_freq, duration=0.42)

# 初始化生成
ensure_sound_assets()

# 尝试初始化本地播放器 (树莓派或物理机本地运行)
_pygame_available = False
try:
    import pygame
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    _pygame_available = True
except Exception:
    pass

def play_sound(sound_name: str, async_play: bool = True):
    """
    播放指定名称的萌趣音效
    :param sound_name: 'happy_chirp' | 'confused_boop' | 'surprise_squeak' | 'thinking_hum' | 'wake_chime'
    :param async_play: 是否在独立线程中异步播放
    """
    if not sound_name.endswith(".wav"):
        sound_name += ".wav"
    filepath = os.path.join(SOUNDS_DIR, sound_name)
    if not os.path.exists(filepath):
        ensure_sound_assets()

    if not _pygame_available or not os.path.exists(filepath):
        return

    def _play():
        try:
            sound = pygame.mixer.Sound(filepath)
            sound.play()
        except Exception as e:
            pass

    if async_play:
        threading.Thread(target=_play, daemon=True).start()
    else:
        _play()

if __name__ == "__main__":
    print(f"🎵 萌趣音效库已就绪，存储目录: {SOUNDS_DIR}")
    for s in ["happy_chirp", "confused_boop", "surprise_squeak", "thinking_hum", "wake_chime"]:
        print(f"  - {s}.wav: {os.path.exists(os.path.join(SOUNDS_DIR, s + '.wav'))}")
