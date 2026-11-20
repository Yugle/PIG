import os
import sys
import json
import time
import wave
import signal
import math

from drivers.eye_renderer import EyeRenderer
from drivers.tts_engine import TTSEngine
from drivers.asr_engine import BaiduASR
from core.memory_manager import MemoryManager
import core.sound_effects as sound_effects
from core.face_tracker import FaceTracker
from core.idle_fsm import IdleFSM
from core.skill_manager import SkillManager
from core.evolution_engine import EvolutionEngine
from core.agent_reasoner import AgentReasoner
from core.prompt_builder import PromptBuilder

# 自动判定运行环境：根据 /proc/device-tree/model 识别树莓派真机或 PC 仿真
is_raspberry_pi = False
if os.path.exists("/proc/device-tree/model"):
    try:
        with open("/proc/device-tree/model", "r") as f:
            model_info = f.read()
            if "Raspberry Pi" in model_info:
                is_raspberry_pi = True
    except Exception:
        pass

# 载入配置
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
if not os.path.exists(CONFIG_FILE):
    print("⚠️ 未找到 config.json，将使用默认配置模板")
    cfg = {
        "api_key": "YOUR_LLM_API_KEY",
        "base_url": "https://api.deepseek.com",
        "llm_model": "deepseek-chat",
        "tts_mode": "edge",
        "pan_pin": 18,
        "tilt_pin": 13,
        "wakeword_model": "hey_jarvis",
        "wakeword_threshold": 0.5,
        "simulation": not is_raspberry_pi
    }
else:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

# 选择加载物理驱动或仿真驱动
if is_raspberry_pi and not cfg.get("simulation", False):
    print("🚀 [环境识别] 检测到树莓派硬件环境，加载物理 HAL 驱动...")
    from drivers.hal_physical import PhysicalRobotHAL as RobotHAL
    hal = RobotHAL(pan_pin=cfg.get("pan_pin", 18), tilt_pin=cfg.get("tilt_pin", 13))
else:
    print("💻 [环境识别] 加载 PC 仿真 HAL 驱动...")
    from drivers.hal_simulation import SimulationRobotHAL as RobotHAL
    scene_file = os.path.join(os.path.dirname(__file__), "simulator", "scene.xml")
    hal = RobotHAL(model_path=scene_file)

eyes = EyeRenderer(240, 320)
if is_raspberry_pi and not cfg.get("simulation", False):
    eyes.start_live_animation(hal, fps=25)
clone_audio = cfg.get("mimo_clone_audio", "") if cfg.get("mimo_clone_enabled", False) else ""
tts = TTSEngine(
    mode=cfg.get("tts_mode", "edge"),
    custom_voice_id=cfg.get("custom_voice_id", ""),
    api_key=cfg.get("custom_tts_api_key", ""),
    mimo_api_key=cfg.get("mimo_api_key", ""),
    mimo_voice=cfg.get("mimo_voice", "冰糖"),
    mimo_clone_audio=clone_audio,
    mimo_design_prompt=cfg.get("mimo_design_prompt", "")
)

import requests

# 初始化百度智能云 ASR 语音识别
asr_engine = None
if cfg.get("baidu_api_key") and cfg.get("baidu_secret_key"):
    asr_engine = BaiduASR(cfg["baidu_api_key"], cfg["baidu_secret_key"])
    print("🎙️ [ASR 初始化] 百度智能云语音识别已就绪")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 初始化两级记忆中枢 (短期上下文 + 长期持久化画像)
mem = MemoryManager(memory_file=os.path.join(DATA_DIR, "memory.json"))
print(f"🧠 [记忆中枢] 已加载主人档案: {mem.long_term_data.get('user_name', '主人')} (亲密指数: Lv.{mem.long_term_data.get('intimacy_level', 1)})")

# 初始化自主生命体征状态机与人脸视觉注视追踪器
idle_fsm = IdleFSM()
face_tracker = FaceTracker(enabled=cfg.get("face_tracking", False))
print(f"👁️ [视觉追踪] 人脸注视追踪初始化状态: {'ON (开启)' if face_tracker.enabled else 'OFF (关闭)'}")

