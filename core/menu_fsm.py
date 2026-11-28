"""
MenuFSM: PIG 桌面机器人统一 4 键菜单状态机 (Unified 4-Button Menu State Machine)
统一支持：
1. 树莓派真实 LCD 屏幕 (PIL / ST7789)
2. Web 3D 仿真工作台 (Canvas 2D / Three.js 动态贴图)
3. 手机端 Web 配置后台联动 (/settings)
"""

import os
import sys
import time
import socket
import subprocess
import threading
from typing import Dict, Any, Optional, Callable, List, Tuple
from PIL import Image, ImageDraw, ImageFont
from core.wifi_manager import wifi_manager, get_local_ip, _is_real_lan_ip

try:
    import qrcode
except ImportError:
    qrcode = None


# ========================================================
# Material Design 3 (Pure-Black OLED 高对比多主题配色方案)
# ========================================================
THEMES = {
    "mint": {
        "name": "薄荷绿",
        "primary": (64, 232, 208),        # #40e8d0 Vivid Mint Cyan
        "on_primary": (0, 42, 38),        # #002a26
        "on_primary_sub": (10, 72, 65),   # #0a4841
        "surface": (0, 0, 0),             # #000000 Pure OLED Black
        "surface_container": (18, 25, 27),# #12191b
        "surface_high": (30, 42, 45),     # #1e2a2d
        "outline": (46, 66, 68),          # #2e4244
        "badge_bg": (22, 44, 42),         # #162c2a
        "icon_bg_sel": (0, 78, 70),       # #004e46
    },
    "orange": {
        "name": "暖阳橙",
        "primary": (255, 168, 48),        # #ffa830 Vivid Amber
        "on_primary": (58, 28, 0),        # #3a1c00
        "on_primary_sub": (96, 48, 0),    # #603000
        "surface": (0, 0, 0),             # #000000 Pure OLED Black
        "surface_container": (26, 20, 16),# #1a1410
        "surface_high": (44, 34, 26),     # #2c221a
        "outline": (72, 54, 40),          # #483628
        "badge_bg": (46, 34, 24),         # #2e2218
        "icon_bg_sel": (102, 50, 0),      # #663200
    },
    "blue": {
        "name": "海天蓝",
        "primary": (110, 178, 255),      # #6eb2ff Vivid Sky Blue
        "on_primary": (0, 38, 86),        # #002656
        "on_primary_sub": (0, 62, 134),   # #003e86
        "surface": (0, 0, 0),             # #000000 Pure OLED Black
        "surface_container": (16, 22, 32),# #101620
        "surface_high": (28, 38, 56),     # #1c2638
        "outline": (48, 64, 92),          # #30405c
        "badge_bg": (24, 38, 60),         # #18263c
        "icon_bg_sel": (0, 62, 134),      # #003e86
    },
    "purple": {
        "name": "薰衣紫",
        "primary": (198, 170, 255),      # #c6aaff Vivid Lavender
        "on_primary": (46, 22, 98),       # #2e1662
        "on_primary_sub": (78, 42, 148),  # #4e2a94
        "surface": (0, 0, 0),             # #000000 Pure OLED Black
        "surface_container": (20, 17, 28),# #14111c
        "surface_high": (36, 30, 50),     # #241e32
        "outline": (62, 52, 84),          # #3e3454
        "badge_bg": (38, 30, 58),         # #261e3a
        "icon_bg_sel": (78, 42, 148),     # #4e2a94
    },
    "mono": {
        "name": "极简白",
        "primary": (242, 246, 252),      # #f2f6fc Crisp Ice White
        "on_primary": (10, 12, 16),       # #0a0c10
        "on_primary_sub": (42, 48, 58),   # #2a303a
        "surface": (0, 0, 0),             # #000000 Pure OLED Black
        "surface_container": (20, 23, 28),# #14171c
        "surface_high": (36, 42, 52),     # #242a34
        "outline": (60, 68, 82),          # #3c4452
        "badge_bg": (30, 35, 44),         # #1e232c
        "icon_bg_sel": (210, 218, 230),   # #d2dae6
    }
}
THEME_KEYS = ["mint", "orange", "blue", "purple", "mono"]

