# -*- coding: utf-8 -*-
"""
具身实体与数字孪生硬件智能体中枢 (hardware_agent.py)
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
import base64
import logging
import threading
from typing import Dict, Any, Optional, Callable, List

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
        hal=None
    ):
        self.cfg = config
        self.memory_manager = memory_manager
        self.skill_manager = skill_manager
        self.evolution_engine = evolution_engine

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

        # 音频输入流与 openWakeWord 模型
        self.has_mic = False
        self.p_audio = None
        self.audio_stream = None
        self.oww_model = None

        self._running = False
        self._worker_thread = None

        # 尝试初始化麦克风
        self._init_microphone()

    def _init_hal(self):
        """初始化物理驱动或轻量级仿真驱动"""
        sim_forced = self.cfg.get("simulation", not IS_RASPBERRY_PI)
        if IS_RASPBERRY_PI and not sim_forced:
            try:
                from drivers.hal_physical import PhysicalRobotHAL
                hal = PhysicalRobotHAL(
                    pan_pin=self.cfg.get("pan_pin", 18),
                    tilt_pin=self.cfg.get("tilt_pin", 13)
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
        """初始化百度智能云 ASR"""
        b_key = self.cfg.get("baidu_api_key")
        b_sec = self.cfg.get("baidu_secret_key")
        if b_key and b_sec:
            try:
                from drivers.asr_engine import BaiduASR
                return BaiduASR(b_key, b_sec)
            except Exception as e:
                logger.warning(f"⚠️ [ASR] 百度语音识别加载失败: {e}")
        return None

    def _init_tts(self):
        """初始化 TTS 引擎"""
        try:
            from drivers.tts_engine import TTSEngine
            return TTSEngine(
                mode=self.cfg.get("tts_mode", "edge"),
                mimo_api_key=self.cfg.get("mimo_api_key", ""),
                mimo_voice=self.cfg.get("mimo_voice", "冰糖"),
                mimo_clone_audio=self.cfg.get("mimo_clone_audio", ""),
                mimo_design_prompt=self.cfg.get("mimo_design_prompt", "")
            )
        except Exception as e:
            logger.warning(f"⚠️ [TTS] TTSEngine 初始化异常: {e}")
            return None

    def _init_microphone(self):
        """初始化物理/蓝牙麦克风及 openWakeWord 监听"""
        if self.mode == "simulation":
            logger.info("ℹ️ [音频] 当前处于纯网页仿真模式，跳过物理麦克风初始化")
            return

        try:
            import pyaudio
            import numpy as np
            from openwakeword.model import Model

            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            RATE = 16000
            CHUNK = 1280

            self.p_audio = pyaudio.PyAudio()
            self.audio_stream = self.p_audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )

            model_name = self.cfg.get("wakeword_model", "hey_jarvis")
            self.oww_model = Model(wakeword_models=[model_name], inference_framework="onnx")
            self.has_mic = True
            logger.info(f"🎙️ [音频模块] 麦克风与 openWakeWord 引擎就绪，监听唤醒词: {model_name}")

        except Exception as e:
            self.has_mic = False
            logger.info(f"ℹ️ [音频模块] 物理麦克风未就绪或未安装依赖 ({e})，将以 Web 虚实大屏模式运行")

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
        return {
            "mode": self.mode,
            "state": self.state,
            "has_mic": self.has_mic,
            "is_raspberry_pi": IS_RASPBERRY_PI,
            "wakeword_model": self.cfg.get("wakeword_model", "hey_jarvis"),
            "wakeword_threshold": float(self.cfg.get("wakeword_threshold", 0.20)),
            "mic_gain": float(self.cfg.get("mic_gain", 2.5)),
            "cur_pan": getattr(self.hal, "cur_pan", 90.0),
            "cur_tilt": getattr(self.hal, "cur_tilt", 90.0),
            "last_waking_time": self.last_waking_time
        }

    def trigger_wake_manually(self, custom_intent: str = "") -> bool:
        """通过 Web 或 API 手动触发硬件唤醒，跳过唤醒词识别"""
        if self.state != self.STATE_IDLE:
            return False
        print("⚡ [手动唤醒] 用户通过 Web 控制台或 API 手动触发唤醒！", flush=True)
        self.last_waking_time = time.time()
        if self.oww_model:
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

        def _wake_worker():
            if custom_intent:
                self.process_voice_intent(custom_intent)
            else:
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
                        "message": "🔍 百度智能云语音识别转写中..."
                    })
                    user_text = self.asr_engine.recognize(clip_path)

                if user_text:
                    print(f"🎙️ [手动唤醒识别成功]: {user_text}", flush=True)
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

        threading.Thread(target=_wake_worker, daemon=True).start()
        return True

    def start(self):
        """启动后台硬件智能体工作线程"""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        logger.info("🚀 [HardwareAgent] 后台硬件监听与数字孪生同步引擎已启动")

    def stop(self):
        """停止工作线程并安全释放音频设备"""
        self._running = False
        if self.audio_stream:
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            except Exception:
                pass
        if self.p_audio:
            try:
                self.p_audio.terminate()
            except Exception:
                pass

    def record_audio_clip(self, max_seconds=6, silence_timeout=0.75, min_seconds=0.5) -> str:
        """动态能量 VAD 静音自适应录音"""
        if not self.audio_stream or not self.p_audio:
            return ""

        import numpy as np
        import pyaudio

        RATE = 16000
        CHUNK = 1280
        FORMAT = pyaudio.paInt16
        CHANNELS = 1

        frames = []
        chunk_per_sec = int(RATE / CHUNK)
        max_chunks = int(chunk_per_sec * max_seconds)
        min_chunks = int(chunk_per_sec * min_seconds)
        silence_limit = int(chunk_per_sec * silence_timeout)

        silence_counter = 0
        has_spoken = False
        rec_meter_counter = 0
        mic_gain = float(self.cfg.get("mic_gain", 2.5))
        speech_thresh = float(self.cfg.get("speech_vad_threshold", 220))

        # 录音开始时立即发送一次电平广播，让前端瞬间进入录音指示状态，杜绝任何停顿感
        self.emit_event("audio_meter", {
            "energy": 60.0,
            "score": 0.0,
            "model": self.cfg.get("wakeword_model", "hey_jarvis"),
            "threshold": float(self.cfg.get("wakeword_threshold", 0.20)),
            "is_listening": True
        })

        for _ in range(max_chunks):
            if not self._running:
                break
            try:
                data = self.audio_stream.read(CHUNK, exception_on_overflow=False)
                audio_raw = np.frombuffer(data, dtype=np.int16)
                if mic_gain != 1.0:
                    audio_np = np.clip(audio_raw.astype(np.float32) * mic_gain, -32768, 32767).astype(np.int16)
                    frames.append(audio_np.tobytes())
                else:
                    audio_np = audio_raw
                    frames.append(data)

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

                if energy > speech_thresh:
                    has_spoken = True
                    silence_counter = 0
                else:
                    if has_spoken:
                        silence_counter += 1
                        if silence_counter >= silence_limit and len(frames) >= min_chunks:
                            print(f"🛑 [VAD] 检测到说话停顿 (持续静音约 {silence_timeout}s)，自动截止录音并提交识别...", flush=True)
                            break
            except Exception:
                break

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

        self.state = self.STATE_THINKING
        self.emit_event("status_change", {
            "status": self.STATE_THINKING,
            "message": "🧠 大脑正在深度思考与规划动作..."
        })

        # 广播用户语音文本给 Web 视窗，模拟气泡出现
        self.emit_event("user_spoken", {"text": user_text})

        from core.prompt_builder import PromptBuilder
        from core.agent_reasoner import AgentReasoner
        from core.streaming_pipeline import get_tts_streamer

        max_len = self.cfg.get("max_reply_length", 150)
        sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=max_len, skill_manager=self.skill_manager)

        api_key = self.cfg.get("api_key", "")
        base_url = self.cfg.get("base_url", "https://token.sensenova.cn/v1").rstrip("/")
        model = self.cfg.get("llm_model", "sensenova-6.8-flash-lite")

        tts_streamer = get_tts_streamer(self.cfg)

        reasoner = AgentReasoner(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_steps=4
        )

        stream_sentences = []
        final_behaviors = []
        final_reply = ""

        try:
            for ev in reasoner.run_react_loop_stream(
                user_text=user_text,
                base_sys_prompt=sys_prompt,
                memory_manager=self.memory_manager,
                skill_manager=self.skill_manager,
                evolution_engine=self.evolution_engine
            ):
                ev_type = ev.get("type")
                if ev_type == "tool_start":
                    self.emit_event("tool_start", {"name": ev.get("name"), "args": ev.get("args")})
                elif ev_type == "observation":
                    self.emit_event("observation", {"name": ev.get("name"), "result": str(ev.get("result"))})
                elif ev_type == "sentence":
                    s_text = ev.get("text", "").strip()
                    if s_text:
                        stream_sentences.append(s_text)
                        self.state = self.STATE_SPEAKING

                        # 异步生成音频
                        import asyncio
                        audio_b = asyncio.run(tts_streamer.synthesize_chunk(s_text))
                        b64_audio = base64.b64encode(audio_b).decode("utf-8") if audio_b else ""

                        # 广播给 Web 孪生大屏
                        self.emit_event("robot_chunk", {
                            "text": s_text,
                            "audio": b64_audio,
                            "seq": len(stream_sentences)
                        })

                        # 如果有物理硬件/TTS引擎，驱动耳机/音响播放
                        if self.tts_engine and audio_b:
                            try:
                                tmp_chunk_path = f"/tmp/chunk_{len(stream_sentences)}.mp3"
                                with open(tmp_chunk_path, "wb") as f:
                                    f.write(audio_b)
                                dur = self.tts_engine.get_audio_duration(tmp_chunk_path)
                                self.tts_engine.play_async(tmp_chunk_path, dur)
                                if self.hal:
                                    self.hal.set_angles(90, 85)
                                # 等待本句音频完整播报完毕，避免流式断句重叠
                                play_start = time.time()
                                while self.tts_engine.is_playing() and (time.time() - play_start) < (dur + 0.5):
                                    time.sleep(0.05)
                            except Exception as e:
                                logger.warning(f"音频播放异常: {e}")

                elif ev_type == "done":
                    final_reply = ev.get("reply") or "".join(stream_sentences) or "收到啦！"
                    final_behaviors = ev.get("behaviors", [])
                    self.emit_event("done", {
                        "reply": final_reply,
                        "behaviors": final_behaviors,
                        "steps": ev.get("steps", [])
                    })

        except Exception as e:
            print(f"⚠️ 语音指令处理异常: {e}", flush=True)
            logger.error(f"语音指令处理异常: {e}")
            final_reply = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"
            self.emit_event("done", {"reply": final_reply, "behaviors": []})

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
            # 若处于纯网页仿真模式或无麦克风，线程低频休眠保活
            if self.mode != "twin" or not self.has_mic or not self.audio_stream:
                time.sleep(0.5)
                continue

            try:
                CHUNK = 1280
                data = self.audio_stream.read(CHUNK, exception_on_overflow=False)
                if not data:
                    time.sleep(0.01)
                    continue

                audio_raw = np.frombuffer(data, dtype=np.int16)
                mic_gain = float(self.cfg.get("mic_gain", 2.5))
                if mic_gain != 1.0:
                    audio_np = np.clip(audio_raw.astype(np.float32) * mic_gain, -32768, 32767).astype(np.int16)
                else:
                    audio_np = audio_raw

                energy = float(np.abs(audio_np).mean())
                w_name = self.cfg.get("wakeword_model", "hey_jarvis")
                threshold = float(self.cfg.get("wakeword_threshold", 0.20))

                # 若当前处于非空闲状态 (思考中/语音播报中/余韵中)，继续平滑读取排空声卡缓冲区，刷新实时音量条，但不重复触发唤醒词
                if self.state != self.STATE_IDLE:
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

                preds = self.oww_model.predict(audio_np)
                max_score = float(max(preds.values())) if preds else 0.0

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

                if max_score > threshold:
                    print(f"\n🔔 [唤醒成功] 物理麦克风监听到唤醒词: {w_name} (置信度={max_score:.3f} > {threshold})！", flush=True)
                    self.last_waking_time = time.time()
                    self.oww_model.reset()

                    # 0. 立即向前端广播唤醒成功电平事件，消除视觉卡顿
                    self.emit_event("audio_meter", {
                        "energy": round(energy, 1),
                        "score": round(max_score, 3),
                        "model": w_name,
                        "threshold": threshold,
                        "is_listening": True,
                        "woke": True
                    })

                    # 1. 切换状态为 WAKING 并全网广播
                    self.state = self.STATE_WAKING
                    self.emit_event("status_change", {
                        "status": self.STATE_WAKING,
                        "wake_word": w_name,
                        "message": f"🔔 听到唤醒词：{w_name}！"
                    })
                    # 姿态同步：抬头 10 度笑眯眯
                    self.emit_event("pose_sync", {"pan": 90, "tilt": 80, "emotion": "HAPPY"})
                    if self.hal:
                        self.hal.set_angles(90, 80)

                    # 异步播放唤醒铃声 (超低延迟非阻塞直推蓝牙耳机)
                    import core.sound_effects as sound_effects
                    sound_effects.play_sound("wake_chime", async_play=True)

                    # 2. 切换为 LISTENING，开始动态录音
                    self.state = self.STATE_LISTENING
                    self.emit_event("status_change", {
                        "status": self.STATE_LISTENING,
                        "message": "🎙️ 正在倾听主人说话..."
                    })

                    clip_path = self.record_audio_clip()

                    # 3. 提交 ASR 转写
                    user_text = ""
                    if clip_path and os.path.exists(clip_path):
                        if self.asr_engine:
                            self.emit_event("status_change", {
                                "status": self.STATE_THINKING,
                                "message": "🔍 百度智能云语音识别转写中..."
                            })
                            user_text = self.asr_engine.recognize(clip_path)

                    if user_text:
                        print(f"🎙️ [识别成功]: {user_text}", flush=True)
                        threading.Thread(target=self.process_voice_intent, args=(user_text,), daemon=True).start()
                    else:
                        print("未检测到有效语音内容，恢复待机", flush=True)
                        self.emit_event("status_change", {
                            "status": self.STATE_IDLE,
                            "message": "未能听清内容，已恢复待机监听"
                        })
                        self.emit_event("pose_sync", {"pan": 90, "tilt": 90, "emotion": "NORMAL"})
                        self.state = self.STATE_IDLE
                        if self.hal:
                            self.hal.set_angles(90, 90)

            except Exception as e:
                logger.warning(f"麦克风监听循环异常: {e}")
                time.sleep(0.3)
