import http.server
import socketserver
import os
import json
import re
import requests
import subprocess
from asr_engine import BaiduASR
from memory_manager import MemoryManager
import sound_effects
from face_tracker import FaceTracker
from idle_fsm import IdleFSM

PORT = 8090
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
mem = MemoryManager()
face_tracker = FaceTracker(enabled=False)
idle_fsm = IdleFSM()

def parse_llm_json(content: str, reasoning: str = "") -> dict:
    """
    五重容错解析大模型输出，确保绝不丢失回答内容：
    1. 纯 JSON 解析
    2. Markdown ```json 代码块提取
    3. 正则匹配最外层花括号 {...}
    4. 纯自然语言回答回退（保留大模型生成文本，自动附赠生动表情动作）
    5. reasoning 思考尾部语义回退
    """
    content = (content or "").strip()
    reasoning = (reasoning or "").strip()

    # 策略 1: 直接 json.loads
    try:
        data = json.loads(content)
        if isinstance(data, dict) and ("reply" in data or "behaviors" in data):
            return data
    except Exception:
        pass

    # 策略 2: 提取 ```json ... ```
    md_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content, re.IGNORECASE)
    if md_match:
        try:
            data = json.loads(md_match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 策略 3: 正则搜索最外层花括号 {...}
    brace_match = re.search(r"(\{[\s\S]*\})", content)
    if brace_match:
        try:
            data = json.loads(brace_match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 策略 4: 如果大模型输出了纯文本（没带 JSON 外壳），直接取大模型回答，绝不吞掉！
    clean_text = re.sub(r"```[\s\S]*?```", "", content).strip()
    if clean_text and len(clean_text) > 1:
        clean_text = re.sub(r"^(?:回答|回复|小猪|机器小猪)[:：]\s*", "", clean_text).strip()
        return {
            "reply": clean_text,
            "behaviors": [
                {"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 700}
            ]
        }

    # 策略 5: 回退从 reasoning 思考链末尾提取
    if reasoning:
        r_brace = re.search(r"(\{[\s\S]*\})", reasoning)
        if r_brace:
            try:
                data = json.loads(r_brace.group(1))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    return None

def load_config():
    cfg_file = os.path.join(DIRECTORY, "config.json")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/web_simulator.html"
            return super().do_GET()

        # 获取记忆中枢档案状态
        if self.path == "/api/memory":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            mem_status = {
                "user_name": mem.long_term_data.get("user_name", "主人"),
                "intimacy_level": mem.long_term_data.get("intimacy_level", 1),
                "interaction_count": mem.long_term_data.get("interaction_count", 0),
                "facts": mem.long_term_data.get("facts", []),
                "history_turns": len(mem.short_term_history) // 2
            }
            self.wfile.write(json.dumps(mem_status, ensure_ascii=False).encode("utf-8"))
            return

        # 获取人脸视觉注视追踪开关状态
        if self.path == "/api/face_tracking":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"enabled": face_tracker.enabled}).encode("utf-8"))
            return

        # 获取非语言萌趣程序化音效流
        if self.path.startswith("/api/sounds"):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            name = query.get("name", ["happy_chirp"])[0]
            if not name.endswith(".wav"): name += ".wav"
            sound_path = os.path.join(DIRECTORY, "assets", "sounds", name)
            if not os.path.exists(sound_path):
                sound_effects.ensure_sound_assets()
            if os.path.exists(sound_path):
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(sound_path, "rb") as sf:
                    self.wfile.write(sf.read())
                return
            else:
                self.send_error(404, "Sound not found")
                return

        # 高拟真中文语音合成 (Edge-TTS zh-CN-XiaoxiaoNeural)
        if self.path.startswith("/api/tts"):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            text = query.get("text", [""])[0]
            if not text:
                self.send_error(400, "Missing text parameter")
                return

            try:
                import asyncio
                import edge_tts

                async def _gen():
                    # 使用 Xiaoxiao 极具亲和力的甜美自然女声
                    comm = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
                    chunks = []
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            chunks.append(chunk["data"])
                    return b"".join(chunks)

                audio_data = asyncio.run(_gen())
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio_data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(audio_data)
                return
            except Exception as e:
                print(f"⚠️ [TTS 合成错误]: {e}")
                self.send_error(500, str(e))
                return

        return super().do_GET()

    def do_POST(self):
        # 人脸视觉注视追踪开关切换
        if self.path == "/api/face_tracking":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                enabled = body.get("enabled", False)
                face_tracker.set_enabled(enabled)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "enabled": face_tracker.enabled}).encode("utf-8"))
            except Exception as e:
                self.send_error(400, str(e))
            return

        # 百度语音识别接口
        if self.path == "/api/asr":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_audio = self.rfile.read(content_length)

            cfg = load_config()
            ak = cfg.get("baidu_api_key", "")
            sk = cfg.get("baidu_secret_key", "")

            if not ak or not sk:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "未配置百度 ASR 密钥"}).encode("utf-8"))
                return

            # 保存并转换为 16kHz WAV
            in_file = "/tmp/web_upload_audio.bin"
            wav_file = "/tmp/web_upload_16k.wav"
            with open(in_file, "wb") as f:
                f.write(raw_audio)

            # ffmpeg 自动识别浏览器录音格式 (webm/wav/ogg/mp4) 并转成 16k 单声道 wav
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", in_file, "-ar", "16000", "-ac", "1", wav_file],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=5
                )
            except Exception as e:
                print(f"⚠️ [ASR 音频转换失败]: {e}")
                wav_file = in_file

            asr = BaiduASR(ak, sk)
            text = asr.recognize(wav_file)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "text": text}, ensure_ascii=False).encode("utf-8"))
            return

        if self.path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                req_json = json.loads(post_data.decode("utf-8"))
                user_text = req_json.get("text", "")
            except Exception:
                user_text = ""

            cfg = load_config()
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "https://token.sensenova.cn/v1").rstrip("/")
            model = cfg.get("llm_model", "sensenova-6.8-flash-lite")
            max_len = cfg.get("max_reply_length", 150)

            sys_prompt = f"""你是桌面的具身实体机器人搭子。根据主人的话，输出富有真实生命体征、连续起伏的肢体语言与回答。
严格输出 JSON 结构，不要包含任何 markdown 或其他文本：
{{
  "reply": "你说话的内容 (口语化、亲切真诚，建议 {max_len} 字以内)",
  "behaviors": [
    {{
      "emotion": "NORMAL | HAPPY | LOVE | WINK | CONFUSED | THINKING | SURPRISED | SLEEPY | DIZZY | SAD | ANGRY",
      "action": "NOD | SHAKE | NONE | LOOK_UP | SURPRISE",
      "pan": 90,
      "tilt": 80,
      "delay": 700
    }}
  ]
}}
说明：
- behaviors: 输出 1 到 3 个连续递进的动作表情时序步骤！真实人类和皮克斯动画都是复合情绪演变的，例如：被夸时先吃惊再害羞心动，分析问题时先思考后豁然开朗点头！
- pan: 水平头部朝向 [30, 150]，90 为正中
- tilt: 俯仰头部朝向 [70, 110]，90 为平视，80 为轻微抬头看人
- delay: 该步骤保持毫秒数 [500, 1200]

【参考示例】
- 用户："哇，我给你带了一整盒草莓蛋糕！" -> {{
    "reply": "哇！真的吗？太好啦！想想就幸福得冒泡~ 谢谢主人！",
    "behaviors": [
      {{"emotion": "SURPRISED", "action": "SURPRISE", "pan": 90, "tilt": 70, "delay": 600}},
      {{"emotion": "HAPPY", "action": "NOD", "pan": 85, "tilt": 75, "delay": 800}},
      {{"emotion": "LOVE", "action": "WINK", "pan": 90, "tilt": 80, "delay": 900}}
    ]
  }}
- 用户："这道复杂的数学题怎么算？" -> {{
    "reply": "这道题有点意思，容本小猪开足马力推演一下... 搞定啦！",
    "behaviors": [
      {{"emotion": "THINKING", "action": "LOOK_UP", "pan": 105, "tilt": 75, "delay": 900}},
      {{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 800}}
    ]
  }}
- 用户："今天加班太累了，我想睡了" -> {{
    "reply": "辛苦啦主人，快盖好被子，晚安好梦哦~",
    "behaviors": [
      {{"emotion": "SAD", "action": "NONE", "pan": 90, "tilt": 85, "delay": 600}},
      {{"emotion": "SLEEPY", "action": "NONE", "pan": 90, "tilt": 105, "delay": 1000}}
    ]
  }}
"""

            reply_data = {
                "reply": "我在听呢，有什么问题尽管问我吧！",
                "action": "NONE",
                "emotion": "HAPPY",
                "pan": 90,
                "tilt": 90
            }

            if any(k in user_text for k in ["清空记忆", "重置记忆", "忘记我"]):
                mem.clear()
                reply_data = {
                    "reply": "主人，我的记忆库已经全部清空啦，我们重新认识吧！",
                    "behaviors": [
                        {"emotion": "SURPRISED", "action": "SURPRISE", "pan": 90, "tilt": 75, "delay": 600},
                        {"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 80, "delay": 800}
                    ]
                }
            elif api_key and "YOUR_LLM_API_KEY" not in api_key:
                try:
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    # 组装带记忆的系统提示词与上下文
                    messages = mem.get_messages_for_llm(user_text, sys_prompt)
                    payload = {
                        "model": model,
                        "messages": messages,
                        "response_format": {"type": "json_object"}
                    }
                    resp = requests.post(url, headers=headers, json=payload, timeout=60)
                    if resp.status_code == 200:
                        choice = resp.json().get("choices", [{}])[0]
                        msg_obj = choice.get("message", {})
                        raw_content = msg_obj.get("content", "")
                        reasoning_content = msg_obj.get("reasoning", "")

                        parsed = parse_llm_json(raw_content, reasoning_content)
                        if parsed:
                            reply_data.update(parsed)
                            # 记录进入记忆中枢
                            mem.record_turn(user_text, reply_data.get("reply", ""))
                        else:
                            print(f"⚠️ [Web-LLM] 未能提取到有效回答，原始输出: {repr(raw_content)}")
                    else:
                        print(f"⚠️ [Web-LLM HTTP 错误 {resp.status_code}]: {resp.text}")
                except Exception as e:
                    print(f"⚠️ [Web-LLM Error]: {e}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(reply_data, ensure_ascii=False).encode("utf-8"))
            return

        return super().do_POST()

def run_server():
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", PORT), CustomHandler) as httpd:
        print("="*60)
        print("🚀 [Web 3D 仿真工作台已就绪]")
        print(f"👉 本地访问地址: http://localhost:{PORT}")
        print("✨ 包含: 3D WebGL 机械实体视角 + 2.4寸动态眼球屏 + 具身动作交互")
        print("="*60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 仿真服务器已关闭")

if __name__ == "__main__":
    run_server()
