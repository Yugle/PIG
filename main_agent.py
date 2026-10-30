import os
import sys
import json
import time
import wave
import signal

from eye_renderer import EyeRenderer
from tts_engine import TTSEngine

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
    from hal_physical import PhysicalRobotHAL as RobotHAL
    hal = RobotHAL(pan_pin=cfg.get("pan_pin", 18), tilt_pin=cfg.get("tilt_pin", 13))
else:
    print("💻 [环境识别] 加载 PC 仿真 HAL 驱动...")
    from hal_simulation import SimulationRobotHAL as RobotHAL
    scene_file = os.path.join(os.path.dirname(__file__), "scene.xml")
    hal = RobotHAL(model_path=scene_file)

eyes = EyeRenderer(240, 320)
tts = TTSEngine(
    mode=cfg.get("tts_mode", "edge"),
    custom_voice_id=cfg.get("custom_voice_id", ""),
    api_key=cfg.get("custom_tts_api_key", "")
)

import requests

# 初始化 OpenAI 兼容大模型客户端 (优先 requests，降级支持 openai sdk)
api_key = cfg.get("api_key", "")
base_url = cfg.get("base_url", "https://token.sensenova.cn/v1").rstrip("/")
llm_model = cfg.get("llm_model", "sensenova-6.8-flash-lite")
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


def record_audio_clip(seconds=4) -> str:
    """录制指定秒数的音频输入"""
    if not stream or not p:
        return ""
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


def process_interaction(user_text: str):
    """
    大脑中枢决策：根据用户表达执行动作、表情与语音回复
    """
    print(f"\n👤 [用户表达]: {user_text}")

    max_len = cfg.get("max_reply_length", 150)
    sys_prompt = f"""你是桌面的具身实体机器人搭子。根据主人的话，输出符合情境的肢体语言与回答。
严格输出 JSON 结构，不要包含任何 markdown 或其他文本：
{{
  "reply": "你说话的内容 (口语化、亲切真诚，适合语音播报，建议 {max_len} 字以内，如主人需要详细解答可适当展开)",
  "action": "NOD | SHAKE | NONE",
  "emotion": "NORMAL | HAPPY | CONFUSED | SAD | ANGRY",
  "pan": 90,
  "tilt": 90
}}
说明：
- pan: 水平头部朝向 [30, 150]，90 为正中
- tilt: 俯仰头部朝向 [70, 110]，90 为平视，80 为轻微抬头看人
- action: NOD(点头认同), SHAKE(摇头否认), NONE(静止)
- emotion: 表情类型
"""

    reply_text = "我在听呢，有什么我可以帮你的吗？"
    emotion = "HAPPY"
    action = "NONE"
    pan = 90
    tilt = 90

    if api_key and "YOUR_LLM_API_KEY" not in api_key:
        try:
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": llm_model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_text}
                ]
            }
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                resp_json = response.json()
                raw_content = resp_json["choices"][0]["message"]["content"].strip()

                # 清理可能包含的 markdown 语法块 ```json ... ```
                if raw_content.startswith("```"):
                    raw_content = raw_content.split("```")[1]
                    if raw_content.startswith("json"):
                        raw_content = raw_content[4:]
                    raw_content = raw_content.strip()

                res = json.loads(raw_content)
                reply_text = res.get("reply", reply_text)
                emotion = res.get("emotion", "NORMAL")
                action = res.get("action", "NONE")
                pan = res.get("pan", 90)
                tilt = res.get("tilt", 90)
            else:
                print(f"⚠️ [LLM 响应异常状态码 {response.status_code}]: {response.text}")
                reply_text = "网络信号有些微弱，我没听清呢。"
                emotion = "CONFUSED"
        except Exception as e:
            print(f"⚠️ [LLM 推理解析异常]: {e}")
            reply_text = "刚才信号闪了一下，我开小差了。"
            emotion = "CONFUSED"
    else:
        # Mock 规则匹配，便于在未配 Key 时体验
        if "你好" in user_text or "嗨" in user_text:
            reply_text = "主人你好呀！今天心情怎么样？"
            emotion = "HAPPY"
            action = "NOD"
            tilt = 80
        elif "不" in user_text or "错" in user_text:
            reply_text = "哎呀，不要难过嘛，我一直在陪着你。"
            emotion = "CONFUSED"
            action = "SHAKE"
        else:
            reply_text = f"收到：{user_text}，我是一只智能机械伴侣小猪。"
            emotion = "HAPPY"

    # 具身执行
    print(f"🤖 [具身动作] 转向 Pan={pan}°, Tilt={tilt}°, 表情={emotion}, 动作={action}")
    hal.set_angles(pan, tilt)
    hal.display_face(eyes.render_eyes(emotion))

    if action == "NOD":
        hal.set_angles(pan, 75)
        hal.set_angles(pan, 95)
        hal.set_angles(pan, 90)
    elif action == "SHAKE":
        hal.set_angles(pan - 20, tilt)
        hal.set_angles(pan + 20, tilt)
        hal.set_angles(pan, tilt)

    # 语音输出
    tts.speak(reply_text)

    # 动作执行完毕，归位待机
    time.sleep(0.5)
    hal.display_face(eyes.render_eyes("NORMAL"))
    hal.set_angles(90, 90)


def run_loop():
    """主调度循环"""
    hal.display_face(eyes.render_eyes("NORMAL"))
    print("\n" + "="*50)
    print("🤖 具身交互引擎已激活！")
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
                    oww_model.reset()

                    # 抬头 10° 并眨眼展示笑意
                    hal.set_angles(90, 80)
                    hal.display_face(eyes.render_eyes("BLINK"))
                    time.sleep(0.12)
                    hal.display_face(eyes.render_eyes("HAPPY"))

                    # 录制用户语音
                    clip_path = record_audio_clip(seconds=4)
                    hal.display_face(eyes.render_eyes("CONFUSED"))

                    user_text = ""
                    if client and clip_path and os.path.exists(clip_path):
                        try:
                            with open(clip_path, "rb") as af:
                                trans = client.audio.transcriptions.create(model="whisper-1", file=af)
                                user_text = trans.text
                        except Exception as e:
                            print(f"⚠️ [ASR 语音转文字异常]: {e}")

                    if user_text:
                        process_interaction(user_text)
                    else:
                        tts.speak("我好像没听清，您能再说一遍吗？")
                        hal.display_face(eyes.render_eyes("NORMAL"))
                        hal.set_angles(90, 90)

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
