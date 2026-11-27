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
from core.wifi_manager import wifi_manager

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
_latest_webcam_frame_ts = 0.0

def get_latest_vision_frame(max_age_sec: float = 12.0) -> bytes:
    """
    获取最新视觉帧字节流。
    严格执行 TTL 时效性校验（默认 12.0 秒），一旦过期立即丢弃，绝对不用历史旧缓存图伪装当前实时抓拍！
    """
    global _latest_webcam_frame, _latest_webcam_frame_ts
    now = time.time()

    # 1. 检查内存中 Web 实时推流帧
    if _latest_webcam_frame:
        if now - _latest_webcam_frame_ts <= max_age_sec:
            if len(_latest_webcam_frame) > 100:
                return _latest_webcam_frame
        else:
            # 超过 TTL 主动清理内存帧，防止任何地方直接误用旧图
            _latest_webcam_frame = None
            _latest_webcam_frame_ts = 0.0

    # 2. 检查临时桥接文件 (/tmp/pig_latest_frame.jpg)
    tmp_path = "/tmp/pig_latest_frame.jpg"
    if os.path.exists(tmp_path):
        try:
            mtime = os.path.getmtime(tmp_path)
            if now - mtime <= max_age_sec:
                with open(tmp_path, "rb") as f:
                    c = f.read()
                    if len(c) > 100:
                        return c
            else:
                # 超过 max_age_sec 秒的旧文件已失效，立即主动清理，防止后续任何逻辑误读
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            pass

    # 3. 检查硬件 HAL（物理真机摄像头）
    if hardware_agent and hardware_agent.hal:
        try:
            from drivers.hal_physical import PhysicalHAL
            if isinstance(hardware_agent.hal, PhysicalHAL):
                img = hardware_agent.hal.capture_image()
                if img and len(img) > 100:
                    return img
        except Exception:
            pass

    return b""

def load_config():
    cfg_file = os.path.join(DIRECTORY, "config.json")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def apply_runtime_config(cur_cfg: dict, updates: dict):
    """统一将设置页 (/settings) 与 3D 控制台保存的配置热同步至内存、舵机、菜单与 SSE 客户端"""
    # 别名字段统一对齐
    if "api_key" in updates:
        cur_cfg["mimo_api_key"] = cur_cfg["api_key"]
    elif "mimo_api_key" in updates:
        cur_cfg["api_key"] = cur_cfg["mimo_api_key"]

    if "mimo_voice" in updates:
        cur_cfg["tts_voice"] = cur_cfg["mimo_voice"]
    elif "tts_voice" in updates:
        cur_cfg["mimo_voice"] = cur_cfg["tts_voice"]

    if "robot_name" in updates:
        cur_cfg["bot_name"] = cur_cfg["robot_name"]
    elif "bot_name" in updates:
        cur_cfg["robot_name"] = cur_cfg["bot_name"]

    if "wakeword_keywords" in updates:
        from drivers.wakeword_engine import parse_keywords_list
        cur_cfg["wakeword_keywords"] = parse_keywords_list(updates["wakeword_keywords"])

    # 持久化至 config.json
    cfg_file = os.path.join(DIRECTORY, "config.json")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cur_cfg, f, ensure_ascii=False, indent=2)

    # 同步长期记忆称谓
    if "user_name" in updates and mem:
        mem.set_user_name(cur_cfg.get("user_name", ""))
    if ("bot_name" in updates or "robot_name" in updates) and mem:
        mem.set_bot_name(cur_cfg.get("bot_name") or cur_cfg.get("robot_name", ""))

    # 同步全局配置与 HardwareAgent
    server_cfg.clear()
    server_cfg.update(cur_cfg)
    if hardware_agent:
        hardware_agent.cfg.update(cur_cfg)
        if getattr(hardware_agent, "tts_engine", None) and hasattr(hardware_agent.tts_engine, "update_config"):
            hardware_agent.tts_engine.update_config(cur_cfg)
        if any(k in updates for k in ["wakeword_keywords", "wakeword_engine", "wakeword_model", "wakeword_threshold"]) and hasattr(hardware_agent, "reload_wakeword"):
            hardware_agent.reload_wakeword()

    # 视觉注视开关热同步
    if "face_tracking" in updates:
        ft_on = bool(updates["face_tracking"])
        face_tracker.set_enabled(ft_on)
        if hardware_agent:
            hardware_agent.set_face_tracking(ft_on)
        if menu_fsm:
            menu_fsm.face_tracking_enabled = ft_on

    if "proactive_care" in updates and proactive_care:
        proactive_care.set_enabled(bool(updates["proactive_care"]))

    # 麦克风静音 / 舵机休眠 / 零点偏置同步至 HAL 与 3D 仿真视窗
    if hardware_agent and hardware_agent.hal and hasattr(hardware_agent.hal, "update_servo_config"):
        if any(k in updates for k in ["pan_trim", "tilt_trim", "pan_invert", "tilt_invert", "pan_min", "pan_max", "tilt_min", "tilt_max", "servo_speed"]):
            hardware_agent.hal.update_servo_config(cur_cfg)

    if menu_fsm:
        if "is_mic_muted" in updates:
            menu_fsm.is_mic_muted = bool(updates["is_mic_muted"])
        if "servo_sleeping" in updates:
            sleep_on = bool(updates["servo_sleeping"])
            menu_fsm.is_servo_sleeping = sleep_on
            menu_fsm._apply_servo_sleep(sleep_on)
            if hardware_agent:
                pan_now = getattr(hardware_agent.hal, "cur_pan", 90.0)
                tilt_now = getattr(hardware_agent.hal, "cur_tilt", 120.0 if sleep_on else 90.0)
                hardware_agent.emit_event("pose_sync", {
                    "pan": pan_now,
                    "tilt": tilt_now,
                    "emotion": "SLEEPY" if sleep_on else "NORMAL"
                })
        menu_fsm._sync_hardware_items()
        menu_fsm._sync_system_items()
        menu_fsm._sync_skill_items()
        menu_fsm._notify_listeners()

    if hardware_agent:
        hardware_agent.emit_event("config_updated", {"updates": list(updates.keys())})
    if skill_mgr:
        skill_mgr.emit_hook("on_config_update", cfg=cur_cfg, updates=updates, ctx=_get_app_ctx())

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
    try:
        if "menu_fsm" in globals() and menu_fsm and hasattr(menu_fsm, "trigger_alarm_overlay"):
            menu_fsm.trigger_alarm_overlay(rem)
    except Exception:
        pass
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
    if isinstance(event_dict, dict) and event_dict.get("type") == "pose_sync" and event_dict.get("emotion"):
        if hardware_agent is not None:
            hardware_agent.current_emotion = str(event_dict["emotion"]).upper()
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
hardware_agent.current_emotion = "NORMAL"
_sync_behavior_seq = 0

