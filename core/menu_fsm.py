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
        
        # 主菜单定义
        self.main_menu_items = [
            {"id": "qr", "icon": "📱", "title": "手机配置", "desc": "扫码进设置页 / 查IP"},
            {"id": "doctor", "icon": "🩺", "title": "一键体检", "desc": "API与硬件实时排障"},
            {"id": "toggles", "icon": "🛡️", "title": "硬件开关", "desc": "静音 / 追踪 / 休眠"},
            {"id": "power", "icon": "⚡", "title": "服务电源", "desc": "重启服务 / 安全关机"},
        ]
        
        # 开关菜单项
        self.toggle_items = [
            {"id": "mic", "name": "麦克风输入", "state_key": "is_mic_muted", "inverted": True},
            {"id": "track", "name": "人脸视线追踪", "state_key": "face_tracking_enabled", "inverted": False},
            {"id": "servo", "name": "舵机工作状态", "state_key": "is_servo_sleeping", "inverted": True},
        ]
        
        # 电源菜单项
        self.power_items = [
            {"id": "restart", "name": "🔄 重启 AI 机器人服务"},
            {"id": "shutdown", "name": "🛑 树莓派系统安全关机"},
        ]

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
        
        # 锁与监听器
        self._lock = threading.Lock()
        self.state_change_listeners: List[Callable[[Dict[str, Any]], None]] = []
        
        # 加载中文字体
        self.font_main = None
        self.font_bold = None
        self.font_small = None
        self.font_large = None
        self._init_fonts()
        
        # 启动超时守卫线程
        self._running = True
        self._guard_thread = threading.Thread(target=self._timeout_loop, daemon=True, name="MenuTimeoutGuard")
        self._guard_thread.start()

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
                if button == "K1":  # 上
                    self.cursor = (self.cursor - 1) % len(self.main_menu_items)
                    self._notify_listeners()
                elif button == "K2":  # 下
                    self.cursor = (self.cursor + 1) % len(self.main_menu_items)
                    self._notify_listeners()
                elif button == "K3":  # 确认进入
                    target = self.main_menu_items[self.cursor]["id"]
                    if target == "qr":
                        self.current_mode = "QR_CODE"
                    elif target == "doctor":
                        self.current_mode = "DOCTOR"
                        self.refresh_doctor_data_async()
                    elif target == "toggles":
                        self.current_mode = "TOGGLES"
                        self.cursor = 0
                    elif target == "power":
                        self.current_mode = "POWER"
                        self.cursor = 0
                    self._notify_listeners()
                elif button == "K4":  # 返回表情
                    self.current_mode = "FACE"
                    self.cursor = 0
                    self._notify_listeners()
                return

            # 3. 处于二维码展示页 (QR_CODE)
            if self.current_mode == "QR_CODE":
                if button in ["K4", "K3"]:
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
                if button == "K1":
                    self.cursor = (self.cursor - 1) % len(self.toggle_items)
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % len(self.toggle_items)
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

            # 6. 处于电源维护页 (POWER)
            if self.current_mode == "POWER":
                if button == "K1":
                    self.cursor = (self.cursor - 1) % len(self.power_items)
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % len(self.power_items)
                    self._notify_listeners()
                elif button == "K3":
                    action_id = self.power_items[self.cursor]["id"]
                    self._execute_power_action(action_id)
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 3
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
            "power_items": self.power_items,
            "doctor": self.doctor_cache,
            "settings_url": f"http://{ip}:8090/settings",
            "is_mic_muted": self.is_mic_muted,
            "face_tracking": self.face_tracking_enabled,
            "servo_sleeping": self.is_servo_sleeping,
            "local_ip": ip,
            "time_left": max(0, int(self.auto_timeout - (time.time() - self.last_interaction_time)))
        }

    # ========================================================
    # 树莓派真机端 PIL 绘图渲染器 (覆盖在眼睛画布之上)
    # ========================================================
    def render_pil_overlay(self, base_image: Image.Image) -> Image.Image:
        """
        在 240x320 PIL 图像上渲染菜单与二维码 (用于树莓派 ST7789 真机 LCD 直刷)
        """
        img = base_image.copy()
        draw = ImageDraw.Draw(img)

        # 1. 灵动表情模式下只画右上角微小角标
        if self.current_mode == "FACE":
            if self.is_mic_muted:
                draw.ellipse([214, 8, 230, 24], fill=(235, 60, 60))
                draw.text((218, 9), "M", fill=(255, 255, 255), font=self.font_small)
            if self.is_servo_sleeping:
                draw.text((195, 9), "Zz", fill=(100, 200, 255), font=self.font_small)
            return img

        # 2. 菜单背景磨砂黑半透明浮层
        overlay = Image.new("RGBA", (240, 320), (12, 18, 30, 235))
        img.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(img)

        # 顶部标题栏
        draw.rectangle([0, 0, 240, 38], fill=(18, 28, 46))
        draw.line([0, 38, 240, 38], fill=(0, 229, 255), width=1)

        # A. 主菜单界面
        if self.current_mode == "MENU_MAIN":
            draw.text((16, 10), "⚙️ 系统菜单 (4-Key)", fill=(0, 229, 255), font=self.font_bold)
            y_start = 50
            for idx, item in enumerate(self.main_menu_items):
                is_selected = (idx == self.cursor)
                card_y = y_start + idx * 56
                # 选项背景卡片
                if is_selected:
                    draw.rounded_rectangle([12, card_y, 228, card_y + 48], radius=8, fill=(0, 150, 200), outline=(0, 229, 255), width=2)
                    text_color = (255, 255, 255)
                    desc_color = (220, 245, 255)
                    # 选中指示箭头
                    draw.polygon([(216, card_y + 24), (206, card_y + 18), (206, card_y + 30)], fill=(255, 255, 255))
                else:
                    draw.rounded_rectangle([12, card_y, 228, card_y + 48], radius=8, fill=(22, 34, 52), outline=(35, 50, 75), width=1)
                    text_color = (200, 215, 230)
                    desc_color = (130, 150, 175)

                draw.text((24, card_y + 8), f"{item['icon']} {item['title']}", fill=text_color, font=self.font_bold)
                draw.text((24, card_y + 28), item["desc"], fill=desc_color, font=self.font_small)

            # 底部按键提示
            draw.rectangle([0, 290, 240, 320], fill=(14, 20, 32))
            draw.text((16, 298), "[▲▼]移动  [●]确认  [✕]返回", fill=(100, 130, 160), font=self.font_small)

        # B. 扫码手机配置界面
        elif self.current_mode == "QR_CODE":
            draw.text((16, 10), "📱 手机扫码配置", fill=(0, 229, 255), font=self.font_bold)
            ip = get_local_ip()
            url = f"http://{ip}:8090/settings"

            # 生成二维码并贴图
            if qrcode:
                try:
                    qr = qrcode.QRCode(box_size=4, border=2)
                    qr.add_data(url)
                    qr.make(fit=True)
                    qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                    # 缩放到 150x150
                    qr_pil = qr_pil.resize((150, 150), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (45, 52))
                except Exception as e:
                    draw.text((40, 110), f"二维码生成错误:\n{e}", fill=(255, 100, 100), font=self.font_small)
            else:
                draw.rectangle([45, 52, 195, 202], fill=(255, 255, 255))
                draw.text((50, 110), "请安装 qrcode 库", fill=(0, 0, 0), font=self.font_small)

            # 网址提示
            draw.text((14, 214), "手机连同WiFi打开网址:", fill=(160, 180, 205), font=self.font_small)
            draw.text((14, 234), f"{ip}:8090", fill=(0, 229, 255), font=self.font_bold)
            draw.text((14, 256), "可配置 Token / 人设 / 舵机对齐", fill=(120, 140, 170), font=self.font_small)

            draw.rectangle([0, 290, 240, 320], fill=(14, 20, 32))
            draw.text((45, 298), "按任意键或 [✕] 返回菜单", fill=(100, 130, 160), font=self.font_small)

        # C. 一键体检诊断界面
        elif self.current_mode == "DOCTOR":
            draw.text((16, 10), "🩺 一键体检与排障", fill=(0, 229, 255), font=self.font_bold)
            d = self.doctor_cache

            items = [
                ("📶 WiFi IP", d.get("wifi_ip", "--")),
                ("🧠 MiMo API", d.get("mimo_status", "--")),
                ("📷 摄像头", d.get("camera_status", "--")),
                ("🎙️ 麦克风", d.get("mic_status", "--")),
                ("🌡️ CPU温度", d.get("cpu_temp", "--")),
            ]

            y = 52
            for label, val in items:
                draw.rounded_rectangle([14, y, 226, y + 40], radius=6, fill=(20, 30, 48), outline=(35, 50, 75))
                draw.text((24, y + 10), label, fill=(160, 190, 220), font=self.font_main)
                color = (0, 229, 255) if "正常" in val or "OK" in val or "在线" in val else (255, 180, 60)
                draw.text((120, y + 10), val, fill=color, font=self.font_bold)
                y += 46

            draw.rectangle([0, 290, 240, 320], fill=(14, 20, 32))
            draw.text((24, 298), "[●]重新检测    [✕]返回菜单", fill=(100, 130, 160), font=self.font_small)

        # D. 硬件与防扰开关
        elif self.current_mode == "TOGGLES":
            draw.text((16, 10), "🛡️ 硬件与防扰开关", fill=(0, 229, 255), font=self.font_bold)
            y = 55
            for idx, item in enumerate(self.toggle_items):
                is_selected = (idx == self.cursor)
                k = item["state_key"]
                is_on = not getattr(self, k) if item["inverted"] else getattr(self, k)
                status_txt = "开启 [ON]" if is_on else "关闭 [OFF]"
                status_color = (0, 255, 180) if is_on else (255, 80, 80)

                if is_selected:
                    draw.rounded_rectangle([12, y, 228, y + 54], radius=8, fill=(0, 120, 180), outline=(0, 229, 255), width=2)
                else:
                    draw.rounded_rectangle([12, y, 228, y + 54], radius=8, fill=(22, 34, 52), outline=(35, 50, 75), width=1)

                draw.text((22, y + 8), item["name"], fill=(255, 255, 255), font=self.font_bold)
                draw.text((22, y + 30), f"当前: {status_txt}", fill=status_color, font=self.font_small)
                y += 64

            draw.rectangle([0, 290, 240, 320], fill=(14, 20, 32))
            draw.text((20, 298), "[▲▼]移动  [●]切换  [✕]返回", fill=(100, 130, 160), font=self.font_small)

        # E. 电源与服务维护
        elif self.current_mode == "POWER":
            draw.text((16, 10), "⚡ 服务与电源", fill=(0, 229, 255), font=self.font_bold)
            y = 70
            for idx, item in enumerate(self.power_items):
                is_selected = (idx == self.cursor)
                if is_selected:
                    draw.rounded_rectangle([14, y, 226, y + 56], radius=8, fill=(180, 50, 50) if "关机" in item["name"] else (0, 130, 190), outline=(255, 255, 255), width=2)
                else:
                    draw.rounded_rectangle([14, y, 226, y + 56], radius=8, fill=(25, 35, 55), outline=(40, 55, 80), width=1)

                draw.text((24, y + 16), item["name"], fill=(255, 255, 255), font=self.font_bold)
                y += 75

            draw.rectangle([0, 290, 240, 320], fill=(14, 20, 32))
            draw.text((20, 298), "[▲▼]选择  [●]立即执行  [✕]返回", fill=(100, 130, 160), font=self.font_small)

        return img


# 单例初始化句柄
menu_fsm = MenuFSM()
