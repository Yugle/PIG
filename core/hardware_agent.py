# -*- coding: utf-8 -*-
"""
PIG 实体与数字孪生硬件智能体中枢 (hardware_agent.py)
核心职能：
1. 双模无缝支持：真机数字孪生模式 (Twin/Physical) vs 纯网页仿真模式 (Simulation)。
2. 物理麦克风与 openWakeWord 离线唤醒词后台常驻监听。
3. 动态能量 VAD 静音自适应录音与百度/Whisper ASR 语音识别。
4. 状态机与虚实孪生事件广播：
   待机 (IDLE) -> 唤醒 (WAKING) -> 倾听 (LISTENING) -> 思考 (THINKING) -> 播报 (SPEAKING) -> 余韵 (AFTERGLOW)
   实时同步给 Web 仿真控制台，让开发者即使无实体舵机也能在大屏上直观看到唤醒状态。
"""

import os
import sys
import time
import json
import wave
import math
import queue
import asyncio
import base64
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, Callable, List
from core.face_tracker import FaceTracker

logger = logging.getLogger("HardwareAgent")

# 树莓派硬件识别
IS_RASPBERRY_PI = False
if os.path.exists("/proc/device-tree/model"):
    try:
        with open("/proc/device-tree/model", "r") as f:
            if "Raspberry Pi" in f.read():
                IS_RASPBERRY_PI = True
    except Exception:
        pass


_global_hardware_agent = None

def get_hardware_agent() -> Optional["HardwareAgent"]:
    """获取全局活跃的 HardwareAgent 实例"""
    global _global_hardware_agent
    return _global_hardware_agent


