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
from skills.feishu_bot.feishu_client import feishu_client
if "feishu" in server_cfg:
    feishu_client.update_config(server_cfg["feishu"])

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

# ========================================================
# 飞书双向消息处理通道 (统一供 WebSocket 长连接与 Webhook 调用)
# ========================================================
def handle_feishu_incoming_message(clean_text: str, open_id: str, chat_id: str, msg_id: str = ""):
    """统一处理飞书传入消息 (无论是 WebSocket 长连接还是 HTTP Webhook 回调)"""
    def _worker():
        try:
            target_id = chat_id if chat_id else open_id
            id_type = "chat_id" if chat_id else "open_id"

            # 1. 优先精准识别远程看家 / 拍照巡检意图 (秒级抓拍直出，杜绝废话)
            text_lower = clean_text.strip().lower()
            snapshot_triggers = ["看看家", "拍照", "拍张照", "拍个照", "看下家", "监控", "看一眼", "巡视", "巡检", "安防", "家里安全", "看看客厅", "看看周围", "拍张照片", "照一张", "拍一下", "看看现场"]
            if any(k in text_lower for k in snapshot_triggers):
                print(f"📷 [Feishu Bot] 命中远程看家与监控抓拍意图: '{clean_text}'")
                dir_val = "center"
                if any(w in text_lower for w in ["向左", "往左", "左边", "左侧", "左看"]):
                    dir_val = "left"
                elif any(w in text_lower for w in ["向右", "往右", "右边", "右侧", "右看"]):
                    dir_val = "right"
                elif any(w in text_lower for w in ["抬头", "向上", "天花板", "高处", "上看"]):
                    dir_val = "up"
                elif any(w in text_lower for w in ["低头", "向下", "地面", "桌面", "低处", "下看"]):
                    dir_val = "down"

                from skills.remote_guard.main import run as run_remote_guard
                report = run_remote_guard(direction=dir_val, question=clean_text, target_id=target_id, id_type=id_type)
                send_res = feishu_client.send_im_message(target_id, "text", {"text": report}, receive_id_type=id_type)
                print(f"📤 [Feishu Bot] 安防抓拍简报已回复至飞书 {target_id}: -> {send_res.get('code', send_res.get('status'))}")
                return

            # 2. 常规对话：进入 ReAct 认知推演链路
            from core.agent_reasoner import AgentReasoner
            from core.prompt_builder import PromptBuilder

            c_cfg = load_config()
            reasoner = AgentReasoner(
                api_key=c_cfg.get("api_key") or c_cfg.get("mimo_api_key") or "",
                base_url=c_cfg.get("base_url") or "https://api.xiaomimimo.com/v1",
                model=c_cfg.get("llm_model") or "mimo-v2.6-flash",
                max_steps=4,
                enable_thinking=c_cfg.get("llm_thinking", False)
            )
            sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=300, skill_manager=skill_mgr)
            sid = f"feishu_{chat_id or open_id}"

            print(f"🤖 [Feishu Bot] 正在为会话 {sid} 进行思考推演: '{clean_text}'...")
            res = reasoner.run_react_loop(
                user_text=clean_text,
                base_sys_prompt=sys_prompt,
                memory_manager=mem,
                skill_manager=skill_mgr,
                evolution_engine=evolution_engine,
                session_id=sid,
                silent=True  # 远程静默执行，绝不触发本地舵机转动与大声发音！
            )
            reply_text = res.get("reply", "小猪收到啦！")
            send_res = feishu_client.send_im_message(target_id, "text", {"text": reply_text}, receive_id_type=id_type)
            print(f"📤 [Feishu Bot] 已回复消息至飞书 {target_id}: '{reply_text[:50]}...' -> {send_res.get('code', send_res.get('status'))}")
        except Exception as e:
            print(f"❌ [Feishu Bot] 处理消息失败: {e}")

    threading.Thread(target=_worker, daemon=True, name="FeishuMsgWorker").start()

