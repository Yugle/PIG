import http.server
import os
import json
import time
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
from core.proactive_care import ProactiveCareManager

PORT = 8090
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DIRECTORY, "data")
os.makedirs(DATA_DIR, exist_ok=True)

mem = MemoryManager(memory_file=os.path.join(DATA_DIR, "memory.json"))
face_tracker = FaceTracker(enabled=False)
idle_fsm = IdleFSM()
skill_mgr = SkillManager(storage_dir=DATA_DIR, memory_manager=mem)
evolution_engine = EvolutionEngine(DATA_DIR, memory_manager=mem, skill_manager=skill_mgr)

_latest_webcam_frame = None

def get_latest_vision_frame():
    """获取最新视觉帧字节流 (优先网页/客户端摄像头，其次硬件 HAL)"""
    global _latest_webcam_frame
    if _latest_webcam_frame and len(_latest_webcam_frame) > 100:
        return _latest_webcam_frame
    tmp_path = "/tmp/pig_latest_frame.jpg"
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, "rb") as f:
                c = f.read()
                if len(c) > 100:
                    return c
        except Exception:
            pass
    if hardware_agent and hardware_agent.hal:
        try:
            return hardware_agent.hal.capture_image()
        except Exception:
            pass
    return None

def load_config():
    cfg_file = os.path.join(DIRECTORY, "config.json")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

server_cfg = load_config()
proactive_care = ProactiveCareManager(
    storage_dir=DATA_DIR,
    memory_manager=mem,
    evolution_engine=evolution_engine,
    skill_manager=skill_mgr,
    config=server_cfg
)

# 待派发给前端 Web 仿真视窗的到点闹钟事件队列
pending_web_alarms = []
alarms_lock = threading.Lock()

def on_alarm_triggered(rem):
    with alarms_lock:
        pending_web_alarms.append(rem)
    print(f"🔔 [WebSim] 收到闹钟事件已推入前端队列: {rem.get('content')}")

skill_mgr.register_trigger_callback(on_alarm_triggered)

