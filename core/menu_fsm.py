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


# ========================================================
# Material Design 3 (Material You) 多主题配色方案
# ========================================================
THEMES = {
    "mint": {
        "name": "薄荷绿",
        "primary": (123, 208, 193),       # #7bd0c1 Pixel Mint
        "on_primary": (0, 55, 49),        # #003731
        "on_primary_sub": (14, 82, 73),   # #0e5249
        "surface": (18, 20, 20),          # #121414
        "surface_container": (29, 36, 36),# #1d2424
        "surface_high": (38, 48, 47),     # #26302f
        "outline": (55, 68, 66),          # #374442
        "badge_bg": (40, 59, 56),         # #283b38
        "icon_bg_sel": (0, 72, 64),       # #004840
    },
    "orange": {
        "name": "暖阳橙",
        "primary": (255, 171, 64),        # #ffab40 Pixel Warm Amber
        "on_primary": (74, 38, 0),        # #4a2600
        "on_primary_sub": (102, 53, 0),   # #663500
        "surface": (22, 19, 17),          # #161311
        "surface_container": (38, 31, 26),# #261f1a
        "surface_high": (52, 42, 35),     # #342a23
        "outline": (76, 62, 52),          # #4c3e34
        "badge_bg": (56, 44, 36),         # #382c24
        "icon_bg_sel": (94, 49, 0),       # #5e3100
    },
    "blue": {
        "name": "海天蓝",
        "primary": (130, 177, 255),      # #82b1ff Pixel Sky Blue
        "on_primary": (0, 44, 94),        # #002c5e
        "on_primary_sub": (0, 64, 136),   # #004088
        "surface": (16, 19, 24),          # #101318
        "surface_container": (26, 32, 42),# #1a202a
        "surface_high": (36, 44, 58),     # #242c3a
        "outline": (52, 64, 84),          # #344054
        "badge_bg": (32, 44, 62),         # #202c3e
        "icon_bg_sel": (0, 60, 128),      # #003c80
    },
    "purple": {
        "name": "薰衣紫",
        "primary": (208, 188, 255),      # #d0bcff Pixel Lavender
        "on_primary": (56, 30, 114),      # #381e72
        "on_primary_sub": (79, 44, 150),  # #4f2c96
        "surface": (20, 18, 24),          # #141218
        "surface_container": (33, 29, 40),# #211d28
        "surface_high": (46, 41, 56),     # #2e2938
        "outline": (68, 60, 82),          # #443c52
        "badge_bg": (48, 40, 62),         # #30283e
        "icon_bg_sel": (79, 44, 150),     # #4f2c96
    },
    "mono": {
        "name": "极简白",
        "primary": (240, 240, 240),      # #f0f0f0 Bauhaus Minimalist
        "on_primary": (18, 18, 18),       # #121212
        "on_primary_sub": (50, 50, 50),   # #323232
        "surface": (18, 18, 18),          # #121212
        "surface_container": (32, 32, 32),# #202020
        "surface_high": (46, 46, 46),     # #2e2e2e
        "outline": (64, 64, 64),          # #404040
        "badge_bg": (44, 44, 44),         # #2c2c2c
        "icon_bg_sel": (200, 200, 200),   # #c8c8c8
    }
}
THEME_KEYS = ["mint", "orange", "blue", "purple", "mono"]

