import http.server
import socketserver
import os
import json
import re
import requests
import subprocess
import threading
from datetime import datetime
from drivers.asr_engine import BaiduASR
from core.memory_manager import MemoryManager
import core.sound_effects as sound_effects
from core.face_tracker import FaceTracker
from core.idle_fsm import IdleFSM
from core.skill_manager import SkillManager
from core.evolution_engine import EvolutionEngine
from core.agent_reasoner import AgentReasoner

PORT = 8090
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DIRECTORY, "data")
os.makedirs(DATA_DIR, exist_ok=True)

mem = MemoryManager(memory_file=os.path.join(DATA_DIR, "memory.json"))
face_tracker = FaceTracker(enabled=False)
idle_fsm = IdleFSM()
skill_mgr = SkillManager(storage_dir=DATA_DIR)
evolution_engine = EvolutionEngine(DATA_DIR, memory_manager=mem, skill_manager=skill_mgr)

# 待派发给前端 Web 仿真视窗的到点闹钟事件队列
pending_web_alarms = []
alarms_lock = threading.Lock()

def on_alarm_triggered(rem):
    with alarms_lock:
        pending_web_alarms.append(rem)
    print(f"🔔 [WebSim] 收到闹钟事件已推入前端队列: {rem.get('content')}")

skill_mgr.register_trigger_callback(on_alarm_triggered)

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
        if self.path in ["/", "", "/web_simulator.html", "/simulator/web_simulator.html"]:
            html_path = os.path.join(DIRECTORY, "simulator", "web_simulator.html")
            if os.path.exists(html_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(html_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, "HTML simulator page not found")
                return

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

        # 小米米家扫码绑定状态查询
        if self.path == "/api/xiaomi/qr_status":
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                data = {
                    "status": qr_auth_manager.status,
                    "qr_url": qr_auth_manager.qr_url,
                    "login_url": qr_auth_manager.login_url,
                    "device_count": len(qr_auth_manager.synced_devices),
                    "devices": qr_auth_manager.synced_devices,
                    "error": qr_auth_manager.error_msg
                }
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 获取活跃日程与闹钟列表
        if self.path == "/api/reminders":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            data = {"status": "ok", "reminders": skill_mgr.list_active_reminders()}
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # 获取自进化动作列表
        if self.path == "/api/evolved_actions":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            data = {"status": "ok", "actions": skill_mgr.list_evolved_actions()}
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # 获取到点待触发的闹钟/日程事件 (前端长轮询/定期拉取)
        if self.path == "/api/alarm_events":
            with alarms_lock:
                events = list(pending_web_alarms)
                pending_web_alarms.clear()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "events": events}, ensure_ascii=False).encode("utf-8"))
            return

        # 获取指定情绪的纯 CSS3 动力学动态 SVG 图像
        if self.path.startswith("/api/svg?"):
            from urllib.parse import urlparse, parse_qs
            from core.svg_animator import SVGAnimator
            query = parse_qs(urlparse(self.path).query)
            emotion = query.get("emotion", ["NORMAL"])[0].upper()
            svg_content = SVGAnimator.generate_svg(emotion)
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(svg_content.encode("utf-8"))
            return

        # 获取全部 12 款动态 SVG 清单
        if self.path == "/api/svg_list":
            emotions = [
                "NORMAL", "HAPPY", "BLINK", "CONFUSED", "SAD", "ANGRY",
                "SURPRISED", "LOVE", "WINK", "SLEEPY", "DIZZY", "THINKING"
            ]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "emotions": emotions}, ensure_ascii=False).encode("utf-8"))
            return

        # 获取技能商店列表与已安装技能
        if self.path == "/api/skills/store":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            data = {
                "status": "ok",
                "store": skill_mgr.list_store_skills(),
                "installed": skill_mgr.list_installed_skills()
            }
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # 获取生物钟作息与反思元认知数据
        if self.path == "/api/circadian":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            data = {
                "status": "ok",
                "circadian": evolution_engine.circadian_data,
                "reflection": mem.long_term_data.get("daily_reflection", {})
            }
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
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
        # 小米米家发起扫码绑定
        if self.path == "/api/xiaomi/qr_start":
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                res = qr_auth_manager.start_qr_session()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return
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

        # 取消提醒或闹钟
        if self.path == "/api/reminders/cancel":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                kw = body.get("keyword", "") or body.get("id", "")
                res = skill_mgr.cancel_reminder(kw)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "cancelled": res}).encode("utf-8"))
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

        # 手动/夜间触发海马体深度记忆升华反思
        if self.path == "/api/reflection/trigger":
            cfg = load_config()
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "https://token.sensenova.cn/v1")
            model = cfg.get("llm_model", "sensenova-6.8-flash-lite")
            res = evolution_engine.trigger_reflection(api_key, base_url, model)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        # 动态安装技能插件
        if self.path == "/api/skills/install":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            sid = req.get("skill_id", "")
            ok = skill_mgr.install_skill(sid)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if ok else "error", "installed": ok}, ensure_ascii=False).encode("utf-8"))
            return

        # 动态卸载技能插件
        if self.path == "/api/skills/uninstall":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            sid = req.get("skill_id", "")
            ok = skill_mgr.uninstall_skill(sid)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if ok else "error", "uninstalled": ok}, ensure_ascii=False).encode("utf-8"))
            return

        # 测试运行指定技能
        if self.path == "/api/skills/run":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            sid = req.get("skill_id", "")
            params = req.get("params", {})
            result = skill_mgr.execute_skill(sid, **params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "result": result}, ensure_ascii=False).encode("utf-8"))
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

            now_dt = datetime.now()
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            current_time_str = f"{now_dt.strftime('%Y-%m-%d %H:%M:%S')} ({weekdays[now_dt.weekday()]})"

            known_actions = skill_mgr.list_evolved_actions()
            actions_desc = ", ".join([f"{k}({v.get('description', '')})" for k, v in known_actions.items()]) if known_actions else "暂无自创动作"

            sys_prompt = f"""你是桌面的具身实体机器人搭子。根据主人的话，输出富有真实生命体征、连续起伏的肢体语言与回答。
当前系统时间为：{current_time_str}。
你已永久掌握的自进化动作库包括：【{actions_desc}】。

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
  ],
  "skill": {{
    "name": "set_timer | set_alarm | cancel_reminder | list_reminders | evolve_action",
    "args": {{ ... }}
  }}
}}
说明：
- behaviors: 输出 1 到 3 个连续递进的动作表情时序步骤！若用户要求表演你已掌握的自进化动作（如转圈舞等），请在 behaviors 中演绎或在回复中热情回应。
- pan: 水平头部朝向 [30, 150]，90 为正中；tilt: 俯仰头部朝向 [70, 110]，90 为平视，80 为轻微抬头看人；delay: 毫秒数 [500, 1200]。
- skill (可选调度字段，非必须):
  1. 用户要求倒计时(如“10分钟后叫我喝水”) -> "skill": {{"name": "set_timer", "args": {{"seconds": 600, "content": "喝水"}}}}
  2. 用户要求设闹钟/提醒(如“明天早上8点叫我”、“下午3点提醒开会”) -> 根据当前系统时间推算，输出 "skill": {{"name": "set_alarm", "args": {{"time_str": "08:00", "content": "起床"}}}}
  3. 用户要求取消闹钟(如“取消喝水提醒”) -> "skill": {{"name": "cancel_reminder", "args": {{"keyword": "喝水"}}}}
  4. 用户要求查有哪些闹钟 -> "skill": {{"name": "list_reminders", "args": {{}}}}
  5. 用户夸赞、教你新动作、或你想自创一套复合肢体动作时 -> "skill": {{"name": "evolve_action", "args": {{"name": "HAPPY_WIGGLE", "description": "开心扭动舞步", "keyframes": [{{"pan": 80, "tilt": 95, "emotion": "HAPPY", "duration": 400}}, {{"pan": 100, "tilt": 95, "emotion": "HAPPY", "duration": 400}}]}}}}

【参考示例】
- 用户："30分钟后提醒我关火" -> {{
    "reply": "好嘞！我已经帮你设好30分钟后的关火倒计时啦，放心交给我！",
    "behaviors": [
      {{"emotion": "THINKING", "action": "LOOK_UP", "pan": 95, "tilt": 80, "delay": 600}},
      {{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 800}}
    ],
    "skill": {{"name": "set_timer", "args": {{"seconds": 1800, "content": "关火"}}}}
  }}
- 用户："帮我看一下我有什么小米设备可以控制" -> {{
    "reply": "好的主人，马上为你扫描已配置的米家智能设备！",
    "behaviors": [
      {{"emotion": "THINKING", "action": "LOOK_UP", "pan": 95, "tilt": 80, "delay": 600}},
      {{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 800}}
    ],
    "skill": {{"name": "xiaomi_iot", "args": {{"device": "all", "action": "查询设备列表"}}}}
  }}
- 用户："做个开心的动作逗逗我" -> {{
    "reply": "看我的秘密开心摇摆！怎么样，有没有被我萌到呀？",
    "behaviors": [
      {{"emotion": "HAPPY", "action": "NOD", "pan": 80, "tilt": 85, "delay": 500}},
      {{"emotion": "LOVE", "action": "WINK", "pan": 100, "tilt": 85, "delay": 600}}
    ],
    "skill": {{"name": "evolve_action", "args": {{"name": "JOY_BOUNCE", "description": "欢快左右小跳跃", "keyframes": [{{"pan": 80, "tilt": 80, "emotion": "HAPPY", "duration": 400}}, {{"pan": 100, "tilt": 80, "emotion": "LOVE", "duration": 400}}, {{"pan": 90, "tilt": 90, "emotion": "WINK", "duration": 400}}]}}}}
  }}

【真实工具调用铁律】:
1. 严禁口嗨欺骗用户！当用户提出查设备、查天气、设闹钟、控制电器等明确意图时，你必须在当轮直接输出 `skill` 调度真实工具，严禁使用“稍等我去查/马上为你扫描”等推脱口嗨用语假装后台在跑！
2. 工具执行后系统会自动将插件真实结果回填给主人，绝不可凭空捏造虚假设备名称！
"""
            # 注入已激活的动态技能插件与商店工具指令
            sys_prompt += "\n" + skill_mgr.get_skills_tools_prompt()

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
                    reasoner = AgentReasoner(
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        max_steps=4
                    )
                    react_out = reasoner.run_react_loop(
                        user_text=user_text,
                        base_sys_prompt=sys_prompt,
                        memory_manager=mem,
                        skill_manager=skill_mgr,
                        evolution_engine=evolution_engine
                    )
                    reply_data["reply"] = react_out["reply"]
                    reply_data["behaviors"] = react_out["behaviors"]
                    reply_data["skill_result"] = react_out.get("final_skill_res")
                    reply_data["steps"] = react_out.get("steps", [])
                except Exception as e:
                    print(f"⚠️ [Web-LLM ReAct Loop Error]: {e}")
                    reply_data["reply"] = "脑筋稍微转了一下，我们重试一次吧。"

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