def _sync_hardware_behaviors(behaviors, default_emotion="HAPPY", hold_sec=6.0):
    """将网页/飞书对话生成的表情与舵机动作序列同步到真机屏幕和舵机"""
    global _sync_behavior_seq
    if not hardware_agent:
        return
    _sync_behavior_seq += 1
    my_seq = _sync_behavior_seq
    seq = list(behaviors or [])
    if not seq and default_emotion:
        seq = [{"emotion": default_emotion, "pan": 90, "tilt": 85, "delay": 1000}]

    def _runner():
        try:
            total_hold = max(float(hold_sec), len(seq) * 1.0)
            if hasattr(hardware_agent, "mark_action_playing"):
                hardware_agent.mark_action_playing(total_hold + 1.0)
            last_emo = default_emotion or "NORMAL"
            elapsed = 0.0
            for step in seq:
                if _sync_behavior_seq != my_seq:
                    return
                emo = str(step.get("emotion") or last_emo or "NORMAL").upper()
                if emo != "NORMAL":
                    last_emo = emo
                hardware_agent.current_emotion = emo
                pan = step.get("pan")
                tilt = step.get("tilt")
                if pan is not None and tilt is not None and hardware_agent.hal:
                    try:
                        hardware_agent.hal.set_angles(float(pan), float(tilt), smooth=True)
                    except Exception:
                        pass
                dt = max(0.3, min(int(step.get("delay") or 1000) / 1000.0, 5.0))
                time.sleep(dt)
                elapsed += dt
            # 若动作序列很短，继续保持主表情到 hold_sec，避免真机一闪而过
            if _sync_behavior_seq == my_seq and last_emo != "NORMAL" and elapsed < total_hold:
                hardware_agent.current_emotion = last_emo
                remain = total_hold - elapsed
                while remain > 0 and _sync_behavior_seq == my_seq:
                    step_t = min(0.5, remain)
                    time.sleep(step_t)
                    remain -= step_t
            if _sync_behavior_seq != my_seq:
                return
            hardware_agent.current_emotion = "NORMAL"
            if hardware_agent.hal:
                try:
                    hardware_agent.hal.set_angles(90, 90, smooth=True)
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ [PhysicalSync] 同步动作表情异常: {e}")

    threading.Thread(target=_runner, daemon=True).start()