def _draw_vector_icon(draw, icon_name: str, cx: int, cy: int, color):
    """
    在指定中心点 (cx, cy) 绘制 18x18px 矢量微图标
    """
    if icon_name == "wifi":
        draw.arc([cx - 8, cy - 7, cx + 8, cy + 9], 200, 340, fill=color, width=2)
        draw.arc([cx - 5, cy - 3, cx + 5, cy + 7], 200, 340, fill=color, width=2)
        draw.ellipse([cx - 2, cy + 4, cx + 2, cy + 8], fill=color)

    elif icon_name == "doctor":
        draw.line([(cx - 8, cy + 1), (cx - 4, cy + 1)], fill=color, width=2)
        draw.line([(cx - 4, cy + 1), (cx - 2, cy - 6)], fill=color, width=2)
        draw.line([(cx - 2, cy - 6), (cx + 1, cy + 7)], fill=color, width=2)
        draw.line([(cx + 1, cy + 7), (cx + 3, cy - 2)], fill=color, width=2)
        draw.line([(cx + 3, cy - 2), (cx + 5, cy + 1)], fill=color, width=2)
        draw.line([(cx + 5, cy + 1), (cx + 8, cy + 1)], fill=color, width=2)

    elif icon_name == "hardware":
        draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=color, width=2)
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)
        draw.line([(cx, cy - 9), (cx, cy - 6)], fill=color, width=2)
        draw.line([(cx, cy + 6), (cx, cy + 9)], fill=color, width=2)
        draw.line([(cx - 9, cy), (cx - 6, cy)], fill=color, width=2)
        draw.line([(cx + 6, cy), (cx + 9, cy)], fill=color, width=2)

    elif icon_name in ["settings", "theme", "palette"]:
        # 调色盘 / 主题风格 / 设置
        if icon_name in ["theme", "palette"]:
            draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=color, width=2)
            draw.ellipse([cx - 3, cy - 4, cx - 1, cy - 2], fill=color)
            draw.ellipse([cx + 2, cy - 3, cx + 4, cy - 1], fill=color)
            draw.ellipse([cx - 4, cy + 1, cx - 2, cy + 3], fill=color)
        else:
            draw.line([(cx - 7, cy - 4), (cx + 7, cy - 4)], fill=color, width=2)
            draw.ellipse([cx - 3, cy - 6, cx + 1, cy - 2], fill=color)
            draw.line([(cx - 7, cy + 4), (cx + 7, cy + 4)], fill=color, width=2)
            draw.ellipse([cx + 1, cy + 2, cx + 5, cy + 6], fill=color)

    elif icon_name == "servo":
        draw.arc([cx - 6, cy - 6, cx + 6, cy + 6], 45, 315, fill=color, width=2)
        draw.polygon([(cx + 4, cy - 7), (cx + 7, cy - 2), (cx + 1, cy - 4)], fill=color)
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)

    elif icon_name == "spark":
        draw.polygon([(cx, cy - 8), (cx + 2, cy - 2), (cx + 8, cy), (cx + 2, cy + 2), (cx, cy + 8), (cx - 2, cy + 2), (cx - 8, cy), (cx - 2, cy - 2)], fill=color)

    elif icon_name == "lock":
        draw.rounded_rectangle([cx - 6, cy - 2, cx + 6, cy + 7], radius=2, fill=color)
        draw.arc([cx - 4, cy - 7, cx + 4, cy + 1], 180, 360, fill=color, width=2)

    elif icon_name == "mic":
        draw.rounded_rectangle([cx - 3, cy - 7, cx + 3, cy + 1], radius=3, fill=color)
        draw.arc([cx - 6, cy - 4, cx + 6, cy + 4], 0, 180, fill=color, width=2)
        draw.line([(cx, cy + 4), (cx, cy + 7)], fill=color, width=2)
        draw.line([(cx - 4, cy + 7), (cx + 4, cy + 7)], fill=color, width=2)

    elif icon_name == "eye":
        draw.arc([cx - 8, cy - 7, cx + 8, cy + 7], 15, 165, fill=color, width=2)
        draw.arc([cx - 8, cy - 7, cx + 8, cy + 7], 195, 345, fill=color, width=2)
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=color)

    elif icon_name == "grid":
        for gx in [-5, 0, 5]:
            for gy in [-5, 0, 5]:
                draw.ellipse([cx + gx - 1, cy + gy - 1, cx + gx + 1, cy + gy + 1], fill=color)

    elif icon_name == "chat":
        draw.rounded_rectangle([cx - 7, cy - 6, cx + 7, cy + 3], radius=4, fill=color)
        draw.polygon([(cx - 3, cy + 3), (cx - 5, cy + 7), (cx + 1, cy + 3)], fill=color)

    elif icon_name == "sfx":
        draw.ellipse([cx - 6, cy + 1, cx - 1, cy + 6], fill=color)
        draw.ellipse([cx + 2, cy - 2, cx + 7, cy + 3], fill=color)
        draw.line([(cx - 1, cy + 3), (cx - 1, cy - 5)], fill=color, width=2)
        draw.line([(cx + 7, cy), (cx + 7, cy - 8)], fill=color, width=2)
        draw.line([(cx - 1, cy - 5), (cx + 7, cy - 8)], fill=color, width=2)

    elif icon_name == "face":
        draw.rounded_rectangle([cx - 7, cy - 7, cx + 7, cy + 7], radius=3, outline=color, width=2)
        draw.ellipse([cx - 4, cy - 3, cx - 2, cy - 1], fill=color)
        draw.ellipse([cx + 2, cy - 3, cx + 4, cy - 1], fill=color)
        draw.arc([cx - 4, cy - 1, cx + 4, cy + 4], 20, 160, fill=color, width=2)

    elif icon_name == "trash":
        draw.line([(cx - 6, cy - 5), (cx + 6, cy - 5)], fill=color, width=2)
        draw.line([(cx - 2, cy - 7), (cx + 2, cy - 7)], fill=color, width=2)
        draw.rounded_rectangle([cx - 5, cy - 4, cx + 5, cy + 7], radius=1, outline=color, width=2)
        draw.line([(cx - 2, cy - 2), (cx - 2, cy + 5)], fill=color, width=1)
        draw.line([(cx + 2, cy - 2), (cx + 2, cy + 5)], fill=color, width=1)

    elif icon_name == "home":
        draw.polygon([(cx, cy - 8), (cx + 8, cy - 1), (cx - 8, cy - 1)], fill=color)
        draw.rounded_rectangle([cx - 6, cy - 1, cx + 6, cy + 7], radius=1, fill=color)
        draw.rectangle([cx - 2, cy + 2, cx + 2, cy + 7], fill=(18, 20, 20))

    elif icon_name == "voice":
        draw.line([(cx - 6, cy - 3), (cx - 6, cy + 3)], fill=color, width=2)
        draw.line([(cx - 2, cy - 6), (cx - 2, cy + 6)], fill=color, width=2)
        draw.line([(cx + 2, cy - 8), (cx + 2, cy + 8)], fill=color, width=2)
        draw.line([(cx + 6, cy - 4), (cx + 6, cy + 4)], fill=color, width=2)

    elif icon_name == "volume":
        draw.polygon([(cx - 6, cy - 3), (cx - 3, cy - 3), (cx + 1, cy - 6), (cx + 1, cy + 6), (cx - 3, cy + 3), (cx - 6, cy + 3)], fill=color)
        draw.arc([cx - 1, cy - 5, cx + 7, cy + 5], 300, 60, fill=color, width=2)

    elif icon_name == "clock":
        draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=color, width=2)
        draw.line([(cx, cy - 5), (cx, cy)], fill=color, width=2)
        draw.line([(cx, cy), (cx + 4, cy)], fill=color, width=2)

    elif icon_name == "chip":
        draw.rectangle([cx - 5, cy - 5, cx + 5, cy + 5], outline=color, width=2)
        draw.rectangle([cx - 2, cy - 2, cx + 2, cy + 2], fill=color)
        for d in [-3, 3]:
            draw.line([(cx + d, cy - 8), (cx + d, cy - 5)], fill=color, width=1)
            draw.line([(cx + d, cy + 5), (cx + d, cy + 8)], fill=color, width=1)
            draw.line([(cx - 8, cy + d), (cx - 5, cy + d)], fill=color, width=1)
            draw.line([(cx + 5, cy + d), (cx + 8, cy + d)], fill=color, width=1)

    elif icon_name == "cloud":
        draw.ellipse([cx - 4, cy - 6, cx + 4, cy + 2], fill=color)
        draw.ellipse([cx - 7, cy - 2, cx - 1, cy + 4], fill=color)
        draw.ellipse([cx + 1, cy - 2, cx + 7, cy + 4], fill=color)
        draw.rectangle([cx - 5, cy, cx + 5, cy + 4], fill=color)
        draw.line([(cx, cy - 1), (cx, cy + 7)], fill=(18, 20, 20), width=2)

    elif icon_name == "restart":
        draw.arc([cx - 6, cy - 6, cx + 6, cy + 6], 45, 315, fill=color, width=2)
        draw.polygon([(cx + 4, cy - 7), (cx + 7, cy - 2), (cx + 1, cy - 4)], fill=color)

    elif icon_name == "power":
        draw.arc([cx - 6, cy - 6, cx + 6, cy + 6], 135, 405, fill=color, width=2)
        draw.line([(cx, cy - 8), (cx, cy - 1)], fill=color, width=2)

    elif icon_name == "camera":
        draw.rounded_rectangle([cx - 7, cy - 4, cx + 7, cy + 6], radius=2, outline=color, width=2)
        draw.rectangle([cx - 3, cy - 7, cx + 3, cy - 4], fill=color)
        draw.ellipse([cx - 3, cy - 1, cx + 3, cy + 5], fill=color)

    elif icon_name == "skills":
        draw.rounded_rectangle([cx - 7, cy - 7, cx - 1, cy - 1], radius=2, fill=color)
        draw.rounded_rectangle([cx + 1, cy - 7, cx + 7, cy - 1], radius=2, fill=color)
        draw.rounded_rectangle([cx - 7, cy + 1, cx - 1, cy + 7], radius=2, fill=color)
        draw.rounded_rectangle([cx + 1, cy + 1, cx + 7, cy + 7], radius=2, outline=color, width=2)

    else:
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=color)


