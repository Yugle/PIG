import http.server
import os
import json
import subprocess
import threading
import queue
import asyncio
import base64
from datetime import datetime
from drivers.asr_engine import BaiduASR
from core.memory_manager import MemoryManager
import core.sound_effects as sound_effects
from core.face_tracker import FaceTracker
from core.idle_fsm import IdleFSM
from core.skill_manager import SkillManager
from core.evolution_engine import EvolutionEngine
from core.agent_reasoner import AgentReasoner
from core.prompt_builder import PromptBuilder
from core.hardware_agent import HardwareAgent

PORT = 8090
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DIRECTORY, "data")
os.makedirs(DATA_DIR, exist_ok=True)

mem = MemoryManager(memory_file=os.path.join(DATA_DIR, "memory.json"))
face_tracker = FaceTracker(enabled=False)
idle_fsm = IdleFSM()
skill_mgr = SkillManager(storage_dir=DATA_DIR, memory_manager=mem)
evolution_engine = EvolutionEngine(DATA_DIR, memory_manager=mem, skill_manager=skill_mgr)

# 待派发给前端 Web 仿真视窗的到点闹钟事件队列
pending_web_alarms = []
alarms_lock = threading.Lock()

def on_alarm_triggered(rem):
    with alarms_lock:
        pending_web_alarms.append(rem)
    print(f"🔔 [WebSim] 收到闹钟事件已推入前端队列: {rem.get('content')}")

skill_mgr.register_trigger_callback(on_alarm_triggered)

def load_config():
    cfg_file = os.path.join(DIRECTORY, "config.json")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# 初始化真机硬件与数字孪生工作中枢
server_cfg = load_config()
hardware_agent = HardwareAgent(
    config=server_cfg,
    memory_manager=mem,
    skill_manager=skill_mgr,
    evolution_engine=evolution_engine
)
hardware_agent.start()

# Web 实时数字孪生 SSE 订阅广播总线
agent_event_subscribers = []
subscribers_lock = threading.Lock()

def on_agent_event_broadcast(event_dict):
    with subscribers_lock:
        dead_subs = []
        for q in agent_event_subscribers:
            try:
                q.put_nowait(event_dict)
            except Exception:
                dead_subs.append(q)
        for d in dead_subs:
            if d in agent_event_subscribers:
                agent_event_subscribers.remove(d)

hardware_agent.register_event_listener(on_agent_event_broadcast)

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

        # 实时数字孪生虚实同步长连接 (Server-Sent Events)
        if self.path == "/api/agent/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_q = queue.Queue(maxsize=100)
            with subscribers_lock:
                agent_event_subscribers.append(client_q)

            try:
                init_ev = {"type": "connected", "status": hardware_agent.get_status()}
                self.wfile.write(f"data: {json.dumps(init_ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

                while True:
                    try:
                        ev = client_q.get(timeout=20.0)
                        msg = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with subscribers_lock:
                    if client_q in agent_event_subscribers:
                        agent_event_subscribers.remove(client_q)
            return

        # 查询当前运行模式与硬件状态
        if self.path == "/api/system/mode":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "hardware_status": hardware_agent.get_status()
            }, ensure_ascii=False).encode("utf-8"))
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

        # 高拟真中文语音合成 (支持 小米 MiMo-TTS 与 Edge-TTS)
        if self.path.startswith("/api/tts"):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(self.path).query)
            text = query.get("text", [""])[0]
            if not text:
                self.send_error(400, "Missing text parameter")
                return

            try:
                cfg = load_config()
                mode = cfg.get("tts_mode", "edge")
                mimo_key = cfg.get("mimo_api_key", "")
                mimo_voice = cfg.get("mimo_voice", "冰糖")

                # 若启用小米 MiMo 语音模型 (支持预置、克隆、设计)
                if mode == "mimo" and mimo_key:
                    from drivers.tts_engine import TTSEngine
                    sim_tts = TTSEngine(
                        mode="mimo",
                        mimo_api_key=mimo_key,
                        mimo_voice=mimo_voice,
                        mimo_clone_audio=cfg.get("mimo_clone_audio", ""),
                        mimo_design_prompt=cfg.get("mimo_design_prompt", "")
                    )
                    out_path, _ = sim_tts.synthesize(text, out_file="/tmp/web_mimo_tts.mp3")
                    if os.path.exists(out_path):
                        with open(out_path, "rb") as f:
                            audio_data = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/mpeg")
                        self.send_header("Content-Length", str(len(audio_data)))
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(audio_data)
                        return

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
        # 切换运行模式: 'twin' (真机孪生) 或 'simulation' (纯网页仿真)
        if self.path == "/api/system/mode":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            req_json = json.loads(post_data.decode("utf-8")) if post_data else {}
            target_mode = req_json.get("mode", "twin")

            msg = hardware_agent.set_mode(target_mode)

            # 持久化到 config.json
            cfg_file = os.path.join(DIRECTORY, "config.json")
            if os.path.exists(cfg_file):
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        cur_cfg = json.load(f)
                    cur_cfg["run_mode"] = target_mode
                    with open(cfg_file, "w", encoding="utf-8") as f:
                        json.dump(cur_cfg, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "message": msg,
                "mode": target_mode,
                "hardware_status": hardware_agent.get_status()
            }, ensure_ascii=False).encode("utf-8"))
            return

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

        # 现场编写 / 自主进化新技能插件
        if self.path == "/api/skills/create":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            sid = req.get("skill_id", "")
            s_name = req.get("name", "")
            desc = req.get("description", "")
            code = req.get("code", "")
            params = req.get("parameters", {})
            res = skill_mgr.create_custom_skill(sid, s_name, desc, code, params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        # ⚡ 流式即时推理与断句语音合成输出接口 (Server-Sent Events)
        if self.path == "/api/chat/stream":
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

            sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=max_len, skill_manager=skill_mgr)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            from core.streaming_pipeline import get_tts_streamer
            tts_streamer = get_tts_streamer(cfg)

            reasoner = AgentReasoner(
                api_key=api_key,
                base_url=base_url,
                model=model,
                max_steps=4
            )

            seq = 0
            try:
                for event in reasoner.run_react_loop_stream(
                    user_text=user_text,
                    base_sys_prompt=sys_prompt,
                    memory_manager=mem,
                    skill_manager=skill_mgr,
                    evolution_engine=evolution_engine
                ):
                    ev_type = event.get("type")
                    if ev_type == "sentence":
                        sentence_text = event.get("text", "").strip()
                        if sentence_text:
                            seq += 1
                            audio_b = asyncio.run(tts_streamer.synthesize_chunk(sentence_text))
                            b64 = base64.b64encode(audio_b).decode("utf-8") if audio_b else ""
                            payload = {
                                "type": "chunk",
                                "seq": seq,
                                "text": sentence_text,
                                "audio": b64
                            }
                            msg = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()

                    elif ev_type in ["tool_start", "observation", "done"]:
                        msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()

            except Exception as e:
                err_ev = {"type": "error", "message": str(e)}
                try:
                    self.wfile.write(f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
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

            sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=max_len, skill_manager=skill_mgr)

            if not user_text.strip():
                reply_data = {
                    "reply": "没有听到你说话呢，请对我说点什么吧~",
                    "action": "NONE",
                    "emotion": "CONFUSED",
                    "pan": 90,
                    "tilt": 90
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(reply_data, ensure_ascii=False).encode("utf-8"))
                return

            reply_data = {
                "reply": "",
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
                    reply_data["reply"] = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"

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