# 统一 4 键菜单状态机 (MenuFSM)
from core.menu_fsm import menu_fsm
menu_fsm.hal = hardware_agent.hal
menu_fsm.agent = hardware_agent

def _get_app_ctx():
    return {
        "load_config": load_config,
        "apply_runtime_config": apply_runtime_config,
        "skill_manager": skill_mgr,
        "memory_manager": mem,
        "evolution_engine": evolution_engine,
        "hardware_agent": hardware_agent,
        "menu_fsm": menu_fsm,
        "data_dir": DATA_DIR,
    }

if skill_mgr:
    skill_mgr.emit_hook("on_startup", ctx=_get_app_ctx())

def _sim_register_master_face():
    global _latest_webcam_frame
    frame = None
    if _latest_webcam_frame:
        import cv2, numpy as np
        nparr = np.frombuffer(_latest_webcam_frame, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return face_tracker.register_master_face(frame)

if skill_mgr:
    skill_mgr.register_face_callback = _sim_register_master_face
if hardware_agent:
    hardware_agent.register_master_face = _sim_register_master_face

def on_menu_state_broadcast(state_dict):
    on_agent_event_broadcast({
        "type": "menu_state",
        "data": state_dict
    })

menu_fsm.add_listener(on_menu_state_broadcast)
if hardware_agent and hardware_agent.hal:
    hardware_agent.hal.add_button_listener(menu_fsm.handle_key)

    # 📡 联动 HLK-LD2402 毫米波人体雷达：人来极速唤醒、人走自动休眠
    def _on_radar_event_link(detected: bool):
        idle_fsm.notify_radar(detected)
        menu_fsm.handle_radar(detected)
        on_agent_event_broadcast({
            "type": "radar_presence",
            "detected": detected,
            "message": "📡 毫米波雷达：检测到人体在场" if detected else "📡 毫米波雷达：检测到人已离席"
        })

    hardware_agent.add_radar_listener(_on_radar_event_link)

    # 📺 真机 ST7789 物理屏幕极速渲染循环 (目标 30 FPS + 脏帧去重)
    if getattr(hardware_agent.hal, "screen", None) is not None:
        def _physical_screen_render_loop():
            from PIL import Image
            from drivers.eye_renderer import EyeRenderer
            renderer = EyeRenderer()
            last_black = False
            last_frame_bytes = None
            target_interval = 1.0 / 30.0
            print("📺 [PhysicalScreen] 物理屏幕极速渲染线程已启动 (240x320 RGB565, 目标 30 FPS)")
            while True:
                t0 = time.monotonic()
                redrawn = False
                try:
                    if getattr(menu_fsm, "_shutting_down", False):
                        return
                    if getattr(menu_fsm, "is_screen_off", False):
                        if not last_black:
                            hardware_agent.hal.display_face(Image.new("RGB", (240, 320), (0, 0, 0)))
                            last_black = True
                            last_frame_bytes = None
                        time.sleep(0.15)
                        continue
                    last_black = False
                    if getattr(menu_fsm, "current_mode", "FACE") == "FACE":
                        emo = getattr(hardware_agent, "current_emotion", "NORMAL") or "NORMAL"
                        frame = renderer.render_eyes(emotion=emo)
                    else:
                        frame = Image.new("RGB", (240, 320), (0, 0, 0))
                    frame = menu_fsm.render_pil_overlay(frame)
                    frame_bytes = frame.tobytes()
                    if frame_bytes != last_frame_bytes:
                        hardware_agent.hal.display_face(frame)
                        last_frame_bytes = frame_bytes
                        redrawn = True
                except Exception as e:
                    print(f"⚠️ [PhysicalScreen] 渲染异常: {e}")
                elapsed = time.monotonic() - t0
                sleep_dur = max(0.002, target_interval - elapsed) if redrawn else 0.025
                time.sleep(sleep_dur)

        threading.Thread(target=_physical_screen_render_loop, daemon=True).start()



class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # 静默所有高频 HTTP 访问日志，避免控制台与后台任务缓冲区溢出导致容器杀进程
        return

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            return json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            return {}

    def _send_json(self, payload, status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_HEAD(self):
        if self.path in ["/settings", "/settings.html", "/skills", "/skills.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        if self.path.startswith("/api/menu/qr_image"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        if self.path == "/api/menu/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
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

        # 手机端无线配置后台 (/settings) 与 独立技能配置后台 (/skills)
        if self.path in ["/settings", "/settings.html", "/skills", "/skills.html"]:
            is_skills = self.path in ["/skills", "/skills.html"]
            page_name = "skills.html" if is_skills else "settings.html"
            page_path = os.path.join(DIRECTORY, "simulator", page_name)
            if os.path.exists(page_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(page_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, f"{page_name} not found")
                return

        if skill_mgr and skill_mgr.dispatch_http("GET", self.path, self, _get_app_ctx()):
            return

        # 获取配置及状态 API
        if self.path == "/api/settings":
            cfg_file = os.path.join(DIRECTORY, "config.json")
            cur_cfg = {}
            if os.path.exists(cfg_file):
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        cur_cfg = json.load(f)
                except Exception:
                    pass
            state = menu_fsm.get_state()
            response_data = {
                "api_key": cur_cfg.get("api_key", ""),
                "llm_model": cur_cfg.get("llm_model", "mimo-v2.6-flash"),
                "robot_name": cur_cfg.get("bot_name") or cur_cfg.get("robot_name", "PIG"),
                "user_name": cur_cfg.get("user_name", "胖虎"),
                "mimo_voice": cur_cfg.get("mimo_voice", "冰糖"),
                "wakeword_keywords": cur_cfg.get("wakeword_keywords", ["你好小猪", "小猪小猪", "小猪同学"]),
                "face_tracking": hardware_agent.cfg.get("face_tracking", True),
                "is_mic_muted": menu_fsm.is_mic_muted,
                "servo_sleeping": menu_fsm.is_servo_sleeping,
                "cur_pan": getattr(hardware_agent.hal, "cur_pan", 90.0) if (hardware_agent and hardware_agent.hal) else 90.0,
                "cur_tilt": getattr(hardware_agent.hal, "cur_tilt", 90.0) if (hardware_agent and hardware_agent.hal) else 90.0,
                "pan_trim": cur_cfg.get("pan_trim", 0.0),
                "tilt_trim": cur_cfg.get("tilt_trim", 0.0),
                "local_ip": state["local_ip"],
                "cpu_temp": state["doctor"].get("cpu_temp", "45°C"),
                "mimo_status": state["doctor"].get("mimo_status", "正常 (OK)"),
                "ota_url": cur_cfg.get("ota_url", ""),
                "firmware_version": getattr(menu_fsm, "version_str", "v0.1.0"),
                "update_status": getattr(menu_fsm, "update_status", "已是最新")
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode("utf-8"))
            return

        # 五维性格与亲密度状态查询 API
        if self.path == "/api/evolution/personality":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            p_data = evolution_engine.get_personality() if evolution_engine else {}
            self.wfile.write(json.dumps(p_data, ensure_ascii=False).encode("utf-8"))
            return

        # 菜单状态查询 API
        if self.path == "/api/menu/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(menu_fsm.get_state(), ensure_ascii=False).encode("utf-8"))
            return

        # 动态二维码图片直通流 (供 2.4寸 Canvas 与真机同构绘制)
        if self.path.startswith("/api/menu/qr_image"):
            try:
                img_bytes = menu_fsm.get_qr_png_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img_bytes)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(img_bytes)
                return
            except Exception as e:
                self.send_error(500, f"QR generation failed: {e}")
                return

        # 一键体检与排障 API
        if self.path == "/api/system/doctor":
            menu_fsm.refresh_doctor_data_async()
            time.sleep(0.12)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(menu_fsm.doctor_cache, ensure_ascii=False).encode("utf-8"))
            return

        # Wi-Fi 状态查询 API
        if self.path == "/api/wifi/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(wifi_manager.get_status(), ensure_ascii=False).encode("utf-8"))
            return

        # Wi-Fi 周边扫描 API
        if self.path == "/api/wifi/scan":
            nets = wifi_manager.scan_networks()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"networks": nets}, ensure_ascii=False).encode("utf-8"))
            return

        # 视觉抓拍快照静态访问接口 (供对话框实时查看小猪眼前捕获的真实现实瞬间)
        if self.path.startswith("/api/vision/snapshot") or self.path.startswith("/api/vision/latest_snapshot"):
            snap_path = "/tmp/pig_latest_snapshot.jpg"
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

        # 获取毫米波雷达状态 (HLK-LD2402 24GHz FMCW)
        if self.path in ["/api/radar", "/api/radar/status"]:
            is_detected = True
            if hardware_agent and hasattr(hardware_agent.hal, "is_radar_detected"):
                is_detected = hardware_agent.hal.is_radar_detected()
            elif hardware_agent:
                is_detected = getattr(hardware_agent, "radar_detected", True)

            cfg = menu_fsm._load_cfg()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "detected": is_detected,
                "sensor_model": "HLK-LD2402 24GHz FMCW",
                "pin_gpio": 26,
                "pin_physical": 37,
                "enabled": cfg.get("radar_presence_wake", True),
                "away_sleep_timeout_sec": idle_fsm.away_sleep_timeout_sec
            }, ensure_ascii=False).encode("utf-8"))
            return


        if self.path in ["/api/stop", "/api/barge_in"]:
            if hardware_agent:
                hardware_agent.stop_speaking()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "barge_in_triggered"}, ensure_ascii=False).encode("utf-8"))
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

        # 获取全部 18 款动态 SVG 清单
        if self.path == "/api/svg_list":
            emotions = [
                "NORMAL", "HAPPY", "LAUGH", "EXCITED", "LOVE", "WINK",
                "SHY", "PROUD", "CURIOUS", "CONFUSED", "THINKING", "SURPRISED",
                "COZY", "SLEEPY", "DIZZY", "SAD", "ANGRY", "BLINK"
            ]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "emotions": emotions}, ensure_ascii=False).encode("utf-8"))
            return

        # 获取技能商店列表与已安装技能 (支持 ?refresh=1 强制从 Gitee 重新拉取)
        if self.path.startswith("/api/skills/store"):
            if "refresh=1" in self.path:
                skill_mgr.fetch_online_registry(force_refresh=True)
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

        # 元数据驱动：统一获取全部带 config_schema 的技能配置与实时状态
        if self.path == "/api/skills/configs":
            cfg = load_config()
            self._send_json({
                "status": "ok",
                "skills": skill_mgr.list_configurable_skills(cfg),
            })
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
                mimo_key = cfg.get("mimo_api_key", "") or cfg.get("api_key", "")
                chosen_voice = cfg.get("mimo_voice") or cfg.get("tts_voice") or cfg.get("edge_voice") or "冰糖"

                from drivers.tts_engine import is_edge_voice
                # 若启用小米 MiMo 语音模型且当前音色非 Edge 音色
                if mode == "mimo" and mimo_key and not is_edge_voice(chosen_voice):
                    from drivers.tts_engine import TTSEngine
                    clone_audio = cfg.get("mimo_clone_audio", "") if cfg.get("mimo_clone_enabled", False) else ""
                    sim_tts = TTSEngine(
                        mode="mimo",
                        mimo_api_key=mimo_key,
                        mimo_voice=chosen_voice,
                        default_voice=cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural"),
                        mimo_clone_audio=clone_audio,
                        mimo_design_prompt=cfg.get("mimo_design_prompt", "")
                    )
                    tmp_out = f"/tmp/web_mimo_tts_{threading.get_ident()}.mp3"
                    out_path, _ = sim_tts.synthesize(text, out_file=tmp_out)
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

                edge_voice = chosen_voice if is_edge_voice(chosen_voice) else (cfg.get("edge_voice") or "zh-CN-XiaoxiaoNeural")

                async def _gen():
                    comm = edge_tts.Communicate(text, edge_voice)
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
        global _latest_webcam_frame, _latest_webcam_frame_ts, _sync_behavior_seq

        if skill_mgr and skill_mgr.dispatch_http("POST", self.path, self, _get_app_ctx()):
            return

        # 网页直接同步真机屏幕表情
        if self.path == "/api/hardware/emotion":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
            emo = str(body.get("emotion") or "NORMAL").upper()
            if hardware_agent is not None:
                _sync_behavior_seq += 1
                hardware_agent.current_emotion = emo
            self._send_json({"status": "ok", "emotion": emo})
            return

        # 手机端无线配置保存 API
        if self.path == "/api/settings":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                cfg_file = os.path.join(DIRECTORY, "config.json")
                if os.path.exists(cfg_file):
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        cur_cfg = json.load(f)
                else:
                    cur_cfg = {}
                for k in ["api_key", "llm_model", "robot_name", "bot_name", "user_name", "mimo_voice", "pan_trim", "tilt_trim", "wakeword_keywords", "ota_url"]:
                    if k in body:
                        cur_cfg[k] = body[k]
                if "face_tracking" in body:
                    cur_cfg["face_tracking"] = bool(body["face_tracking"])

                apply_runtime_config(cur_cfg, body)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "state": menu_fsm.get_state()}).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 4按键实体/虚拟事件分发 API (K1~K4)
        if self.path == "/api/hardware/button":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                btn = body.get("button", "K3")
                evt = body.get("event", "click")
                seq = body.get("seq", 0)
                if hardware_agent and hardware_agent.hal:
                    hardware_agent.hal.trigger_button(btn, evt)
                else:
                    menu_fsm.handle_key(btn, evt)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "seq": seq, "menu_state": menu_fsm.get_state()}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 舵机实时角度微调/校准
        if self.path == "/api/servo/move":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                pan = float(body.get("pan", 90))
                tilt = float(body.get("tilt", 90))
                if hardware_agent and hardware_agent.hal:
                    hardware_agent.hal.set_angles(pan, tilt, smooth=True)
                    actual_pan = getattr(hardware_agent.hal, "cur_pan", pan)
                    actual_tilt = getattr(hardware_agent.hal, "cur_tilt", tilt)
                    hardware_agent.emit_event("pose_sync", {"pan": actual_pan, "tilt": actual_tilt})
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "pan": pan, "tilt": tilt}).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

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

        # 检查系统更新 (真实查询 Git Remote Tags，零 mock)
        if self.path == "/api/system/update_check":
            from core.version import version_manager
            res = version_manager.check_update_sync()
            menu_fsm.update_status = res.get("status", "已是最新版本")
            menu_fsm._sync_system_items()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        # 执行 OTA 固件更新
        if self.path == "/api/system/update_perform":
            from core.version import version_manager
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
            target_tag = body.get("tag")
            res = version_manager.perform_ota_update(target_tag)
            menu_fsm.version_str = version_manager.current_version
            menu_fsm.update_status = res.get("message", "更新完成")
            menu_fsm._sync_system_items()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        # 重启机器人服务
        if self.path == "/api/system/restart":
            menu_fsm._execute_power_action("restart")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "restarting"}, ensure_ascii=False).encode("utf-8"))
            return

        # 安全关闭系统
        if self.path == "/api/system/shutdown":
            menu_fsm._execute_power_action("shutdown")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "shutting_down"}, ensure_ascii=False).encode("utf-8"))
            return

        # Wi-Fi 配网连接
        if self.path == "/api/wifi/connect":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                ssid = body.get("ssid", "")
                pwd = body.get("password", "")
                res = wifi_manager.connect(ssid, pwd)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # Wi-Fi 开启/关闭 SoftAP 配网热点
        if self.path == "/api/wifi/ap_mode":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                enable = body.get("enable", True)
                res = wifi_manager.start_softap() if enable else wifi_manager.stop_softap()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 直接在屏幕展开关于 PIG 介绍彩蛋 (前后端全同步)
        if self.path in ["/api/menu/easter_egg", "/api/menu/enter_easter_egg"]:
            try:
                menu_fsm.enter_easter_egg()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "state": menu_fsm.get_state()}, ensure_ascii=False).encode("utf-8"))
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

        # 毫米波人体雷达仿真触发接口 (POST: {"detected": true/false})
        if self.path in ["/api/radar", "/api/radar/trigger"]:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
            except Exception:
                body = {}
            detected = bool(body.get("detected", True))
            if hardware_agent and hasattr(hardware_agent.hal, "trigger_radar"):
                hardware_agent.hal.trigger_radar(detected)
            elif hardware_agent:
                hardware_agent._on_radar_event(detected)
            idle_fsm.notify_radar(detected)
            menu_fsm.handle_radar(detected)


            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "detected": detected,
                "message": f"毫米波雷达状态已切换为: {'有人在场 (0.1s极速中断唤醒)' if detected else '无人离席 (启动自动低功耗休眠倒计时)'}"
            }, ensure_ascii=False).encode("utf-8"))
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

                apply_runtime_config(cfg, updates)

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
                _latest_webcam_frame_ts = time.time()
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
                _latest_webcam_frame_ts = time.time()
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
                    res["identity"] = getattr(face_tracker, "last_identity", "UNREGISTERED")
                    res["similarity"] = getattr(face_tracker, "last_similarity", 0.0)

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

        # 📸 录入当前人脸为唯一主人 (SFace 128-d Feature Embedding)
        if self.path == "/api/vision/register_master_face":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
                req_json = json.loads(post_data.decode("utf-8")) if post_data else {}
                img_data = req_json.get("image", "")
                frame = None
                if img_data:
                    if "," in img_data:
                        img_data = img_data.split(",", 1)[1]
                    img_bytes = base64.b64decode(img_data)
                    import cv2, numpy as np
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                reg_res = face_tracker.register_master_face(frame)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(reg_res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 🌱 五维性格微调与交互演化驱动
        if self.path == "/api/evolution/drift":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length)
                req_json = json.loads(post_data.decode("utf-8")) if post_data else {}
                evt = req_json.get("event", "interaction")
                delta = req_json.get("delta", {})
                note = req_json.get("note", "")
                res = evolution_engine.drift_personality(evt, delta, note) if evolution_engine else {}
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
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

        # 闹钟稍后再响 (贪睡延后)
        if self.path == "/api/reminders/snooze":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                rid = body.get("id", "") or ""
                minutes = float(body.get("minutes", 5) or 5)
                content = body.get("content", "") or ""
                rem = skill_mgr.snooze_reminder(reminder_id=rid, minutes=minutes, content=content)
                if "menu_fsm" in globals() and menu_fsm and hasattr(menu_fsm, "dismiss_alarm_overlay"):
                    menu_fsm.dismiss_alarm_overlay()
                if rem:
                    self._send_json({"status": "ok", "reminder": rem})
                else:
                    self._send_json({"status": "error", "msg": "未找到可延后的闹钟"}, status_code=404)
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

        # 元数据驱动：保存任意技能插件的独立配置并热同步
        if self.path == "/api/skills/configs":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            sid = (req.get("skill_id") or "").strip()
            values = req.get("values") if isinstance(req.get("values"), dict) else {}
            if not sid:
                self._send_json({"status": "error", "message": "缺少 skill_id"}, status_code=400)
                return

            hook_res = skill_mgr.invoke_plugin_hook(sid, "on_config_save", values=values, ctx=_get_app_ctx()) if skill_mgr else None
            if isinstance(hook_res, dict):
                self._send_json(hook_res)
                return

            cur_cfg = load_config()
            plugin = skill_mgr.installed_plugins.get(sid, {})
            schema = (plugin.get("meta") or {}).get("config_schema") or {}
            section = schema.get("config_section") or f"skills_config.{sid}"

            if section == "root":
                cur_cfg.update(values)
                updates = dict(values)
            elif section.startswith("skills_config."):
                sub_key = section.split(".", 1)[1]
                cur_cfg.setdefault("skills_config", {}).setdefault(sub_key, {}).update(values)
                updates = {"skills_config": cur_cfg["skills_config"]}
            elif section == "none":
                updates = {}
            else:
                cur_cfg.setdefault(section, {}).update(values)
                updates = {section: cur_cfg[section]}

            apply_runtime_config(cur_cfg, updates)
            self._send_json({"status": "ok", "skill_id": sid, "state": menu_fsm.get_state()})
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

            # 🌱 动态性格与亲密度演化驱动 (根据用户对话情感自然演进)
            if evolution_engine and user_text:
                try:
                    delta = {"curiosity": 1}
                    note = "日常语音交互"
                    if any(w in user_text for w in ["真棒", "喜欢", "可爱", "厉害", "谢谢", "好乖", "爱你", "亲亲"]):
                        delta = {"lively": 2, "clingy": 2}
                        note = "收到主人的夸奖与鼓励"
                    elif any(w in user_text for w in ["累", "难过", "困", "生病", "烦", "痛", "不开心", "失眠"]):
                        delta = {"empathy": 3, "clingy": 1}
                        note = "主人倾诉疲惫与低落心事"
                    elif any(w in user_text for w in ["笨", "傻", "调皮", "别装", "切", "哈哈", "逗你", "搞快点"]):
                        delta = {"sharp_tongued": 2, "lively": 1}
                        note = "主仆互怼调侃"
                    evolution_engine.drift_personality("chat", delta, note)
                except Exception:
                    pass

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

            from core.streaming_pipeline import get_tts_streamer, stream_sentence_to_queue
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
            audio_order_q = queue.Queue()
            sse_lock = threading.Lock()
            audio_seq = 0
            audio_stopped = False

            def _emit_sse(obj: dict):
                with sse_lock:
                    self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()

            def _audio_stream_worker():
                nonlocal audio_seq
                while True:
                    item = audio_order_q.get()
                    try:
                        if item is None:
                            break
                        a_txt, sub_q = item
                        sub_idx = 0
                        while True:
                            tag, _, wire_b = sub_q.get()
                            if tag == "done":
                                break
                            if wire_b:
                                audio_seq += 1
                                _emit_sse({
                                    "type": "audio_chunk",
                                    "seq": audio_seq,
                                    "text": a_txt if sub_idx == 0 else "",
                                    "audio": base64.b64encode(wire_b).decode("utf-8"),
                                })
                                sub_idx += 1
                    except Exception as ex:
                        print(f"⚠️ [WebSim] 音频切片流式推送异常: {ex}")
                    finally:
                        audio_order_q.task_done()

            audio_thread = threading.Thread(target=_audio_stream_worker, daemon=True, name="WebSSEAudio")
            audio_thread.start()

            print(f"👉 [Stream Start] User text: {user_text}", flush=True)
            if hardware_agent is not None:
                _sync_behavior_seq += 1
                hardware_agent.current_emotion = "THINKING"
            stream_reply_len = 0
            synced_behaviors = None
            saw_text_delta = False
            try:
                for event in reasoner.run_react_loop_stream(
                    user_text=user_text,
                    base_sys_prompt=sys_prompt,
                    memory_manager=mem,
                    skill_manager=skill_mgr,
                    evolution_engine=evolution_engine
                ):
                    ev_type = event.get("type")
                    if ev_type != "text_delta":
                        print(f"👉 [Stream Ev] {ev_type}", flush=True)
                    if ev_type == "behaviors":
                        synced_behaviors = event.get("behaviors")
                        _sync_hardware_behaviors(synced_behaviors, default_emotion="NORMAL", hold_sec=12.0)
                        _emit_sse(event)
                    elif ev_type == "text_delta":
                        delta_txt = event.get("text", "")
                        if delta_txt:
                            saw_text_delta = True
                            stream_reply_len += len(delta_txt)
                            _emit_sse({
                                "type": "chunk",
                                "seq": 0,
                                "text": delta_txt,
                                "audio": ""
                            })
                    elif ev_type == "sentence":
                        sentence_text = event.get("text", "").strip()
                        if sentence_text:
                            if not saw_text_delta:
                                stream_reply_len += len(sentence_text)
                                _emit_sse({
                                    "type": "chunk",
                                    "seq": 0,
                                    "text": sentence_text,
                                    "audio": ""
                                })

                            sub_q = stream_sentence_to_queue(tts_executor, tts_streamer, sentence_text)
                            audio_order_q.put((sentence_text, sub_q))

                    elif ev_type in ["tool_start", "observation", "thought"]:
                        _emit_sse(event)

                    elif ev_type == "done":
                        # 同步真机屏幕表情与舵机动作（若流中未前置播放或工具覆盖了新动作，再触发）
                        done_bh = event.get("behaviors")
                        if done_bh != synced_behaviors:
                            hold_s = max(6.0, min(stream_reply_len * 0.25, 12.0))
                            _sync_hardware_behaviors(done_bh, default_emotion="NORMAL", hold_sec=hold_s)
                        if not audio_stopped:
                            audio_order_q.put(None)
                            audio_stopped = True
                        audio_thread.join(timeout=20.0)
                        _emit_sse(event)
                        self.close_connection = True

            except Exception as e:
                try:
                    _emit_sse({"type": "error", "message": str(e)})
                except Exception:
                    pass
            finally:
                if not audio_stopped:
                    audio_order_q.put(None)
                    audio_stopped = True
                audio_thread.join(timeout=3.0)
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

            # 🌱 动态性格与亲密度演化驱动 (根据用户对话情感自然演进)
            if evolution_engine and user_text:
                try:
                    delta = {"curiosity": 1}
                    note = "日常语音交互"
                    if any(w in user_text for w in ["真棒", "喜欢", "可爱", "厉害", "谢谢", "好乖", "爱你", "亲亲"]):
                        delta = {"lively": 2, "clingy": 2}
                        note = "收到主人的夸奖与鼓励"
                    elif any(w in user_text for w in ["累", "难过", "困", "生病", "烦", "痛", "不开心", "失眠"]):
                        delta = {"empathy": 3, "clingy": 1}
                        note = "主人倾诉疲惫与低落心事"
                    elif any(w in user_text for w in ["笨", "傻", "调皮", "别装", "切", "哈哈", "逗你", "搞快点"]):
                        delta = {"sharp_tongued": 2, "lively": 1}
                        note = "主仆互怼调侃"
                    evolution_engine.drift_personality("chat", delta, note)
                except Exception:
                    pass

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
                    hold_s = max(6.0, min(len(str(reply_data.get("reply") or "")) * 0.25, 12.0))
                    _sync_hardware_behaviors(reply_data.get("behaviors"), default_emotion="HAPPY", hold_sec=hold_s)
                except Exception as e:
                    print(f"⚠️ [Web-LLM ReAct Loop Error]: {e}")
                    reply_data["reply"] = f"抱歉主人，执行过程遇到异常错误：{e}，未能完成操作。"

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(reply_data, ensure_ascii=False).encode("utf-8"))
            return

        self.send_error(404, "Not Found")

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