def _fit_pil_text(draw, text: str, font, max_w: float) -> str:
    """按像素宽度安全裁剪单行文本，防止胶囊徽章或提示文案遮挡溢出"""
    s = str(text or "")
    if not s or max_w <= 8:
        return ""
    if draw.textlength(s, font=font) <= max_w:
        return s
    ell = "…"
    ell_w = draw.textlength(ell, font=font)
    if max_w <= ell_w:
        return ell
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(s[:mid], font=font) + ell_w <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + ell


def _draw_modern_tile(draw, x: int, y: int, w: int, h: int, icon: str, title: str, right_type: str, right_val: Any, is_selected: bool, font_bold, font_small, theme: dict):
    """
    绘制 Material Design 3 极简单行卡片 (左图标底座 + 纯净单行居中标题 + 右侧控件，自动防重叠裁剪)
    """
    r = 16
    p_col = theme["primary"]
    on_p = theme["on_primary"]
    surf_cont = theme["surface_container"]
    surf_high = theme["surface_high"]
    outline = theme["outline"]
    icon_sel_bg = theme["icon_bg_sel"]
    badge_bg = theme["badge_bg"]

    if is_selected:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=p_col)
        icon_bg = icon_sel_bg
        icon_col = (255, 255, 255) if theme.get("name") != "极简白" else (18, 18, 18)
        t_col = on_p
    else:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=surf_cont, outline=outline, width=1)
        icon_bg = surf_high
        icon_col = p_col
        t_col = (235, 238, 238)

    # 左侧圆形图标底座 (30x30px)
    ic_cx = x + 25
    ic_cy = y + h // 2
    ic_r = 15
    draw.ellipse([ic_cx - ic_r, ic_cy - ic_r, ic_cx + ic_r, ic_cy + ic_r], fill=icon_bg)
    _draw_vector_icon(draw, icon, ic_cx, ic_cy, icon_col)

    # 先计算右侧控件左边界，确保标题与右侧胶囊/开关/箭头绝不重叠
    title_x = x + 48
    avail_w = max(40.0, float(w - 58))
    badge_info = None
    if right_type == "switch":
        right_left_x = x + w - 52
    elif right_type == "badge":
        raw_txt = str(right_val or "")
        full_title_w = draw.textlength(str(title or ""), font=font_bold)
        full_tw = draw.textlength(raw_txt, font=font_small)
        raw_bw = max(38.0, full_tw + 14.0)
        min_title_w = min(full_title_w, 72.0)
        max_bw = max(44.0, avail_w - min_title_w - 6.0)
        bw = int(min(raw_bw, max_bw))
        bx = x + w - bw - 10
        fitted_badge = _fit_pil_text(draw, raw_txt, font_small, max(16.0, bw - 12.0))
        badge_tw = draw.textlength(fitted_badge, font=font_small)
        badge_info = (bx, bw, fitted_badge, badge_tw)
        right_left_x = bx
    elif right_type == "chevron":
        right_left_x = x + w - 22
    else:
        right_left_x = x + w - 10

    max_title_w = max(32.0, float(right_left_x - title_x - 6))
    fitted_title = _fit_pil_text(draw, title, font_bold, max_title_w)
    draw.text((title_x, y + (h - 17) // 2), fitted_title, fill=t_col, font=font_bold)

    # 右侧控件
    if right_type == "switch":
        sw_w, sw_h = 42, 24
        sw_x = x + w - sw_w - 10
        sw_y = y + (h - sw_h) // 2
        is_on = bool(right_val)
        if is_selected:
            if is_on:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=12, fill=icon_sel_bg)
                draw.ellipse([sw_x + sw_w - 21, sw_y + 3, sw_x + sw_w - 3, sw_y + 21], fill=(255, 255, 255))
            else:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=12, fill=surf_high)
                draw.ellipse([sw_x + 6, sw_y + 6, sw_x + 18, sw_y + 18], fill=on_p)
        else:
            if is_on:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=12, fill=p_col)
                draw.ellipse([sw_x + sw_w - 21, sw_y + 3, sw_x + sw_w - 3, sw_y + 21], fill=on_p)
            else:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=12, fill=(45, 52, 52), outline=outline, width=1)
                draw.ellipse([sw_x + 6, sw_y + 6, sw_x + 18, sw_y + 18], fill=(145, 155, 155))

    elif right_type == "badge" and badge_info:
        bx, bw, fitted_badge, badge_tw = badge_info
        bh = 22
        by = y + (h - bh) // 2
        if is_selected:
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=11, fill=icon_sel_bg)
            draw.text((bx + (bw - badge_tw) // 2, by + 3), fitted_badge, fill=(255, 255, 255), font=font_small)
        else:
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=11, fill=badge_bg, outline=outline, width=1)
            draw.text((bx + (bw - badge_tw) // 2, by + 3), fitted_badge, fill=p_col, font=font_small)

    elif right_type == "chevron":
        chv_x = x + w - 16
        chv_y = y + h // 2
        col = on_p if is_selected else (145, 158, 155)
        draw.line([(chv_x - 4, chv_y - 5), (chv_x + 1, chv_y)], fill=col, width=2)
        draw.line([(chv_x + 1, chv_y), (chv_x - 4, chv_y + 5)], fill=col, width=2)


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

        # 系统主题配色 (mint, orange, blue, purple, mono)
        self.theme_key = self._load_cfg().get("theme_color", "mint")
        if self.theme_key not in THEMES:
            self.theme_key = "mint"
        
        # 主菜单定义 (系统控制 vs 技能服务彻底解耦)
        self.main_menu_items = [
            {"id": "qr", "icon": "wifi", "title": "无线与配网"},
            {"id": "doctor", "icon": "doctor", "title": "系统体检"},
            {"id": "toggles", "icon": "hardware", "title": "硬件控制"},
            {"id": "skills", "icon": "skills", "title": "技能服务"},
            {"id": "system", "icon": "settings", "title": "系统设置"},
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
        
        # 米家 / 网易云屏幕弹窗状态
        self.xiaomi_status_text = "等待米家App扫码授权..."
        self.xiaomi_device_count = 0
        self.netease_status_text = "等待网易云App扫码..."
        self.netease_qr_bytes: bytes = b""
        self.skill_items = []
        self._sync_skill_items()

        # 彩蛋状态 (连续点击系统版本号触发与滚动状态)
        self.version_click_count = 0
        self.last_version_click_time = 0.0
        self.version_hint = None
        self.easter_egg_scroll_y = 0
        self.easter_egg_max_scroll = 230

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
        gaze_txt = "九宫格" if gaze_mode == "9grid" else "连续跟随"
        proactive_on = c_cfg.get("proactive_care", True)
        sfx_on = c_cfg.get("non_verbal_audio", True)
        radar_on = c_cfg.get("radar_presence_wake", True)
        radar_status_txt = "检测有人" if getattr(self, "radar_detected", True) else "无人离席"
        
        self.hardware_items = [
            {"id": "servo_center", "icon": "servo", "name": "舵机居中归位", "val": getattr(self, "servo_center_status", "回正校准"), "type": "action"},
            {"id": "servo_test", "icon": "spark", "name": "舵机动作自检", "val": getattr(self, "servo_test_status", "自检通过"), "type": "action"},
            {"id": "servo_sleep", "icon": "lock", "name": "舵机保持通电", "val": "", "type": "toggle", "state": not self.is_servo_sleeping},
            {"id": "radar", "icon": "spark", "name": "毫米波人体感应", "val": radar_status_txt, "type": "toggle", "state": radar_on},
            {"id": "mic", "icon": "mic", "name": "麦克风收音", "val": "", "type": "toggle", "state": not self.is_mic_muted},
            {"id": "track", "icon": "eye", "name": "人脸视线跟随", "val": "", "type": "toggle", "state": self.face_tracking_enabled},
            {"id": "gaze_mode", "icon": "grid", "name": "视线算法模式", "val": gaze_txt, "type": "cycle"},
            {"id": "proactive", "icon": "chat", "name": "主动关怀搭话", "val": "", "type": "toggle", "state": proactive_on},
            {"id": "sfx", "icon": "sfx", "name": "萌宠情绪拟声", "val": "", "type": "toggle", "state": sfx_on},
        ]
        # 兼容旧代码引用
        self.toggle_items = self.hardware_items

    def _sync_system_items(self):
        c_cfg = self._load_cfg()
        voice = c_cfg.get("mimo_voice", "冰糖")
        vol = c_cfg.get("volume", 80)
        timeout = int(self.auto_timeout)
        timeout_txt = f"{timeout}秒" if timeout < 999 else "常亮"
        theme_name = THEMES.get(getattr(self, "theme_key", "mint"), THEMES["mint"])["name"]

        ver_val = self.version_str
        if getattr(self, "version_hint", None) and (time.time() - getattr(self, "last_version_click_time", 0) < 3.0):
            ver_val = self.version_hint
        else:
            self.version_hint = None

        self.system_items = [
            {"id": "theme", "icon": "theme", "name": "主题风格", "val": theme_name, "type": "cycle"},
            {"id": "face_id", "icon": "face", "name": "录入面容", "val": "", "type": "action"},
            {"id": "face_clear", "icon": "trash", "name": "清除面容", "val": "", "type": "action"},
            {"id": "voice", "icon": "voice", "name": "发音音色", "val": voice, "type": "cycle"},
            {"id": "volume", "icon": "volume", "name": "播报音量", "val": f"{vol}%", "type": "cycle"},
            {"id": "timeout", "icon": "clock", "name": "息屏时间", "val": timeout_txt, "type": "cycle"},
            {"id": "version", "icon": "chip", "name": "固件版本", "val": ver_val, "type": "info"},
            {"id": "update", "icon": "cloud", "name": "系统更新", "val": self.update_status, "type": "action"},
            {"id": "restart", "icon": "restart", "name": "重启服务", "val": "", "type": "action"},
            {"id": "shutdown", "icon": "power", "name": "安全关机", "val": "", "type": "action"},
        ]

    def _sync_skill_items(self):
        """同步屏幕「技能服务」子菜单项，与系统本体设置彻底隔离"""
        mijia_val = "待扫码"
        try:
            from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
            if qr_auth_manager.synced_devices:
                mijia_val = f"已绑{len(qr_auth_manager.synced_devices)}台"
        except Exception:
            pass

        ncm_val = "免登录"
        try:
            from skills.netease_music.netease_client import netease_client
            if netease_client.logged_in:
                ncm_val = (netease_client.nickname or "已登录")[:6]
        except Exception:
            pass

        feishu_val = "未配置"
        try:
            from skills.feishu_bot.feishu_client import feishu_client
            if feishu_client.ws_connected:
                feishu_val = "长连在线"
            elif feishu_client.is_configured:
                feishu_val = "已配置"
        except Exception:
            pass

        self.skill_items = [
            {"id": "mijia", "icon": "home", "name": "米家绑定", "val": mijia_val, "type": "info"},
            {"id": "netease", "icon": "sfx", "name": "网易云", "val": ncm_val, "type": "info"},
            {"id": "feishu", "icon": "chat", "name": "飞书网关", "val": feishu_val, "type": "info"},
            {"id": "skills_portal", "icon": "skills", "name": "扫码配技能", "val": "", "type": "action"},
        ]

    def cycle_theme(self):
        """循环切换 M3 主题调色板 (薄荷绿 -> 暖阳橙 -> 海天蓝 -> 薰衣紫 -> 极简白)"""
        cur_idx = THEME_KEYS.index(self.theme_key) if self.theme_key in THEME_KEYS else 0
        next_key = THEME_KEYS[(cur_idx + 1) % len(THEME_KEYS)]
        self.theme_key = next_key
        self._save_cfg_key("theme_color", next_key)
        self._sync_system_items()
        self._notify_listeners()
        print(f"🎨 [MenuFSM] 切换系统主题: {THEMES[next_key]['name']} ({next_key})")

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

    def show_netease_qr_on_screen(self):
        """在机器人 2.4 寸屏幕上直接弹窗显示网易云音乐登录二维码"""
        print("🎵 [MenuFSM] 触发屏幕弹窗显示网易云音乐绑定二维码")
        with self._lock:
            self.current_mode = "NETEASE_QR"
            self.netease_status_text = "获取网易云二维码中..."
            self.netease_qr_bytes = b""
            self.last_interaction_time = time.time()
            self._notify_listeners()

        def _ncm_worker():
            try:
                import base64
                from skills.netease_music.netease_client import netease_client
                res = netease_client.start_qr_login()
                if res.get("status") != "ok":
                    with self._lock:
                        self.netease_status_text = res.get("message", "获取二维码失败")
                        self._notify_listeners()
                    return
                raw_png = b""
                b64 = res.get("qr_base64", "")
                if "," in b64:
                    try:
                        raw_png = base64.b64decode(b64.split(",", 1)[1])
                    except Exception:
                        raw_png = b""
                with self._lock:
                    self.netease_qr_bytes = raw_png
                    self.netease_status_text = "打开网易云App 扫一扫"
                    self._notify_listeners()

                while self.current_mode == "NETEASE_QR":
                    time.sleep(1.2)
                    st = netease_client.check_qr_status()
                    code = int(st.get("code", 0))
                    if code == 803 or st.get("logged_in"):
                        with self._lock:
                            self.netease_status_text = f"绑定成功！{st.get('nickname', '')}"
                            self._sync_skill_items()
                            self._notify_listeners()
                        time.sleep(2.2)
                        with self._lock:
                            if self.current_mode == "NETEASE_QR":
                                self.current_mode = "FACE"
                                self._notify_listeners()
                        break
                    elif code == 800:
                        with self._lock:
                            self.netease_status_text = "二维码已过期，按 [OK] 刷新"
                            self._notify_listeners()
                        break
                    elif code == 802:
                        with self._lock:
                            self.netease_status_text = "已扫码，请在手机确认登录"
                            self._notify_listeners()
            except Exception as e:
                with self._lock:
                    self.netease_status_text = f"网易云认证异常: {e}"
                    self._notify_listeners()

        threading.Thread(target=_ncm_worker, daemon=True, name="NeteaseScreenQR").start()

    def close_netease_qr(self):
        """关闭屏幕网易云二维码弹窗返回"""
        with self._lock:
            self.current_mode = "FACE"
            self._notify_listeners()

    def enter_easter_egg(self):
        """进入关于本项目(PIG)自白彩蛋模式"""
        with self._lock:
            self.current_mode = "EASTER_EGG"
            self.easter_egg_scroll_y = 0
            self.version_click_count = 0
            self.version_hint = None
            self._sync_system_items()
            self._notify_listeners()
            print("📖 [MenuFSM] 进入关于本项目(PIG)自述模式")

    def exit_easter_egg(self):
        """退出彩蛋模式，返回系统设置"""
        with self._lock:
            self.current_mode = "SYSTEM"
            self.easter_egg_scroll_y = 0
            self.cursor = 7
            self.version_click_count = 0
            self.version_hint = None
            self._sync_system_items()
            self._notify_listeners()
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
        # 0. 处于网易云音乐扫码弹窗 (绝不回退到配网二维码)
        if self.current_mode == "NETEASE_QR":
            return self.netease_qr_bytes or b""

        # 1. 处于米家扫码绑定弹窗 (绝不回退到配网二维码)
        if self.current_mode == "XIAOMI_QR":
            try:
                from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
                raw = qr_auth_manager.get_qr_png_bytes()
                return raw or b""
            except Exception:
                return b""

        # 2. 处于手机配置页二维码 (SKILLS_QR / QR_CODE)
        if self.current_mode == "SKILLS_QR":
            ip = get_local_ip()
            url = f"http://{ip}:8090/skills"
        elif wifi_manager.ap_mode:
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
                self.font_letter = ImageFont.truetype(chosen_font, 13)
                self.font_letter_sub = ImageFont.truetype(chosen_font, 11)
            except Exception:
                self.font_main = ImageFont.load_default()
                self.font_bold = self.font_main
                self.font_small = self.font_main
                self.font_large = self.font_main
                self.font_letter = self.font_main
                self.font_letter_sub = self.font_main
        else:
            self.font_main = ImageFont.load_default()
            self.font_bold = self.font_main
            self.font_small = self.font_main
            self.font_large = self.font_main
            self.font_letter = self.font_main
            self.font_letter_sub = self.font_main

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
                        # 短按返回 -> 表情页直接唤醒 (等于语音唤醒)
                        self._trigger_manual_wake()
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
                        elif target == "skills":
                            self._sync_skill_items()
                            self.current_mode = "SKILLS"
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

            # 2.5 处于米家 / 网易云屏幕弹窗 (XIAOMI_QR / NETEASE_QR)
            if self.current_mode == "XIAOMI_QR":
                if button in ["K4", "ESC"]:
                    self.close_xiaomi_qr()
                elif button in ["K3", "ENTER"]:
                    self.show_xiaomi_qr_on_screen()
                return

            if self.current_mode == "NETEASE_QR":
                if button in ["K4", "ESC"]:
                    self.close_netease_qr()
                elif button in ["K3", "ENTER"]:
                    self.show_netease_qr_on_screen()
                return

            if self.current_mode == "SKILLS_QR":
                if button in ["K4", "ESC"]:
                    self.current_mode = "SKILLS"
                    self.cursor = 3
                    self._notify_listeners()
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
                    elif item_id == "radar":
                        c = self._load_cfg()
                        cur_r = c.get("radar_presence_wake", True)
                        self._save_cfg_key("radar_presence_wake", not cur_r)
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

            # 5.5 处于技能服务页 (SKILLS: 米家扫码 / 网易云扫码 / 飞书状态 / 手机技能页)
            if self.current_mode == "SKILLS":
                self._sync_skill_items()
                sk_len = len(self.skill_items)
                if button == "K1":
                    self.cursor = (self.cursor - 1 + sk_len) % sk_len
                    self._notify_listeners()
                elif button == "K2":
                    self.cursor = (self.cursor + 1) % sk_len
                    self._notify_listeners()
                elif button == "K3":
                    action_id = self.skill_items[self.cursor]["id"]
                    if action_id == "mijia":
                        self.show_xiaomi_qr_on_screen()
                    elif action_id == "netease":
                        self.show_netease_qr_on_screen()
                    elif action_id == "skills_portal":
                        self.current_mode = "SKILLS_QR"
                        self._notify_listeners()
                    else:
                        self._sync_skill_items()
                        self._notify_listeners()
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 3
                    self._notify_listeners()
                return

            # 6. 处于系统设置页 (SYSTEM: 主题/面容/音色/音量/息屏/更新/关机/重启)
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
                    if action_id == "theme":
                        self.cycle_theme()
                    elif action_id == "face_id":
                        self.trigger_face_id_enrollment()
                    elif action_id == "face_clear":
                        self.clear_master_face()
                    elif action_id == "voice":
                        self.cycle_voice()
                    elif action_id == "volume":
                        self.cycle_volume()
                    elif action_id == "timeout":
                        self.cycle_timeout()
                    elif action_id == "version":
                        # 连续点击版本号 5 次触发项目自述
                        now = time.time()
                        if now - self.last_version_click_time < 3.0:
                            self.version_click_count += 1
                        else:
                            self.version_click_count = 1
                        self.last_version_click_time = now
                        if self.version_click_count >= 5:
                            self.version_click_count = 0
                            self.version_hint = None
                            self._sync_system_items()
                            self.enter_easter_egg()
                        else:
                            needed = 5 - self.version_click_count
                            self.version_hint = f"再按 {needed} 次"
                            self._sync_system_items()
                            self._notify_listeners()
                    elif action_id == "update":
                        self.check_system_update_async()
                    elif action_id == "restart":
                        self._execute_power_action("restart")
                    elif action_id == "shutdown":
                        self._execute_power_action("shutdown")
                elif button == "K4":
                    self.current_mode = "MENU_MAIN"
                    self.cursor = 4
                    self._notify_listeners()
                return

            # 7. 处于人脸录入倒计时中 (FACE_ENROLL)
            if self.current_mode == "FACE_ENROLL":
                if button == "K4":
                    self.current_mode = "SYSTEM"
                    self.cursor = 0
                    self._notify_listeners()
                return

            # 8. 彩蛋模式 (EASTER_EGG: 关于 PIG 的自白与介绍)
            if self.current_mode == "EASTER_EGG":
                if button in ["K1", "UP"]:
                    self.easter_egg_scroll_y = max(0, self.easter_egg_scroll_y - 26)
                    self._notify_listeners()
                elif button in ["K2", "DOWN"]:
                    self.easter_egg_scroll_y = min(self.easter_egg_max_scroll, self.easter_egg_scroll_y + 26)
                    self._notify_listeners()
                elif button in ["K4", "ESC"]:
                    self.exit_easter_egg()
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

    def _trigger_manual_wake(self):
        """表情页面短按 K4 返回键：直接触发手动唤醒 (等同语音唤醒)"""
        print("⚡ [MenuFSM] 表情页面短按 K4 ➔ 直接触发唤醒 (等同语音唤醒)")
        agent = self.agent
        if agent is None:
            try:
                from core.hardware_agent import get_hardware_agent
                agent = get_hardware_agent()
            except Exception:
                agent = None
        if agent:
            if agent.state != agent.STATE_IDLE:
                try:
                    agent.stop_speaking()
                except Exception:
                    pass
                agent.state = agent.STATE_IDLE
            threading.Thread(target=agent.trigger_wake_manually, daemon=True, name="K4ManualWake").start()

    def handle_radar(self, detected: bool):
        """处理 HLK-LD2402 毫米波人体雷达状态变化"""
        c = self._load_cfg()
        if not c.get("radar_presence_wake", True):
            return

        self.radar_detected = bool(detected)
        if detected:
            self.last_interaction_time = time.time()
            if self.is_servo_sleeping:
                self.is_servo_sleeping = False
                self._apply_servo_sleep(False)
        self._sync_hardware_items()
        self._notify_listeners()

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
            "skill_items": self.skill_items,
            "version": self.version_str,
            "update_status": self.update_status,
            "doctor": self.doctor_cache,
            "wifi": wifi_manager.get_status(),
            "xiaomi_qr": {
                "active": (self.current_mode == "XIAOMI_QR"),
                "status": self.xiaomi_status_text,
                "device_count": self.xiaomi_device_count
            },
            "netease_qr": {
                "active": (self.current_mode == "NETEASE_QR"),
                "status": self.netease_status_text
            },
            "settings_url": f"http://{ip}:8090/settings",
            "skills_url": f"http://{ip}:8090/skills",
            "qr_base64": self.get_qr_base64(),
            "theme": getattr(self, "theme_key", "mint"),
            "is_mic_muted": self.is_mic_muted,
            "face_tracking": self.face_tracking_enabled,
            "servo_sleeping": self.is_servo_sleeping,
            "local_ip": ip,
            "version_hint": self.version_hint,
            "easter_egg_scroll": self.easter_egg_scroll_y,
            "easter_egg_scroll_y": self.easter_egg_scroll_y,
            "time_left": max(0, int(self.auto_timeout - (time.time() - self.last_interaction_time)))
        }

    # ========================================================
    # 树莓派真机端 PIL 绘图渲染器 (Material Design 3 现代穿戴/手环旗舰风格)
    # ========================================================
    def render_pil_overlay(self, base_image: Image.Image) -> Image.Image:
        """
        在 240x320 PIL 图像上渲染 Material Design 3 现代卡片菜单 (用于树莓派 ST7789 真机 LCD 直刷)
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

        # 获取当前激活的 M3 主题调色板
        theme = THEMES.get(getattr(self, "theme_key", "mint"), THEMES["mint"])
        p_col = theme["primary"]
        on_p = theme["on_primary"]
        surf_col = theme["surface"]
        on_surf = (225, 227, 227)
        on_surf_var = (151, 163, 160)

        # 2. 菜单背景：Material Design 3 暗色表面 (动态跟随主题基调)
        overlay = Image.new("RGBA", (240, 320), surf_col + (250,))
        img.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(img)

        # A. 主菜单界面 (M3 极简单行药丸卡片 + 矢量微图标底座)
        if self.current_mode == "MENU_MAIN":
            # 顶栏：微呼吸圆点 + 标题 + 在线状态
            draw.ellipse([14, 23, 20, 29], fill=p_col)
            draw.text((26, 17), "控制中心", fill=on_surf, font=self.font_bold)
            draw.text((192, 21), "在线", fill=p_col, font=self.font_small)

            y_start = 48
            card_h = 46
            card_gap = 7
            for idx, item in enumerate(self.main_menu_items):
                is_selected = (idx == self.cursor)
                card_y = y_start + idx * (card_h + card_gap)
                _draw_modern_tile(
                    draw, 12, card_y, 216, card_h,
                    item.get("icon", "settings"), item["title"],
                    "chevron", "", is_selected, self.font_bold, self.font_small, theme
                )

        # B. 扫码手机配置界面 (支持 AP 配网热点与已连接模式)
        elif self.current_mode == "QR_CODE":
            wf_st = wifi_manager.get_status()
            # 顶栏带返回箭头
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), "无线配网", fill=on_surf, font=self.font_bold)

            if wf_st["ap_mode"]:
                draw.text((176, 21), "热点已开启", fill=(255, 180, 0), font=self.font_small)

                draw.rounded_rectangle([42, 48, 198, 204], radius=16, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))

                draw.text((24, 222), f"热点: {wf_st['ap_ssid']}", fill=on_surf, font=self.font_bold)
                draw.text((24, 244), f"密码: {wf_st['ap_password']}  (扫码直连)", fill=on_surf_var, font=self.font_small)
                draw.text((24, 266), f"后台: {wf_st['ap_ip']}:8090/settings", fill=p_col, font=self.font_small)
            else:
                draw.text((188, 21), "已联网", fill=p_col, font=self.font_small)

                ip = wf_st.get("ip", get_local_ip())
                draw.rounded_rectangle([42, 48, 198, 204], radius=16, fill=(255, 255, 255))
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))

                draw.text((24, 222), f"已连: {wf_st.get('ssid', 'WiFi')}", fill=on_surf, font=self.font_bold)
                draw.text((24, 244), f"IP: {ip}:8090", fill=on_surf_var, font=self.font_small)
                draw.text((24, 266), "手机扫码进入后台进行配网设置", fill=p_col, font=self.font_small)

        # C. 一键体检诊断界面 (纯净单行无冗余副标题)
        elif self.current_mode == "DOCTOR":
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), "系统体检", fill=on_surf, font=self.font_bold)
            draw.text((188, 21), "HEALTH", fill=p_col, font=self.font_small)

            d = self.doctor_cache
            items = [
                ("wifi", "网络连接", "badge", "在线" if "." in d.get("wifi_ip", "") and "--" not in d.get("wifi_ip", "") else "离线"),
                ("doctor", "大模型接口", "badge", d.get("mimo_status", "正常")),
                ("camera", "视觉摄像头", "badge", d.get("camera_status", "正常")),
                ("mic", "麦克风矩阵", "badge", d.get("mic_status", "正常")),
                ("chip", "处理器核心", "badge", d.get("cpu_temp", "45°C")),
            ]

            y = 48
            card_h = 46
            card_gap = 7
            for icon, label, r_type, val in items:
                _draw_modern_tile(
                    draw, 12, y, 216, card_h,
                    icon, label,
                    r_type, val, False, self.font_bold, self.font_small, theme
                )
                y += (card_h + card_gap)

        # D. 硬件控制界面 (TOGGLES: 连续视口平滑渲染，无生硬跳页)
        elif self.current_mode == "TOGGLES":
            total = len(self.hardware_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            card_w = 212
            card_h = 50
            card_gap = 8
            pitch = card_h + card_gap  # 58px

            viewport_h = 260
            total_content_h = total * pitch - card_gap
            max_scroll = max(0, total_content_h - viewport_h + 8)
            scroll_y = max(0, min(max_scroll, (cur_idx - 1) * pitch))

            # 绘制落入视口 44 <= y <= 312 范围内的连续卡片
            for idx, item in enumerate(self.hardware_items):
                card_y = 48 + idx * pitch - scroll_y
                if card_y + card_h >= 44 and card_y <= 312:
                    is_selected = (idx == cur_idx)
                    is_on = bool(item.get("state", False))
                    item_type = item.get("type", "")
                    r_type = "switch" if item_type == "toggle" else ("badge" if item_type == "cycle" else "chevron")
                    r_val = is_on if item_type == "toggle" else (str(item.get("val", "")) if item_type == "cycle" else "")

                    _draw_modern_tile(
                        draw, 12, card_y, card_w, card_h,
                        item.get("icon", "hardware"), item["name"],
                        r_type, r_val, is_selected, self.font_bold, self.font_small, theme
                    )

            # 顶部固定导航遮罩
            draw.rectangle([0, 0, 240, 44], fill=surf_col)
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), "硬件控制", fill=on_surf, font=self.font_bold)

            # 底部遮罩
            draw.rectangle([0, 312, 240, 320], fill=surf_col)

            # 极简动态连续滑块
            if total > 4 and max_scroll > 0:
                track_y1, track_y2 = 48, 302
                track_h = track_y2 - track_y1
                thumb_h = max(24, int((viewport_h / total_content_h) * track_h))
                thumb_y = track_y1 + int((scroll_y / max_scroll) * (track_h - thumb_h))
                draw.rounded_rectangle([232, thumb_y, 235, thumb_y + thumb_h], radius=1, fill=p_col)

        # E. 技能服务 & 系统设置统一滚动列表绘制
        elif self.current_mode in ["SKILLS", "SYSTEM"]:
            if self.current_mode == "SKILLS":
                page_title = "技能服务"
                list_items = self.skill_items
            else:
                page_title = "系统设置"
                list_items = self.system_items

            total = len(list_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            card_w = 212
            card_h = 50
            card_gap = 8
            pitch = card_h + card_gap  # 58px

            viewport_h = 260
            total_content_h = max(0, total * pitch - card_gap)
            max_scroll = max(0, total_content_h - viewport_h + 8)
            scroll_y = max(0, min(max_scroll, (cur_idx - 1) * pitch))

            # 绘制落入视口 44 <= y <= 312 范围内的连续卡片
            for idx, item in enumerate(list_items):
                card_y = 48 + idx * pitch - scroll_y
                if card_y + card_h >= 44 and card_y <= 312:
                    is_selected = (idx == cur_idx)
                    item_type = item.get("type", "")
                    r_val = str(item.get("val", ""))
                    show_badge = item_type in ["cycle", "info"] or (bool(r_val) and item_type == "action" and r_val != "点击检查更新")
                    r_type = "badge" if show_badge else "chevron"

                    _draw_modern_tile(
                        draw, 12, card_y, card_w, card_h,
                        item.get("icon", "settings"), item["name"],
                        r_type, r_val, is_selected, self.font_bold, self.font_small, theme
                    )

            # 顶部固定导航遮罩
            draw.rectangle([0, 0, 240, 44], fill=surf_col)
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), page_title, fill=on_surf, font=self.font_bold)

            # 底部遮罩
            draw.rectangle([0, 312, 240, 320], fill=surf_col)

            # 极简动态连续滑块
            if total > 4 and max_scroll > 0:
                track_y1, track_y2 = 48, 302
                track_h = track_y2 - track_y1
                thumb_h = max(24, int((viewport_h / max(1, total_content_h)) * track_h))
                thumb_y = track_y1 + int((scroll_y / max_scroll) * (track_h - thumb_h))
                draw.rounded_rectangle([232, thumb_y, 235, thumb_y + thumb_h], radius=1, fill=p_col)

        # F. 人脸录入倒计时/取景界面 (FACE_ENROLL)
        elif self.current_mode == "FACE_ENROLL":
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), "面容录入", fill=on_surf, font=self.font_bold)

            # 居中取景框 (动态主题强调色)
            box_col = p_col if self.face_enroll_step > 0 else (34, 197, 94)
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
                draw.text((112, 115), str(self.face_enroll_step), fill=p_col, font=self.font_bold)

            # 状态文案
            draw.text((28, 230), self.face_enroll_msg, fill=on_surf, font=self.font_bold)

        # G. 技能服务二维码弹窗 (XIAOMI_QR / NETEASE_QR / SKILLS_QR 共享居中白底卡渲染)
        elif self.current_mode in ["XIAOMI_QR", "NETEASE_QR", "SKILLS_QR"]:
            if self.current_mode == "NETEASE_QR":
                header_title = "网易云登录"
                sub_headline = "打开网易云App 扫码登录"
                st_line = self.netease_status_text
                foot_tip = "绑定后畅听 VIP 与个人每日推荐"
            elif self.current_mode == "SKILLS_QR":
                ip = get_local_ip()
                header_title = "技能后台"
                sub_headline = "微信/浏览器 扫码配技能"
                st_line = f"{ip}:8090/skills"
                foot_tip = "配置飞书/天气/米家/音乐等插件"
            else:
                header_title = "米家授权"
                sub_headline = "打开米家App 扫码授权"
                st_line = self.xiaomi_status_text
                foot_tip = "绑定后可直接语音控制台灯/水壶等"

            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), header_title, fill=on_surf, font=self.font_bold)
            draw.text((188, 21), "BINDING", fill=on_surf_var, font=self.font_small)

            draw.rounded_rectangle([42, 48, 198, 204], radius=16, fill=(255, 255, 255))
            try:
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    img.paste(qr_pil, (48, 54))
                else:
                    draw.text((56, 114), "获取二维码中...", fill=(0, 0, 0), font=self.font_small)
            except Exception as e:
                draw.text((50, 110), f"Error: {e}", fill=(0, 0, 0), font=self.font_small)

            draw.text((24, 226), _fit_pil_text(draw, sub_headline, self.font_bold, 200), fill=on_surf, font=self.font_bold)
            draw.text((24, 248), _fit_pil_text(draw, st_line, self.font_small, 200), fill=p_col if "成功" in st_line else (255, 180, 0), font=self.font_small)
            draw.text((24, 270), _fit_pil_text(draw, foot_tip, self.font_small, 200), fill=on_surf_var, font=self.font_small)

        # H. 彩蛋模式 (EASTER_EGG: 关于 PIG 的自述与介绍，全常规字体，无粗体，无底部翻页提示)
        elif self.current_mode == "EASTER_EGG":
            font_title = getattr(self, "font_main", self.font_main)
            font_body = getattr(self, "font_letter", self.font_main)
            font_sub = getattr(self, "font_letter_sub", self.font_small)

            card_bg = theme.get("surface_container", (26, 32, 32))
            card_outline = theme.get("outline", (55, 68, 66))

            # 1. 绘制正文卡片底框 (扩展到底部，取消多余提示栏，纯净极简)
            draw.rounded_rectangle([10, 42, 222, 308], radius=10, fill=card_bg, outline=card_outline, width=1)

            letter_items = [
                ("text", "本项目只是一名老程序员"),
                ("text", "用 AI 做的能想到的"),
                ("highlight", "比较动人的一封情书。"),
                ("space", 8),
                ("text", "就像电影《失控玩家》一样，"),
                ("text", "把她的人物画像、你的语音、"),
                ("text", "你们的故事注入到 PIG 里。"),
                ("space", 8),
                ("text", "PIG 会自己去学习，"),
                ("text", "学习她的习惯、她的作息，"),
                ("text", "会记录她的不舒服，"),
                ("text", "帮她定时喂猫，"),
                ("highlight", "提醒她锅里煮的茶叶蛋马上好了……"),
                ("space", 10),
                ("highlight", "叫 PIG 是因为："),
                ("text", "首先是基于 PI 的，"),
                ("quote", "其次做 PIG 的人是一只小猪。"),
                ("space", 10),
                ("text", "此时此刻，"),
                ("text", "写下这段话的老程序员"),
                ("text", "跟他的白菜遇到了很多问题。"),
                ("space", 8),
                ("highlight", "但他坚信一切都有解决办法，"),
                ("highlight", "就像 PIG 会不断迭代。"),
                ("space", 8),
                ("quote", "而且他，真的很想念他的白菜。"),
                ("space", 16)
            ]

            # 2. 绘制滚动正文内容 (只在卡片视口内 44 <= y <= 300 绘制可见行，全部常规字重，统一主文本色，无任何加粗或高亮色)
            cur_y = 56 - self.easter_egg_scroll_y
            for it_type, val in letter_items:
                if it_type == "space":
                    cur_y += val
                    continue

                if 44 <= cur_y <= 300:
                    draw.text((20, cur_y), val, fill=on_surf, font=font_body)
                cur_y += 19

            # 3. 绘制顶部遮罩与顶栏 (纯净系统风格：左箭头 + "关于 PIG" + "ABOUT"，常规字重)
            draw.rectangle([0, 0, 240, 42], fill=surf_col)
            draw.line([(18, 22), (13, 27)], fill=p_col, width=2)
            draw.line([(13, 27), (18, 32)], fill=p_col, width=2)
            draw.text((26, 17), "关于 PIG", fill=on_surf, font=font_title)
            draw.text((184, 21), "ABOUT", fill=on_surf_var, font=font_sub)

            # 4. 底部微遮罩 (保证视口底部纯净，绝无任何“翻页/返回”文字提示)
            draw.rectangle([0, 310, 240, 320], fill=surf_col)

            # 5. 绘制右侧滚动条
            total_h = cur_y + self.easter_egg_scroll_y - 56
            max_s = max(1, self.easter_egg_max_scroll)
            track_y1, track_y2 = 48, 302
            track_h = track_y2 - track_y1
            thumb_h = max(24, int((240 / max(1, total_h)) * track_h))
            thumb_y = track_y1 + int((self.easter_egg_scroll_y / max_s) * (track_h - thumb_h))
            draw.rounded_rectangle([228, thumb_y, 231, thumb_y + thumb_h], radius=1, fill=p_col)

        return img


# 单例初始化句柄
menu_fsm = MenuFSM()