# 初始化真机硬件与数字孪生工作中枢
hardware_agent = HardwareAgent(
    config=server_cfg,
    memory_manager=mem,
    skill_manager=skill_mgr,
    evolution_engine=evolution_engine,
    proactive_care=proactive_care
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

    def do_HEAD(self):
        if self.path.startswith("/api/vision/snapshot") or self.path.startswith("/api/vision/latest_snapshot"):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            return
        return super().do_HEAD()

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

        # 视觉抓拍快照静态访问接口 (供对话框实时查看小猪眼前捕获的真实现实瞬间)
        if self.path.startswith("/api/vision/snapshot") or self.path.startswith("/api/vision/latest_snapshot"):
            snap_path = "/tmp/pig_visual_qa_snapshot.jpg"
            if not os.path.exists(snap_path):
                snap_path = "/tmp/pig_latest_frame.jpg"
            if os.path.exists(snap_path):
                try:
                    with open(snap_path, "rb") as f:
                        img_bytes = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(img_bytes)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.end_headers()
                    self.wfile.write(img_bytes)
                    return
                except Exception as e:
                    self.send_error(500, str(e))
                    return
            self.send_error(404, "No visual snapshot available")
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

        # 获取系统完整配置与软硬件环境状态 (局域网设置中心)
        if self.path == "/api/system/config":
            cfg = load_config()
            merged_info = dict(cfg)
            if os.path.exists(os.path.join(DATA_DIR, "memory.json")):
                try:
                    with open(os.path.join(DATA_DIR, "memory.json"), "r", encoding="utf-8") as f:
                        m_data = json.load(f)
                        merged_info["bot_name"] = m_data.get("bot_name", "PIG")
                        merged_info["user_name"] = m_data.get("user_name", "胖虎")
                except Exception:
                    pass
            import socket, platform
            hostname = socket.gethostname()
            local_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass
            merged_info["system_env"] = {
                "platform": platform.platform(),
                "hostname": hostname,
                "local_ip": local_ip,
                "is_raspberry_pi": os.path.exists("/proc/device-tree/model")
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(merged_info, ensure_ascii=False).encode("utf-8"))
            return

        # 获取主动关怀状态与配置
        if self.path == "/api/proactive_care":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(proactive_care.get_status(), ensure_ascii=False).encode("utf-8"))
            return

        # 触发摸头杀或打断 (GET 调试通道)
        if self.path == "/api/touch":
            if hardware_agent:
                if hasattr(hardware_agent.hal, "trigger_touch"):
                    hardware_agent.hal.trigger_touch()
                else:
                    hardware_agent._on_touch_event()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "touch_triggered"}, ensure_ascii=False).encode("utf-8"))
            return

        if self.path in ["/api/stop", "/api/barge_in"]:
            if hardware_agent:
                hardware_agent.stop_speaking()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "barge_in_triggered"}, ensure_ascii=False).encode("utf-8"))
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
                    clone_audio = cfg.get("mimo_clone_audio", "") if cfg.get("mimo_clone_enabled", False) else ""
                    sim_tts = TTSEngine(
                        mode="mimo",
                        mimo_api_key=mimo_key,
                        mimo_voice=mimo_voice,
                        mimo_clone_audio=clone_audio,
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
        global _latest_webcam_frame
        # 调整唤醒词灵敏度阈值与麦克风软件增益
        if self.path == "/api/wakeword/config":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                if "threshold" in body:
                    val = float(body["threshold"])
                    hardware_agent.cfg["wakeword_threshold"] = val
                if "mic_gain" in body:
                    gain = float(body["mic_gain"])
                    hardware_agent.cfg["mic_gain"] = gain
                if "model" in body:
                    hardware_agent.cfg["wakeword_model"] = str(body["model"])

                # 持久化到 config.json
                cfg_file = os.path.join(DIRECTORY, "config.json")
                if os.path.exists(cfg_file):
                    try:
                        with open(cfg_file, "r", encoding="utf-8") as f:
                            cur_cfg = json.load(f)
                        if "threshold" in body:
                            cur_cfg["wakeword_threshold"] = hardware_agent.cfg["wakeword_threshold"]
                        if "mic_gain" in body:
                            cur_cfg["mic_gain"] = hardware_agent.cfg["mic_gain"]
                        with open(cfg_file, "w", encoding="utf-8") as f:
                            json.dump(cur_cfg, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "wakeword_threshold": hardware_agent.cfg.get("wakeword_threshold"),
                    "mic_gain": hardware_agent.cfg.get("mic_gain"),
                    "hardware_status": hardware_agent.get_status()
                }, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(400, str(e))
            return

        # 手动触发硬件唤醒 (跳过唤醒词检测，直接倾听并与主人对话)
        if self.path == "/api/wake":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                intent = body.get("intent", "")
                success = hardware_agent.trigger_wake_manually(custom_intent=intent)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok" if success else "busy"}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

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
        # 摸头与全双工打断 POST 接口
        if self.path == "/api/touch":
            if hardware_agent:
                if hasattr(hardware_agent.hal, "trigger_touch"):
                    hardware_agent.hal.trigger_touch()
                else:
                    hardware_agent._on_touch_event()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "touch_triggered"}, ensure_ascii=False).encode("utf-8"))
            return

        if self.path in ["/api/stop", "/api/barge_in"]:
            if hardware_agent:
                hardware_agent.stop_speaking()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "barge_in_triggered"}, ensure_ascii=False).encode("utf-8"))
            return

        # 人脸视觉注视追踪开关切换
        if self.path == "/api/face_tracking":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                enabled = body.get("enabled", False)
                face_tracker.set_enabled(enabled)
                if hardware_agent:
                    hardware_agent.set_face_tracking(enabled)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "enabled": face_tracker.enabled}).encode("utf-8"))
            except Exception as e:
                self.send_error(400, str(e))
            return

        # 持久化更新系统配置 (局域网设置中心保存)
        if self.path == "/api/system/config":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                updates = json.loads(post_data.decode("utf-8"))
                cfg = load_config()
                for k, v in updates.items():
                    if k in ["system_env", "status"]:
                        continue
                    cfg[k] = v

                cfg_file = os.path.join(DIRECTORY, "config.json")
                with open(cfg_file, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)

                # 同步热生效
                if "user_name" in updates:
                    mem.set_user_name(updates["user_name"])
                if "bot_name" in updates or "robot_name" in updates:
                    new_b = updates.get("bot_name") or updates.get("robot_name")
                    mem.set_bot_name(new_b)
                if "face_tracking" in updates:
                    face_tracker.set_enabled(bool(updates["face_tracking"]))
                    if hardware_agent:
                        hardware_agent.set_face_tracking(bool(updates["face_tracking"]))
                if "proactive_care" in updates:
                    proactive_care.set_enabled(bool(updates["proactive_care"]))
                if hardware_agent:
                    hardware_agent.cfg.update(cfg)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "配置已保存并即时热生效！"}).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 网页仿真端主动上传图片让小猪观察 (支持本地拍照或文件选择)
        if self.path == "/api/vision/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                img_data = body.get("image", "")
                if "," in img_data:
                    img_data = img_data.split(",", 1)[1]
                img_bytes = base64.b64decode(img_data)
                _latest_webcam_frame = img_bytes
                with open("/tmp/pig_latest_frame.jpg", "wb") as f:
                    f.write(img_bytes)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "图片已传递给小猪的眼睛！"}).encode("utf-8"))
            except Exception as e:
                self.send_error(400, str(e))
            return

        # 电脑真实摄像头视觉注视帧处理接口 (Sim-to-Real PC Webcam)
        if self.path == "/api/vision/face_detect":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                img_data = body.get("image", "")
                if "," in img_data:
                    img_data = img_data.split(",", 1)[1]
                img_bytes = base64.b64decode(img_data)
                _latest_webcam_frame = img_bytes
                try:
                    with open("/tmp/pig_latest_frame.jpg", "wb") as f:
                        f.write(img_bytes)
                except Exception:
                    pass
                import numpy as np
                import cv2
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                # 确保检测器处于激活状态
                face_tracker.enabled = True
                has_face, ox, oy = face_tracker.detect_face(frame)

                # 核心优先级管理：视线注视追踪为最低优先级
                # 若硬件智能体正在执行动作、思考、语音播报，视线追踪绝对不抢占舵机
                is_busy = False
                if hardware_agent:
                    if hasattr(hardware_agent, "is_action_playing") and hardware_agent.is_action_playing():
                        is_busy = True
                    if hardware_agent.state not in ("IDLE", "AFTERGLOW"):
                        is_busy = True

                res = {
                    "status": "ok",
                    "has_face": bool(has_face),
                    "offset_x": round(float(ox), 3),
                    "offset_y": round(float(oy), 3),
                    "gaze_suppressed": is_busy,
                    "suppress_reason": "action_or_speech_priority" if is_busy else "none"
                }

                if has_face:
                    cur_p = float(getattr(hardware_agent.hal, "cur_pan", 90.0))
                    cur_t = float(getattr(hardware_agent.hal, "cur_tilt", 90.0))
                    gaze_mode = hardware_agent.cfg.get("gaze_tracking_mode", "9grid") if hardware_agent else "9grid"
                    lx, ly, tp, tt = face_tracker.compute_gaze_and_angles(float(ox), float(oy), cur_p, cur_t, is_moving_camera=False, mode=gaze_mode)
                    res["look_x"] = int(lx)
                    res["look_y"] = int(ly)
                    res["target_pan"] = round(float(tp), 1)
                    res["target_tilt"] = round(float(tt), 1)
                    res["sector"] = getattr(face_tracker, "cur_sector", "CENTER")

                    if face_tracker.last_box:
                        res["box"] = {
                            "x": int(face_tracker.last_box["x"]),
                            "y": int(face_tracker.last_box["y"]),
                            "w": int(face_tracker.last_box["w"]),
                            "h": int(face_tracker.last_box["h"])
                        }

                    # 仅在非动作占用时驱动实体舵机 (动作绝对独占原则)
                    if not is_busy and hardware_agent and hasattr(hardware_agent, "hal") and hardware_agent.hal:
                        hardware_agent.hal.set_angles(float(tp), float(tt))

                def _safe_json_serialize(obj):
                    if hasattr(obj, "item"):
                        return obj.item()
                    return str(obj)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, default=_safe_json_serialize).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 主动关怀开关与配置切换
        if self.path == "/api/proactive_care":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                if "enabled" in body:
                    proactive_care.set_enabled(body["enabled"])
                elif body.get("action") == "toggle":
                    proactive_care.toggle()
                elif body.get("action") == "snooze":
                    proactive_care.snooze(body.get("minutes", 120))
                elif body.get("action") == "unsnooze":
                    proactive_care.unsnooze()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(proactive_care.get_status(), ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(400, str(e))
            return

        # 手动立即触发主动关怀测试
        if self.path == "/api/proactive_care/test":
            content_length = int(self.headers.get("Content-Length", 0))
            scene = "morning"
            if content_length > 0:
                try:
                    post_data = self.rfile.read(content_length)
                    body = json.loads(post_data.decode("utf-8"))
                    scene = body.get("scene", "morning")
                except Exception:
                    pass
            payload = proactive_care.trigger_test(scene=scene)
            hardware_agent.trigger_proactive_care_payload(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
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
            base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1")
            model = cfg.get("llm_model", "mimo-v2.6-flash")
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
            base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
            model = cfg.get("llm_model", "mimo-v2.6-flash")
            max_len = cfg.get("max_reply_length", 200)

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
                max_steps=4,
                enable_thinking=cfg.get("llm_thinking", False)
            )

            from concurrent.futures import ThreadPoolExecutor
            tts_executor = ThreadPoolExecutor(max_workers=3)
            audio_futures = []

            def _drain_ready_audio(force_all=False):
                nonlocal audio_futures
                while audio_futures:
                    if not force_all and not audio_futures[0][2].done():
                        break
                    a_seq, a_txt, a_fut = audio_futures.pop(0)
                    try:
                        audio_b = a_fut.result(timeout=6.0 if force_all else 0.02)
                        if audio_b:
                            b64 = base64.b64encode(audio_b).decode("utf-8")
                            a_payload = {
                                "type": "audio_chunk",
                                "seq": a_seq,
                                "text": a_txt,
                                "audio": b64
                            }
                            self.wfile.write(f"data: {json.dumps(a_payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                            self.wfile.flush()
                    except Exception as ex:
                        print(f"⚠️ [WebSim] 音频切片合成异常: {ex}")

            seq = 0
            print(f"👉 [Stream Start] User text: {user_text}", flush=True)
            try:
                for event in reasoner.run_react_loop_stream(
                    user_text=user_text,
                    base_sys_prompt=sys_prompt,
                    memory_manager=mem,
                    skill_manager=skill_mgr,
                    evolution_engine=evolution_engine
                ):
                    ev_type = event.get("type")
                    print(f"👉 [Stream Ev] {ev_type}", flush=True)
                    if ev_type == "sentence":
                        sentence_text = event.get("text", "").strip()
                        if sentence_text:
                            seq += 1
                            # 1. 立即将文本切片推至前端渲染（零延时上屏）
                            payload = {
                                "type": "chunk",
                                "seq": seq,
                                "text": sentence_text,
                                "audio": ""
                            }
                            msg = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()

                            # 2. 提交后台线程池并发合成音频
                            fut = tts_executor.submit(
                                lambda txt: asyncio.run(tts_streamer.synthesize_chunk(txt)),
                                sentence_text
                            )
                            audio_futures.append((seq, sentence_text, fut))
                            _drain_ready_audio(force_all=False)

                    elif ev_type in ["tool_start", "observation", "thought"]:
                        msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                        _drain_ready_audio(force_all=False)

                    elif ev_type == "done":
                        # 在发送 done 前排空并送达所有已合成的音频切片
                        _drain_ready_audio(force_all=True)
                        msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                        self.close_connection = True

            except Exception as e:
                err_ev = {"type": "error", "message": str(e)}
                try:
                    self.wfile.write(f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
            finally:
                _drain_ready_audio(force_all=True)
                tts_executor.shutdown(wait=False)
                self.close_connection = True
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
            base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
            model = cfg.get("llm_model", "mimo-v2.6-flash")
            max_len = cfg.get("max_reply_length", 200)

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
                        max_steps=4,
                        enable_thinking=cfg.get("llm_thinking", False)
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
                    if react_out.get("visual_snapshot"):
                        reply_data["visual_snapshot"] = react_out["visual_snapshot"]
                    elif any(s.get("action") == "visual_qa" for s in reply_data["steps"]):
                        reply_data["visual_snapshot"] = f"/api/vision/snapshot?t={int(time.time()*1000)}"
                    if reply_data["behaviors"] and hardware_agent and hasattr(hardware_agent, "mark_action_playing"):
                        hardware_agent.mark_action_playing(3.0)
                except Exception as e:
                    print(f"⚠️ [Web-LLM ReAct Loop Error]: {e}")
                    reply_data["reply"] = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(reply_data, ensure_ascii=False).encode("utf-8"))
            return

        return super().do_POST()

class ReusableThreadingServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    allow_reuse_port = True
    daemon_threads = True

def run_server():
    with ReusableThreadingServer(("", PORT), CustomHandler) as httpd:
        print("="*60)
        print("🚀 [PIG 3D 仿真工作台已就绪]")
        print(f"👉 本地访问地址: http://localhost:{PORT}")
        print("✨ 包含: 3D WebGL 机械实体视角 + 2.4寸动态眼球屏 + PIG 动作交互")
        print("="*60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 仿真服务器已关闭")

if __name__ == "__main__":
    run_server()