# 初始化技能与自进化动作调度中枢
skill_mgr = SkillManager(storage_dir=DATA_DIR, memory_manager=mem)

def on_hardware_alarm(rem):
    """物理机闹钟/日程到点主动抢占播报"""
    print(f"🚨 [MainAgent 物理抢占] 到点提醒: {rem.get('content')}")
    sound_effects.play_sound("wake_chime")
    hal.set_angles(90, 75)
    hal.display_face(eyes.render_eyes("SURPRISED"))
    time.sleep(0.5)
    alert_text = f"主人，时间到啦！提醒你：{rem.get('content')}！"
    try:
        a_path, a_dur = tts.synthesize(alert_text)
        tts.play_async(a_path, a_dur)
        hal.set_angles(90, 80)
        hal.display_face(eyes.render_eyes("HAPPY"))
        time.sleep(min(3.5, a_dur))
    except Exception as e:
        print(f"⚠️ [闹钟语音播报异常]: {e}")
    hal.set_angles(90, 90)
    hal.display_face(eyes.render_eyes("NORMAL"))

skill_mgr.register_trigger_callback(on_hardware_alarm)

# 初始化元认知自进化中枢 (生物钟作息学习 + 海马体夜间反思)
evolution_engine = EvolutionEngine(DATA_DIR, memory_manager=mem, skill_manager=skill_mgr)
api_key = cfg.get("api_key", "")
base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
llm_model = cfg.get("llm_model", "mimo-v2.6-flash")
client = None

if api_key and "YOUR_LLM_API_KEY" not in api_key:
    print(f"🧠 [大脑初始化] 已配置大模型: {llm_model} @ {base_url}")
else:
    print("ℹ️ [大脑初始化] 未检测到有效 API Key，将使用本地规则模式")

# 音频与唤醒词模块初始化
has_mic = False
p = None
stream = None
oww_model = None

try:
    import pyaudio
    import numpy as np
    from openwakeword.model import Model

    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    CHUNK = 1280

    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    oww_model = Model(wakeword_models=[cfg.get("wakeword_model", "hey_jarvis")], inference_framework="onnx")
    has_mic = True
    print(f"🎙️ [音频模块] 麦克风与 openWakeWord 监听引擎就绪，模型: {cfg.get('wakeword_model', 'hey_jarvis')}")
except Exception as e:
    print(f"ℹ️ [音频模块] 物理麦克风/唤醒词库未就绪 ({e})，将支持终端命令行交互模式")


def record_audio_clip(max_seconds=15, silence_timeout=0.9, min_seconds=0.6) -> str:
    """
    智能动态录音 (基于能量 VAD 静音检测)：
    - 告别死等 4 秒！
    - 开始说话后，停顿超过 0.9 秒自动截止并提交转写；
    - 设有最长保护 (默认 15 秒) 与最短过滤 (0.6 秒)。
    """
    if not stream or not p:
        return ""

    frames = []
    chunk_per_sec = int(RATE / CHUNK)
    max_chunks = int(chunk_per_sec * max_seconds)
    min_chunks = int(chunk_per_sec * min_seconds)
    silence_limit = int(chunk_per_sec * silence_timeout)

    silence_counter = 0
    has_spoken = False
    print("🎙️ [正在倾听...] 请说话 (说完停顿约 1 秒自动开始回答)")

    for _ in range(max_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)

        # 动态计算语音振幅能量
        audio_np = np.frombuffer(data, dtype=np.int16)
        energy = float(np.abs(audio_np).mean())

        # 能量超过阈值判定为有人说话
        if energy > 650:
            has_spoken = True
            silence_counter = 0
        else:
            if has_spoken:
                silence_counter += 1
                if silence_counter >= silence_limit and len(frames) >= min_chunks:
                    print("🛑 [检测到说话停顿] 自动截断录音并立即开始识别...")
                    break

    wav_path = "/tmp/user_input.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
    return wav_path


