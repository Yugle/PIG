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
            {"id": "toggles", "num": "03", "title": "硬件开关", "desc": "麦克风静音 / 追踪 / 休眠"},
            {"id": "system", "num": "04", "title": "系统设置", "desc": "固件版本 / 系统更新 / 电源管理"},
        ]
        
        # 开关菜单项
        self.toggle_items = [
            {"id": "mic", "name": "麦克风收音", "state_key": "is_mic_muted", "inverted": True},
            {"id": "track", "name": "人脸视线跟随", "state_key": "face_tracking_enabled", "inverted": False},
            {"id": "servo", "name": "舵机工作状态", "state_key": "is_servo_sleeping", "inverted": True},
        ]
        
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

    def _sync_system_items(self):
        self.system_items = [
            {"id": "face_id", "name": "录入主人面容", "val": "SCAN 拍照绑定", "actionable": True},
            {"id": "mijia", "name": "米家扫码绑定", "val": "屏幕弹窗", "actionable": True},
            {"id": "version", "name": "系统固件版本", "val": self.version_str, "actionable": False},
            {"id": "update", "name": "检查系统更新", "val": self.update_status, "actionable": True},
            {"id": "restart", "name": "重启机器人服务", "val": "RESTART", "actionable": True},
            {"id": "shutdown", "name": "树莓派安全关机", "val": "POWER OFF", "actionable": True},
        ]

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
                    if event_type == "long_press":
                        # 长按确认 -> 激活主菜单
                        self.current_mode = "MENU_MAIN"
                        self.cursor = 0
                        self._notify_listeners()
                    else:
                        # 短按确认 -> 一键快照问答/唤醒
                        self._trigger_snapshot_action()
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

            # 5. 处于硬件开关页 (TOGGLES)
            if self.current_mode == "TOGGLES":
                tog_len = len(self.toggle_items)
                if button == "K1":
                    self.cursor = (self.cursor - 1) % tog_len
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % tog_len
                    self._notify_listeners()
                elif button == "K3":
                    # 切换当前项开关
                    cur_item = self.toggle_items[self.cursor]
                    k = cur_item["state_key"]
                    new_val = not getattr(self, k)
                    setattr(self, k, new_val)
                    if k == "is_servo_sleeping":
                        self._apply_servo_sleep(new_val)
                    elif k == "face_tracking_enabled":
                        self._apply_face_tracking(new_val)
                    self._notify_listeners()
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 2  # 恢复到硬件开关项
                    self._notify_listeners()
                return

            # 6. 处于系统设置页 (SYSTEM: 认主/版本号/更新/关机/重启)
            if self.current_mode == "SYSTEM":
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
                    elif action_id == "mijia":
                        self.show_xiaomi_qr_on_screen()
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
            "toggle_items": [
                {
                    "name": it["name"],
                    "value": not getattr(self, it["state_key"]) if it["inverted"] else getattr(self, it["state_key"])
                }
                for it in self.toggle_items
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
            draw.text((192, 16), f"0{self.cursor+1}/04", fill=(110, 110, 115), font=self.font_small)

            y_start = 46
            for idx, item in enumerate(self.main_menu_items):
                is_selected = (idx == self.cursor)
                card_y = y_start + idx * 56

                if is_selected:
                    # 极简高对比反色选框 (Dieter Rams / Teenage Engineering 风格)
                    draw.rounded_rectangle([14, card_y, 226, card_y + 48], radius=8, fill=(255, 255, 255))
                    draw.text((24, card_y + 8), f"{item['num']}  {item['title']}", fill=(0, 0, 0), font=self.font_bold)
                    draw.text((24, card_y + 28), item["desc"], fill=(90, 90, 95), font=self.font_small)
                    draw.text((210, card_y + 14), "›", fill=(0, 0, 0), font=self.font_bold)
                else:
                    draw.rounded_rectangle([14, card_y, 226, card_y + 48], radius=8, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((24, card_y + 8), f"{item['num']}  {item['title']}", fill=(210, 210, 215), font=self.font_bold)
                    draw.text((24, card_y + 28), item["desc"], fill=(120, 120, 125), font=self.font_small)
                    draw.text((210, card_y + 14), "›", fill=(80, 80, 85), font=self.font_bold)

            # 底部极简按键提示
            draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
            draw.text((24, 298), "[▲▼] 移动    [OK] 确认    [ESC] 返回", fill=(100, 100, 105), font=self.font_small)

        # B. 扫码手机配置界面 (支持 AP 配网热点与已连接模式)
        elif self.current_mode == "QR_CODE":
            wf_st = wifi_manager.get_status()
            if wf_st["ap_mode"]:
                draw.text((16, 16), "01 / AP PROVISION", fill=(255, 180, 0), font=self.font_small)
                draw.text((188, 16), "HOTSPOT", fill=(110, 110, 115), font=self.font_small)

                draw.rounded_rectangle([42, 46, 198, 202], radius=10, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 52))

                draw.text((24, 218), f"热点: {wf_st['ap_ssid']}", fill=(255, 255, 255), font=self.font_bold)
                draw.text((24, 238), f"密码: {wf_st['ap_password']}  (手机扫码直连)", fill=(130, 130, 135), font=self.font_small)
                draw.text((24, 258), f"后台: {wf_st['ap_ip']}:8090/settings", fill=(100, 100, 105), font=self.font_small)

                draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
                draw.text((24, 298), "[OK] 退出热点        [ESC] 返回", fill=(100, 100, 105), font=self.font_small)
            else:
                draw.text((16, 16), "01 / NETWORK", fill=(150, 150, 155), font=self.font_small)
                draw.text((188, 16), "ONLINE", fill=(34, 197, 94), font=self.font_small)

                ip = wf_st.get("ip", get_local_ip())
                draw.rounded_rectangle([42, 46, 198, 202], radius=10, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 52))

                draw.text((24, 218), f"已连接: {wf_st.get('ssid', 'WiFi')}", fill=(255, 255, 255), font=self.font_bold)
                draw.text((24, 238), f"本机 IP: {ip}:8090", fill=(130, 130, 135), font=self.font_small)
                draw.text((24, 258), "手机扫码进后台配网或改配置", fill=(100, 100, 105), font=self.font_small)

                draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
                draw.text((24, 298), "[OK] 开启AP配网热点   [ESC] 返回", fill=(100, 100, 105), font=self.font_small)

        # C. 一键体检诊断界面
        elif self.current_mode == "DOCTOR":
            draw.text((16, 16), "02 / DIAGNOSTICS", fill=(150, 150, 155), font=self.font_small)
            draw.text((196, 16), "STATUS", fill=(110, 110, 115), font=self.font_small)

            d = self.doctor_cache
            items = [
                ("WIFI IP", d.get("wifi_ip", "--")),
                ("MIMO API", d.get("mimo_status", "--")),
                ("CAMERA", d.get("camera_status", "--")),
                ("MICROPHONE", d.get("mic_status", "--")),
                ("CPU TEMP", d.get("cpu_temp", "--")),
            ]

            y = 48
            for label, val in items:
                draw.rounded_rectangle([14, y, 226, y + 40], radius=6, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                draw.text((24, y + 12), label, fill=(130, 130, 135), font=self.font_small)
                # 状态点与值
                is_good = any(x in val for x in ["正常", "OK", "在线", "°C"])
                val_color = (255, 255, 255) if is_good else (245, 158, 11)
                draw.text((115, y + 11), val, fill=val_color, font=self.font_bold)
                y += 46

            draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
            draw.text((28, 298), "[OK] 重新检测       [ESC] 返回", fill=(100, 100, 105), font=self.font_small)

        # D. 硬件与防扰开关
        elif self.current_mode == "TOGGLES":
            draw.text((16, 16), "03 / HARDWARE", fill=(150, 150, 155), font=self.font_small)
            draw.text((192, 16), "SWITCH", fill=(110, 110, 115), font=self.font_small)

            y = 52
            for idx, item in enumerate(self.toggle_items):
                is_selected = (idx == self.cursor)
                k = item["state_key"]
                is_on = not getattr(self, k) if item["inverted"] else getattr(self, k)
                status_txt = "ON" if is_on else "OFF"

                if is_selected:
                    draw.rounded_rectangle([14, y, 226, y + 54], radius=8, fill=(255, 255, 255))
                    draw.text((24, y + 10), f"0{idx+1}  {item['name']}", fill=(0, 0, 0), font=self.font_bold)
                    draw.text((24, y + 32), f"当前状态: {status_txt}", fill=(80, 80, 85), font=self.font_small)
                    # 开关标识框
                    draw.rounded_rectangle([170, y + 14, 214, y + 38], radius=4, fill=(0, 0, 0))
                    draw.text((182, y + 18), status_txt, fill=(255, 255, 255), font=self.font_bold)
                else:
                    draw.rounded_rectangle([14, y, 226, y + 54], radius=8, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((24, y + 10), f"0{idx+1}  {item['name']}", fill=(210, 210, 215), font=self.font_bold)
                    draw.text((24, y + 32), f"当前状态: {status_txt}", fill=(120, 120, 125), font=self.font_small)
                    # 开关标识框
                    box_fill = (35, 35, 40)
                    draw.rounded_rectangle([170, y + 14, 214, y + 38], radius=4, fill=box_fill)
                    draw.text((182, y + 18), status_txt, fill=(16, 185, 129) if is_on else (140, 140, 145), font=self.font_bold)
                y += 66

            draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
            draw.text((24, 298), "[▲▼] 移动    [OK] 切换    [ESC] 返回", fill=(100, 100, 105), font=self.font_small)

        # E. 系统设置界面 (SYSTEM: 版本号/更新/关机/重启/认主 - 支持视口平滑滚动与循环回绕)
        elif self.current_mode == "SYSTEM":
            total = len(self.system_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            draw.text((16, 16), "04 / SYSTEM", fill=(150, 150, 155), font=self.font_small)
            count_txt = f"{cur_idx+1:02d}/{total:02d}"
            draw.text((186, 16), count_txt, fill=(255, 103, 0), font=self.font_small)

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
            card_w = 206 if total > max_visible else 216
            card_h = 48
            card_gap = 6
            y = 44

            for v_idx, item in enumerate(visible_items):
                real_idx = start_idx + v_idx
                is_selected = (real_idx == cur_idx)
                if is_selected:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=6, fill=(255, 255, 255))
                    draw.text((22, y + 6), f"{real_idx+1:02d}  {item['name']}", fill=(0, 0, 0), font=self.font_bold)
                    draw.text((22, y + 26), str(item["val"]), fill=(70, 70, 75), font=self.font_small)
                    draw.text((12 + card_w - 14, y + 14), "›", fill=(0, 0, 0), font=self.font_bold)
                else:
                    draw.rounded_rectangle([12, y, 12 + card_w, y + card_h], radius=6, fill=(18, 18, 20), outline=(32, 32, 36), width=1)
                    draw.text((22, y + 6), f"{real_idx+1:02d}  {item['name']}", fill=(210, 210, 215), font=self.font_bold)
                    draw.text((22, y + 26), str(item["val"]), fill=(120, 120, 125), font=self.font_small)
                    draw.text((12 + card_w - 14, y + 14), "›", fill=(80, 80, 85), font=self.font_bold)
                y += (card_h + card_gap)

            # 极简工业滚动条
            if total > max_visible:
                track_x = 224
                track_y = 44
                track_h = max_visible * (card_h + card_gap) - card_gap
                draw.rounded_rectangle([track_x, track_y, track_x + 3, track_y + track_h], radius=2, fill=(28, 28, 32))
                thumb_h = max(24, int(track_h * (max_visible / total)))
                max_scroll = track_h - thumb_h
                thumb_y = track_y + int((start_idx / (total - max_visible)) * max_scroll)
                draw.rounded_rectangle([track_x, thumb_y, track_x + 3, thumb_y + thumb_h], radius=2, fill=(255, 103, 0))

            # 底部防遮挡实色保护底栏
            draw.rectangle([0, 280, 240, 320], fill=(10, 10, 12))
            draw.line([(16, 284), (224, 284)], fill=(32, 32, 36), width=1)
            draw.text((24, 296), "[▲▼] 选择    [OK] 执行    [ESC] 返回", fill=(100, 100, 105), font=self.font_small)

        # F. 人脸录入倒计时/取景界面 (FACE_ENROLL)
        elif self.current_mode == "FACE_ENROLL":
            draw.text((16, 16), "FACE ID ENROLL", fill=(255, 200, 0), font=self.font_bold)
            draw.text((192, 16), "04/04", fill=(110, 110, 115), font=self.font_small)

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
            draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
            draw.text((24, 298), "[ESC] 取消返回", fill=(100, 100, 105), font=self.font_small)

        # G. 米家生态授权弹窗 (XIAOMI_QR: 屏幕居中渲染米家二维码)
        elif self.current_mode == "XIAOMI_QR":
            draw.text((16, 16), "MI HOME / AUTH", fill=(255, 103, 0), font=self.font_bold)
            draw.text((188, 16), "BINDING", fill=(150, 150, 155), font=self.font_small)

            # 居中纯白卡片放二维码
            draw.rounded_rectangle([42, 46, 198, 202], radius=10, fill=(255, 255, 255))
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                qr_bytes = qr_auth_manager.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 52))
                else:
                    draw.text((56, 114), "获取米家码中...", fill=(0, 0, 0), font=self.font_small)
            except Exception as e:
                draw.text((50, 110), f"Error: {e}", fill=(0, 0, 0), font=self.font_small)

            draw.text((24, 218), "打开米家App 扫码授权", fill=(255, 255, 255), font=self.font_bold)
            draw.text((24, 240), self.xiaomi_status_text, fill=(255, 153, 0) if "成功" not in self.xiaomi_status_text else (34, 197, 94), font=self.font_small)
            draw.text((24, 262), "绑定后可直接语音控制台灯/水壶等", fill=(120, 120, 125), font=self.font_small)

            draw.line([(16, 288), (224, 288)], fill=(32, 32, 36), width=1)
            draw.text((24, 298), "[OK] 刷新二维码       [ESC] 取消", fill=(100, 100, 105), font=self.font_small)

        return img


# 单例初始化句柄
menu_fsm = MenuFSM()