# 启动飞书官方 WebSocket 长连接守护线程 (零公网 IP / 零域名直连)
if "feishu" in server_cfg:
    feishu_client.update_config(server_cfg["feishu"])
    if feishu_client.app_id and feishu_client.app_secret:
        feishu_client.start_ws_service(handle_feishu_incoming_message)

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

# 统一 4 键菜单状态机 (MenuFSM)
from core.menu_fsm import menu_fsm
menu_fsm.hal = hardware_agent.hal
menu_fsm.agent = hardware_agent

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


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # 静默所有高频 HTTP 访问日志，避免控制台与后台任务缓冲区溢出导致容器杀进程
        return

    def do_HEAD(self):
        if self.path in ["/settings", "/settings.html"]:
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

        # 手机端无线配置后台 (/settings)
        if self.path in ["/settings", "/settings.html"]:
            settings_path = os.path.join(DIRECTORY, "simulator", "settings.html")
            if os.path.exists(settings_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(settings_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, "settings.html not found")
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
                "robot_name": cur_cfg.get("robot_name", "PIG"),
                "user_name": cur_cfg.get("user_name", "胖虎"),
                "mimo_voice": cur_cfg.get("mimo_voice", "冰糖"),
                "face_tracking": hardware_agent.cfg.get("face_tracking", True),
                "is_mic_muted": menu_fsm.is_mic_muted,
                "servo_sleeping": menu_fsm.is_servo_sleeping,
                "local_ip": state["local_ip"],
                "cpu_temp": state["doctor"].get("cpu_temp", "45°C"),
                "mimo_status": state["doctor"].get("mimo_status", "正常 (OK)"),
                "feishu": cur_cfg.get("feishu", {})
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode("utf-8"))
            return

        # 飞书机器人状态与多 Session 活跃列表 API
        if self.path == "/api/feishu/status":
            from skills.feishu_bot.feishu_client import feishu_client
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "configured": feishu_client.is_configured,
                "has_webhook": bool(feishu_client.webhook_url and "demo" not in feishu_client.webhook_url),
                "has_app": bool(feishu_client.app_id and feishu_client.app_secret),
                "ws_connected": feishu_client.ws_connected,
                "last_open_id": feishu_client.last_user_open_id,
                "active_sessions": mem.list_active_sessions() if mem else {}
            }, ensure_ascii=False).encode("utf-8"))
            return

        # 📷 飞书远程安防抓拍现场测试 API (GET 支持)
        if self.path == "/api/feishu/test_snapshot":
            try:
                from skills.remote_guard.main import run as run_remote_guard
                report = run_remote_guard(direction="center", question="前端设置页一键远程巡检抓拍测试")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "现场抓拍与安防卡片已成功触发并推向飞书！", "report": report}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False).encode("utf-8"))
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
                for k in ["api_key", "llm_model", "robot_name", "user_name", "mimo_voice"]:
                    if k in body:
                        cur_cfg[k] = body[k]
                        if k == "api_key":
                            cur_cfg["mimo_api_key"] = body[k]
                if "face_tracking" in body:
                    cur_cfg["face_tracking"] = bool(body["face_tracking"])
                    hardware_agent.cfg["face_tracking"] = bool(body["face_tracking"])
                    menu_fsm.face_tracking_enabled = bool(body["face_tracking"])
                if "is_mic_muted" in body:
                    menu_fsm.is_mic_muted = bool(body["is_mic_muted"])
                if "servo_sleeping" in body:
                    menu_fsm.is_servo_sleeping = bool(body["servo_sleeping"])
                    menu_fsm._apply_servo_sleep(menu_fsm.is_servo_sleeping)
                if "feishu" in body and isinstance(body["feishu"], dict):
                    cur_cfg.setdefault("feishu", {}).update(body["feishu"])
                    from skills.feishu_bot.feishu_client import feishu_client
                    feishu_client.update_config(cur_cfg["feishu"])
                    if feishu_client.app_id and feishu_client.app_secret:
                        feishu_client.start_ws_service(handle_feishu_incoming_message)

                with open(cfg_file, "w", encoding="utf-8") as f:
                    json.dump(cur_cfg, f, ensure_ascii=False, indent=2)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "state": menu_fsm.get_state()}).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 飞书事件回调 Webhook (URL 校验 + 消息接收与多 Session 隔离闭环)
        if self.path == "/api/feishu/webhook":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8")) if post_data else {}

                # 1. 飞书开放平台 URL 校验 (Challenge Verification)
                if body.get("type") == "url_verification":
                    challenge = body.get("challenge", "")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"challenge": challenge}).encode("utf-8"))
                    return

                # 2. 飞书事件消息推送 (im.message.receive_v1)
                event = body.get("event", {})
                if event:
                    msg = event.get("message", {})
                    msg_type = msg.get("message_type")
                    if msg_type == "text":
                        content_json = json.loads(msg.get("content", "{}"))
                        user_raw_text = content_json.get("text", "")
                        import re
                        clean_text = re.sub(r"@_user_\d+\s*", "", user_raw_text).strip()
                        sender = event.get("sender", {}).get("sender_id", {})
                        open_id = sender.get("open_id", "")
                        chat_id = msg.get("chat_id", "")

                        if clean_text:
                            handle_feishu_incoming_message(clean_text, open_id, chat_id, msg.get("message_id", ""))

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 发送测试飞书告警/日记卡片 API
        if self.path == "/api/feishu/test_notify":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
                body = json.loads(post_data.decode("utf-8")) if post_data else {}
                msg_type = body.get("type", "alert")
                title = body.get("title", "PIG 机器人测试告警")
                content = body.get("content", "这是一条来自树莓派 PIG 的测试推送！飞书告警与通知通道已打通。")

                from skills.feishu_bot.feishu_client import feishu_client
                if msg_type == "diary":
                    p_data = evolution_engine.get_personality() if evolution_engine else {}
                    radar = p_data.get("radar", {})
                    lvl = p_data.get("intimacy_level", 2)
                    tag = p_data.get("persona_tag", "元气治愈系桌宠")
                    res = feishu_client.send_evolution_card(
                        diary_text=content,
                        radar_dict=radar,
                        intimacy_level=lvl,
                        persona_tag=tag
                    )
                else:
                    res = feishu_client.send_alert_card(title, content, detail="硬件健康正常 | 多 Session 架构就绪")
                if isinstance(res, dict) and res.get("status") == "error":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": res.get("message", "推送失败"), "result": res}, ensure_ascii=False).encode("utf-8"))
                    return
                if isinstance(res, dict) and res.get("code", 0) != 0 and "msg" in res:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": f"飞书返回错误码 {res.get('code')}: {res.get('msg')}", "result": res}, ensure_ascii=False).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "result": res}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 📷 飞书远程安防抓拍现场测试 API
        if self.path == "/api/feishu/test_snapshot":
            try:
                from skills.remote_guard.main import run as run_remote_guard
                report = run_remote_guard(direction="center", question="前端设置页一键远程巡检抓拍测试")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "现场抓拍与安防卡片已成功触发并推向飞书！", "report": report}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False).encode("utf-8"))
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

        # 小米米家直接在屏幕弹窗显示绑定二维码 (不走浏览器弹窗)
        if self.path in ["/api/xiaomi/qr_start_screen", "/api/xiaomi/qr_start"]:
            try:
                menu_fsm.show_xiaomi_qr_on_screen()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "waiting",
                    "action": "displayed_on_robot_screen",
                    "message": "已在小猪屏幕上显示米家授权二维码"
                }, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 关闭屏幕上的米家弹窗
        if self.path == "/api/xiaomi/qr_dismiss_screen":
            try:
                menu_fsm.close_xiaomi_qr()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8"))
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