def process_interaction(user_text: str):
    """
    大脑中枢决策：根据用户表达执行动作、表情与语音回复
    """
    print(f"\n👤 [用户表达]: {user_text}")

    max_len = cfg.get("max_reply_length", 200)
    sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=max_len, skill_manager=skill_mgr)

    reply_text = "没有听清你的声音呢，请再说一次吧~"
    behaviors = []

    # 全双工打断 (Barge-in): 只要主人开启新一轮交互，立即打断并停止上一句正在播放的语音
    tts.stop()

    # 通知闲置状态机已进入活跃交互
    idle_fsm.notify_active()

    # P0: 即时拟人垫音与零延迟思考动作 (打破 10s 死寂，感知零延迟)
    sound_effects.play_sound("thinking_hum")
    hal.set_angles(85, 78) # 好奇歪头
    hal.display_face(eyes.render_eyes("THINKING", look_x=10, look_y=-14))

    stream_played = False

    # 清空记忆指令拦截
    if any(k in user_text for k in ["清空记忆", "重置记忆", "忘记我"]):
        mem.clear()
        reply_text = "主人，我的记忆库已经全部清空啦，我们重新认识吧！"
        behaviors = [
            {"emotion": "SURPRISED", "action": "SURPRISE", "pan": 90, "tilt": 75, "delay": 600},
            {"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 80, "delay": 800}
        ]
    elif api_key and "YOUR_LLM_API_KEY" not in api_key:
        try:
            reasoner = AgentReasoner(
                api_key=api_key,
                base_url=base_url,
                model=llm_model,
                max_steps=4
            )
            # 开启高并发流式推理：首句边思考边发声 (TTFA ~1.5s)
            stream_sentences = []
            for ev in reasoner.run_react_loop_stream(
                user_text=user_text,
                base_sys_prompt=sys_prompt,
                memory_manager=mem,
                skill_manager=skill_mgr,
                evolution_engine=evolution_engine
            ):
                ev_type = ev.get("type")
                if ev_type == "tool_start":
                    print(f"⚡ [技能调用] 正在执行: {ev.get('name')}({ev.get('args')})")
                elif ev_type == "observation":
                    print(f"🔍 [观察反馈] {ev.get('name')} ➔ {str(ev.get('result'))[:60]}")
                elif ev_type == "sentence":
                    s_text = ev.get("text", "").strip()
                    if s_text:
                        stream_sentences.append(s_text)
                        print(f"🗣️ [流式断句语音]: {s_text}")
                        try:
                            s_path, s_dur = tts.synthesize(s_text)
                            tts.play_async(s_path, s_dur)
                            stream_played = True
                            # 播报期间微动伴随
                            hal.set_angles(90, 85)
                            hal.display_face(eyes.render_eyes("HAPPY"))
                            t_start = time.time()
                            while (time.time() - t_start < s_dur) and tts.is_playing():
                                time.sleep(0.08)
                        except Exception as e:
                            print(f"⚠️ [流式单句音频播放异常]: {e}")
                elif ev_type == "done":
                    reply_text = ev.get("reply") or "".join(stream_sentences) or "收到啦！"
                    behaviors = ev.get("behaviors", [])

        except Exception as e:
            print(f"⚠️ [Agent-LLM ReAct Loop 异常]: {e}")
            reply_text = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"
            behaviors = [{"emotion": "CONFUSED", "action": "NONE", "pan": 90, "tilt": 90, "delay": 600}]
    else:
        # Mock 规则匹配
        if "你好" in user_text or "嗨" in user_text:
            reply_text = "主人你好呀！今天心情怎么样？"
            behaviors = [
                {"emotion": "SURPRISED", "action": "NONE", "pan": 90, "tilt": 80, "delay": 500},
                {"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 800}
            ]
        else:
            reply_text = f"收到：{user_text}"
            behaviors = [{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]

    # P2: 配合第一阶段情感播放萌趣音效
    first_emo = behaviors[0].get("emotion", "HAPPY") if behaviors else "HAPPY"
    if first_emo in ["HAPPY", "LOVE", "WINK"]:
        sound_effects.play_sound("happy_chirp")
    elif first_emo in ["CONFUSED", "THINKING"]:
        sound_effects.play_sound("confused_boop")
    elif first_emo in ["SURPRISED", "ANGRY"]:
        sound_effects.play_sound("surprise_squeak")

    # 若未走流式播放（如规则模式或流式降级），进行整段合成播报
    if not stream_played and reply_text:
        audio_path, total_dur = tts.synthesize(reply_text)
        print(f"🔊 [TTS 准备就绪，音频实测总长: {total_dur:.2f}秒]: {reply_text}")
        tts.play_async(audio_path, total_dur)
    else:
        total_dur = 2.0

    # 3. 将真实音频总长等分给各个动作阶段，确保每个动作在播放中平滑展开
    if not behaviors:
        behaviors = [{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 80}]

    stage_dur = max(0.4, total_dur / len(behaviors))

    p_val, t_val = 90, 90
    for b in behaviors:
        p_val = b.get("pan", 90)
        t_val = b.get("tilt", 90)
        emo_val = b.get("emotion", "HAPPY")
        act_val = b.get("action", "NONE")

        print(f"🤖 [硬件时序动作] Pan={p_val}°, Tilt={t_val}°, 表情={emo_val}, 动作={act_val} (维持 {stage_dur:.2f}s)")
        hal.set_angles(p_val, t_val)
        hal.display_face(eyes.render_eyes(emo_val))

        # 特征动作执行
        if act_val == "NOD":
            hal.set_angles(p_val, max(60, t_val - 14))
            time.sleep(0.18)
            hal.set_angles(p_val, min(120, t_val + 14))
            time.sleep(0.18)
            hal.set_angles(p_val, t_val)
        elif act_val == "SHAKE":
            hal.set_angles(max(30, p_val - 18), t_val)
            time.sleep(0.16)
            hal.set_angles(min(150, p_val + 18), t_val)
            time.sleep(0.16)
            hal.set_angles(p_val, t_val)
        elif act_val == "LOOK_UP":
            hal.set_angles(p_val, 70)
        elif act_val == "SURPRISE":
            hal.set_angles(p_val, 75)

        # 在该情绪阶段内，跟随语调保持自然微点头起伏，直到该阶段时长耗尽
        start_stage = time.time()
        phase = 0.0
        while (time.time() - start_stage < stage_dur) and tts.is_playing():
            phase += 0.5
            hal.set_angles(p_val, t_val + math.sin(phase) * 2.2, smooth=False)
            time.sleep(0.12)

    # 4. 音频若尚未彻底播完，保持平滑律动持续至最后一毫秒
    phase = 0.0
    while tts.is_playing():
        phase += 0.5
        hal.set_angles(p_val, t_val + math.sin(phase) * 2.0, smooth=False)
        time.sleep(0.12)

    # 5. 话音刚落：进入 1.8 秒“专注倾听与情感余韵” (Emotional Afterglow & Attentive Listening)
    # 保持最后的情绪与肢体姿态，轻微呼吸注视主人，绝不机械面瘫秒归位
    print("👂 [情感余韵] 保持姿态注视主人，等待互动反馈...")
    afterglow_start = time.time()
    phase = 0.0
    while time.time() - afterglow_start < 1.8:
        phase += 0.35
        # 极轻柔的专注倾听呼吸微动 (幅度 < 1.0度)
        hal.set_angles(p_val, t_val + math.sin(phase) * 0.9, smooth=False)
        time.sleep(0.08)

    # 6. 自然平滑淡出回日常待机态
    qr_file = os.path.join(DATA_DIR, "xiaomi_qr.png")
    if os.path.exists(qr_file) and time.time() - os.path.getmtime(qr_file) < 20:
        try:
            from PIL import Image
            qr_img = Image.open(qr_file).convert("RGB").resize((240, 240))
            hal.display_face(qr_img)
            time.sleep(4.0)
        except Exception as e:
            print(f"⚠️ [屏幕渲染二维码失败]: {e}")
    hal.display_face(eyes.render_eyes("NORMAL"))
    hal.set_angles(90, 90)
    idle_fsm.finish_active()


def run_loop():
    """主调度循环"""
    hal.display_face(eyes.render_eyes("NORMAL"))
    print("\n" + "="*50)
    print("🤖 具身交互引擎已激活！")
    print(f"🎵 非语言萌趣音效: 已激活 (Procedural Audio)")
    print(f"🫁 闲置呼吸状态机: 已激活 (Sine Wave Breathing & FSM)")
    print(f"👁️ 人脸视觉注视追踪: {'ON (开启)' if face_tracker.enabled else 'OFF (关闭)'}")
    if has_mic:
        print("🔔 等待唤醒词唤醒 (如: 小猪小猪 / Hey Jarvis)...")
    else:
        print("⌨️  已进入终端文字交互调试模式 (输入消息回车即可测试，输入 exit 退出)")
    print("="*50 + "\n")

    while True:
        if has_mic:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                audio_np = np.frombuffer(data, dtype=np.int16)
                preds = oww_model.predict(audio_np)

                if any(v > cfg.get("wakeword_threshold", 0.5) for v in preds.values()):
                    print("\n🔔 [唤醒成功] 触发具身注意力机制！")
                    sound_effects.play_sound("wake_chime")
                    idle_fsm.notify_active()
                    oww_model.reset()

                    # 抬头 10° 并眨眼展示笑意
                    hal.set_angles(90, 80)
                    hal.display_face(eyes.render_eyes("BLINK"))
                    time.sleep(0.12)
                    hal.display_face(eyes.render_eyes("HAPPY"))

                    # 动态 VAD 智能录制用户语音 (说完即停，最长 15s)
                    clip_path = record_audio_clip()
                    hal.display_face(eyes.render_eyes("CONFUSED"))

                    user_text = ""
                    if clip_path and os.path.exists(clip_path):
                        if asr_engine:
                            user_text = asr_engine.recognize(clip_path)
                        elif client:
                            try:
                                with open(clip_path, "rb") as af:
                                    trans = client.audio.transcriptions.create(model="whisper-1", file=af)
                                    user_text = trans.text
                            except Exception as e:
                                print(f"⚠️ [ASR 语音转文字异常]: {e}")

                    if user_text:
                        process_interaction(user_text)
                    else:
                        sound_effects.play_sound("confused_boop")
                        tts.speak("我好像没听清，您能再说一遍吗？")
                        hal.display_face(eyes.render_eyes("NORMAL"))
                        hal.set_angles(90, 90)
                else:
                    # P1: 闲置状态机与呼吸生命体征微动更新
                    fsm_info = idle_fsm.update()
                    if fsm_info["pan_offset"] != 0 or fsm_info["tilt_offset"] != 0:
                        hal.set_angles(90 + fsm_info["pan_offset"], 90 + fsm_info["tilt_offset"], smooth=False)
                    if fsm_info["emotion"]:
                        hal.display_face(eyes.render_eyes(fsm_info["emotion"]))
                    if fsm_info["proactive_text"]:
                        print(f"💬 [主动关怀播报]: {fsm_info['proactive_text']}")
                        tts.speak(fsm_info["proactive_text"])

                    # P3: 人脸视觉注视追踪 (若配置开关开启)
                    if face_tracker.enabled:
                        try:
                            img_bytes = hal.capture_image()
                            if img_bytes:
                                nparr = np.frombuffer(img_bytes, np.uint8)
                                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                has_face, ox, oy = face_tracker.detect_face(frame)
                                if has_face:
                                    lx, ly, tp, tt = face_tracker.compute_gaze_and_angles(ox, oy, hal.cur_pan, hal.cur_tilt)
                                    hal.display_face(eyes.render_eyes("NORMAL", look_x=lx, look_y=ly))
                                    if abs(ox) > 0.22 or abs(oy) > 0.22:
                                        hal.set_angles(tp, tt)
                        except Exception:
                            pass

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"⚠️ 循环异常: {e}")
                time.sleep(0.5)
        else:
            try:
                line = input("💬 请输入对话内容: ").strip()
                if not line:
                    continue
                if line.lower() in ["exit", "quit", "q"]:
                    break
                process_interaction(line)
            except (KeyboardInterrupt, EOFError):
                break


def cleanup():
    print("\n🛑 正在停止机器人主控系统...")
    try:
        if stream:
            stream.stop_stream()
            stream.close()
        if p:
            p.terminate()
        hal.close()
    except Exception:
        pass
    print("✅ 系统已安全退出。")


if __name__ == "__main__":
    def sig_handler(sig, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        run_loop()
    finally:
        cleanup()