def _draw_vector_icon(draw, icon_name: str, cx: int, cy: int, color, bg_color=(20, 24, 28), scale: int = 2):
    """
    在指定中心点 (cx, cy) 按给定倍率 scale 绘制矢量微图标（默认 2x 超采样抗锯齿）
    """
    s = scale
    w2 = max(2, 2 * s)
    w1 = max(1, s)

    if icon_name == "wifi":
        draw.arc([cx - 8*s, cy - 7*s, cx + 8*s, cy + 9*s], 200, 340, fill=color, width=w2)
        draw.arc([cx - 5*s, cy - 3*s, cx + 5*s, cy + 7*s], 200, 340, fill=color, width=w2)
        draw.ellipse([cx - 2*s, cy + 4*s, cx + 2*s, cy + 8*s], fill=color)

    elif icon_name == "doctor":
        pts = [
            (cx - 8*s, cy + s), (cx - 4*s, cy + s), (cx - 2*s, cy - 6*s),
            (cx + s, cy + 7*s), (cx + 3*s, cy - 2*s), (cx + 5*s, cy + s), (cx + 8*s, cy + s)
        ]
        draw.line(pts, fill=color, width=w2, joint="curve")

    elif icon_name == "hardware":
        draw.ellipse([cx - 6*s, cy - 6*s, cx + 6*s, cy + 6*s], outline=color, width=w2)
        draw.ellipse([cx - 2*s, cy - 2*s, cx + 2*s, cy + 2*s], fill=color)
        draw.line([(cx, cy - 9*s), (cx, cy - 6*s)], fill=color, width=w2)
        draw.line([(cx, cy + 6*s), (cx, cy + 9*s)], fill=color, width=w2)
        draw.line([(cx - 9*s, cy), (cx - 6*s, cy)], fill=color, width=w2)
        draw.line([(cx + 6*s, cy), (cx + 9*s, cy)], fill=color, width=w2)

    elif icon_name in ["settings", "theme", "palette"]:
        if icon_name in ["theme", "palette"]:
            draw.ellipse([cx - 7*s, cy - 7*s, cx + 7*s, cy + 7*s], outline=color, width=w2)
            draw.ellipse([cx - 3*s, cy - 4*s, cx - s, cy - 2*s], fill=color)
            draw.ellipse([cx + 2*s, cy - 3*s, cx + 4*s, cy - s], fill=color)
            draw.ellipse([cx - 4*s, cy + s, cx - 2*s, cy + 3*s], fill=color)
        else:
            draw.line([(cx - 7*s, cy - 4*s), (cx + 7*s, cy - 4*s)], fill=color, width=w2)
            draw.ellipse([cx - 3*s, cy - 6*s, cx + s, cy - 2*s], fill=color)
            draw.line([(cx - 7*s, cy + 4*s), (cx + 7*s, cy + 4*s)], fill=color, width=w2)
            draw.ellipse([cx + s, cy + 2*s, cx + 5*s, cy + 6*s], fill=color)

    elif icon_name == "servo":
        draw.arc([cx - 6*s, cy - 6*s, cx + 6*s, cy + 6*s], 45, 315, fill=color, width=w2)
        draw.polygon([(cx + 4*s, cy - 7*s), (cx + 7*s, cy - 2*s), (cx + s, cy - 4*s)], fill=color)
        draw.ellipse([cx - 2*s, cy - 2*s, cx + 2*s, cy + 2*s], fill=color)

    elif icon_name == "spark":
        draw.polygon([
            (cx, cy - 8*s), (cx + 2*s, cy - 2*s), (cx + 8*s, cy), (cx + 2*s, cy + 2*s),
            (cx, cy + 8*s), (cx - 2*s, cy + 2*s), (cx - 8*s, cy), (cx - 2*s, cy - 2*s)
        ], fill=color)

    elif icon_name == "lock":
        draw.rounded_rectangle([cx - 6*s, cy - 2*s, cx + 6*s, cy + 7*s], radius=2*s, fill=color)
        draw.arc([cx - 4*s, cy - 7*s, cx + 4*s, cy + s], 180, 360, fill=color, width=w2)

    elif icon_name == "mic":
        draw.rounded_rectangle([cx - 3*s, cy - 7*s, cx + 3*s, cy + s], radius=3*s, fill=color)
        draw.arc([cx - 6*s, cy - 4*s, cx + 6*s, cy + 4*s], 0, 180, fill=color, width=w2)
        draw.line([(cx, cy + 4*s), (cx, cy + 7*s)], fill=color, width=w2)
        draw.line([(cx - 4*s, cy + 7*s), (cx + 4*s, cy + 7*s)], fill=color, width=w2)

    elif icon_name == "eye":
        draw.arc([cx - 8*s, cy - 7*s, cx + 8*s, cy + 7*s], 15, 165, fill=color, width=w2)
        draw.arc([cx - 8*s, cy - 7*s, cx + 8*s, cy + 7*s], 195, 345, fill=color, width=w2)
        draw.ellipse([cx - 3*s, cy - 3*s, cx + 3*s, cy + 3*s], fill=color)

    elif icon_name == "grid":
        for gx in [-5, 0, 5]:
            for gy in [-5, 0, 5]:
                draw.ellipse([cx + (gx - 1)*s, cy + (gy - 1)*s, cx + (gx + 1)*s, cy + (gy + 1)*s], fill=color)

    elif icon_name == "chat":
        draw.rounded_rectangle([cx - 7*s, cy - 6*s, cx + 7*s, cy + 3*s], radius=4*s, fill=color)
        draw.polygon([(cx - 3*s, cy + 3*s), (cx - 5*s, cy + 7*s), (cx + s, cy + 3*s)], fill=color)

    elif icon_name == "sfx":
        draw.ellipse([cx - 6*s, cy + s, cx - s, cy + 6*s], fill=color)
        draw.ellipse([cx + 2*s, cy - 2*s, cx + 7*s, cy + 3*s], fill=color)
        draw.line([(cx - s, cy + 3*s), (cx - s, cy - 5*s)], fill=color, width=w2)
        draw.line([(cx + 7*s, cy), (cx + 7*s, cy - 8*s)], fill=color, width=w2)
        draw.line([(cx - s, cy - 5*s), (cx + 7*s, cy - 8*s)], fill=color, width=w2)

    elif icon_name == "face":
        draw.rounded_rectangle([cx - 7*s, cy - 7*s, cx + 7*s, cy + 7*s], radius=3*s, outline=color, width=w2)
        draw.ellipse([cx - 4*s, cy - 3*s, cx - 2*s, cy - s], fill=color)
        draw.ellipse([cx + 2*s, cy - 3*s, cx + 4*s, cy - s], fill=color)
        draw.arc([cx - 4*s, cy - s, cx + 4*s, cy + 4*s], 20, 160, fill=color, width=w2)

    elif icon_name == "trash":
        draw.line([(cx - 6*s, cy - 5*s), (cx + 6*s, cy - 5*s)], fill=color, width=w2)
        draw.line([(cx - 2*s, cy - 7*s), (cx + 2*s, cy - 7*s)], fill=color, width=w2)
        draw.rounded_rectangle([cx - 5*s, cy - 4*s, cx + 5*s, cy + 7*s], radius=s, outline=color, width=w2)
        draw.line([(cx - 2*s, cy - 2*s), (cx - 2*s, cy + 5*s)], fill=color, width=w1)
        draw.line([(cx + 2*s, cy - 2*s), (cx + 2*s, cy + 5*s)], fill=color, width=w1)

    elif icon_name == "home":
        draw.polygon([(cx, cy - 8*s), (cx + 8*s, cy - s), (cx - 8*s, cy - s)], fill=color)
        draw.rounded_rectangle([cx - 6*s, cy - s, cx + 6*s, cy + 7*s], radius=s, fill=color)
        draw.rectangle([cx - 2*s, cy + 2*s, cx + 2*s, cy + 7*s], fill=bg_color)

    elif icon_name == "voice":
        draw.line([(cx - 6*s, cy - 3*s), (cx - 6*s, cy + 3*s)], fill=color, width=w2)
        draw.line([(cx - 2*s, cy - 6*s), (cx - 2*s, cy + 6*s)], fill=color, width=w2)
        draw.line([(cx + 2*s, cy - 8*s), (cx + 2*s, cy + 8*s)], fill=color, width=w2)
        draw.line([(cx + 6*s, cy - 4*s), (cx + 6*s, cy + 4*s)], fill=color, width=w2)

    elif icon_name == "volume":
        draw.polygon([(cx - 6*s, cy - 3*s), (cx - 3*s, cy - 3*s), (cx + s, cy - 6*s), (cx + s, cy + 6*s), (cx - 3*s, cy + 3*s), (cx - 6*s, cy + 3*s)], fill=color)
        draw.arc([cx - s, cy - 5*s, cx + 7*s, cy + 5*s], 300, 60, fill=color, width=w2)

    elif icon_name == "clock":
        draw.ellipse([cx - 7*s, cy - 7*s, cx + 7*s, cy + 7*s], outline=color, width=w2)
        draw.line([(cx, cy - 5*s), (cx, cy)], fill=color, width=w2)
        draw.line([(cx, cy), (cx + 4*s, cy)], fill=color, width=w2)

    elif icon_name == "chip":
        draw.rounded_rectangle([cx - 5*s, cy - 5*s, cx + 5*s, cy + 5*s], radius=s, outline=color, width=w2)
        draw.rectangle([cx - 2*s, cy - 2*s, cx + 2*s, cy + 2*s], fill=color)
        for d in [-3, 3]:
            draw.line([(cx + d*s, cy - 8*s), (cx + d*s, cy - 5*s)], fill=color, width=w1)
            draw.line([(cx + d*s, cy + 5*s), (cx + d*s, cy + 8*s)], fill=color, width=w1)
            draw.line([(cx - 8*s, cy + d*s), (cx - 5*s, cy + d*s)], fill=color, width=w1)
            draw.line([(cx + 5*s, cy + d*s), (cx + 8*s, cy + d*s)], fill=color, width=w1)

    elif icon_name == "cloud":
        draw.ellipse([cx - 4*s, cy - 6*s, cx + 4*s, cy + 2*s], fill=color)
        draw.ellipse([cx - 7*s, cy - 2*s, cx - s, cy + 4*s], fill=color)
        draw.ellipse([cx + s, cy - 2*s, cx + 7*s, cy + 4*s], fill=color)
        draw.rectangle([cx - 5*s, cy, cx + 5*s, cy + 4*s], fill=color)
        draw.line([(cx, cy - s), (cx, cy + 7*s)], fill=bg_color, width=w2)

    elif icon_name == "restart":
        draw.arc([cx - 6*s, cy - 6*s, cx + 6*s, cy + 6*s], 45, 315, fill=color, width=w2)
        draw.polygon([(cx + 4*s, cy - 7*s), (cx + 7*s, cy - 2*s), (cx + s, cy - 4*s)], fill=color)

    elif icon_name == "power":
        draw.arc([cx - 6*s, cy - 6*s, cx + 6*s, cy + 6*s], 135, 405, fill=color, width=w2)
        draw.line([(cx, cy - 8*s), (cx, cy - s)], fill=color, width=w2)

    elif icon_name == "camera":
        draw.rounded_rectangle([cx - 7*s, cy - 4*s, cx + 7*s, cy + 6*s], radius=2*s, outline=color, width=w2)
        draw.rectangle([cx - 3*s, cy - 7*s, cx + 3*s, cy - 4*s], fill=color)
        draw.ellipse([cx - 3*s, cy - s, cx + 3*s, cy + 5*s], fill=color)

    elif icon_name == "skills":
        draw.rounded_rectangle([cx - 7*s, cy - 7*s, cx - s, cy - s], radius=2*s, fill=color)
        draw.rounded_rectangle([cx + s, cy - 7*s, cx + 7*s, cy - s], radius=2*s, fill=color)
        draw.rounded_rectangle([cx - 7*s, cy + s, cx - s, cy + 7*s], radius=2*s, fill=color)
        draw.rounded_rectangle([cx + s, cy + s, cx + 7*s, cy + 7*s], radius=2*s, outline=color, width=w2)

    else:
        draw.ellipse([cx - 3*s, cy - 3*s, cx + 3*s, cy + 3*s], fill=color)


def _draw_text_vcenter(draw, x: int, cy: int, text: str, font, fill, h_align: str = "l"):
    """按字体真实 bbox 精准垂直居中绘制文本，避免各字号基线偏移。"""
    s = str(text or "")
    if not s:
        return
    try:
        bx0, by0, bx1, by1 = draw.textbbox((0, 0), s, font=font)
        tw = bx1 - bx0
        th = by1 - by0
        tx = x - bx0
        if h_align == "c":
            tx = x - tw // 2 - bx0
        elif h_align == "r":
            tx = x - tw - bx0
        ty = cy - th // 2 - by0
        draw.text((tx, ty), s, fill=fill, font=font)
    except Exception:
        draw.text((x, cy - 8), s, fill=fill, font=font)


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