class HardwareAgent:
    """
    真机硬件智能体与数字孪生中枢
    """

    STATE_IDLE = "IDLE"             # 待机监听唤醒词
    STATE_WAKING = "WAKING"         # 听到唤醒词，抬头微笑
    STATE_LISTENING = "LISTENING"   # 正在 VAD 动态录音中
    STATE_THINKING = "THINKING"     # ASR 转写或大模型思考中
    STATE_SPEAKING = "SPEAKING"     # 流式播报与肢体动作展开中
    STATE_AFTERGLOW = "AFTERGLOW"   # 话毕情感余韵与注视倾听中

    def __init__(
        self,
        config: Dict[str, Any],
        memory_manager,
        skill_manager,
        evolution_engine=None,
        proactive_care=None,
        hal=None,
        face_tracker=None,
    ):
        global _global_hardware_agent
        _global_hardware_agent = self

        self.cfg = config
        self.memory_manager = memory_manager
        self.skill_manager = skill_manager
        self.evolution_engine = evolution_engine
        self.proactive_care = proactive_care

        # 运行模式: 'twin' (真机孪生) 或 'simulation' (纯网页仿真)
        self.mode = self.cfg.get("run_mode", "twin")
        self.state = self.STATE_IDLE
        self.last_active_time = time.time()
        self.last_waking_time = 0.0

        # 事件订阅回调队列
        self.event_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._lock = threading.Lock()

        # 硬件 HAL 初始化
        self.hal = hal or self._init_hal()

        # 语音识别与合成引擎
        self.asr_engine = self._init_asr()
        self.tts_engine = self._init_tts()

        # 音频输入流与离线语音唤醒引擎 (sherpa-onnx 中文优先，兼容 openWakeWord)
        self.has_mic = False
        self.p_audio = None
        self.audio_stream = None
        self._audio_lock = threading.Lock()
        self._recording_active = False
        self._mic_rate = 16000
        self._mic_channels = 1
        self._mic_chunk = 1280
        self._mic_device_name = ""
        self._mic_read_errors = 0
        self.oww_model = None
        self.wakeword_engine = None

        self._running = False
        self._worker_thread = None
        self._vision_thread = None

        # 动作执行独占锁与时间戳 (动作优先级高于注视追踪)
        self._action_busy_until = 0.0

        # 全双工打断 (Barge-in) 请求标记
        self._interrupt_requested = False

        # 人脸视觉注视与两级头部追踪引擎（优先复用外部传入实例，保障全链路单实例状态一致）
        if face_tracker is not None:
            self.face_tracker = face_tracker
            self.face_tracker.set_enabled(self.cfg.get("face_tracking", False))
        else:
            self.face_tracker = FaceTracker(enabled=self.cfg.get("face_tracking", False))
        self.latest_camera_frame = None
        self.latest_camera_frame_ts = 0.0
        if self.skill_manager:
            self.skill_manager.register_face_callback = self.register_master_face

        # 尝试初始化麦克风
        self._init_microphone()

        # 注册 TTP223 摸头传感器与全双工打断回调
        if hasattr(self.hal, "add_touch_listener"):
            self.hal.add_touch_listener(self._on_touch_event)

        # 注册 HLK-LD2402 24GHz 毫米波雷达监听回调 (人来唤醒 / 人走自动休眠)
        self.radar_detected = True
        self.radar_listeners = []
        if hasattr(self.hal, "add_radar_listener"):
            self.hal.add_radar_listener(self._on_radar_event)


    def _init_hal(self):
        """初始化物理驱动或轻量级仿真驱动"""
        sim_forced = self.cfg.get("simulation", not IS_RASPBERRY_PI)
        if IS_RASPBERRY_PI and not sim_forced:
            try:
                from drivers.hal_physical import PhysicalRobotHAL
                hal = PhysicalRobotHAL(
                    pan_pin=self.cfg.get("pan_pin", 18),
                    tilt_pin=self.cfg.get("tilt_pin", 13),
                    bl_pin=self.cfg.get("bl_pin", 16),
                    screen_brightness=self.cfg.get("screen_brightness", 100),
                )
                logger.info("🚀 [HAL] 已加载树莓派物理真机驱动 (Physical HAL)")
                return hal
            except Exception as e:
                logger.warning(f"⚠️ [HAL] 物理驱动初始化失败，回退到仿真驱动: {e}")

        from drivers.hal_simulation import SimulationRobotHAL
        scene_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulator", "scene.xml")
        hal = SimulationRobotHAL(model_path=scene_file)
        logger.info("💻 [HAL] 已加载轻量级仿真驱动 (Simulation HAL)")
        return hal

    def _init_asr(self):
        """初始化 ASR 语音识别引擎 (优先小米 MiMo 原生 ASR，兼容百度)"""
        try:
            from drivers.asr_engine import get_asr_engine
            asr = get_asr_engine(self.cfg)
            if asr:
                logger.info(f"🎙️ [ASR] 语音识别引擎已就绪: {asr.__class__.__name__}")
            return asr
        except Exception as e:
            logger.warning(f"⚠️ [ASR] 语音识别引擎初始化异常: {e}")
            return None

    def _init_tts(self):
        """初始化 TTS 引擎"""
        try:
            from drivers.tts_engine import TTSEngine
            clone_audio = self.cfg.get("mimo_clone_audio", "") if self.cfg.get("mimo_clone_enabled", False) else ""
            return TTSEngine(
                mode=self.cfg.get("tts_mode", "edge"),
                mimo_api_key=self.cfg.get("mimo_api_key", ""),
                mimo_voice=self.cfg.get("mimo_voice", "冰糖"),
                mimo_clone_audio=clone_audio,
                mimo_design_prompt=self.cfg.get("mimo_design_prompt", "")
            )
        except Exception as e:
            logger.warning(f"⚠️ [TTS] TTSEngine 初始化异常: {e}")
            return None

    @staticmethod
    def _rank_input_devices(p_audio) -> List[tuple]:
        """扫描并按优先级排序 PyAudio 录音设备 (真实 USB 麦 > 系统默认总线 > 其他 > 摄像头内置麦)"""
        ranked = []
        try:
            count = p_audio.get_device_count()
        except Exception:
            return ranked

        for idx in range(count):
            try:
                info = p_audio.get_device_info_by_index(idx)
            except Exception:
                continue
            if int(info.get("maxInputChannels", 0) or 0) < 1:
                continue
            name = str(info.get("name") or f"device_{idx}")
            low = name.lower()
            is_cam = any(k in low for k in ("camera", "webcam", "uvc", "imx", "ov5647"))
            is_sys = low in ("default", "sysdefault", "pulse", "pipewire") or low.startswith(("sysdefault:", "pulse", "pipewire"))
            is_usb_mic = any(k in low for k in ("pnp", "mic", "microphone", "usb", "headset", "dongle", "fifine", "jabra"))
            is_virtual_unused = any(k in low for k in ("dmix", "spdif", "iec958", "hdmi", "vc4"))

            if is_usb_mic and not is_cam:
                tier = 0
            elif is_sys:
                tier = 1
            elif not is_cam and not is_virtual_unused:
                tier = 2
            elif is_cam:
                tier = 3
            else:
                tier = 4
            ranked.append((tier, idx, info))

        ranked.sort(key=lambda item: (item[0], item[1]))
        return ranked

    @staticmethod
    def _probe_and_open_input(p_audio, dev_idx: Optional[int], dev_info: Dict[str, Any]):
        """尝试打开指定录音设备并预读 1 帧验证可用性，自动兼容 16kHz 与原生 48k/44.1kHz"""
        import pyaudio

        max_ch = int(dev_info.get("maxInputChannels", 1) or 1)
        default_sr = int(round(float(dev_info.get("defaultSampleRate") or 48000)))
        rates = []
        for r in (16000, default_sr, 48000, 44100):
            if r > 0 and r not in rates:
                rates.append(r)
        channels_try = [1] + ([2] if max_ch >= 2 else [])

        for rate in rates:
            chunk = max(256, int(round(1280 * rate / 16000.0)))
            for ch in channels_try:
                stream = None
                try:
                    stream = p_audio.open(
                        format=pyaudio.paInt16,
                        channels=ch,
                        rate=rate,
                        input=True,
                        input_device_index=dev_idx,
                        frames_per_buffer=chunk,
                    )
                    probe = stream.read(chunk, exception_on_overflow=False)
                    if probe:
                        return stream, rate, ch, chunk
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    if stream is not None:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
        return None

    def _read_mic_16k_frame(self) -> bytes:
        """从当前麦克风读取一帧 80ms 音频，并统一转换为 16kHz 单声道 PCM16 bytes (1280 samples)"""
        if not self.audio_stream:
            return b""
        chunk = max(256, int(getattr(self, "_mic_chunk", 1280)))
        data = self.audio_stream.read(chunk, exception_on_overflow=False)
        if not data:
            return b""

        ch = int(getattr(self, "_mic_channels", 1))
        rate = int(getattr(self, "_mic_rate", 16000))
        if ch == 1 and rate == 16000:
            return data

        import numpy as np
        arr = np.frombuffer(data, dtype=np.int16)
        if ch > 1:
            n_frames = len(arr) // ch
            if n_frames <= 0:
                return b""
            arr = arr[:n_frames * ch].reshape(n_frames, ch).mean(axis=1)
        if rate != 16000 and len(arr) > 1:
            x_old = np.linspace(0.0, 1.0, num=len(arr), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=1280, endpoint=False)
            arr = np.interp(x_new, x_old, arr.astype(np.float32))
        return np.clip(arr, -32768, 32767).astype(np.int16).tobytes()

    def _close_microphone(self):
        with self._audio_lock:
            if self.audio_stream:
                try:
                    self.audio_stream.stop_stream()
                    self.audio_stream.close()
                except Exception:
                    pass
                self.audio_stream = None
            if self.p_audio:
                try:
                    self.p_audio.terminate()
                except Exception:
                    pass
                self.p_audio = None
            self.has_mic = False

    def _init_microphone(self):
        """初始化物理/蓝牙麦克风及离线唤醒引擎 (支持 ALSA 默认失败时自动遍历可用 USB 麦与重采样兜底)"""
        if self.mode == "simulation":
            logger.info("ℹ️ [音频] 当前处于纯网页仿真模式，跳过物理麦克风初始化")
            return

        try:
            import pyaudio
            from drivers.wakeword_engine import WakeWordEngine

            engine = self.wakeword_engine or WakeWordEngine(self.cfg)
            if not engine.is_ready:
                raise RuntimeError("未找到可用的离线唤醒引擎 (请检查 sherpa-onnx 中文模型或 openWakeWord)")

            self._close_microphone()
            p_audio = pyaudio.PyAudio()
            opened = None
            chosen_name = "default"

            # 1. 先尝试系统 ALSA/PulseAudio 默认输入设备
            try:
                def_info = p_audio.get_default_input_device_info()
                chosen_name = str(def_info.get("name") or "default")
                opened = self._probe_and_open_input(p_audio, None, def_info)
            except Exception as def_err:
                logger.info(f"ℹ️ [音频] 默认输入设备打开失败，启动全声卡扫描 ({def_err})")

            # 2. 默认设备失败（常见于重启后 USB card 序号或 Device/Device_1 漂移）：自动遍历可用物理 USB 麦
            if opened is None:
                for tier, dev_idx, dev_info in self._rank_input_devices(p_audio):
                    opened = self._probe_and_open_input(p_audio, dev_idx, dev_info)
                    if opened is not None:
                        chosen_name = f"[{dev_idx}] {dev_info.get('name', dev_idx)}"
                        break

            if opened is None:
                try:
                    p_audio.terminate()
                except Exception:
                    pass
                raise RuntimeError("未探测到可用的物理录音设备 (请检查 USB 麦克风插口及 arecord -l)")

            stream, rate, ch, chunk = opened
            self.p_audio = p_audio
            self.audio_stream = stream
            self._mic_rate = rate
            self._mic_channels = ch
            self._mic_chunk = chunk
            self._mic_device_name = chosen_name
            self._mic_read_errors = 0
            self.wakeword_engine = engine
            self.oww_model = engine.oww_model
            self.has_mic = True
            logger.info(
                f"🎙️ [音频模块] 麦克风与唤醒引擎就绪 ({engine.backend}): {engine.model_label} "
                f"| 设备={chosen_name} ({rate}Hz, ch={ch})"
            )

        except Exception as e:
            self.has_mic = False
            logger.info(f"ℹ️ [音频模块] 物理麦克风未就绪或未安装依赖 ({e})，将以 Web 虚实大屏模式运行")

    def reload_wakeword(self):
        """热重载离线唤醒词配置 (支持动态修改中文唤醒词后免重启生效)"""
        if self.wakeword_engine:
            try:
                self.wakeword_engine.reload(self.cfg)
                self.oww_model = self.wakeword_engine.oww_model
            except Exception as e:
                logger.warning(f"⚠️ [WakeWord] 热重载异常: {e}")

    def register_event_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """注册状态变迁与事件监听器 (供 Web SSE 广播)"""
        with self._lock:
            if callback not in self.event_listeners:
                self.event_listeners.append(callback)

    def unregister_event_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._lock:
            if callback in self.event_listeners:
                self.event_listeners.remove(callback)

    def emit_event(self, ev_type: str, data: Optional[Dict[str, Any]] = None):
        """广播状态给所有 Web 孪生大屏客户端"""
        payload = {"type": ev_type, "timestamp": time.time(), "state": self.state}
        if data:
            payload.update(data)

        with self._lock:
            listeners = list(self.event_listeners)

        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                pass

    def set_mode(self, new_mode: str) -> str:
        """切换运行模式: 'twin' (真机孪生) 或 'simulation' (纯仿真)"""
        if new_mode not in ["twin", "simulation"]:
            return f"无效模式: {new_mode}"

        old_mode = self.mode
        self.mode = new_mode
        self.cfg["run_mode"] = new_mode

        if new_mode == "twin" and not self.has_mic:
            self._init_microphone()

        self.emit_event("mode_changed", {
            "mode": self.mode,
            "has_mic": self.has_mic,
            "message": f"模式已切换为：{'🤖 真机数字孪生模式' if self.mode == 'twin' else '💻 纯网页仿真模式'}"
        })
        return f"运行模式已从【{old_mode}】切换至【{self.mode}】"

    def get_status(self) -> Dict[str, Any]:
        """获取当前硬件与智能体实时状态"""
        skill_payloads: Dict[str, Any] = {}
        try:
            from core.skill_manager import get_skill_manager
            sm = get_skill_manager()
            if sm:
                skill_payloads = sm.collect_status_payload()
        except Exception:
            pass
        status = {
            "mode": self.mode,
            "state": self.state,
            "has_mic": self.has_mic,
            "is_raspberry_pi": IS_RASPBERRY_PI,
            "wakeword_engine": self.wakeword_engine.backend if self.wakeword_engine else self.cfg.get("wakeword_engine", "auto"),
            "wakeword_model": self.wakeword_engine.model_label if self.wakeword_engine else self.cfg.get("wakeword_model", "hey_jarvis"),
            "wakeword_keywords": self.cfg.get("wakeword_keywords", ["你好小猪", "小猪小猪"]),
            "wakeword_threshold": float(self.cfg.get("wakeword_threshold", 0.20)),
            "mic_gain": float(self.cfg.get("mic_gain", 2.5)),
            "mic_device": getattr(self, "_mic_device_name", ""),
            "cur_pan": getattr(self.hal, "cur_pan", 90.0),
            "cur_tilt": getattr(self.hal, "cur_tilt", 90.0),
            "radar_detected": getattr(self, "radar_detected", True),
            "last_waking_time": self.last_waking_time
        }
        status.update(skill_payloads)
        return status

    def _pause_music_for_voice(self):
        """语音唤醒/摸头/打断说话前广播中断钩子，由音乐等音频插件自行静默让路"""
        try:
            from core.skill_manager import get_skill_manager
            sm = get_skill_manager()
            if sm:
                sm.emit_hook("on_voice_interrupt")
        except Exception:
            pass

    def trigger_wake_manually(self, custom_intent: str = "") -> bool:
        """通过 Web 或 API 手动触发硬件唤醒，跳过唤醒词识别"""
        if self.state != self.STATE_IDLE:
            return False
        self._pause_music_for_voice()
        print("⚡ [手动唤醒] 用户通过 Web 控制台或 API 手动触发唤醒！", flush=True)
        self.last_waking_time = time.time()
        if self.wakeword_engine:
            self.wakeword_engine.reset()
        elif self.oww_model:
            self.oww_model.reset()

        self.state = self.STATE_WAKING
        self.emit_event("status_change", {
            "status": self.STATE_WAKING,
            "wake_word": "MANUAL",
            "message": "⚡ 手动触发唤醒就绪！"
        })
        self.emit_event("pose_sync", {"pan": 90, "tilt": 80, "emotion": "HAPPY"})
        if self.hal:
            self.hal.set_angles(90, 80)

        import core.sound_effects as sound_effects
        sound_effects.play_sound("wake_chime", async_play=True)

        self._start_listening_flow(custom_intent=custom_intent)
        return True

    def _start_listening_flow(self, custom_intent: str = ""):
        """执行录音 -> ASR -> 意图思考的标准倾听工作流"""
        def _worker():
            if custom_intent:
                self.process_voice_intent(custom_intent)
                return

            self.state = self.STATE_LISTENING
            self.emit_event("status_change", {
                "status": self.STATE_LISTENING,
                "message": "🎙️ 正在倾听主人说话..."
            })
            clip_path = self.record_audio_clip()
            user_text = ""
            if clip_path and os.path.exists(clip_path) and self.asr_engine:
                self.emit_event("status_change", {
                    "status": self.STATE_THINKING,
                    "message": "🔍 语音识别转写中..."
                })
                user_text = self.asr_engine.recognize(clip_path)

            if user_text:
                print(f"🎙️ [识别成功]: {user_text}", flush=True)
                threading.Thread(target=self.process_voice_intent, args=(user_text,), daemon=True).start()
            else:
                self.emit_event("status_change", {
                    "status": self.STATE_IDLE,
                    "message": "未能听清内容，已恢复待机监听"
                })
                self.emit_event("pose_sync", {"pan": 90, "tilt": 90, "emotion": "NORMAL"})
                self.state = self.STATE_IDLE
                if self.hal:
                    self.hal.set_angles(90, 90)

        threading.Thread(target=_worker, daemon=True).start()

    def stop_speaking(self):
        """立即掐断当前正在进行的语音播放 (Barge-in 打断)"""
        self._interrupt_requested = True
        self._pause_music_for_voice()
        if self.tts_engine:
            try:
                self.tts_engine.stop()
            except Exception:
                pass
        self.emit_event("barge_in", {"action": "interrupted", "timestamp": time.time()})

    def _play_touch_choreography(self, my_seq: int, hold_shy_sec: float = 1.4):
        """后台执行摸头连贯动作：低下 -> 左右摇头 -> 害羞保持 -> 平滑回正"""
        try:
            keyframes = [
                # (pan, tilt, emotion, min_step_sec) — tilt>90 为低头，pan 72<->108 为摇头
                (90, 108, "COZY", 0.32),
                (72, 108, "COZY", 0.28),
                (108, 108, "COZY", 0.28),
                (90, 106, "SHY", hold_shy_sec),
            ]
            for pan, tilt, emo, min_step_sec in keyframes:
                if getattr(self, "_touch_seq", 0) != my_seq:
                    return
                if self.state not in [self.STATE_IDLE, self.STATE_AFTERGLOW, "TOUCHED"]:
                    return
                t0 = time.monotonic()
                self.emit_event("pose_sync", {"pan": pan, "tilt": tilt, "emotion": emo})
                if self.hal:
                    self.hal.set_angles(pan, tilt, smooth=True)
                elapsed = time.monotonic() - t0
                remain = min_step_sec - elapsed
                if remain > 0:
                    time.sleep(remain)

            if getattr(self, "_touch_seq", 0) != my_seq:
                return
            if self.state in [self.STATE_IDLE, self.STATE_AFTERGLOW, "TOUCHED"]:
                self.state = self.STATE_IDLE
                self.emit_event("status_change", {
                    "status": self.STATE_IDLE,
                    "message": "就绪 (Online)"
                })
                self.emit_event("pose_sync", {"pan": 90, "tilt": 90, "emotion": "NORMAL"})
                if self.hal:
                    self.hal.set_angles(90, 90, smooth=True)
        except Exception as e:
            logger.warning(f"⚠️ [Touch] 摸头动作编排异常: {e}")

    def _on_touch_event(self):
        """处理摸头传感器 (TTP223) 触摸事件 (带 0.8s 防抖节流)"""
        now = time.time()
        if now - getattr(self, "_last_touch_ts", 0.0) < 0.8:
            return
        self._last_touch_ts = now
        self._touch_seq = getattr(self, "_touch_seq", 0) + 1
        my_seq = self._touch_seq

        print("👆 [Touch] 捕获到头顶摸头传感器 (TTP223) 触发！", flush=True)
        logger.info("👆 [Touch] 捕获到头顶摸头传感器 (TTP223) 信号")
        self._pause_music_for_voice()

        import core.sound_effects as sound_effects

        # 1. 若当前正在播报语音，摸头作为全双工硬件打断 (Barge-in)
        if self.state == self.STATE_SPEAKING:
            logger.info("🛑 [Barge-in] 播报中检测到摸头，立即打断语音播放！")
            self.stop_speaking()
            self.state = self.STATE_AFTERGLOW
            self.mark_action_playing(2.6)
            self.emit_event("status_change", {
                "status": self.STATE_AFTERGLOW,
                "message": "✋ 主人摸头打断，低头害羞中..."
            })
            sound_effects.play_sound("purr", async_play=True)
            threading.Thread(
                target=self._play_touch_choreography,
                args=(my_seq, 1.2),
                daemon=True,
                name="TouchChoreographyBarge"
            ).start()
            return

        # 2. 待机或余韵状态下，触发低下摇头后害羞的情感摸头杀
        self.mark_action_playing(2.8)
        self.emit_event("touch", {"action": "pet_head", "timestamp": time.time()})
        self.emit_event("status_change", {
            "status": "TOUCHED",
            "message": "😳 低下头摇了摇，害羞地享受摸头杀..."
        })
        sound_effects.play_sound("purr", async_play=True)

        if self.evolution_engine:
            try:
                self.evolution_engine.record_interaction()
            except Exception:
                pass

        threading.Thread(
            target=self._play_touch_choreography,
            args=(my_seq, 1.4),
            daemon=True,
            name="TouchChoreography"
        ).start()

    def add_radar_listener(self, callback):
        """注册外层毫米波雷达事件监听器"""
        if callback not in self.radar_listeners:
            self.radar_listeners.append(callback)

    def _on_radar_event(self, detected: bool):
        """处理 HLK-LD2402 毫米波人体雷达信号跳变 (True=有人, False=无人)"""
        was_detected = self.radar_detected
        self.radar_detected = bool(detected)
        logger.info(f"📡 [Radar] 毫米波雷达感应状态: {'有人靠近/在场 (HIGH)' if detected else '无人离席 (LOW)'}")

        # 广播给前端数字孪生大屏与事件订阅者
        self.emit_event("radar_presence", {
            "detected": self.radar_detected,
            "message": "📡 毫米波雷达：检测到人体在场" if detected else "📡 毫米波雷达：检测到人已离席"
        })

        if detected and not was_detected:
            # 🚀 人来 0.1s 极速硬件中断唤醒：
            # 若待机中且无独占动作，立即抬头睁眼待命
            if self.state in (self.STATE_IDLE, self.STATE_AFTERGLOW) and not self.is_action_playing():
                self.emit_event("pose_sync", {"pan": 90, "tilt": 85, "emotion": "HAPPY"})
                if self.hal:
                    self.hal.set_angles(90, 85)
                # 播放轻微清脆的欢迎音效
                import core.sound_effects as sound_effects
                sound_effects.play_sound("happy_chirp", async_play=True)

        # 广播给外层监听器 (例如 idle_fsm / menu_fsm)
        for cb in list(self.radar_listeners):
            try:
                cb(self.radar_detected)
            except Exception as e:
                logger.warning(f"雷达外层回调异常: {e}")


    def trigger_proactive_care_payload(self, payload: Dict[str, Any]):
        """执行主动关怀载荷 (广播事件、移动姿态、播放音效与异步语音)"""
        if not payload:
            return
        print(f"❤️ [主动关怀触发]: [{payload.get('title')}] {payload.get('text')}", flush=True)

        # 1. 广播主动关怀事件 (供 Web 孪生大屏展示专属卡片)
        self.emit_event("proactive_care", payload)

        # 2. 姿态与表情同步
        pan = payload.get("pan", 90)
        tilt = payload.get("tilt", 80)
        emotion = payload.get("emotion", "HAPPY")
        self.emit_event("pose_sync", {"pan": pan, "tilt": tilt, "emotion": emotion})
        if self.hal:
            self.hal.set_angles(pan, tilt)

        # 3. 播放清脆萌趣音效
        import core.sound_effects as sound_effects
        sound_effects.play_sound(payload.get("sound", "happy_chirp"), async_play=True)

        # 4. 异步语音播报 (绝不阻塞主控监听循环)
        text = payload.get("text", "")
        if text:
            def _proactive_speech():
                time.sleep(0.35)
                if self.tts_engine:
                    self.state = self.STATE_SPEAKING
                    self.emit_event("status_change", {
                        "status": self.STATE_SPEAKING,
                        "message": payload.get("title", "❤️ 主动关怀")
                    })
                    try:
                        audio_path, duration = self.tts_engine.synthesize(text)
                        audio_bytes = b""
                        if audio_path and os.path.exists(audio_path):
                            with open(audio_path, "rb") as f:
                                audio_bytes = f.read()
                        if audio_bytes:
                            self.emit_event("robot_chunk", {
                                "text": text,
                                "audio": base64.b64encode(audio_bytes).decode("utf-8"),
                                "seq": 0
                            })
                        # 物理硬件播放 (树莓派直连或声卡)
                        if audio_path and os.path.exists(audio_path):
                            dur = duration if duration > 0 else self.tts_engine.get_audio_duration(audio_path)
                            self.tts_engine.play_async(audio_path, dur)
                            play_start = time.time()
                            while self.tts_engine.is_playing() and (time.time() - play_start) < (dur + 0.5):
                                time.sleep(0.05)
                    except Exception as e:
                        logger.warning(f"主动关怀语音播报异常: {e}")

                    self.emit_event("done", {"reply": text})
                    self.state = self.STATE_IDLE
                    self.emit_event("status_change", {
                        "status": self.STATE_IDLE,
                        "message": "就绪 (Online)"
                    })
            threading.Thread(target=_proactive_speech, daemon=True).start()

    def start(self):
        """启动后台硬件智能体工作线程"""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="AudioOwwLoop")
        self._worker_thread.start()
        self._vision_thread = threading.Thread(target=self._vision_loop, daemon=True, name="VisionGazeLoop")
        self._vision_thread.start()
        logger.info("🚀 [HardwareAgent] 后台硬件监听与数字孪生同步引擎已启动")

    def mark_action_playing(self, duration_sec: float = 1.0):
        """标记当前正在执行动作/表演/播报，舵机由动作独占，视线追踪必须让位"""
        self._action_busy_until = max(getattr(self, "_action_busy_until", 0.0), time.time() + duration_sec)

    def is_action_playing(self) -> bool:
        """当前是否有显式动作或技能表演正在执行"""
        return time.time() < getattr(self, "_action_busy_until", 0.0)

    def set_face_tracking(self, enabled: bool):
        """动态启停人脸视觉注视追踪"""
        self.face_tracker.set_enabled(enabled)

    def register_master_face(self, frame=None) -> Dict[str, Any]:
        """真机与仿真统一的人脸认主录入 (SFace 128-d 特征)：优先实时抓拍并支持 3 帧重试"""
        import numpy as np
        try:
            import cv2
        except ImportError:
            cv2 = None

        if frame is not None:
            return self.face_tracker.register_master_face(frame)

        # 1. 真机 / HAL 优先实时抓拍（最多连拍 3 帧，规避刚启动自动曝光暗帧或眨眼）
        last_res: Optional[Dict[str, Any]] = None
        if self.hal and hasattr(self.hal, "capture_image") and cv2 is not None:
            for attempt in range(3):
                try:
                    img_bytes = self.hal.capture_image()
                    if img_bytes and len(img_bytes) > 100:
                        nparr = np.frombuffer(img_bytes, np.uint8)
                        cand = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if cand is not None:
                            self.latest_camera_frame = cand
                            self.latest_camera_frame_ts = time.time()
                            res = self.face_tracker.register_master_face(cand)
                            last_res = res
                            if res.get("status") == "ok" or res.get("success"):
                                return res
                except Exception as e:
                    logger.warning(f"⚠️ [FaceID] 从 HAL capture_image 异常: {e}")
                if attempt < 2:
                    time.sleep(0.15)

        # 2. 若 HAL 未捕获有效帧（如网页纯仿真模式使用浏览器摄像头），回退读取主模块最新推流帧
        main_mod = sys.modules.get("__main__")
        if main_mod and hasattr(main_mod, "get_latest_vision_frame") and cv2 is not None:
            try:
                web_bytes = main_mod.get_latest_vision_frame(max_age_sec=12.0)
                if web_bytes and len(web_bytes) > 100:
                    nparr = np.frombuffer(web_bytes, np.uint8)
                    web_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if web_frame is not None:
                        return self.face_tracker.register_master_face(web_frame)
            except Exception:
                pass

        # 3. 回退到近期缓存帧（12s TTL 内）
        now = time.time()
        last_cam_ts = getattr(self, "latest_camera_frame_ts", 0.0)
        if getattr(self, "latest_camera_frame", None) is not None and (now - last_cam_ts <= 12.0):
            return self.face_tracker.register_master_face(self.latest_camera_frame)

        if last_res is not None:
            return last_res
        err = "无法从摄像头捕获有效画面，请确认摄像头已连接或网页端已开启摄像头"
        return {"status": "error", "success": False, "msg": err, "message": err}

    def _vision_loop(self):
        """低功耗人脸视觉注视与两级头部追踪后台循环 (~10FPS) - 最低优先级生效"""
        import numpy as np
        try:
            import cv2
        except ImportError:
            cv2 = None

        while self._running:
            if not self.face_tracker.enabled or cv2 is None or not self.hal:
                time.sleep(0.5)
                continue

            # 仿真模式下由 Web 前端 /api/vision/face_detect 单独接管驱动，避免多线程重叠调用
            if not IS_RASPBERRY_PI:
                time.sleep(0.5)
                continue

            # 核心优先级管理：视线注视追踪为最低优先级
            # 若当前正在执行肢体动作、思考、语音播报，视线追踪绝对不抢占舵机
            is_busy = (self.state not in (self.STATE_IDLE, self.STATE_AFTERGLOW)) or self.is_action_playing()

            try:
                img_bytes = self.hal.capture_image()
                if img_bytes:
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    self.latest_camera_frame = frame
                    self.latest_camera_frame_ts = time.time()
                    has_face, ox, oy = self.face_tracker.detect_face(frame)
                    if has_face:
                        cur_p = getattr(self.hal, "cur_pan", 90.0)
                        cur_t = getattr(self.hal, "cur_tilt", 90.0)
                        lx, ly, tp, tt = self.face_tracker.compute_gaze_and_angles(ox, oy, cur_p, cur_t)
                        # 仅在非动作占用时，才允许驱动实体舵机 (动作绝对独占原则)
                        if not is_busy and (abs(ox) > 0.22 or abs(oy) > 0.22):
                            self.hal.set_angles(tp, tt)
                        # 广播视觉注视事件给数字孪生 / Web 前端 (用于前端更新眼球和 3D 头骨)
                        self.emit_event("gaze_track", {
                            "has_face": True,
                            "offset_x": round(ox, 2),
                            "offset_y": round(oy, 2),
                            "look_x": lx,
                            "look_y": ly,
                            "target_pan": tp,
                            "target_tilt": tt,
                            "suppressed": is_busy,
                            "reason": "action_or_speech_priority" if is_busy else "normal"
                        })
                time.sleep(0.08)  # 约 10~12 FPS，极低功耗占用
            except Exception:
                time.sleep(0.2)

    def stop(self):
        """停止工作线程并安全释放音频设备"""
        self._running = False
        self._close_microphone()

    def record_audio_clip(self, max_seconds=8, silence_timeout=None, min_seconds=0.45) -> str:
        """动态能量 VAD 静音自适应录音 (支持噪声基线自适应、唤醒尾音屏蔽与首字前滚保留)"""
        if not self.audio_stream or not self.p_audio:
            return ""

        from collections import deque
        import numpy as np
        import pyaudio

        RATE = 16000
        CHUNK = 1280
        FORMAT = pyaudio.paInt16
        CHANNELS = 1

        if silence_timeout is None:
            silence_timeout = float(self.cfg.get("vad_silence_timeout", 0.85))

        frames = []
        preroll = deque(maxlen=3)
        chunk_per_sec = max(1, int(RATE / CHUNK))
        max_chunks = int(chunk_per_sec * max_seconds)
        no_speech_limit = int(chunk_per_sec * min(4.0, max_seconds))
        min_chunks = int(chunk_per_sec * min_seconds)
        silence_limit = max(4, int(chunk_per_sec * silence_timeout))

        silence_counter = 0
        voiced_streak = 0
        has_spoken = False
        rec_meter_counter = 0
        mic_gain = float(self.cfg.get("mic_gain", 2.5))
        speech_thresh = float(self.cfg.get("speech_vad_threshold", 220))
        noise_samples = []

        # 录音开始时立即发送一次电平广播，让前端瞬间进入录音指示状态，杜绝任何停顿感
        self.emit_event("audio_meter", {
            "energy": 60.0,
            "score": 0.0,
            "model": self.cfg.get("wakeword_model", "hey_jarvis"),
            "threshold": float(self.cfg.get("wakeword_threshold", 0.20)),
            "is_listening": True
        })

        self._recording_active = True
        try:
            with self._audio_lock:
                # 丢弃唤醒词阶段残留的陈旧缓冲帧，避免“你好小猪”尾音或提示音误触发录音截止
                try:
                    if hasattr(self.audio_stream, "get_read_available"):
                        avail = int(self.audio_stream.get_read_available() or 0)
                        step_chunk = max(256, int(getattr(self, "_mic_chunk", CHUNK)))
                        drain_n = min(avail // step_chunk, 4)
                        for _ in range(drain_n):
                            self.audio_stream.read(step_chunk, exception_on_overflow=False)
                except Exception:
                    pass

                for idx in range(max_chunks):
                    if not self._running:
                        break
                    try:
                        data = self._read_mic_16k_frame()
                        if not data:
                            continue
                        audio_raw = np.frombuffer(data, dtype=np.int16)
                        if mic_gain != 1.0:
                            raw_peak = float(np.max(np.abs(audio_raw))) if len(audio_raw) else 0.0
                            eff_gain = min(mic_gain, 28000.0 / raw_peak) if raw_peak > 0 else mic_gain
                            audio_np = np.clip(audio_raw.astype(np.float32) * eff_gain, -32768, 32767).astype(np.int16)
                            chunk_bytes = audio_np.tobytes()
                        else:
                            audio_np = audio_raw
                            chunk_bytes = data

                        energy = float(np.abs(audio_np).mean())

                        rec_meter_counter += 1
                        if rec_meter_counter >= 2:
                            rec_meter_counter = 0
                            self.emit_event("audio_meter", {
                                "energy": round(energy, 1),
                                "score": 0.0,
                                "model": self.cfg.get("wakeword_model", "hey_jarvis"),
                                "threshold": float(self.cfg.get("wakeword_threshold", 0.20)),
                                "is_listening": True
                            })

                        # 前 2 帧 (~160ms) 用于屏蔽唤醒提示音“叮”并采样环境底噪基线
                        if idx < 2:
                            noise_samples.append(energy)
                            preroll.append(chunk_bytes)
                            continue

                        noise_floor = (sum(noise_samples) / len(noise_samples)) if noise_samples else 0.0
                        dyn_thresh = max(speech_thresh, noise_floor * 2.2)

                        if energy > dyn_thresh:
                            voiced_streak += 1
                            silence_counter = 0
                            if not has_spoken:
                                preroll.append(chunk_bytes)
                                # 连续 2 帧高于阈值才确认真人开口，防止单帧爆音/尾音误切段
                                if voiced_streak >= 2:
                                    has_spoken = True
                                    frames.extend(preroll)
                            else:
                                frames.append(chunk_bytes)
                        else:
                            voiced_streak = 0
                            if not has_spoken:
                                noise_samples.append(energy)
                                if len(noise_samples) > 8:
                                    noise_samples.pop(0)
                                preroll.append(chunk_bytes)
                                if idx >= no_speech_limit:
                                    break
                            else:
                                frames.append(chunk_bytes)
                                silence_counter += 1
                                if silence_counter >= silence_limit and len(frames) >= min_chunks:
                                    print(f"🛑 [VAD] 检测到说话停顿 (持续静音约 {silence_timeout}s)，自动截止录音并提交识别...", flush=True)
                                    break
                    except Exception:
                        break
        finally:
            self._recording_active = False

        if not has_spoken or len(frames) < 4:
            return ""

        wav_path = "/tmp/user_input.wav"
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.p_audio.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))
            return wav_path
        except Exception as e:
            logger.warning(f"保存录音文件失败: {e}")
            return ""

    def process_voice_intent(self, user_text: str):
        """处理通过麦克风唤醒后接收到的语音指令 (驱动大脑 ReAct 流式闭环)"""
        if not user_text.strip():
            return

        self._interrupt_requested = False
        self.state = self.STATE_THINKING
        self.emit_event("status_change", {
            "status": self.STATE_THINKING,
            "message": "🧠 大脑正在深度思考与规划动作..."
        })

        # 广播用户语音文本给 Web 视窗，模拟气泡出现
        self.emit_event("user_spoken", {"text": user_text})

        from core.prompt_builder import PromptBuilder
        from core.agent_reasoner import AgentReasoner
        from core.streaming_pipeline import get_tts_streamer, stream_sentence_to_queue

        max_len = self.cfg.get("max_reply_length", 200)
        sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=max_len, skill_manager=self.skill_manager)

        api_key = self.cfg.get("api_key", "")
        base_url = self.cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
        model = self.cfg.get("llm_model", "mimo-v2.6-flash")

        tts_streamer = get_tts_streamer(self.cfg)

        reasoner = AgentReasoner(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_steps=4,
            enable_thinking=self.cfg.get("llm_thinking", False)
        )

        stream_sentences = []
        final_behaviors = []
        final_reply = ""
        done_payload = None
        saw_text_delta = False

        # 真流式并发合成 + 常驻 PCM 管道直推：~370ms 拿到首块音频即刻写入树莓派喇叭并同步推 Web 大屏
        tts_executor = ThreadPoolExecutor(max_workers=2)
        play_queue: "queue.Queue[Optional[tuple]]" = queue.Queue()

        def _playback_worker():
            pcm_stream_open = False
            audio_seq = 0
            try:
                while True:
                    item = play_queue.get()
                    try:
                        if item is None or self._interrupt_requested:
                            break
                        s_txt, sub_q = item
                        sub_idx = 0
                        while True:
                            tag, pcm_b, wire_b = sub_q.get()
                            if tag == "done" or self._interrupt_requested:
                                break
                            if not wire_b:
                                continue

                            b64_audio = base64.b64encode(wire_b).decode("utf-8")

                            # 1. 优先立即触发真机物理喇叭出声
                            started_fallback_file = False
                            est_dur = max(3.0, len(s_txt) * 0.45 + 1.5)
                            if self.tts_engine:
                                try:
                                    if pcm_b:
                                        if not pcm_stream_open and hasattr(self.tts_engine, "start_pcm_stream"):
                                            pcm_stream_open = self.tts_engine.start_pcm_stream(24000)
                                            if self.hal:
                                                self.hal.set_angles(90, 85)
                                        if pcm_stream_open:
                                            self.tts_engine.write_pcm_chunk(pcm_b)
                                    else:
                                        # 非 PCM 回退（如 voicedesign/edge-tts MP3）
                                        if pcm_stream_open:
                                            self.tts_engine.finish_pcm_stream()
                                            t_flush = time.time()
                                            while self.tts_engine.is_playing() and (time.time() - t_flush) < 15.0:
                                                if self._interrupt_requested:
                                                    self.tts_engine.stop()
                                                    break
                                                time.sleep(0.03)
                                            pcm_stream_open = False
                                        tmp_chunk_path = f"/tmp/chunk_{audio_seq + 1}.mp3"
                                        with open(tmp_chunk_path, "wb") as f:
                                            f.write(wire_b)
                                        self.tts_engine.play_async(tmp_chunk_path, est_dur)
                                        started_fallback_file = True
                                        if self.hal:
                                            self.hal.set_angles(90, 85)
                                except Exception as ex:
                                    logger.warning(f"音频播放异常: {ex}")

                            # 2. 物理喇叭写入后向 Web 孪生大屏推流，按统一音频流序号递增
                            audio_seq += 1
                            self.emit_event("robot_audio", {
                                "text": s_txt if sub_idx == 0 else "",
                                "audio": b64_audio,
                                "seq": audio_seq
                            })
                            sub_idx += 1

                            if started_fallback_file and self.tts_engine:
                                play_start = time.time()
                                while self.tts_engine.is_playing() and (time.time() - play_start) < (est_dur + 1.0):
                                    if self._interrupt_requested:
                                        self.tts_engine.stop()
                                        break
                                    time.sleep(0.04)
                    finally:
                        play_queue.task_done()
            finally:
                if pcm_stream_open and self.tts_engine:
                    if self._interrupt_requested:
                        self.tts_engine.stop()
                    else:
                        self.tts_engine.finish_pcm_stream()
                        t_tail = time.time()
                        while self.tts_engine.is_playing() and (time.time() - t_tail) < 30.0:
                            if self._interrupt_requested:
                                self.tts_engine.stop()
                                break
                            time.sleep(0.04)

        play_thread = threading.Thread(target=_playback_worker, daemon=True, name="VoiceTTSPlayback")
        play_thread.start()

        try:
            for ev in reasoner.run_react_loop_stream(
                user_text=user_text,
                base_sys_prompt=sys_prompt,
                memory_manager=self.memory_manager,
                skill_manager=self.skill_manager,
                evolution_engine=self.evolution_engine
            ):
                if self._interrupt_requested:
                    logger.info("🛑 [Barge-in] 收到打断信号，停止后续句子生成。")
                    break

                ev_type = ev.get("type")
                if ev_type == "tool_start":
                    self.emit_event("tool_start", {"name": ev.get("name"), "args": ev.get("args")})
                elif ev_type == "observation":
                    self.emit_event("observation", {"name": ev.get("name"), "result": str(ev.get("result"))})
                elif ev_type == "behaviors":
                    bh = ev.get("behaviors")
                    if bh:
                        final_behaviors = bh
                        self.emit_event("behaviors", {"behaviors": bh})
                elif ev_type == "text_delta":
                    td = ev.get("text", "")
                    if td:
                        saw_text_delta = True
                        self.emit_event("robot_chunk", {
                            "text": td,
                            "audio": "",
                            "seq": 0
                        })
                elif ev_type == "sentence":
                    s_text = ev.get("text", "").strip()
                    if s_text:
                        stream_sentences.append(s_text)
                        self.state = self.STATE_SPEAKING
                        if not saw_text_delta:
                            self.emit_event("robot_chunk", {
                                "text": s_text,
                                "audio": "",
                                "seq": 0
                            })
                        sub_q = stream_sentence_to_queue(tts_executor, tts_streamer, s_text)
                        play_queue.put((s_text, sub_q))

                elif ev_type == "done":
                    final_reply = ev.get("reply") or "".join(stream_sentences) or "收到啦！"
                    final_behaviors = ev.get("behaviors", final_behaviors)
                    done_payload = {
                        "reply": final_reply,
                        "behaviors": final_behaviors,
                        "steps": ev.get("steps", [])
                    }
                    if ev.get("visual_snapshot"):
                        done_payload["visual_snapshot"] = ev.get("visual_snapshot")

            play_queue.put(None)
            play_thread.join(timeout=120.0)
            if done_payload and not self._interrupt_requested:
                self.emit_event("done", done_payload)

        except Exception as e:
            print(f"⚠️ 语音指令处理异常: {e}", flush=True)
            logger.error(f"语音指令处理异常: {e}")
            final_reply = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"
            play_queue.put(None)
            play_thread.join(timeout=5.0)
            self.emit_event("done", {"reply": final_reply, "behaviors": []})
        finally:
            tts_executor.shutdown(wait=False)

        # 若中途被摸头或语音打断，跳过正常余韵，由打断处理逻辑恢复待机
        if self._interrupt_requested:
            return

        # 话毕进入 1.8 秒情感余韵与注视倾听
        self.state = self.STATE_AFTERGLOW
        self.emit_event("status_change", {
            "status": self.STATE_AFTERGLOW,
            "message": "👂 情感余韵与专注倾听 (AFTERGLOW)..."
        })
        time.sleep(1.8)

        # 恢复待机监听
        self.state = self.STATE_IDLE
        self.emit_event("status_change", {
            "status": self.STATE_IDLE,
            "message": f"🎙️ 物理麦克风待机监听唤醒词中 [{self.cfg.get('wakeword_model', 'hey_jarvis')}]..."
        })
        self.emit_event("pose_sync", {"pan": 90, "tilt": 90, "emotion": "NORMAL"})
        if self.hal:
            self.hal.set_angles(90, 90)

    def _run_loop(self):
        """物理麦克风监听工作循环"""
        import numpy as np

        meter_counter = 0
        while self._running:
            # 若处于纯网页仿真模式或无麦克风，线程低频休眠保活；真机模式下每 10 秒自动重探 USB 麦支持热插拔自愈
            if self.mode != "twin" or not self.has_mic or not self.audio_stream:
                time.sleep(0.5)
                if self.mode == "twin" and (not self.has_mic or not self.audio_stream):
                    probe_counter = getattr(self, "_mic_retry_counter", 0) + 1
                    self._mic_retry_counter = probe_counter
                    if probe_counter >= 20:
                        self._mic_retry_counter = 0
                        self._init_microphone()
                        if self.has_mic:
                            self.emit_event("mode_changed", {
                                "mode": self.mode,
                                "has_mic": True,
                                "message": f"🎙️ 已热重连物理麦克风：{self._mic_device_name}",
                            })
                if self.proactive_care and self.state == self.STATE_IDLE:
                    sim_counter = getattr(self, "_sim_care_tick_counter", 0) + 1
                    self._sim_care_tick_counter = sim_counter
                    if sim_counter >= 4:  # 每 ~2.0 秒巡检一次
                        self._sim_care_tick_counter = 0
                        care_payload = self.proactive_care.tick(energy=0.0, is_talking=False)
                        if care_payload:
                            self.trigger_proactive_care_payload(care_payload)
                continue

            if self._recording_active or self.state in (self.STATE_WAKING, self.STATE_LISTENING):
                time.sleep(0.04)
                continue

            try:
                with self._audio_lock:
                    if self._recording_active or self.state in (self.STATE_WAKING, self.STATE_LISTENING):
                        data = b""
                    else:
                        data = self._read_mic_16k_frame()
                if not data:
                    time.sleep(0.01)
                    continue
                self._mic_read_errors = 0

                audio_raw = np.frombuffer(data, dtype=np.int16)
                mic_gain = float(self.cfg.get("mic_gain", 2.5))
                if mic_gain != 1.0:
                    # 自适应防削波增益：人声近讲时不产生方波削波失真，保障离线唤醒模型声学特征清晰
                    raw_peak = float(np.max(np.abs(audio_raw))) if len(audio_raw) else 0.0
                    eff_gain = min(mic_gain, 28000.0 / raw_peak) if raw_peak > 0 else mic_gain
                    audio_np = np.clip(audio_raw.astype(np.float32) * eff_gain, -32768, 32767).astype(np.int16)
                else:
                    audio_np = audio_raw

                energy = float(np.abs(audio_np).mean())
                w_name = self.wakeword_engine.model_label if self.wakeword_engine else self.cfg.get("wakeword_model", "hey_jarvis")
                threshold = float(self.cfg.get("wakeword_threshold", 0.20))

                # 若当前处于非空闲状态 (思考中/语音播报中/余韵中)，继续平滑读取排空声卡缓冲区，刷新实时音量条
                if self.state != self.STATE_IDLE:
                    # 全双工语音打断检测 (Voice Barge-in)
                    # 当机器人正在播报 (SPEAKING) 时，若主人大声喊出唤醒词打断
                    if self.state == self.STATE_SPEAKING and self.wakeword_engine:
                        max_score, hit_kw = self.wakeword_engine.predict(audio_np)
                        barge_thresh = 0.5 if self.wakeword_engine.backend == "sherpa_cn" else max(0.38, threshold * 1.5)
                        if max_score > barge_thresh:
                            hit_label = hit_kw or w_name
                            logger.info(f"🛑 [Voice Barge-in] 播报中监听到唤醒词打断 [{hit_label}] ({max_score:.2f})！掐断播报并进入倾听。")
                            self.stop_speaking()
                            self.last_waking_time = time.time()
                            self.wakeword_engine.reset()
                            self.state = self.STATE_WAKING
                            self.emit_event("status_change", {
                                "status": self.STATE_WAKING,
                                "wake_word": hit_label,
                                "message": f"✋ 主人语音打断：{hit_label}！"
                            })
                            self.emit_event("pose_sync", {"pan": 90, "tilt": 80, "emotion": "HAPPY"})
                            if self.hal:
                                self.hal.set_angles(90, 80)
                            import core.sound_effects as sound_effects
                            sound_effects.play_sound("wake_chime", async_play=True)
                            self._start_listening_flow()
                            continue

                    meter_counter += 1
                    if meter_counter >= 2:
                        meter_counter = 0
                        self.emit_event("audio_meter", {
                            "energy": round(energy, 1),
                            "score": 0.0,
                            "model": w_name,
                            "threshold": threshold,
                            "is_listening": (self.state == self.STATE_LISTENING)
                        })
                    continue

                max_score, hit_kw = self.wakeword_engine.predict(audio_np) if self.wakeword_engine else (0.0, "")

                # 每 ~160ms (2个 chunk) 广播一次实时音频电平与唤醒得分
                meter_counter += 1
                if meter_counter >= 2:
                    meter_counter = 0
                    self.emit_event("audio_meter", {
                        "energy": round(energy, 1),
                        "score": round(max_score, 3),
                        "model": w_name,
                        "threshold": threshold,
                        "is_listening": False
                    })

                # 定期巡检主动关怀触发机制 (每 ~1.6s 轻量判定一次)
                wake_hit = bool(self.wakeword_engine and ((self.wakeword_engine.backend == "sherpa_cn" and max_score >= 0.5) or (self.wakeword_engine.backend != "sherpa_cn" and max_score > threshold)))
                if self.proactive_care and self.state == self.STATE_IDLE and not wake_hit:
                    care_counter = getattr(self, "_care_tick_counter", 0) + 1
                    self._care_tick_counter = care_counter
                    if care_counter >= 20:
                        self._care_tick_counter = 0
                        care_payload = self.proactive_care.tick(energy=energy, is_talking=False)
                        if care_payload:
                            self.trigger_proactive_care_payload(care_payload)

                if wake_hit:
                    hit_label = hit_kw or w_name
                    self._pause_music_for_voice()
                    print(f"\n🔔 [唤醒成功] 物理麦克风监听到唤醒词: {hit_label} (置信度={max_score:.3f})！", flush=True)
                    self.last_waking_time = time.time()
                    if self.wakeword_engine:
                        self.wakeword_engine.reset()

                    # 0. 立即向前端广播唤醒成功电平事件，消除视觉卡顿
                    self.emit_event("audio_meter", {
                        "energy": round(energy, 1),
                        "score": round(max_score, 3),
                        "model": hit_label,
                        "threshold": threshold,
                        "is_listening": True,
                        "woke": True
                    })

                    # 1. 切换状态为 WAKING 并全网广播
                    self.state = self.STATE_WAKING
                    self.emit_event("status_change", {
                        "status": self.STATE_WAKING,
                        "wake_word": hit_label,
                        "message": f"🔔 听到唤醒词：{hit_label}！"
                    })
                    # 姿态同步：抬头 10 度笑眯眯
                    self.emit_event("pose_sync", {"pan": 90, "tilt": 80, "emotion": "HAPPY"})
                    if self.hal:
                        self.hal.set_angles(90, 80)

                    # 异步播放唤醒铃声 (超低延迟非阻塞直推蓝牙耳机)
                    import core.sound_effects as sound_effects
                    sound_effects.play_sound("wake_chime", async_play=True)

                    # 2. 启动标准倾听流 (录音 -> ASR -> ReAct 闭环)
                    self._start_listening_flow()

            except Exception as e:
                logger.warning(f"麦克风监听循环异常: {e}")
                self._mic_read_errors = getattr(self, "_mic_read_errors", 0) + 1
                if self._mic_read_errors >= 8:
                    logger.warning("🔄 [音频] 连续读麦异常，释放旧句柄并等待自动重连...")
                    self._close_microphone()
                    self.emit_event("mode_changed", {"mode": self.mode, "has_mic": False})
                time.sleep(0.3)
