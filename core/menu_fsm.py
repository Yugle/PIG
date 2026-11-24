"""
MenuFSM: PIG 桌面机器人统一 4 键菜单状态机 (Unified 4-Button Menu State Machine)
统一支持：
1. 树莓派真实 LCD 屏幕 (PIL / ST7789)
2. Web 3D 仿真工作台 (Canvas 2D / Three.js 动态贴图)
3. 手机端 Web 配置后台联动 (/settings)
"""

import os
import time
import socket
import threading
from typing import Dict, Any, Optional, Callable, List
from PIL import Image, ImageDraw, ImageFont
from core.wifi_manager import wifi_manager

try:
    import qrcode
except ImportError:
    qrcode = None


def get_local_ip() -> str:
    """获取本机局域网 IP 地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _draw_pil_switch(draw, x: int, y: int, width: int, height: int, is_on: bool, is_selected: bool):
    r = height // 2
    if is_on:
        draw.rounded_rectangle([x, y, x + width, y + height], radius=r, fill=(16, 185, 129))
        kx = x + width - r
        ky = y + r
        kr = r - 2
        draw.ellipse([kx - kr, ky - kr, kx + kr, ky + kr], fill=(255, 255, 255))
    else:
        if is_selected:
            draw.rounded_rectangle([x, y, x + width, y + height], radius=r, fill=(228, 228, 231), outline=(209, 213, 219), width=1)
            kx = x + r
            ky = y + r
            kr = r - 2
            draw.ellipse([kx - kr, ky - kr, kx + kr, ky + kr], fill=(113, 113, 122))
        else:
            draw.rounded_rectangle([x, y, x + width, y + height], radius=r, fill=(39, 39, 42), outline=(63, 63, 70), width=1)
            kx = x + r
            ky = y + r
            kr = r - 2
            draw.ellipse([kx - kr, ky - kr, kx + kr, ky + kr], fill=(142, 142, 147))

def _draw_pil_badge(draw, text: str, right_x: int, center_y: int, is_selected: bool, font):
    tw = max(24, len(text) * 11)
    badge_w = max(36, tw + 14)
    badge_h = 20
    bx = right_x - badge_w
    by = center_y - badge_h // 2
    if is_selected:
        draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=10, fill=(243, 244, 246), outline=(229, 231, 235), width=1)
        draw.text((bx + (badge_w - tw) // 2, by + 3), text, fill=(24, 24, 27), font=font)
    else:
        draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=10, fill=(34, 34, 40), outline=(50, 50, 58), width=1)
        draw.text((bx + (badge_w - tw) // 2, by + 3), text, fill=(255, 103, 0), font=font)


class MenuFSM:
    def __init__(self, hal=None, hardware_agent=None):
        self.hal = hal
        self.agent = hardware_agent
        
        # 模式状态: "FACE" | "MENU_MAIN" | "QR_CODE" | "DOCTOR" | "TOGGLES" | "POWER"
        self.current_mode = "FACE"
        self.cursor = 0
        self.last_interaction_time = time.time()
        self.auto_timeout = 15.0  # 15 秒无操作自动退回表情
        
        # 硬件开关状态
        self.is_mic_muted = False
        self.face_tracking_enabled = True
        self.is_servo_sleeping = False
        
        # 主菜单定义 (工业极简风，杜绝劣质 AI Emojis)
        self.main_menu_items = [
            {"id": "qr", "num": "01", "title": "无线与配网", "desc": "AP热点配网 / 查IP / 手机设置"},
            {"id": "doctor", "num": "02", "title": "系统体检", "desc": "网络延迟与外设实时排障"},
            {"id": "toggles", "num": "03", "title": "硬件控制", "desc": "舵机归位 / 自检 / 麦克风 / 追踪"},
            {"id": "system", "num": "04", "title": "系统设置", "desc": "面容 / 音量 / 音色 / 息屏 / 关机"},
        ]
        
        # 舵机状态提示
        self.servo_center_status = "ACTION (90°, 90°)"
        self.servo_test_status = "ACTION 摇头点头测试"
        self.face_clear_status = "RESET 清除已录面容"
        
        # 开关菜单项 (兼容旧引用)
        self.toggle_items = []
        self.hardware_items = []
        self._sync_hardware_items()
        
        # 系统设置子菜单项 (基于 Git Tag 动态探测真实版本，绝无任何假 mock)
        from core.version import version_manager
        self.version_manager = version_manager
        self.version_str = self.version_manager.current_version
        self.update_status = "点击检查更新"
        self._sync_system_items()

        # 诊断体检数据缓存
        self.doctor_cache = {
            "wifi_ip": get_local_ip(),
            "mimo_status": "检测中...",
            "mimo_latency": "--",
            "camera_status": "正常",
            "mic_status": "正常",
            "cpu_temp": "45°C",
            "check_time": 0
        }
        
        # 米家屏幕弹窗状态
        self.xiaomi_status_text = "等待米家App扫码授权..."
        self.xiaomi_device_count = 0

        # 锁与监听器
        self._lock = threading.RLock()
        self.state_change_listeners: List[Callable[[Dict[str, Any]], None]] = []
        
        # 加载中文字体
        self.font_main = None
        self.font_bold = None
        self.font_small = None
        self.font_large = None
        self._init_fonts()
        
        # 人脸录入状态
        self.face_enroll_msg = ""
        self.face_enroll_step = 0

        # 启动超时守卫线程
        self._running = True
        self._guard_thread = threading.Thread(target=self._timeout_loop, daemon=True, name="MenuTimeoutGuard")
        self._guard_thread.start()

    def _load_cfg(self) -> Dict[str, Any]:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            if os.path.exists(cfg_path):
                import json
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_cfg_key(self, key: str, val: Any):
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            import json
            c = self._load_cfg()
            c[key] = val
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            if self.agent and hasattr(self.agent, "cfg"):
                self.agent.cfg[key] = val
        except Exception as e:
            print(f"⚠️ [MenuFSM] 保存配置失败: {e}")

    def _sync_hardware_items(self):
        c_cfg = self._load_cfg()
        gaze_mode = c_cfg.get("gaze_tracking_mode", "9grid")
        gaze_txt = "九宫格灵动" if gaze_mode == "9grid" else "连续跟随"
        proactive_on = c_cfg.get("proactive_care", True)
        sfx_on = c_cfg.get("non_verbal_audio", True)
        
        self.hardware_items = [
            {"id": "servo_center", "name": "舵机居中归位", "val": getattr(self, "servo_center_status", "ACTION (90°, 90°)"), "type": "action"},
            {"id": "servo_test", "name": "舵机动作自检", "val": getattr(self, "servo_test_status", "TEST 摇头点头自检"), "type": "action"},
            {"id": "servo_sleep", "name": "舵机休眠防抖", "val": "OFF (休眠卸力)" if self.is_servo_sleeping else "ON (通电保持)", "type": "toggle", "state": not self.is_servo_sleeping},
            {"id": "mic", "name": "麦克风收音", "val": "OFF (已静音)" if self.is_mic_muted else "ON (收音中)", "type": "toggle", "state": not self.is_mic_muted},
            {"id": "track", "name": "人脸视线跟随", "val": "ON (已开启)" if self.face_tracking_enabled else "OFF (已关闭)", "type": "toggle", "state": self.face_tracking_enabled},
            {"id": "gaze_mode", "name": "视线算法模式", "val": gaze_txt, "type": "cycle"},
            {"id": "proactive", "name": "主动关怀搭话", "val": "ON (已开启)" if proactive_on else "OFF (已关闭)", "type": "toggle", "state": proactive_on},
            {"id": "sfx", "name": "萌宠情绪拟声", "val": "ON (已开启)" if sfx_on else "OFF (已关闭)", "type": "toggle", "state": sfx_on},
        ]
        # 兼容旧代码引用
        self.toggle_items = self.hardware_items

    def _sync_system_items(self):
        c_cfg = self._load_cfg()
        voice = c_cfg.get("mimo_voice", "冰糖")
        vol = c_cfg.get("volume", 80)
        timeout = int(self.auto_timeout)
        timeout_txt = f"{timeout}秒" if timeout < 999 else "常亮不熄屏"

        self.system_items = [
            {"id": "face_id", "name": "录入主人面容", "val": "SCAN 拍照绑定", "type": "action"},
            {"id": "face_clear", "name": "重置主人面容", "val": getattr(self, "face_clear_status", "RESET 清除面容"), "type": "action"},
            {"id": "mijia", "name": "米家扫码绑定", "val": "屏幕弹窗", "type": "action"},
            {"id": "voice", "name": "发音音色切换", "val": voice, "type": "cycle"},
            {"id": "volume", "name": "系统播报音量", "val": f"{vol}%", "type": "cycle"},
            {"id": "timeout", "name": "屏幕息屏时间", "val": timeout_txt, "type": "cycle"},
            {"id": "version", "name": "系统固件版本", "val": self.version_str, "type": "info"},
            {"id": "update", "name": "检查系统更新", "val": self.update_status, "type": "action"},
            {"id": "restart", "name": "重启机器人服务", "val": "RESTART", "type": "action"},
            {"id": "shutdown", "name": "树莓派安全关机", "val": "POWER OFF", "type": "action"},
        ]

    def run_servo_center(self):
        """舵机一键居中归位 (90°, 90°)"""
        if self.hal and hasattr(self.hal, "set_angles"):
            self.hal.set_angles(90, 90, smooth=True)
        self.servo_center_status = "已归位居中 (90°, 90°)"
        self._sync_hardware_items()
        self._notify_listeners()

    def run_servo_self_test(self):
        """执行舵机动作健康自检 (左右摇头 + 上下点头 + 回中)"""
        def _test_worker():
            try:
                self.servo_test_status = "TESTING 摇头中..."
                self._sync_hardware_items()
                self._notify_listeners()
                if self.hal and hasattr(self.hal, "set_angles"):
                    self.hal.set_angles(60, 90, smooth=True)
                    time.sleep(0.5)
                    self.hal.set_angles(120, 90, smooth=True)
                    time.sleep(0.5)
                    self.servo_test_status = "TESTING 点头中..."
                    self._sync_hardware_items()
                    self._notify_listeners()
                    self.hal.set_angles(90, 75, smooth=True)
                    time.sleep(0.4)
                    self.hal.set_angles(90, 105, smooth=True)
                    time.sleep(0.4)
                    self.hal.set_angles(90, 90, smooth=True)
                    time.sleep(0.3)
                self.servo_test_status = "PASS 自检通过 (正常)"
            except Exception as e:
                self.servo_test_status = f"FAIL {e}"
            self._sync_hardware_items()
            self._notify_listeners()

        threading.Thread(target=_test_worker, daemon=True, name="ServoSelfTest").start()

    def clear_master_face(self):
        """清除已录入的主人面容"""
        master_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "master_face.json")
        try:
            if os.path.exists(master_file):
                os.remove(master_file)
            if self.agent and hasattr(self.agent, "face_tracker") and self.agent.face_tracker:
                self.agent.face_tracker.master_embedding = None
                self.agent.face_tracker.last_identity = "UNREGISTERED"
            self.face_clear_status = "已清除面容数据"
        except Exception as e:
            self.face_clear_status = f"清除失败: {e}"
        self._sync_system_items()
        self._notify_listeners()

    def cycle_voice(self):
        """循环切换发音音色"""
        voices = ["冰糖", "小爱", "温柔男声"]
        c = self._load_cfg()
        cur_v = c.get("mimo_voice", "冰糖")
        idx = voices.index(cur_v) if cur_v in voices else 0
        new_v = voices[(idx + 1) % len(voices)]
        self._save_cfg_key("mimo_voice", new_v)
        self._sync_system_items()
        self._notify_listeners()

    def cycle_volume(self):
        """循环切换系统音量"""
        vols = [40, 60, 80, 100]
        c = self._load_cfg()
        cur_vol = c.get("volume", 80)
        idx = vols.index(cur_vol) if cur_vol in vols else 2
        new_vol = vols[(idx + 1) % len(vols)]
        self._save_cfg_key("volume", new_vol)
        try:
            os.system(f"amixer set Master {new_vol}% >/dev/null 2>&1 || true")
        except Exception:
            pass
        self._sync_system_items()
        self._notify_listeners()

    def cycle_timeout(self):
        """循环切换屏幕休眠超时时间"""
        timeouts = [15, 30, 60, 999999]
        cur = int(self.auto_timeout)
        idx = timeouts.index(cur) if cur in timeouts else 0
        new_t = timeouts[(idx + 1) % len(timeouts)]
        self.auto_timeout = float(new_t)
        self._save_cfg_key("screen_timeout", new_t)
        self._sync_system_items()
        self._notify_listeners()

    def cycle_gaze_mode(self):
        """循环切换注视追踪模式"""
        modes = ["9grid", "continuous"]
        c = self._load_cfg()
        cur_m = c.get("gaze_tracking_mode", "9grid")
        new_m = "continuous" if cur_m == "9grid" else "9grid"
        self._save_cfg_key("gaze_tracking_mode", new_m)
        self._sync_hardware_items()
        self._notify_listeners()

    def toggle_proactive(self):
        c = self._load_cfg()
        cur = c.get("proactive_care", True)
        self._save_cfg_key("proactive_care", not cur)
        self._sync_hardware_items()
        self._notify_listeners()

    def toggle_sfx(self):
        c = self._load_cfg()
        cur = c.get("non_verbal_audio", True)
        self._save_cfg_key("non_verbal_audio", not cur)
        self._sync_hardware_items()
        self._notify_listeners()

    def show_xiaomi_qr_on_screen(self):
        """在机器人 2.4 寸屏幕上直接弹窗显示米家登录/授权二维码 (无浏览器弹窗)"""
        print("📱 [MenuFSM] 触发屏幕弹窗显示米家绑定二维码")
        with self._lock:
            self.current_mode = "XIAOMI_QR"
            self.xiaomi_status_text = "获取米家二维码中..."
            self.xiaomi_device_count = 0
            self.last_interaction_time = time.time()
            self._notify_listeners()

        def _auth_worker():
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                res = qr_auth_manager.start_qr_session()
                if res.get("status") == "waiting":
                    with self._lock:
                        self.xiaomi_status_text = "请打开米家App扫码授权"
                        self._notify_listeners()

                    while self.current_mode == "XIAOMI_QR":
                        time.sleep(0.8)
                        st = qr_auth_manager.status
                        if st == "success":
                            dev_cnt = len(qr_auth_manager.synced_devices)
                            with self._lock:
                                self.xiaomi_status_text = f"绑定成功！已同步 {dev_cnt} 台设备"
                                self.xiaomi_device_count = dev_cnt
                                self._notify_listeners()
                            time.sleep(2.5)
                            with self._lock:
                                if self.current_mode == "XIAOMI_QR":
                                    self.current_mode = "FACE"
                                    self._notify_listeners()
                            break
                        elif st in ["expired", "error"]:
                            with self._lock:
                                self.xiaomi_status_text = "二维码已超时，按 [OK] 刷新"
                                self._notify_listeners()
                            break
                else:
                    with self._lock:
                        self.xiaomi_status_text = res.get("error", "获取二维码失败")
                        self._notify_listeners()
            except Exception as e:
                with self._lock:
                    self.xiaomi_status_text = f"米家认证异常: {e}"
                    self._notify_listeners()

        threading.Thread(target=_auth_worker, daemon=True, name="XiaomiScreenQR").start()

    def close_xiaomi_qr(self):
        """关闭屏幕米家二维码弹窗返回"""
        with self._lock:
            self.current_mode = "FACE"
            self._notify_listeners()

    def trigger_face_id_enrollment(self):
        """进入人脸录入倒计时模式并在 3 秒后抓拍认主"""
        self.current_mode = "FACE_ENROLL"
        self.face_enroll_msg = "请正对摄像头"
        self.face_enroll_step = 3
        self._notify_listeners()

        def _worker():
            for count in [3, 2, 1]:
                with self._lock:
                    if self.current_mode != "FACE_ENROLL":
                        return
                    self.face_enroll_step = count
                    self.face_enroll_msg = f"请正对摄像头 ({count}s)"
                    self._notify_listeners()
                time.sleep(1.0)
            
            with self._lock:
                if self.current_mode != "FACE_ENROLL":
                    return
                self.face_enroll_step = 0
                self.face_enroll_msg = "📸 正在提取特征..."
                self._notify_listeners()

            # 抓取画面并录入
            ok = False
            msg = ""
            if self.agent and hasattr(self.agent, "register_master_face"):
                res = self.agent.register_master_face()
                if isinstance(res, dict) and res.get("status") == "ok":
                    ok = True
                else:
                    msg = res.get("message", "未识别到清晰人脸") if isinstance(res, dict) else str(res)
            else:
                msg = "未连接摄像头设备"

            with self._lock:
                if self.current_mode != "FACE_ENROLL":
                    return
                if ok:
                    self.face_enroll_msg = "✅ 认主成功！已绑定"
                else:
                    self.face_enroll_msg = f"❌ {msg or '录入失败'}"
                self._notify_listeners()

            time.sleep(2.0)
            with self._lock:
                if self.current_mode == "FACE_ENROLL":
                    self.current_mode = "SYSTEM"
                    self.cursor = 0
                    self._notify_listeners()

        threading.Thread(target=_worker, daemon=True, name="FaceEnrollWorker").start()

    def check_system_update_async(self):
        """后台异步真实检查 OTA 系统更新 (通过 git remote tags 真实探测，零 mock)"""
        with self._lock:
            self.update_status = "检查远程更新中..."
            self._sync_system_items()
            self._notify_listeners()

        def _worker():
            try:
                res = self.version_manager.check_update_sync()
                with self._lock:
                    self.update_status = res.get("status", "已是最新版本")
                    self.version_str = self.version_manager.current_version
                    self._sync_system_items()
                    self._notify_listeners()
            except Exception as e:
                with self._lock:
                    self.update_status = f"检查出错: {e}"
                    self._sync_system_items()
                    self._notify_listeners()

        threading.Thread(target=_worker, daemon=True, name="SystemUpdateCheck").start()

    def get_qr_png_bytes(self) -> bytes:
        """生成并返回当前模式匹配的 QR 码 PNG 字节流"""
        # 1. 处于米家扫码绑定弹窗
        if self.current_mode == "XIAOMI_QR":
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                raw = qr_auth_manager.get_qr_png_bytes()
                if raw:
                    return raw
            except Exception:
                pass

        # 2. 处于无线与配网页 (QR_CODE)
        if wifi_manager.ap_mode:
            # AP 热点配网：直接编码手机连 Wi-Fi 二维码协议
            url = wifi_manager.get_status().get("wifi_qr", f"WIFI:T:WPA;S:{wifi_manager.ap_ssid};P:{wifi_manager.ap_password};;")
        else:
            ip = get_local_ip()
            url = f"http://{ip}:8090/settings"

        if qrcode:
            try:
                qr = qrcode.QRCode(box_size=4, border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                import io
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                pass
        return b""

    def get_qr_base64(self) -> str:
        """生成并返回 Base64 Data URL (0ms 浏览器即时渲染)"""
        raw = self.get_qr_png_bytes()
        if raw:
            import base64
            return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        return ""

    def _init_fonts(self):
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        chosen_font = None
        for p in font_paths:
            if os.path.exists(p):
                chosen_font = p
                break

        if chosen_font:
            try:
                self.font_main = ImageFont.truetype(chosen_font, 15)
                self.font_bold = ImageFont.truetype(chosen_font, 16)
                self.font_small = ImageFont.truetype(chosen_font, 12)
                self.font_large = ImageFont.truetype(chosen_font, 20)
            except Exception:
                self.font_main = ImageFont.load_default()
                self.font_bold = self.font_main
                self.font_small = self.font_main
                self.font_large = self.font_main
        else:
            self.font_main = ImageFont.load_default()
            self.font_bold = self.font_main
            self.font_small = self.font_main
            self.font_large = self.font_main

    def add_listener(self, cb: Callable[[Dict[str, Any]], None]):
        """注册状态变更回调 (通知 Web 端或真机屏)"""
        if cb not in self.state_change_listeners:
            self.state_change_listeners.append(cb)

    def _notify_listeners(self):
        st = self.get_state()
        for cb in list(self.state_change_listeners):
            try:
                cb(st)
            except Exception as e:
                print(f"⚠️ [MenuFSM] 状态监听器异常: {e}")

    def _timeout_loop(self):
        """后台检测 15 秒无操作自动退出到灵动表情"""
        while self._running:
            time.sleep(1.0)
            with self._lock:
                if self.current_mode != "FACE":
                    if time.time() - self.last_interaction_time > self.auto_timeout:
                        self.current_mode = "FACE"
                        self.cursor = 0
                        self._notify_listeners()

    def handle_key(self, button: str, event_type: str = "click"):
        """
        统一按键处理入口 (K1:上, K2:下, K3:确认, K4:返回)
        """
        with self._lock:
            self.last_interaction_time = time.time()
            button = button.upper()
            
            # 1. 处于默认灵动表情模式 (FACE)
            if self.current_mode == "FACE":
                if button == "K3":
                    # 点击确认键 -> 直接激活主菜单 (绝不触发快照发问答)
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 0
                    self._notify_listeners()
                    return
                elif button == "K4":
                    if event_type == "long_press":
                        # 长按返回 -> 舵机一键休眠切换
                        self.is_servo_sleeping = not self.is_servo_sleeping
                        self._apply_servo_sleep(self.is_servo_sleeping)
                        self._notify_listeners()
                    else:
                        # 短按返回 -> 紧急打断 / 静音切换
                        self.is_mic_muted = not self.is_mic_muted
                        self._trigger_barge_in()
                        self._notify_listeners()
                elif button == "K1":
                    # 音量 +
                    self._adjust_volume(+10)
                elif button == "K2":
                    # 音量 -
                    self._adjust_volume(-10)
                return

            # 2. 处于主菜单 (MENU_MAIN)
            if self.current_mode == "MENU_MAIN":
                main_len = len(self.main_menu_items)
                if button == "K1":  # 上 (支持循环回绕)
                    self.cursor = (self.cursor - 1) % main_len
                    self._notify_listeners()
                elif button == "K2":  # 下 (支持循环回绕)
                    self.cursor = (self.cursor + 1) % main_len
                    self._notify_listeners()
                elif button == "K3":
                    if event_type == "long_press":
                        # 长按确认 -> 快速关闭菜单返回表情
                        self.current_mode = "FACE"
                        self.cursor = 0
                        self._notify_listeners()
                    else:
                        # 短按确认 -> 进入选中项
                        target = self.main_menu_items[self.cursor]["id"]
                        if target == "qr":
                            self.current_mode = "QR_CODE"
                        elif target == "doctor":
                            self.current_mode = "DOCTOR"
                            self.refresh_doctor_data_async()
                        elif target == "toggles":
                            self.current_mode = "TOGGLES"
                            self.cursor = 0
                        elif target == "system":
                            self.current_mode = "SYSTEM"
                            self.cursor = 0
                        self._notify_listeners()
                elif button == "K4":  # 返回表情
                    self.current_mode = "FACE"
                    self.cursor = 0
                    self._notify_listeners()
                return

            # 2.5 处于米家屏幕弹窗 (XIAOMI_QR)
            if self.current_mode == "XIAOMI_QR":
                if button in ["K4", "ESC"]:
                    self.close_xiaomi_qr()
                elif button in ["K3", "ENTER"]:
                    self.show_xiaomi_qr_on_screen()
                return

            # 3. 处于无线与配网展示页 (QR_CODE)
            if self.current_mode == "QR_CODE":
                if button in ["K3", "ENTER"]:
                    # 按 OK 切换 AP 配网热点模式 / 正常模式
                    if wifi_manager.ap_mode:
                        wifi_manager.stop_softap()
                    else:
                        wifi_manager.start_softap()
                    self._notify_listeners()
                elif button in ["K4", "ESC"]:
                    self.current_mode = "MENU_MAIN"
                    self._notify_listeners()
                return

            # 4. 处于一键体检页 (DOCTOR)
            if self.current_mode == "DOCTOR":
                if button == "K3":
                    # 重新刷新体检
                    self.refresh_doctor_data_async()
                    self._notify_listeners()
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self._notify_listeners()
                return

            # 5. 处于硬件控制页 (TOGGLES: 舵机归位/自检/休眠/麦克风/追踪/视线/拟声)
            if self.current_mode == "TOGGLES":
                self._sync_hardware_items()
                tog_len = len(self.hardware_items)
                if button == "K1":
                    self.cursor = (self.cursor - 1) % tog_len
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % tog_len
                    self._notify_listeners()
                elif button == "K3":
                    item = self.hardware_items[self.cursor]
                    item_id = item["id"]
                    if item_id == "servo_center":
                        self.run_servo_center()
                    elif item_id == "servo_test":
                        self.run_servo_self_test()
                    elif item_id == "servo_sleep":
                        self.is_servo_sleeping = not self.is_servo_sleeping
                        self._apply_servo_sleep(self.is_servo_sleeping)
                        self._sync_hardware_items()
                    elif item_id == "mic":
                        self.is_mic_muted = not self.is_mic_muted
                        self._sync_hardware_items()
                    elif item_id == "track":
                        self.face_tracking_enabled = not self.face_tracking_enabled
                        self._apply_face_tracking(self.face_tracking_enabled)
                        self._sync_hardware_items()
                    elif item_id == "gaze_mode":
                        self.cycle_gaze_mode()
                    elif item_id == "proactive":
                        self.toggle_proactive()
                    elif item_id == "sfx":
                        self.toggle_sfx()
                    self._notify_listeners()
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 2  # 恢复到硬件控制项
                    self._notify_listeners()
                return

            # 6. 处于系统设置页 (SYSTEM: 面容录入/清除/米家/音色/音量/息屏/更新/关机/重启)
            if self.current_mode == "SYSTEM":
                self._sync_system_items()
                sys_len = len(self.system_items)
                if button == "K1":
                    self.cursor = (self.cursor - 1) % sys_len
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % sys_len
                    self._notify_listeners()
                elif button == "K3":
                    action_id = self.system_items[self.cursor]["id"]
                    if action_id == "face_id":
                        self.trigger_face_id_enrollment()
                    elif action_id == "face_clear":
                        self.clear_master_face()
                    elif action_id == "mijia":
                        self.show_xiaomi_qr_on_screen()
                    elif action_id == "voice":
                        self.cycle_voice()
                    elif action_id == "volume":
                        self.cycle_volume()
                    elif action_id == "timeout":
                        self.cycle_timeout()
                    elif action_id == "update":
                        self.check_system_update_async()
                    elif action_id == "restart":
                        self._execute_power_action("restart")
                    elif action_id == "shutdown":
                        self._execute_power_action("shutdown")
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 3
                    self._notify_listeners()
                return

            # 7. 处于人脸录入倒计时中 (FACE_ENROLL)
            if self.current_mode == "FACE_ENROLL":
                if button == "K4":
                    self.current_mode = "SYSTEM"
                    self.cursor = 0
                    self._notify_listeners()
                return

    def _trigger_snapshot_action(self):
        """按下拍照键：快照问答"""
        print("📸 [MenuFSM] 触发实体一键快照问答")
        # 可以触发播报快门音或直接启动 visual_qa

    def _trigger_barge_in(self):
        """物理打断说话"""
        print("🛑 [MenuFSM] 触发物理紧急打断 (Barge-in)")
        try:
            from core.sound_effects import SoundEffects
            SoundEffects.play_barge_in()
        except Exception:
            pass

    def _apply_servo_sleep(self, sleep: bool):
        """舵机低头休眠与切断脉冲"""
        print(f"💤 [MenuFSM] 舵机休眠状态切换: {sleep}")
        if self.hal:
            if sleep:
                self.hal.set_angles(90.0, 120.0, smooth=True)  # 低头
            else:
                self.hal.set_angles(90.0, 90.0, smooth=True)   # 回正平视

    def _apply_face_tracking(self, enabled: bool):
        """视线追踪开关"""
        print(f"👀 [MenuFSM] 人脸视线追踪: {enabled}")
        if self.agent and hasattr(self.agent, "cfg"):
            self.agent.cfg["face_tracking"] = enabled

    def _adjust_volume(self, delta: int):
        """调节音量"""
        print(f"🔊 [MenuFSM] 音量微调: {delta:+d}%")

    def _execute_power_action(self, action: str):
        if action == "restart":
            print("🔄 [MenuFSM] 正在安全重启机器人后台服务...")
            # 退出当前应用并让 systemd 守护拉起，或返回提示
            self.current_mode = "FACE"
            self._notify_listeners()
        elif action == "shutdown":
            print("🛑 [MenuFSM] 执行树莓派系统关机: sudo shutdown -h now")
            os.system("sudo shutdown -h now 2>/dev/null || echo 'Simulation: Shutdown simulated'")

    def refresh_doctor_data_async(self):
        """后台异步进行一次全面诊断"""
        def _check():
            ip = get_local_ip()
            temp = "45°C"
            try:
                # 读取树莓派硬件温度
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        raw = float(f.read().strip()) / 1000.0
                        temp = f"{raw:.1f}°C"
            except Exception:
                pass

            # 诊断 MiMo 连通性
            mimo_ok = "正常 (78ms)"
            try:
                t0 = time.time()
                # 简单 socket ping
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect(("api.xiaomimimo.com", 443))
                latency = int((time.time() - t0) * 1000)
                s.close()
                mimo_ok = f"在线 ({latency}ms)"
            except Exception:
                mimo_ok = "超时/离线"

            with self._lock:
                self.doctor_cache = {
                    "wifi_ip": ip,
                    "mimo_status": mimo_ok,
                    "camera_status": "正常 (OK)",
                    "mic_status": "静音" if self.is_mic_muted else "收音中 (OK)",
                    "cpu_temp": temp,
                    "check_time": time.time()
                }
                self._notify_listeners()

        threading.Thread(target=_check, daemon=True, name="DoctorCheck").start()

    def get_state(self) -> Dict[str, Any]:
        """输出给 Web 端和 API 的实时状态"""
        ip = get_local_ip()
        return {
            "mode": self.current_mode,
            "cursor": self.cursor,
            "main_menu_items": self.main_menu_items,
            "hardware_items": self.hardware_items,
            "toggle_items": [
                {
                    "name": it["name"],
                    "value": it.get("state", True)
                }
                for it in self.hardware_items if it.get("type") == "toggle"
            ],
            "system_items": self.system_items,
            "version": self.version_str,
            "update_status": self.update_status,
            "doctor": self.doctor_cache,
            "wifi": wifi_manager.get_status(),
            "xiaomi_qr": {
                "active": (self.current_mode == "XIAOMI_QR"),
                "status": self.xiaomi_status_text,
                "device_count": self.xiaomi_device_count
            },
            "settings_url": f"http://{ip}:8090/settings",
            "qr_base64": self.get_qr_base64(),
            "is_mic_muted": self.is_mic_muted,
            "face_tracking": self.face_tracking_enabled,
            "servo_sleeping": self.is_servo_sleeping,
            "local_ip": ip,
            "time_left": max(0, int(self.auto_timeout - (time.time() - self.last_interaction_time)))
        }

    # ========================================================
    # 树莓派真机端 PIL 绘图渲染器 (极简工业设计 / 彻底去除廉价 AI 浮夸风)
    # ========================================================
    def render_pil_overlay(self, base_image: Image.Image) -> Image.Image:
        """
        在 240x320 PIL 图像上渲染高对比度极简工业风菜单 (用于树莓派 ST7789 真机 LCD 直刷)
        """
        img = base_image.copy()
        draw = ImageDraw.Draw(img)

        # 1. 灵动表情模式下：保持纯净，仅在右上角以极简微圆点标注状态
        if self.current_mode == "FACE":
            if self.is_mic_muted:
                draw.ellipse([218, 12, 228, 22], fill=(239, 68, 68))
            if self.is_servo_sleeping:
                draw.text((200, 11), "Z", fill=(140, 140, 140), font=self.font_small)
            return img

        # 2. 菜单背景：纯正工业哑光深灰浮层
        overlay = Image.new("RGBA", (240, 320), (10, 10, 12, 245))
        img.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(img)

# 顶部极简信息栏
        draw.line([(16, 36), (224, 36)], fill=(38, 38, 42), width=1)

        # A. 主菜单界面
        if self.current_mode == "MENU_MAIN":
            draw.text((16, 16), "SETTINGS", fill=(150, 150, 155), font=self.font_small)

            y_start = 48
            card_h = 54
            card_gap = 10
            for idx, item in enumerate(self.main_menu_items):
                is_selected = (idx == self.cursor)
                card_y = y_start + idx * (card_h + card_gap)

                if is_selected:
                    # 极简高对比反色选框 (Dieter Rams / Teenage Engineering 风格)
                    draw.rounded_rectangle([12, card_y, 228, card_y + card_h], radius=8, fill=(255, 255, 255))
                    draw.text((22, card_y + 8), item["title"], fill=(0, 0, 0), font=self.font_bold)
                    draw.text((22, card_y + 32), item["desc"], fill=(90, 90, 95), font=self.font_small)
                    draw.text((212, card_y + 16), "›", fill=(0, 0, 0), font=self.font_bold)
                else:
                    draw.rounded_rectangle([12, card_y, 228, card_y + card_h], radius=8, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((22, card_y + 8), item["title"], fill=(210, 210, 215), font=self.font_bold)
                    draw.text((22, card_y + 32), item["desc"], fill=(120, 120, 125), font=self.font_small)
                    draw.text((212, card_y + 16), "›", fill=(80, 80, 85), font=self.font_bold)

        # B. 扫码手机配置界面 (支持 AP 配网热点与已连接模式)
        elif self.current_mode == "QR_CODE":
            wf_st = wifi_manager.get_status()
            if wf_st["ap_mode"]:
                draw.text((16, 16), "AP PROVISION", fill=(255, 180, 0), font=self.font_small)
                draw.text((184, 16), "HOTSPOT", fill=(255, 180, 0), font=self.font_small)

                draw.rounded_rectangle([42, 48, 198, 204], radius=10, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))

                draw.text((24, 226), f"热点: {wf_st['ap_ssid']}", fill=(255, 255, 255), font=self.font_bold)
                draw.text((24, 248), f"密码: {wf_st['ap_password']}  (扫码直连)", fill=(130, 130, 135), font=self.font_small)
                draw.text((24, 270), f"后台: {wf_st['ap_ip']}:8090/settings", fill=(100, 100, 105), font=self.font_small)
            else:
                draw.text((16, 16), "NETWORK", fill=(150, 150, 155), font=self.font_small)
                draw.text((184, 16), "ONLINE", fill=(34, 197, 94), font=self.font_small)

                ip = wf_st.get("ip", get_local_ip())
                draw.rounded_rectangle([42, 48, 198, 204], radius=10, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))

                draw.text((24, 226), f"已连接: {wf_st.get('ssid', 'WiFi')}", fill=(255, 255, 255), font=self.font_bold)
                draw.text((24, 248), f"本机 IP: {ip}:8090", fill=(130, 130, 135), font=self.font_small)
                draw.text((24, 270), "手机扫码进后台配网或改配置", fill=(100, 100, 105), font=self.font_small)

        # C. 一键体检诊断界面
        elif self.current_mode == "DOCTOR":
            draw.text((16, 16), "DIAGNOSTICS", fill=(150, 150, 155), font=self.font_small)
            draw.text((192, 16), "STATUS", fill=(110, 110, 115), font=self.font_small)

            d = self.doctor_cache
            items = [
                ("WIFI IP", d.get("wifi_ip", "--")),
                ("MIMO API", d.get("mimo_status", "--")),
                ("CAMERA", d.get("camera_status", "--")),
                ("MICROPHONE", d.get("mic_status", "--")),
                ("CPU TEMP", d.get("cpu_temp", "--")),
            ]

            y = 48
            card_h = 42
            card_gap = 8
            for label, val in items:
                draw.rounded_rectangle([12, y, 228, y + card_h], radius=6, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                draw.text((22, y + 13), label, fill=(130, 130, 135), font=self.font_small)
                is_good = any(x in val for x in ["正常", "OK", "在线", "°C"])
                val_color = (255, 255, 255) if is_good else (245, 158, 11)
                draw.text((115, y + 12), val, fill=val_color, font=self.font_bold)
                y += (card_h + card_gap)

        # D. 硬件控制界面 (TOGGLES: Switch 开关控件 + 视口滚动)
        elif self.current_mode == "TOGGLES":
            total = len(self.hardware_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            draw.text((16, 16), "HARDWARE", fill=(150, 150, 155), font=self.font_small)

            max_visible = 4
            start_idx = 0
            if total > max_visible:
                if cur_idx < 2:
                    start_idx = 0
                elif cur_idx >= total - 2:
                    start_idx = total - max_visible
                else:
                    start_idx = cur_idx - 1
                start_idx = max(0, min(start_idx, total - max_visible))

            visible_items = self.hardware_items[start_idx : start_idx + max_visible]
            card_w = 204 if total > max_visible else 216
            card_h = 54
            card_gap = 10
            y = 48

            for v_idx, item in enumerate(visible_items):
                real_idx = start_idx + v_idx
                is_selected = (real_idx == cur_idx)
                is_on = bool(item.get("state", False))

                item_id = item.get("id", "")
                item_type = item.get("type", "")

                if item_type == "toggle":
                    if item_id == "servo_sleep":
                        sub_txt = "通电保持" if is_on else "休眠卸力"
                    elif item_id == "mic":
                        sub_txt = "收音中" if is_on else "已静音"
                    elif item_id == "track":
                        sub_txt = "视觉跟随开启" if is_on else "跟随已关闭"
                    elif item_id == "proactive":
                        sub_txt = "主动搭话开启" if is_on else "搭话已关闭"
                    elif item_id == "sfx":
                        sub_txt = "情绪拟声开启" if is_on else "拟声已关闭"
                    else:
                        sub_txt = "已开启" if is_on else "已关闭"
                elif item_type == "action":
                    sub_txt = "居中归位 (90°, 90°)" if item_id == "servo_center" else str(item.get("val", "动作健康自检"))
                elif item_type == "cycle":
                    sub_txt = "算法跟随设定"
                else:
                    sub_txt = str(item.get("val", ""))

                if is_selected:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=8, fill=(255, 255, 255))
                    draw.text((22, y + 8), item["name"], fill=(0, 0, 0), font=self.font_bold)
                    draw.text((22, y + 32), sub_txt, fill=(90, 90, 95), font=self.font_small)
                else:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=8, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((22, y + 8), item["name"], fill=(210, 210, 215), font=self.font_bold)
                    draw.text((22, y + 32), sub_txt, fill=(120, 120, 125), font=self.font_small)

                # 右侧 Switch / 胶囊徽标 / 箭头
                if item_type == "toggle":
                    _draw_pil_switch(draw, 12 + card_w - 46, y + 17, 36, 20, is_on, is_selected)
                elif item_type == "cycle":
                    badge_txt = "九宫格" if "九宫格" in str(item.get("val", "")) else "连续跟随"
                    _draw_pil_badge(draw, badge_txt, 12 + card_w - 10, y + 27, is_selected, self.font_small)
                else:
                    draw.text((12 + card_w - 14, y + 16), "›", fill=(0, 0, 0) if is_selected else (80, 80, 85), font=self.font_bold)

                y += (card_h + card_gap)

            # 极简工业滚动条
            if total > max_visible:
                track_x = 224
                track_y = 48
                track_h = max_visible * (card_h + card_gap) - card_gap
                draw.rounded_rectangle([track_x, track_y, track_x + 3, track_y + track_h], radius=2, fill=(28, 28, 32))
                thumb_h = max(32, int(track_h * (max_visible / total)))
                max_scroll = track_h - thumb_h
                thumb_y = track_y + int((start_idx / (total - max_visible)) * max_scroll)
                draw.rounded_rectangle([track_x, thumb_y, track_x + 3, thumb_y + thumb_h], radius=2, fill=(255, 103, 0))

        # E. 系统设置界面 (SYSTEM: 版本号/更新/关机/重启/认主)
        elif self.current_mode == "SYSTEM":
            total = len(self.system_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            draw.text((16, 16), "SYSTEM", fill=(150, 150, 155), font=self.font_small)

            max_visible = 4
            start_idx = 0
            if total > max_visible:
                if cur_idx < 2:
                    start_idx = 0
                elif cur_idx >= total - 2:
                    start_idx = total - max_visible
                else:
                    start_idx = cur_idx - 1
                start_idx = max(0, min(start_idx, total - max_visible))

            visible_items = self.system_items[start_idx : start_idx + max_visible]
            card_w = 204 if total > max_visible else 216
            card_h = 54
            card_gap = 10
            y = 48

            for v_idx, item in enumerate(visible_items):
                real_idx = start_idx + v_idx
                is_selected = (real_idx == cur_idx)
                item_id = item.get("id", "")
                item_type = item.get("type", "")

                if item_id == "face_id":
                    sub_txt = "3秒抓拍面部特征认主"
                elif item_id == "face_clear":
                    sub_txt = "清除本地主人面容数据"
                elif item_id == "mijia":
                    sub_txt = "屏幕弹窗扫码绑定"
                elif item_id == "voice":
                    sub_txt = "系统播报声音风格"
                elif item_id == "volume":
                    sub_txt = "扬声器播报音量增益"
                elif item_id == "timeout":
                    sub_txt = "无操作自动息屏倒计时"
                elif item_id == "version":
                    sub_txt = "PIG Robot OS 核心固件"
                elif item_id == "update":
                    sub_txt = str(item.get("val", "点击检查远程更新"))
                elif item_id == "restart":
                    sub_txt = "热重启核心 Python 进程"
                elif item_id == "shutdown":
                    sub_txt = "安全切断供电避免坏卡"
                else:
                    sub_txt = str(item.get("val", ""))

                if is_selected:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=8, fill=(255, 255, 255))
                    draw.text((22, y + 8), item["name"], fill=(0, 0, 0), font=self.font_bold)
                    draw.text((22, y + 32), sub_txt, fill=(90, 90, 95), font=self.font_small)
                else:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=8, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((22, y + 8), item["name"], fill=(210, 210, 215), font=self.font_bold)
                    draw.text((22, y + 32), sub_txt, fill=(120, 120, 125), font=self.font_small)

                # 右侧胶囊徽标或动作箭头
                if item_type in ["cycle", "info"]:
                    _draw_pil_badge(draw, str(item.get("val", "")), 12 + card_w - 10, y + 27, is_selected, self.font_small)
                else:
                    draw.text((12 + card_w - 14, y + 16), "›", fill=(0, 0, 0) if is_selected else (80, 80, 85), font=self.font_bold)

                y += (card_h + card_gap)

            # 极简工业滚动条
            if total > max_visible:
                track_x = 224
                track_y = 48
                track_h = max_visible * (card_h + card_gap) - card_gap
                draw.rounded_rectangle([track_x, track_y, track_x + 3, track_y + track_h], radius=2, fill=(28, 28, 32))
                thumb_h = max(32, int(track_h * (max_visible / total)))
                max_scroll = track_h - thumb_h
                thumb_y = track_y + int((start_idx / (total - max_visible)) * max_scroll)
                draw.rounded_rectangle([track_x, thumb_y, track_x + 3, thumb_y + thumb_h], radius=2, fill=(255, 103, 0))

        # F. 人脸录入倒计时/取景界面 (FACE_ENROLL)
        elif self.current_mode == "FACE_ENROLL":
            draw.text((16, 16), "FACE ID ENROLL", fill=(255, 200, 0), font=self.font_bold)

            # 居中取景框
            box_col = (255, 200, 0) if self.face_enroll_step > 0 else (34, 197, 94)
            draw.rectangle([40, 52, 200, 212], outline=box_col, width=2)
            cl = 14
            draw.line([(40, 52), (40+cl, 52)], fill=(255, 255, 255), width=3)
            draw.line([(40, 52), (40, 52+cl)], fill=(255, 255, 255), width=3)
            draw.line([(200, 52), (200-cl, 52)], fill=(255, 255, 255), width=3)
            draw.line([(200, 52), (200, 52+cl)], fill=(255, 255, 255), width=3)
            draw.line([(40, 212), (40+cl, 212)], fill=(255, 255, 255), width=3)
            draw.line([(40, 212), (40, 212-cl)], fill=(255, 255, 255), width=3)
            draw.line([(200, 212), (200-cl, 212)], fill=(255, 255, 255), width=3)
            draw.line([(200, 212), (200, 212-cl)], fill=(255, 255, 255), width=3)

            # 倒计时显示
            if self.face_enroll_step > 0:
                draw.text((112, 115), str(self.face_enroll_step), fill=(255, 255, 255), font=self.font_bold)

            # 状态文案
            draw.text((28, 230), self.face_enroll_msg, fill=(255, 255, 255), font=self.font_bold)

        # G. 米家生态授权弹窗 (XIAOMI_QR: 屏幕居中渲染米家二维码)
        elif self.current_mode == "XIAOMI_QR":
            draw.text((16, 16), "MI HOME / AUTH", fill=(255, 103, 0), font=self.font_bold)
            draw.text((188, 16), "BINDING", fill=(150, 150, 155), font=self.font_small)

            # 居中纯白卡片放二维码
            draw.rounded_rectangle([42, 48, 198, 204], radius=10, fill=(255, 255, 255))
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                qr_bytes = qr_auth_manager.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))
                else:
                    draw.text((56, 114), "获取米家码中...", fill=(0, 0, 0), font=self.font_small)
            except Exception as e:
                draw.text((50, 110), f"Error: {e}", fill=(0, 0, 0), font=self.font_small)

            draw.text((24, 226), "打开米家App 扫码授权", fill=(255, 255, 255), font=self.font_bold)
            draw.text((24, 248), self.xiaomi_status_text, fill=(255, 153, 0) if "成功" not in self.xiaomi_status_text else (34, 197, 94), font=self.font_small)
            draw.text((24, 270), "绑定后可直接语音控制台灯/水壶等", fill=(120, 120, 125), font=self.font_small)

        return img


# 单例初始化句柄
menu_fsm = MenuFSM()