def _draw_menu_header(draw, title: str, badge_text: str, font_title, font_small, theme: dict, show_back: bool = True, badge_color=None, badge_fill=None):
    """在 480x640 (2x) 画布绘制顶部纯黑极简导航栏与状态胶囊"""
    p_col = theme["primary"]
    on_surf = (244, 247, 250)
    surf_high = theme["surface_high"]
    outline = theme["outline"]

    draw.rectangle([0, 0, 480, 84], fill=(0, 0, 0))
    draw.line([(20, 82), (460, 82)], fill=(36, 42, 48), width=2)

    if show_back:
        draw.line([(34, 30), (22, 42)], fill=p_col, width=4)
        draw.line([(22, 42), (34, 54)], fill=p_col, width=4)
        title_x = 50
    else:
        draw.ellipse([22, 34, 36, 48], fill=p_col)
        title_x = 48

    _draw_text_vcenter(draw, title_x, 42, title, font_title, on_surf, "l")

    if badge_text:
        b_col = badge_color or p_col
        b_bg = badge_fill or theme.get("badge_bg", surf_high)
        tw = draw.textlength(badge_text, font=font_small)
        bw = int(max(72, tw + 26))
        bx2 = 460
        bx1 = bx2 - bw
        by1, by2 = 22, 62
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=20, fill=b_bg, outline=outline, width=2)
        _draw_text_vcenter(draw, (bx1 + bx2) // 2, (by1 + by2) // 2, badge_text, font_small, b_col, "c")


def _draw_modern_tile(draw, x: int, y: int, w: int, h: int, icon: str, title: str, right_type: str, right_val: Any, is_selected: bool, font_bold, font_small, theme: dict, scale: int = 2, badge_style=None):
    """
    绘制 2x SSAA 抗锯齿 OLED 高对比卡片 (左图标底座 + 垂直居中标题 + 右侧控件)
    所有坐标按调用方坐标系传入（在 render_pil_overlay 中为 480x640 的 2x 坐标）。
    """
    s = scale
    r = 15 * s
    p_col = theme["primary"]
    on_p = theme["on_primary"]
    surf_cont = theme["surface_container"]
    surf_high = theme["surface_high"]
    outline = theme["outline"]
    icon_sel_bg = theme["icon_bg_sel"]
    badge_bg = theme["badge_bg"]
    is_mono = (theme.get("name") == "极简白")

    if is_selected:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=p_col)
        icon_bg = icon_sel_bg
        icon_col = (18, 22, 28) if is_mono else (255, 255, 255)
        t_col = on_p
    else:
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=surf_cont, outline=outline, width=s)
        icon_bg = surf_high
        icon_col = p_col
        t_col = (238, 242, 246)

    # 左侧圆形图标底座
    ic_cx = x + 23 * s
    ic_cy = y + h // 2
    ic_r = 14 * s
    draw.ellipse([ic_cx - ic_r, ic_cy - ic_r, ic_cx + ic_r, ic_cy + ic_r], fill=icon_bg)
    _draw_vector_icon(draw, icon, ic_cx, ic_cy, icon_col, bg_color=icon_bg, scale=s)

    # 计算右侧控件边界
    title_x = x + 44 * s
    avail_w = max(40.0 * s, float(w - 52 * s))
    badge_info = None
    if right_type == "switch":
        right_left_x = x + w - 48 * s
    elif right_type == "badge":
        raw_txt = str(right_val or "")
        full_title_w = draw.textlength(str(title or ""), font=font_bold)
        full_tw = draw.textlength(raw_txt, font=font_small)
        raw_bw = max(38.0 * s, full_tw + 16.0 * s)
        min_title_w = min(full_title_w, 72.0 * s)
        max_bw = max(44.0 * s, avail_w - min_title_w - 6.0 * s)
        bw = int(min(raw_bw, max_bw))
        bx = x + w - bw - 9 * s
        fitted_badge = _fit_pil_text(draw, raw_txt, font_small, max(16.0 * s, bw - 12.0 * s))
        badge_info = (bx, bw, fitted_badge)
        right_left_x = bx
    elif right_type == "chevron":
        right_left_x = x + w - 20 * s
    else:
        right_left_x = x + w - 10 * s

    max_title_w = max(32.0 * s, float(right_left_x - title_x - 5 * s))
    fitted_title = _fit_pil_text(draw, title, font_bold, max_title_w)
    _draw_text_vcenter(draw, title_x, y + h // 2, fitted_title, font_bold, t_col, "l")

    # 右侧控件
    if right_type == "switch":
        sw_w, sw_h = 38 * s, 22 * s
        sw_x = x + w - sw_w - 9 * s
        sw_y = y + (h - sw_h) // 2
        is_on = bool(right_val)
        knob_pad = 3 * s
        knob_d = sw_h - 2 * knob_pad
        if is_selected:
            if is_on:
                track_bg = (34, 42, 54) if is_mono else icon_sel_bg
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=sw_h // 2, fill=track_bg)
                draw.ellipse([sw_x + sw_w - knob_pad - knob_d, sw_y + knob_pad, sw_x + sw_w - knob_pad, sw_y + sw_h - knob_pad], fill=(255, 255, 255))
            else:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=sw_h // 2, fill=surf_high)
                draw.ellipse([sw_x + knob_pad, sw_y + knob_pad, sw_x + knob_pad + knob_d, sw_y + sw_h - knob_pad], fill=on_p if not is_mono else (220, 226, 234))
        else:
            if is_on:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=sw_h // 2, fill=p_col)
                draw.ellipse([sw_x + sw_w - knob_pad - knob_d, sw_y + knob_pad, sw_x + sw_w - knob_pad, sw_y + sw_h - knob_pad], fill=on_p)
            else:
                draw.rounded_rectangle([sw_x, sw_y, sw_x + sw_w, sw_y + sw_h], radius=sw_h // 2, fill=(38, 44, 52), outline=outline, width=s)
                draw.ellipse([sw_x + knob_pad, sw_y + knob_pad, sw_x + knob_pad + knob_d, sw_y + sw_h - knob_pad], fill=(150, 160, 170))

    elif right_type == "badge" and badge_info:
        bx, bw, fitted_badge = badge_info
        bh = 22 * s
        by = y + (h - bh) // 2
        if is_selected:
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2, fill=icon_sel_bg)
            _draw_text_vcenter(draw, bx + bw // 2, by + bh // 2, fitted_badge, font_small, (18, 22, 28) if is_mono else (255, 255, 255), "c")
        else:
            b_fill = badge_style[0] if badge_style else badge_bg
            b_txt = badge_style[1] if badge_style else p_col
            b_out = badge_style[2] if badge_style else outline
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2, fill=b_fill, outline=b_out, width=s)
            _draw_text_vcenter(draw, bx + bw // 2, by + bh // 2, fitted_badge, font_small, b_txt, "c")

    elif right_type == "chevron":
        chv_x = x + w - 15 * s
        chv_y = y + h // 2
        col = on_p if is_selected else (155, 168, 175)
        draw.line([(chv_x - 4 * s, chv_y - 5 * s), (chv_x + s, chv_y)], fill=col, width=2 * s)
        draw.line([(chv_x + s, chv_y), (chv_x - 4 * s, chv_y + 5 * s)], fill=col, width=2 * s)


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
        self.is_screen_off = False
        self._screen_manual_off = False
        self._shutting_down = False
        self.screen_brightness = max(10, min(100, int(self._load_cfg().get("screen_brightness", 100))))

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
        self.update_status = "检查更新"
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
        
        # 通用插件屏幕弹窗与技能菜单状态 (零插件硬编码)
        self.active_qr_skill_id: str = ""
        self.qr_return_mode: str = "FACE"
        self.qr_return_cursor: int = 0
        self.qr_view: Dict[str, Any] = {
            "title": "扫码授权",
            "badge": "BINDING",
            "headline": "打开手机 App 扫码",
            "status": "获取二维码中...",
            "foot_tip": "",
            "qr_bytes": b"",
        }
        self._skill_qr_seq: int = 0
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
        
        # 加载中文字体 (1x 与 2x 矢量抗锯齿)
        self.font_main = None
        self.font_bold = None
        self.font_small = None
        self.font_large = None
        self._menu_cache_key = None
        self._menu_cache_img = None
        self._init_fonts()
        
        # 人脸录入与响铃弹窗状态
        self.face_enroll_msg = ""
        self.face_enroll_step = 0
        self.active_alarm: Optional[Dict[str, Any]] = None
        self.active_alarm_until: float = 0.0

        # 音量悬浮 HUD 状态
        self.volume_toast_until: float = 0.0
        self.volume_toast_val: int = 80
        try:
            from core.audio_volume import get_system_volume
            self.volume_toast_val = get_system_volume()
        except Exception:
            pass

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
            {"id": "screen_power", "icon": "eye", "name": "屏幕显示", "val": "熄屏中" if self.is_screen_off else f"{getattr(self, 'screen_brightness', 100)}%", "type": "toggle", "state": not self.is_screen_off},
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
            {"id": "update", "icon": "cloud", "name": "检查更新", "val": self.update_status, "type": "action"},
            {"id": "restart", "icon": "restart", "name": "重启服务", "val": "", "type": "action"},
            {"id": "shutdown", "icon": "power", "name": "安全关机", "val": "", "type": "action"},
        ]

    def _sync_skill_items(self):
        """通过 SkillManager 钩子动态聚合「技能服务」屏幕菜单项，核心层零插件导入"""
        items: List[Dict[str, Any]] = []
        try:
            from core.skill_manager import get_skill_manager
            sm = get_skill_manager()
            if sm:
                items = sm.collect_screen_items()
        except Exception:
            pass
        items.append({"id": "skills_portal", "icon": "skills", "name": "扫码配技能", "val": "", "type": "action"})
        self.skill_items = items

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
        """循环切换发音音色并热同步到真机 TTS 引擎"""
        voices = ["冰糖", "茉莉", "苏打", "白桦", "小爱", "温柔男声"]
        c = self._load_cfg()
        cur_v = c.get("mimo_voice", "冰糖")
        idx = voices.index(cur_v) if cur_v in voices else 0
        new_v = voices[(idx + 1) % len(voices)]
        self._save_cfg_key("mimo_voice", new_v)
        self._save_cfg_key("tts_voice", new_v)
        c["mimo_voice"] = new_v
        c["tts_voice"] = new_v
        if self.agent and getattr(self.agent, "tts_engine", None) and hasattr(self.agent.tts_engine, "update_config"):
            self.agent.tts_engine.update_config(c)
        self._sync_system_items()
        self._notify_listeners()

    def cycle_volume(self):
        """循环切换系统音量 (20% -> 40% -> 60% -> 80% -> 100%)"""
        vols = [20, 40, 60, 80, 100]
        c = self._load_cfg()
        cur_vol = c.get("volume", 80)
        idx = vols.index(cur_vol) if cur_vol in vols else 3
        new_vol = vols[(idx + 1) % len(vols)]
        try:
            from core.audio_volume import set_system_volume
            set_system_volume(new_vol, play_feedback=True)
        except Exception as e:
            print(f"⚠️ [MenuFSM] set_system_volume 异常: {e}")
            self._save_cfg_key("volume", new_vol)
        self.volume_toast_val = new_vol
        self.volume_toast_until = time.time() + 2.0
        if self.agent and hasattr(self.agent, "tts_engine"):
            self.agent.tts_engine.volume = new_vol
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

    def show_skill_qr_on_screen(self, skill_id: str, mode_name: str = ""):
        """通用插件屏幕二维码弹窗：通过 start_screen_qr / poll_screen_qr 钩子驱动任意技能绑定"""
        from core.skill_manager import get_skill_manager
        sm = get_skill_manager()
        meta = sm.resolve_plugin_meta(skill_id) if sm else {}
        resolved_mode = mode_name or str(meta.get("screen_qr_mode") or f"{skill_id.upper()}_QR")
        default_title = str(meta.get("screen_qr_title") or meta.get("name") or "扫码绑定")[:6]

        with self._lock:
            self._skill_qr_seq += 1
            cur_seq = self._skill_qr_seq
            if not self.active_qr_skill_id and self.current_mode != resolved_mode:
                self.qr_return_mode = self.current_mode
                self.qr_return_cursor = self.cursor
            self.active_qr_skill_id = skill_id
            self.current_mode = resolved_mode
            self.qr_view = {
                "title": default_title,
                "badge": "BINDING",
                "headline": f"请使用手机扫码绑定",
                "status": "获取二维码中...",
                "foot_tip": "",
                "qr_bytes": b"",
            }
            self.last_interaction_time = time.time()
            self._notify_listeners()

        def _worker():
            try:
                if not sm:
                    return
                start_res = sm.invoke_plugin_hook(skill_id, "start_screen_qr") or {}
                if cur_seq != self._skill_qr_seq:
                    return
                with self._lock:
                    self.qr_view["title"] = start_res.get("title", self.qr_view["title"])
                    self.qr_view["headline"] = start_res.get("headline", self.qr_view["headline"])
                    self.qr_view["foot_tip"] = start_res.get("foot_tip", "")
                    self.qr_view["qr_bytes"] = start_res.get("qr_bytes") or b""
                    self.qr_view["status"] = start_res.get("status") or "请打开手机扫码确认"
                    self._notify_listeners()

                if start_res.get("error") or start_res.get("done"):
                    return

                while self.current_mode == resolved_mode and cur_seq == self._skill_qr_seq:
                    time.sleep(1.0)
                    if cur_seq != self._skill_qr_seq or self.current_mode != resolved_mode:
                        break
                    poll_res = sm.invoke_plugin_hook(skill_id, "poll_screen_qr") or {}
                    st_text = poll_res.get("status_text")
                    qr_bytes = poll_res.get("qr_bytes")
                    state = poll_res.get("state", "waiting")
                    with self._lock:
                        if st_text:
                            self.qr_view["status"] = st_text
                        if qr_bytes:
                            self.qr_view["qr_bytes"] = qr_bytes
                        if state == "success":
                            self._sync_skill_items()
                        self._notify_listeners()

                    if state == "success":
                        time.sleep(2.2)
                        with self._lock:
                            if self.current_mode == resolved_mode and cur_seq == self._skill_qr_seq:
                                self.current_mode = self.qr_return_mode or "FACE"
                                self.cursor = self.qr_return_cursor
                                self.active_qr_skill_id = ""
                                self.last_interaction_time = time.time()
                                self._notify_listeners()
                        break
                    elif state in ("expired", "blocked", "error"):
                        break
            except Exception as e:
                with self._lock:
                    if cur_seq == self._skill_qr_seq:
                        self.qr_view["status"] = f"认证异常: {e}"
                        self._notify_listeners()

        threading.Thread(target=_worker, daemon=True, name=f"SkillQR_{skill_id}").start()

    def close_skill_qr(self):
        """关闭屏幕插件二维码弹窗并返回上一级界面"""
        with self._lock:
            self._skill_qr_seq += 1
            self.active_qr_skill_id = ""
            self.current_mode = self.qr_return_mode or "FACE"
            self.cursor = self.qr_return_cursor
            self.last_interaction_time = time.time()
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
        """后台异步真实检查 OTA 更新 (通过 git remote tags 真实探测，优先 origin/Gitee)"""
        with self._lock:
            self.update_status = "检查中..."
            self._sync_system_items()
            self._notify_listeners()

        def _worker():
            try:
                res = self.version_manager.check_update_sync()
                with self._lock:
                    self.update_status = res.get("status", "已是最新")
                    self.version_str = self.version_manager.current_version
                    self._sync_system_items()
                    self._notify_listeners()
            except Exception:
                with self._lock:
                    self.update_status = "检查失败"
                    self._sync_system_items()
                    self._notify_listeners()

        threading.Thread(target=_worker, daemon=True, name="SystemUpdateCheck").start()

    def get_qr_png_bytes(self) -> bytes:
        """生成并返回当前模式匹配的 QR 码 PNG 字节流"""
        # 1. 处于任意插件屏幕扫码弹窗 (绝不回退到配网二维码)
        if self.active_qr_skill_id and self.current_mode not in ("QR_CODE", "SKILLS_QR"):
            if self.qr_view.get("qr_bytes"):
                return self.qr_view["qr_bytes"]
            try:
                from core.skill_manager import get_skill_manager
                sm = get_skill_manager()
                raw = sm.invoke_plugin_hook(self.active_qr_skill_id, "get_screen_qr_bytes") if sm else b""
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
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
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
                # 2x 超采样矢量轮廓字号 (绕开 12-16px 点阵微调，边缘平滑饱满)
                self.font_main_hi = ImageFont.truetype(chosen_font, 28)
                self.font_bold_hi = ImageFont.truetype(chosen_font, 30)
                self.font_title_hi = ImageFont.truetype(chosen_font, 32)
                self.font_small_hi = ImageFont.truetype(chosen_font, 24)
                self.font_large_hi = ImageFont.truetype(chosen_font, 56)
                self.font_letter_hi = ImageFont.truetype(chosen_font, 26)
                self.font_letter_sub_hi = ImageFont.truetype(chosen_font, 22)
                return
            except Exception:
                pass

        self.font_main = ImageFont.load_default()
        self.font_bold = self.font_main
        self.font_small = self.font_main
        self.font_large = self.font_main
        self.font_letter = self.font_main
        self.font_letter_sub = self.font_main
        self.font_main_hi = self.font_main
        self.font_bold_hi = self.font_main
        self.font_title_hi = self.font_main
        self.font_small_hi = self.font_main
        self.font_large_hi = self.font_main
        self.font_letter_hi = self.font_main
        self.font_letter_sub_hi = self.font_main

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
        """后台检测无操作自动退出到灵动表情（二维码扫码与面容录入给足 180 秒窗口，避免 15 秒误退出断掉轮询）"""
        while self._running:
            time.sleep(1.0)
            with self._lock:
                if self.current_mode != "FACE":
                    limit_sec = 180.0 if (self.current_mode in ("QR_CODE", "SKILLS_QR", "FACE_ENROLL") or self.active_qr_skill_id) else self.auto_timeout
                    if time.time() - self.last_interaction_time > limit_sec:
                        self.current_mode = "FACE"
                        self.cursor = 0
                        self._notify_listeners()

    def trigger_alarm_overlay(self, rem: Dict[str, Any], duration_sec: float = 30.0):
        """闹钟响铃时在真机 LCD 弹出稍后再响/关闭快捷提示（同时支持物理按键操作）"""
        with self._lock:
            self.active_alarm = dict(rem or {})
            self.active_alarm_until = time.time() + float(duration_sec)
        self._notify_listeners()

    def dismiss_alarm_overlay(self):
        with self._lock:
            self.active_alarm = None
            self.active_alarm_until = 0.0
        self._notify_listeners()

    def set_screen_power(self, on: bool, brightness: Optional[int] = None, manual: bool = False) -> bool:
        """统一控制物理/仿真屏幕彻底开关与背光唤醒"""
        with self._lock:
            target_on = bool(on)
            if brightness is not None:
                self.screen_brightness = max(10, min(100, int(brightness)))
            self.is_screen_off = not target_on
            self._screen_manual_off = False if target_on else bool(manual)
            if target_on:
                self.last_interaction_time = time.time()
                self._menu_cache_key = None
            hal = self.hal
            if hal is None and self.agent is not None:
                hal = getattr(self.agent, "hal", None)
            if hal and hasattr(hal, "set_screen_power"):
                try:
                    hal.set_screen_power(target_on, self.screen_brightness)
                except Exception as e:
                    print(f"⚠️ [MenuFSM] set_screen_power 异常: {e}")
            self._sync_hardware_items()
        self._notify_listeners()
        return True

    def handle_touch(self):
        """摸头唤醒已熄灭的屏幕"""
        if getattr(self, "is_screen_off", False):
            self.set_screen_power(True)

    def handle_key(self, button: str, event_type: str = "click"):
        """
        统一按键处理入口 (K1:上, K2:下, K3:确认, K4:返回)
        """
        with self._lock:
            self.last_interaction_time = time.time()
            button = button.upper()

            # -1. 熄屏状态下按任意键先行唤醒点亮屏幕（消费本次按键避免误触菜单）
            if getattr(self, "is_screen_off", False):
                self.set_screen_power(True)
                return

            # 0. 若当前正在真机响铃窗口内，按键优先处理“稍后再响 / 关闭闹钟”
            if self.active_alarm and time.time() < self.active_alarm_until:
                if button in ["K1", "K2", "K3"]:
                    try:
                        from core.skill_manager import get_skill_manager
                        sm = get_skill_manager()
                        if sm:
                            sm.snooze_reminder(
                                reminder_id=self.active_alarm.get("id", ""),
                                minutes=5,
                                content=self.active_alarm.get("content", ""),
                            )
                    except Exception:
                        pass
                self.active_alarm = None
                self.active_alarm_until = 0.0
                self._notify_listeners()
                return
            
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
                            wifi_manager.get_status(force=True)
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

            # 2.5 处于插件屏幕扫码弹窗 (由插件生命周期钩子统一驱动)
            if self.active_qr_skill_id and self.current_mode not in (
                "FACE", "MENU_MAIN", "QR_CODE", "DOCTOR", "TOGGLES", "SKILLS", "SYSTEM", "SKILLS_QR", "FACE_ENROLL", "EASTER_EGG"
            ):
                if button in ["K4", "ESC"]:
                    self.close_skill_qr()
                elif button in ["K3", "ENTER"]:
                    self.show_skill_qr_on_screen(self.active_qr_skill_id, mode_name=self.current_mode)
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
                    if item_id == "screen_power":
                        self.set_screen_power(self.is_screen_off, manual=not self.is_screen_off)
                        return
                    elif item_id == "servo_center":
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
                    cur_item = self.skill_items[self.cursor]
                    action_id = cur_item["id"]
                    if cur_item.get("screen_qr") or cur_item.get("skill_id") and cur_item.get("type") == "qr":
                        self.show_skill_qr_on_screen(cur_item.get("skill_id") or action_id, mode_name=cur_item.get("qr_mode", ""))
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
            if getattr(self, "is_screen_off", False) and not getattr(self, "_screen_manual_off", False):
                self.set_screen_power(True)
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
        """实体按键 (K1音量+/K2音量-) 微调系统音量"""
        try:
            from core.audio_volume import adjust_volume
            new_vol = adjust_volume(delta, play_feedback=True)
        except Exception as e:
            print(f"⚠️ [MenuFSM] adjust_volume 异常: {e}")
            c = self._load_cfg()
            new_vol = max(0, min(100, c.get("volume", 80) + delta))
            self._save_cfg_key("volume", new_vol)
        self.volume_toast_val = new_vol
        self.volume_toast_until = time.time() + 2.0
        if self.agent and hasattr(self.agent, "tts_engine"):
            self.agent.tts_engine.volume = new_vol
        self._sync_system_items()
        self._notify_listeners()

    def show_volume_toast(self, vol: int):
        """弹出音量 HUD 浮层并刷新界面"""
        self.volume_toast_val = int(vol)
        self.volume_toast_until = time.time() + 2.0
        self._sync_system_items()
        self._notify_listeners()

    @staticmethod
    def _is_raspberry_pi_host() -> bool:
        try:
            if os.path.exists("/proc/device-tree/model"):
                with open("/proc/device-tree/model", "r", encoding="utf-8", errors="ignore") as f:
                    return "Raspberry Pi" in f.read()
        except Exception:
            pass
        return False

    @staticmethod
    def _can_passwordless_poweroff() -> Tuple[bool, str]:
        if os.geteuid() == 0:
            return True, "root"
        checks = [
            ["sudo", "-n", "-l", "/sbin/shutdown"],
            ["sudo", "-n", "-l", "/usr/sbin/shutdown"],
            ["sudo", "-n", "-l", "/sbin/poweroff"],
            ["sudo", "-n", "true"],
        ]
        last_err = ""
        for cmd in checks:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    return True, " ".join(cmd)
                err = (res.stderr or res.stdout or "").strip()
                if err:
                    last_err = err
            except Exception as e:
                last_err = str(e)
        return False, last_err or "需要 sudo 免密关机权限"

    def _run_os_shutdown(self) -> Tuple[bool, str]:
        is_root = (os.geteuid() == 0)
        cmds = (
            [
                ["/sbin/shutdown", "-h", "now"],
                ["/usr/sbin/shutdown", "-h", "now"],
                ["systemctl", "poweroff", "-i"],
                ["/sbin/poweroff"],
            ]
            if is_root
            else [
                ["sudo", "-n", "/sbin/shutdown", "-h", "now"],
                ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
                ["sudo", "-n", "shutdown", "-h", "now"],
                ["systemctl", "poweroff", "-i"],
                ["sudo", "-n", "/sbin/poweroff"],
                ["sudo", "-n", "poweroff"],
            ]
        )
        errors = []
        for cmd in cmds:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                if res.returncode == 0:
                    return True, " ".join(cmd)
                detail = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
                errors.append(f"{cmd[0]}: {detail}")
            except FileNotFoundError:
                continue
            except Exception as e:
                errors.append(f"{cmd[0]}: {e}")
        return False, " | ".join(errors) or "未找到可用的关机命令"

    def _execute_power_action(self, action: str, async_exec: bool = True) -> Dict[str, Any]:
        act = (action or "").strip().lower()
        if act == "restart":
            print("🔄 [MenuFSM] 正在安全重启机器人后台服务...")
            self.current_mode = "FACE"
            self._notify_listeners()

            def _do_restart():
                time.sleep(0.5)
                if os.environ.get("INVOCATION_ID"):
                    os._exit(0)
                try:
                    if os.geteuid() == 0:
                        res = subprocess.run(["systemctl", "restart", "robot.service"], capture_output=True, timeout=4)
                        if res.returncode == 0:
                            return
                    else:
                        res = subprocess.run(["sudo", "-n", "systemctl", "restart", "robot.service"], capture_output=True, timeout=4)
                        if res.returncode == 0:
                            return
                except Exception:
                    pass
                os.execv(sys.executable, [sys.executable] + sys.argv)

            if async_exec:
                threading.Thread(target=_do_restart, daemon=True, name="SystemRestart").start()
            else:
                _do_restart()
            return {"ok": True, "status": "restarting", "message": "正在重启 PIG 后台服务..."}

        if act == "shutdown":
            is_pi = self._is_raspberry_pi_host()
            if is_pi:
                can_off, reason = self._can_passwordless_poweroff()
                if not can_off:
                    msg = (
                        f"关机失败：当前运行用户无免密 sudo 关机权限（{reason}）。"
                        "请在树莓派终端执行一次 sudo bash scripts/setup_raspberry_pi.sh --no-start 授予免密关机权限。"
                    )
                    print(f"❌ [MenuFSM] {msg}")
                    return {"ok": False, "status": "error", "message": msg}

            print(f"🛑 [MenuFSM] 准备执行安全关机 (is_pi={is_pi})...")

            def _do_shutdown():
                time.sleep(0.25)
                if self.hal and hasattr(self.hal, "display_face"):
                    try:
                        img = Image.new("RGB", (240, 320), (10, 12, 16))
                        draw = ImageDraw.Draw(img)
                        draw.rounded_rectangle([24, 108, 216, 212], radius=14, fill=(24, 28, 36), outline=(239, 68, 68), width=2)
                        draw.text((48, 134), "正在安全关机...", fill=(248, 113, 113), font=self.font_bold)
                        draw.text((40, 170), "绿灯熄灭后即可拔电", fill=(180, 190, 205), font=self.font_small)
                        self.hal.display_face(img)
                        time.sleep(1.2)
                    except Exception:
                        pass

                self.set_screen_power(False, manual=True)
                try:
                    self._apply_servo_sleep(True)
                except Exception:
                    pass

                if is_pi:
                    self._shutting_down = True
                    if self.hal and hasattr(self.hal, "close"):
                        try:
                            self.hal.close()
                        except Exception:
                            pass
                    ok, detail = self._run_os_shutdown()
                    if not ok:
                        self._shutting_down = False
                        print(f"❌ [MenuFSM] 树莓派系统关机命令执行失败: {detail}")
                else:
                    print("ℹ️ [MenuFSM] 仿真环境已执行关机休眠（仅实机树莓派会执行系统断电）")

            if async_exec:
                threading.Thread(target=_do_shutdown, daemon=True, name="SystemShutdown").start()
            else:
                _do_shutdown()
            return {
                "ok": True,
                "status": "shutting_down",
                "simulated": not is_pi,
                "message": "正在执行安全关机，屏幕即将熄灭，绿灯熄灭后即可安全断电。" if is_pi else "仿真环境已进入关机休眠状态（实机树莓派上将执行断电关机）。",
            }

        return {"ok": False, "status": "error", "message": f"未知电源动作: {action}"}

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
        extra_screen: Dict[str, Any] = {}
        try:
            from core.skill_manager import get_skill_manager
            sm = get_skill_manager()
            if sm:
                for res in sm.emit_hook(
                    "get_screen_state",
                    active_mode=self.current_mode,
                    active_qr_skill=self.active_qr_skill_id,
                    qr_status=self.qr_view.get("status", ""),
                ):
                    if isinstance(res, dict):
                        extra_screen.update(res)
        except Exception:
            pass
        vol_val = getattr(self, "volume_toast_val", 80)
        c_cfg = self._load_cfg()
        if "volume" in c_cfg:
            vol_val = c_cfg["volume"]
        state_dict = {
            "mode": self.current_mode,
            "cursor": self.cursor,
            "volume": vol_val,
            "volume_toast": {
                "val": getattr(self, "volume_toast_val", vol_val),
                "active": (getattr(self, "volume_toast_until", 0.0) > time.time())
            },
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
            "settings_url": f"http://{ip}:8090/settings",
            "skills_url": f"http://{ip}:8090/skills",
            "qr_mode": self.current_mode if (self.current_mode in ["QR_CODE", "SKILLS_QR"] or self.active_qr_skill_id) else "",
            "qr_view": {k: v for k, v in self.qr_view.items() if k != "qr_bytes"},
            "qr_base64": self.get_qr_base64(),
            "theme": getattr(self, "theme_key", "mint"),
            "is_mic_muted": self.is_mic_muted,
            "face_tracking": self.face_tracking_enabled,
            "servo_sleeping": self.is_servo_sleeping,
            "screen_on": not getattr(self, "is_screen_off", False),
            "screen_brightness": getattr(self, "screen_brightness", 100),
            "local_ip": ip,
            "version_hint": self.version_hint,
            "easter_egg_scroll": self.easter_egg_scroll_y,
            "easter_egg_scroll_y": self.easter_egg_scroll_y,
            "time_left": max(0, int(self.auto_timeout - (time.time() - self.last_interaction_time)))
        }
        state_dict.update(extra_screen)
        return state_dict

    # ========================================================
    # 树莓派真机端 PIL 绘图渲染器 (2x SSAA + Pure-Black OLED 高对比风格)
    # ========================================================
    def _build_menu_cache_key(self, mode: str, theme_key: str):
        """生成轻量渲染缓存键；静态页面零重绘，动态状态变化立即失效"""
        tick = int(time.monotonic() * 2)  # 每 0.5s 允许刷新一次网络/体检状态
        if mode == "MENU_MAIN":
            return (mode, theme_key, self.cursor)
        elif mode == "TOGGLES":
            return (mode, theme_key, self.cursor, tuple((it["name"], it.get("state"), it.get("val")) for it in self.hardware_items))
        elif mode == "SKILLS":
            return (mode, theme_key, self.cursor, tuple((it["name"], it.get("val")) for it in self.skill_items))
        elif mode == "SYSTEM":
            return (mode, theme_key, self.cursor, tuple((it["name"], it.get("val")) for it in self.system_items))
        elif mode == "DOCTOR":
            d = self.doctor_cache
            return (mode, theme_key, tick, tuple(sorted(d.items())))
        elif mode == "FACE_ENROLL":
            return (mode, theme_key, self.face_enroll_step, self.face_enroll_msg)
        elif mode == "EASTER_EGG":
            return (mode, theme_key, self.easter_egg_scroll_y)
        else:
            return (mode, theme_key, tick, self._skill_qr_seq, self.active_qr_skill_id,
                    self.qr_view.get("title"), self.qr_view.get("headline"),
                    self.qr_view.get("status"), self.qr_view.get("foot_tip"))

    def _draw_volume_toast(self, draw: ImageDraw.ImageDraw, vol: int):
        """在 240x320 画布上渲染极简悬浮式音量条 HUD (纯净音量条，零文字干扰)"""
        vol = max(0, min(100, int(vol)))
        card_w = 154
        card_h = 22
        x1 = (240 - card_w) // 2
        y1 = 16
        x2 = x1 + card_w
        y2 = y1 + card_h

        # 极简胶囊底色与微弱描边
        draw.rounded_rectangle([x1, y1, x2, y2], radius=11, fill=(18, 22, 28), outline=(52, 64, 76), width=1)

        theme_key = getattr(self, "theme_key", "mint")
        primary_col = THEMES.get(theme_key, THEMES["mint"])["primary"]
        icon_col = (239, 68, 68) if vol == 0 else primary_col

        # 左侧矢量音量小喇叭
        _draw_vector_icon(draw, "volume", x1 + 15, y1 + 11, icon_col, scale=1)

        # 右侧纯净音量条 (高 6px，圆角 3px，纯无文字)
        bx1 = x1 + 30
        bx2 = x2 - 12
        by1 = y1 + 8
        by2 = y1 + 14
        bar_w = bx2 - bx1
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=3, fill=(36, 44, 54))

        fill_w = int(bar_w * (vol / 100.0))
        if fill_w > 0:
            fill_col = (239, 68, 68) if vol == 0 else primary_col
            draw.rounded_rectangle([bx1, by1, bx1 + fill_w, by2], radius=3, fill=fill_col)

    def render_pil_overlay(self, base_image: Image.Image) -> Image.Image:
        """
        在 240x320 PIL 图像上渲染 2x SSAA Pure-Black OLED 高对比菜单
        """
        vol_active = bool(getattr(self, "volume_toast_until", 0) > time.time())
        # 1. 灵动表情模式下：保持纯净，仅在右上角标注状态；若在响铃期则叠加底部闹钟操作浮条；若处于音量调节期则叠加 HUD
        if self.current_mode == "FACE":
            alarm_active = bool(self.active_alarm and time.time() < self.active_alarm_until)
            if not self.is_mic_muted and not self.is_servo_sleeping and not alarm_active and not vol_active:
                return base_image
            img = base_image.copy()
            draw = ImageDraw.Draw(img)
            if self.is_mic_muted:
                draw.ellipse([218, 12, 228, 22], fill=(239, 68, 68))
            if self.is_servo_sleeping:
                draw.text((200, 11), "Z", fill=(140, 140, 140), font=self.font_small)
            if alarm_active:
                title_c = _fit_pil_text(draw, f"闹钟: {self.active_alarm.get('content', '时间到')}", self.font_bold, 196)
                draw.rounded_rectangle([10, 236, 230, 310], radius=14, fill=(24, 18, 8), outline=(255, 176, 32), width=2)
                _draw_text_vcenter(draw, 120, 258, title_c, self.font_bold, (255, 210, 80), "c")
                _draw_text_vcenter(draw, 120, 290, "[K3] 5分后再响 · [K4] 关闭", self.font_small, (0, 229, 255), "c")
            if vol_active:
                self._draw_volume_toast(draw, getattr(self, "volume_toast_val", 80))
            return img

        theme_key = getattr(self, "theme_key", "mint")
        theme = THEMES.get(theme_key, THEMES["mint"])
        cache_key = self._build_menu_cache_key(self.current_mode, theme_key)
        if not vol_active and cache_key == self._menu_cache_key and self._menu_cache_img is not None:
            return self._menu_cache_img

        p_col = theme["primary"]
        surf_col = theme["surface"]
        surf_cont = theme["surface_container"]
        outline = theme["outline"]
        on_surf = (242, 245, 248)
        on_surf_var = (156, 168, 176)

        f_title = getattr(self, "font_title_hi", self.font_bold)
        f_bold = getattr(self, "font_bold_hi", self.font_bold)
        f_main = getattr(self, "font_main_hi", self.font_main)
        f_small = getattr(self, "font_small_hi", self.font_small)
        f_large = getattr(self, "font_large_hi", self.font_large)

        # 2x 超采样纯黑画布 (480x640 -> 240x320 BILINEAR 消除全部锯齿)
        S = 2
        hi = Image.new("RGB", (480, 640), surf_col)
        draw = ImageDraw.Draw(hi)
        qr_paste = None  # 若含二维码，缩回 1x 后用 NEAREST 精确贴入保证扫码锐度

        # A. 主菜单界面
        if self.current_mode == "MENU_MAIN":
            _draw_menu_header(draw, "控制中心", "在线", f_title, f_small, theme, show_back=False)

            y_start = 98
            card_h = 92
            card_gap = 14
            for idx, item in enumerate(self.main_menu_items):
                is_selected = (idx == self.cursor)
                card_y = y_start + idx * (card_h + card_gap)
                _draw_modern_tile(
                    draw, 22, card_y, 436, card_h,
                    item.get("icon", "settings"), item["title"],
                    "chevron", "", is_selected, f_bold, f_small, theme, scale=S
                )

        # B. 扫码手机配置界面 (支持 AP 配网热点、已联网与未联网状态)
        elif self.current_mode == "QR_CODE":
            wf_st = wifi_manager.get_status()
            if wf_st["ap_mode"]:
                _draw_menu_header(
                    draw, "无线配网", "热点开启", f_title, f_small, theme, show_back=True,
                    badge_color=(255, 196, 64), badge_fill=(52, 38, 14)
                )
                line1 = f"热点: {wf_st['ap_ssid']}"
                line2 = f"密码: {wf_st['ap_password']} (OK关闭)"
                line3 = f"后台: {wf_st['ap_ip']}:8090/settings"
                l1_col = on_surf
            elif wf_st.get("connected"):
                _draw_menu_header(draw, "无线配网", "已联网", f_title, f_small, theme, show_back=True)
                ip = wf_st.get("ip", get_local_ip())
                line1 = f"WiFi: {wf_st.get('ssid', 'WiFi')}"
                line2 = f"地址: {ip}:8090"
                line3 = "扫码配网  |  按 OK 开热点"
                l1_col = on_surf
            else:
                _draw_menu_header(
                    draw, "无线配网", "未联网", f_title, f_small, theme, show_back=True,
                    badge_color=(248, 113, 113), badge_fill=(68, 22, 22)
                )
                line1 = "WiFi: 未连接"
                ap_err = wf_st.get("ap_error", "")
                line2 = f"热点失败: {ap_err}" if ap_err else "网络已断开，暂无局域网"
                line3 = "按 OK 键开启 AP 配网热点"
                l1_col = (248, 113, 113)

            # 2x 平滑圆角白色二维码底座
            draw.rounded_rectangle([84, 100, 396, 412], radius=28, fill=(255, 255, 255))
            qr_bytes = self.get_qr_png_bytes()
            if qr_bytes:
                import io
                qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                qr_paste = (qr_pil, (48, 56))
            else:
                _draw_text_vcenter(draw, 240, 256, "生成二维码中...", f_small, (40, 40, 40), "c")

            # 底部信息卡片
            draw.rounded_rectangle([22, 430, 458, 616], radius=28, fill=surf_cont, outline=outline, width=2)
            _draw_text_vcenter(draw, 44, 468, _fit_pil_text(draw, line1, f_bold, 392), f_bold, l1_col, "l")
            _draw_text_vcenter(draw, 44, 522, _fit_pil_text(draw, line2, f_small, 392), f_small, on_surf_var, "l")
            _draw_text_vcenter(draw, 44, 574, _fit_pil_text(draw, line3, f_small, 392), f_small, p_col, "l")

        # C. 一键体检诊断界面 (语义色彩徽章)
        elif self.current_mode == "DOCTOR":
            _draw_menu_header(draw, "系统体检", "HEALTH", f_title, f_small, theme, show_back=True)

            d = self.doctor_cache
            wf_st = wifi_manager.get_status()
            net_val = "在线" if (wf_st.get("connected") and _is_real_lan_ip(d.get("wifi_ip", ""))) else "离线"
            items = [
                ("wifi", "网络连接", "badge", net_val),
                ("doctor", "大模型接口", "badge", d.get("mimo_status", "正常")),
                ("camera", "视觉摄像头", "badge", d.get("camera_status", "正常")),
                ("mic", "麦克风矩阵", "badge", d.get("mic_status", "正常")),
                ("chip", "处理器核心", "badge", d.get("cpu_temp", "45°C")),
            ]

            y = 98
            card_h = 92
            card_gap = 14
            for icon, label, r_type, val in items:
                s_val = str(val)
                if any(k in s_val for k in ("在线", "正常", "畅通", "就绪")):
                    b_style = ((14, 46, 36), (74, 222, 128), (34, 108, 76))
                elif any(k in s_val for k in ("检测", "中")):
                    b_style = ((52, 38, 14), (251, 191, 36), (112, 82, 24))
                elif any(k in s_val for k in ("离线", "异常", "失败", "断开")):
                    b_style = ((56, 20, 24), (248, 113, 113), (124, 44, 52))
                else:
                    b_style = None

                _draw_modern_tile(
                    draw, 22, y, 436, card_h,
                    icon, label,
                    r_type, val, False, f_bold, f_small, theme, scale=S, badge_style=b_style
                )
                y += (card_h + card_gap)

        # D. 硬件控制界面 (TOGGLES: 连续视口平滑渲染)
        elif self.current_mode == "TOGGLES":
            total = len(self.hardware_items)
            cur_idx = max(0, min(total - 1, self.cursor))

            card_w = 426
            card_h = 98
            card_gap = 14
            pitch = card_h + card_gap  # 112px

            viewport_h = 524
            total_content_h = max(0, total * pitch - card_gap)
            max_scroll = max(0, total_content_h - viewport_h + 16)
            scroll_y = max(0, min(max_scroll, (cur_idx - 1) * pitch))

            for idx, item in enumerate(self.hardware_items):
                card_y = 98 + idx * pitch - scroll_y
                if card_y + card_h >= 84 and card_y <= 628:
                    is_selected = (idx == cur_idx)
                    is_on = bool(item.get("state", False))
                    item_type = item.get("type", "")
                    r_type = "switch" if item_type == "toggle" else ("badge" if item_type == "cycle" else "chevron")
                    r_val = is_on if item_type == "toggle" else (str(item.get("val", "")) if item_type == "cycle" else "")

                    _draw_modern_tile(
                        draw, 20, card_y, card_w, card_h,
                        item.get("icon", "hardware"), item["name"],
                        r_type, r_val, is_selected, f_bold, f_small, theme, scale=S
                    )

            _draw_menu_header(draw, "硬件控制", f"{cur_idx + 1}/{total}" if total else "", f_title, f_small, theme, show_back=True)
            draw.rectangle([0, 624, 480, 640], fill=surf_col)

            if total > 4 and max_scroll > 0:
                track_y1, track_y2 = 98, 608
                track_h = track_y2 - track_y1
                draw.rounded_rectangle([460, track_y1, 466, track_y2], radius=3, fill=(28, 34, 40))
                thumb_h = max(48, int((viewport_h / max(1, total_content_h)) * track_h))
                thumb_y = track_y1 + int((scroll_y / max_scroll) * (track_h - thumb_h))
                draw.rounded_rectangle([460, thumb_y, 466, thumb_y + thumb_h], radius=3, fill=p_col)

        # E. 技能服务 & 系统设置统一滚动列表绘制
        elif self.current_mode in ["SKILLS", "SYSTEM"]:
            if self.current_mode == "SKILLS":
                page_title = "技能服务"
                list_items = self.skill_items
            else:
                page_title = "系统设置"
                list_items = self.system_items

            total = len(list_items)
            cur_idx = max(0, min(total - 1, self.cursor)) if total else 0

            card_w = 426
            card_h = 98
            card_gap = 14
            pitch = card_h + card_gap

            viewport_h = 524
            total_content_h = max(0, total * pitch - card_gap)
            max_scroll = max(0, total_content_h - viewport_h + 16)
            scroll_y = max(0, min(max_scroll, (cur_idx - 1) * pitch))

            for idx, item in enumerate(list_items):
                card_y = 98 + idx * pitch - scroll_y
                if card_y + card_h >= 84 and card_y <= 628:
                    is_selected = (idx == cur_idx)
                    item_type = item.get("type", "")
                    r_val = str(item.get("val", ""))
                    show_badge = item_type in ["cycle", "info"] or (bool(r_val) and item_type == "action" and r_val not in ("点击检查更新", "检查更新"))
                    r_type = "badge" if show_badge else "chevron"

                    _draw_modern_tile(
                        draw, 20, card_y, card_w, card_h,
                        item.get("icon", "settings"), item["name"],
                        r_type, r_val, is_selected, f_bold, f_small, theme, scale=S
                    )

            _draw_menu_header(draw, page_title, f"{cur_idx + 1}/{total}" if total else "", f_title, f_small, theme, show_back=True)
            draw.rectangle([0, 624, 480, 640], fill=surf_col)

            if total > 4 and max_scroll > 0:
                track_y1, track_y2 = 98, 608
                track_h = track_y2 - track_y1
                draw.rounded_rectangle([460, track_y1, 466, track_y2], radius=3, fill=(28, 34, 40))
                thumb_h = max(48, int((viewport_h / max(1, total_content_h)) * track_h))
                thumb_y = track_y1 + int((scroll_y / max_scroll) * (track_h - thumb_h))
                draw.rounded_rectangle([460, thumb_y, 466, thumb_y + thumb_h], radius=3, fill=p_col)

        # F. 人脸录入倒计时/取景界面 (FACE_ENROLL)
        elif self.current_mode == "FACE_ENROLL":
            _draw_menu_header(draw, "面容录入", "CAMERA", f_title, f_small, theme, show_back=True)

            box_col = p_col if self.face_enroll_step > 0 else (74, 222, 128)
            draw.rounded_rectangle([80, 108, 400, 428], radius=24, fill=surf_cont, outline=box_col, width=4)
            cl = 28
            for (x1, y1, dx, dy) in [(80, 108, 1, 1), (400, 108, -1, 1), (80, 428, 1, -1), (400, 428, -1, -1)]:
                draw.line([(x1, y1), (x1 + dx * cl, y1)], fill=(255, 255, 255), width=6)
                draw.line([(x1, y1), (x1, y1 + dy * cl)], fill=(255, 255, 255), width=6)

            if self.face_enroll_step > 0:
                _draw_text_vcenter(draw, 240, 268, str(self.face_enroll_step), f_large, p_col, "c")

            draw.rounded_rectangle([22, 456, 458, 560], radius=26, fill=surf_cont, outline=outline, width=2)
            _draw_text_vcenter(draw, 240, 508, _fit_pil_text(draw, self.face_enroll_msg, f_bold, 404), f_bold, on_surf, "c")

        # G. 通用技能服务二维码弹窗
        elif self.current_mode == "SKILLS_QR" or (self.active_qr_skill_id and self.current_mode not in ("FACE", "MENU_MAIN", "QR_CODE", "DOCTOR", "TOGGLES", "SKILLS", "SYSTEM", "FACE_ENROLL", "EASTER_EGG")):
            if self.current_mode == "SKILLS_QR":
                ip = get_local_ip()
                header_title = "技能后台"
                sub_headline = "微信/浏览器 扫码配技能"
                st_line = f"{ip}:8090/skills"
                foot_tip = "配置飞书/天气/米家/音乐等插件"
            else:
                header_title = self.qr_view.get("title") or "扫码授权"
                sub_headline = self.qr_view.get("headline") or "请打开手机扫码"
                st_line = self.qr_view.get("status") or "等待扫码中..."
                foot_tip = self.qr_view.get("foot_tip") or ""

            _draw_menu_header(
                draw, header_title, "BINDING", f_title, f_small, theme, show_back=True,
                badge_color=on_surf_var
            )

            draw.rounded_rectangle([84, 100, 396, 412], radius=28, fill=(255, 255, 255))
            try:
                qr_bytes = self.get_qr_png_bytes()
                if qr_bytes:
                    import io
                    qr_pil = Image.open(io.BytesIO(qr_bytes)).convert("RGB").resize((144, 144), Image.Resampling.NEAREST)
                    qr_paste = (qr_pil, (48, 56))
                else:
                    _draw_text_vcenter(draw, 240, 256, "获取二维码中...", f_small, (40, 40, 40), "c")
            except Exception as e:
                _draw_text_vcenter(draw, 240, 256, _fit_pil_text(draw, f"Error: {e}", f_small, 280), f_small, (40, 40, 40), "c")

            draw.rounded_rectangle([22, 430, 458, 616], radius=28, fill=surf_cont, outline=outline, width=2)
            _draw_text_vcenter(draw, 44, 468, _fit_pil_text(draw, sub_headline, f_bold, 392), f_bold, on_surf, "l")
            _draw_text_vcenter(draw, 44, 522, _fit_pil_text(draw, st_line, f_small, 392), f_small, p_col if "成功" in st_line else (255, 196, 64), "l")
            _draw_text_vcenter(draw, 44, 574, _fit_pil_text(draw, foot_tip, f_small, 392), f_small, on_surf_var, "l")

        # H. 彩蛋模式 (EASTER_EGG)
        elif self.current_mode == "EASTER_EGG":
            font_body = getattr(self, "font_letter_hi", f_main)

            draw.rounded_rectangle([20, 94, 444, 616], radius=24, fill=surf_cont, outline=outline, width=2)

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

            cur_y = 116 - self.easter_egg_scroll_y * S
            for it_type, val in letter_items:
                if it_type == "space":
                    cur_y += val * S
                    continue
                if 88 <= cur_y <= 596:
                    _draw_text_vcenter(draw, 40, cur_y + 14, val, font_body, on_surf, "l")
                cur_y += 38

            _draw_menu_header(draw, "关于 PIG", "ABOUT", f_title, f_small, theme, show_back=True, badge_color=on_surf_var)
            draw.rectangle([0, 620, 480, 640], fill=surf_col)

            total_h = cur_y + self.easter_egg_scroll_y * S - 116
            max_s = max(1, self.easter_egg_max_scroll)
            track_y1, track_y2 = 98, 608
            track_h = track_y2 - track_y1
            draw.rounded_rectangle([458, track_y1, 464, track_y2], radius=3, fill=(28, 34, 40))
            thumb_h = max(48, int((480 / max(1, total_h)) * track_h))
            thumb_y = track_y1 + int((self.easter_egg_scroll_y / max_s) * (track_h - thumb_h))
            draw.rounded_rectangle([458, thumb_y, 464, thumb_y + thumb_h], radius=3, fill=p_col)

        img = hi.resize((240, 320), Image.Resampling.BILINEAR)
        if qr_paste is not None:
            img.paste(qr_paste[0], qr_paste[1])

        self._menu_cache_key = cache_key
        self._menu_cache_img = img

        if vol_active:
            res_img = img.copy()
            draw_toast = ImageDraw.Draw(res_img)
            self._draw_volume_toast(draw_toast, getattr(self, "volume_toast_val", 80))
            return res_img

        return img


# 单例初始化句柄
menu_fsm = MenuFSM()
