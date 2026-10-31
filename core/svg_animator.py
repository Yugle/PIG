"""
SVG 动态表情生成器 (Animated SVG Eye Generator)
专为桌面机器人打造的高清矢量微表情生成中枢。
纯 CSS3 动力学驱动：内置仿生呼吸律动、非线性眨眼、微扫视漫游与 12 种情绪专属循环动效。
生成的 SVG 具有 0 外部依赖、极致体积（<3KB）、任意尺寸无损缩放、原生 60 帧 GPU 硬件加速特性。
"""

import os
from typing import Optional

class SVGAnimator:
    """生成带有独立 CSS3 Keyframe 动画的高清矢量 SVG 表情"""

    COMMON_STYLES = """
      /* 基础仿生微动作 */
      @keyframes breathe {
        0%, 100% { transform: scale(1.0); }
        50% { transform: scale(1.035); }
      }
      @keyframes naturalBlink {
        0%, 86%, 100% { transform: scaleY(1); }
        90% { transform: scaleY(0.08); }
        94% { transform: scaleY(1.06); }
        97% { transform: scaleY(1.0); }
      }
      @keyframes microSaccade {
        0%, 35% { transform: translate(0px, 0px); }
        40%, 70% { transform: translate(4px, -2px); }
        75%, 100% { transform: translate(-3px, 2px); }
      }
      @keyframes heartBeat {
        0%, 100% { transform: scale(1.0); }
        14% { transform: scale(1.24); }
        28% { transform: scale(1.06); }
        42% { transform: scale(1.18); }
        70% { transform: scale(1.0); }
      }
      @keyframes dizzySpin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      @keyframes floatZ1 {
        0% { transform: translate(0, 0) scale(0.6); opacity: 0; }
        30% { opacity: 0.9; }
        80% { transform: translate(14px, -28px) scale(1.1); opacity: 0.6; }
        100% { transform: translate(22px, -45px) scale(1.3); opacity: 0; }
      }
      @keyframes floatZ2 {
        0% { transform: translate(0, 0) scale(0.6); opacity: 0; }
        30% { opacity: 0.9; }
        80% { transform: translate(16px, -32px) scale(1.1); opacity: 0.6; }
        100% { transform: translate(26px, -52px) scale(1.4); opacity: 0; }
      }
      @keyframes tearDrop {
        0% { transform: translateY(0); opacity: 0; }
        20% { opacity: 0.9; }
        70% { opacity: 0.9; }
        100% { transform: translateY(35px); opacity: 0; }
      }
      @keyframes angryQuiver {
        0%, 100% { transform: translate(0, 0); }
        25% { transform: translate(-1.5px, 1px); }
        50% { transform: translate(1.5px, -1px); }
        75% { transform: translate(-1px, -1px); }
      }
      @keyframes happyBounce {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-6px); }
      }
      @keyframes thinkScan {
        0%, 100% { transform: translate(0, -6px); }
        50% { transform: translate(8px, -10px); }
      }

      .bg { fill: #0a0e14; }
      .cyan-eye { fill: #00e5ff; }
      .pink-eye { fill: #ff4081; }
      .red-eye { fill: #ff3344; }
      .orange-eye { stroke: #ff9800; fill: none; }
      .highlight { fill: #ffffff; }

      .breathe-group {
        animation: breathe 3.6s ease-in-out infinite;
        transform-origin: 120px 160px;
      }
      .blink-group {
        animation: naturalBlink 4.0s cubic-bezier(0.4, 0, 0.2, 1) infinite;
        transform-origin: 120px 160px;
      }
      .saccade-group {
        animation: microSaccade 6.5s ease-in-out infinite;
      }
      .heart-beat {
        animation: heartBeat 1.4s ease-in-out infinite;
        transform-origin: center;
      }
      .dizzy-spin {
        animation: dizzySpin 3.0s linear infinite;
        transform-origin: center;
      }
      .angry-quiver {
        animation: angryQuiver 0.12s ease-in-out infinite;
      }
      .happy-bounce {
        animation: happyBounce 1.8s ease-in-out infinite;
        transform-origin: 120px 160px;
      }
      .tear-drop {
        animation: tearDrop 2.2s ease-in infinite;
      }
      .z1 {
        animation: floatZ1 3.2s ease-out infinite;
        transform-origin: 185px 125px;
      }
      .z2 {
        animation: floatZ2 3.2s ease-out 1.5s infinite;
        transform-origin: 185px 125px;
      }
      .think-scan {
        animation: thinkScan 3.5s ease-in-out infinite;
      }
    """

    @classmethod
    def generate_svg(cls, emotion: str = "NORMAL", width: int = 240, height: int = 320) -> str:
        """
        生成指定情绪的高清动态 SVG 代码。
        :param emotion: NORMAL | HAPPY | BLINK | CONFUSED | SAD | ANGRY | SURPRISED | LOVE | WINK | SLEEPY | DIZZY | THINKING
        :param width: 视图宽度 (默认 240)
        :param height: 视图高度 (默认 320)
        :return: 包含完整 XML 与独立 CSS 动画的 SVG 字符串
        """
        emotion = (emotion or "NORMAL").upper()

        elements = ""

        if emotion == "NORMAL":
            elements = f"""
  <g class="breathe-group">
    <g class="saccade-group">
      <g class="blink-group">
        <!-- 左眼 -->
        <rect class="cyan-eye" x="24" y="110" width="72" height="100" rx="22"/>
        <circle class="highlight" cx="44" cy="130" r="8" opacity="0.95"/>
        <!-- 右眼 -->
        <rect class="cyan-eye" x="144" y="110" width="72" height="100" rx="22"/>
        <circle class="highlight" cx="164" cy="130" r="8" opacity="0.95"/>
      </g>
    </g>
  </g>
"""

        elif emotion == "HAPPY":
            elements = f"""
  <g class="happy-bounce">
    <!-- 明亮向上弯曲的微笑笑眼 -->
    <path d="M 22 170 Q 60 115 98 170" fill="none" stroke="#00e5ff" stroke-width="14" stroke-linecap="round"/>
    <path d="M 142 170 Q 180 115 218 170" fill="none" stroke="#00e5ff" stroke-width="14" stroke-linecap="round"/>
    <!-- 腮红光晕 -->
    <ellipse cx="28" cy="192" rx="14" ry="7" fill="#ff4081" opacity="0.45"/>
    <ellipse cx="212" cy="192" rx="14" ry="7" fill="#ff4081" opacity="0.45"/>
  </g>
"""

        elif emotion == "BLINK":
            elements = f"""
  <g class="breathe-group">
    <!-- 闭眼状态细条 -->
    <rect class="cyan-eye" x="24" y="156" width="72" height="8" rx="4"/>
    <rect class="cyan-eye" x="144" y="156" width="72" height="8" rx="4"/>
  </g>
"""

        elif emotion == "CONFUSED":
            elements = f"""
  <g class="breathe-group">
    <g class="saccade-group">
      <!-- 左眼大眼 -->
      <g class="blink-group">
        <rect class="cyan-eye" x="24" y="110" width="72" height="100" rx="22"/>
        <circle class="highlight" cx="44" cy="130" r="8" opacity="0.95"/>
      </g>
      <!-- 右眼好奇眯眼 -->
      <g style="transform-origin: 180px 160px; transform: rotate(-8deg);">
        <rect class="cyan-eye" x="156" y="136" width="48" height="48" rx="14"/>
        <circle class="highlight" cx="170" cy="150" r="5" opacity="0.9"/>
      </g>
    </g>
  </g>
"""

        elif emotion == "SAD":
            elements = f"""
  <g class="breathe-group">
    <!-- 沮丧下垂弧度眼 -->
    <path d="M 22 155 Q 60 205 98 155" fill="none" stroke="#00e5ff" stroke-width="13" stroke-linecap="round"/>
    <path d="M 142 155 Q 180 205 218 155" fill="none" stroke="#00e5ff" stroke-width="13" stroke-linecap="round"/>
    <!-- 下落的小眼泪 -->
    <g class="tear-drop">
      <path d="M 180 185 C 176 195 174 200 174 205 C 174 212 180 216 186 216 C 192 216 198 212 198 205 C 198 200 196 195 186 185 Z" fill="#00e5ff" opacity="0.85"/>
    </g>
  </g>
"""

        elif emotion == "ANGRY":
            elements = f"""
  <g class="angry-quiver">
    <!-- 左斜怒眼 -->
    <g>
      <rect class="red-eye" x="24" y="115" width="72" height="90" rx="18"/>
      <polygon points="20,110 100,140 100,110" fill="#0a0e14"/>
      <circle class="highlight" cx="46" cy="148" r="6" opacity="0.85"/>
    </g>
    <!-- 右斜怒眼 -->
    <g>
      <rect class="red-eye" x="144" y="115" width="72" height="90" rx="18"/>
      <polygon points="140,140 220,110 220,140" fill="#0a0e14"/>
      <circle class="highlight" cx="166" cy="148" r="6" opacity="0.85"/>
    </g>
  </g>
"""

        elif emotion == "SURPRISED":
            elements = f"""
  <g class="breathe-group">
    <!-- 吃惊大圆眼 -->
    <g class="blink-group">
      <circle cx="60" cy="160" r="46" fill="#00e5ff"/>
      <circle cx="60" cy="160" r="18" fill="#0a0e14"/>
      <circle class="highlight" cx="50" cy="148" r="6" opacity="0.95"/>

      <circle cx="180" cy="160" r="46" fill="#00e5ff"/>
      <circle cx="180" cy="160" r="18" fill="#0a0e14"/>
      <circle class="highlight" cx="170" cy="148" r="6" opacity="0.95"/>
    </g>
  </g>
"""

        elif emotion == "LOVE":
            elements = f"""
  <g class="heart-beat" style="transform-origin: 60px 160px;">
    <!-- 左爱心 -->
    <path class="pink-eye" d="M 60 185 C 60 185 24 156 24 138 C 24 122 36 112 50 112 C 58 112 60 118 60 118 C 60 118 62 112 70 112 C 84 112 96 122 96 138 C 96 156 60 185 60 185 Z"/>
    <circle class="highlight" cx="48" cy="132" r="5" opacity="0.9"/>
  </g>
  <g class="heart-beat" style="transform-origin: 180px 160px; animation-delay: 0.1s;">
    <!-- 右爱心 -->
    <path class="pink-eye" d="M 180 185 C 180 185 144 156 144 138 C 144 122 156 112 170 112 C 178 112 180 118 180 118 C 180 118 182 112 190 112 C 204 112 216 122 216 138 C 216 156 180 185 180 185 Z"/>
    <circle class="highlight" cx="168" cy="132" r="5" opacity="0.9"/>
  </g>
"""

        elif emotion == "WINK":
            elements = f"""
  <g class="breathe-group">
    <!-- 左眼单眼调皮笑眼 -->
    <path d="M 22 170 Q 60 120 98 170" fill="none" stroke="#00e5ff" stroke-width="14" stroke-linecap="round"/>
    <!-- 右眼明亮睁大 -->
    <g class="blink-group">
      <rect class="cyan-eye" x="144" y="110" width="72" height="100" rx="22"/>
      <circle class="highlight" cx="164" cy="130" r="8" opacity="0.95"/>
    </g>
  </g>
"""

        elif emotion == "SLEEPY":
            elements = f"""
  <g class="breathe-group" style="animation-duration: 4.8s;">
    <!-- 沉重困倦半闭眼 -->
    <rect x="26" y="166" width="68" height="14" rx="6" fill="#64b5f6"/>
    <rect x="146" y="166" width="68" height="14" rx="6" fill="#64b5f6"/>
    <!-- 浮动上升的 z Z Z 泡泡 -->
    <text class="z1" x="180" y="130" fill="#00e5ff" font-family="sans-serif" font-weight="bold" font-size="20">z</text>
    <text class="z2" x="195" y="110" fill="#00e5ff" font-family="sans-serif" font-weight="bold" font-size="28">Z</text>
  </g>
"""

        elif emotion == "DIZZY":
            elements = f"""
  <!-- 左右双同心反向/同向旋转蚊香圈 -->
  <g class="dizzy-spin" style="transform-origin: 60px 160px;">
    <circle cx="60" cy="160" r="38" class="orange-eye" stroke-width="6" stroke-dasharray="50 15"/>
    <circle cx="60" cy="160" r="24" class="orange-eye" stroke-width="5" stroke-dasharray="35 12"/>
    <circle cx="60" cy="160" r="10" class="orange-eye" stroke-width="4"/>
  </g>
  <g class="dizzy-spin" style="transform-origin: 180px 160px; animation-direction: reverse;">
    <circle cx="180" cy="160" r="38" class="orange-eye" stroke-width="6" stroke-dasharray="50 15"/>
    <circle cx="180" cy="160" r="24" class="orange-eye" stroke-width="5" stroke-dasharray="35 12"/>
    <circle cx="180" cy="160" r="10" class="orange-eye" stroke-width="4"/>
  </g>
"""

        elif emotion == "THINKING":
            elements = f"""
  <g class="breathe-group">
    <!-- 思考眯眼 + 视线向上扫描 -->
    <g class="think-scan">
      <rect class="cyan-eye" x="24" y="125" width="72" height="75" rx="18"/>
      <circle class="highlight" cx="60" cy="142" r="10" opacity="0.95"/>

      <rect class="cyan-eye" x="144" y="125" width="72" height="75" rx="18"/>
      <circle class="highlight" cx="180" cy="142" r="10" opacity="0.95"/>
    </g>
  </g>
"""

        else:
            return cls.generate_svg("NORMAL", width, height)

        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <style>
{cls.COMMON_STYLES}
    </style>
  </defs>
  <!-- 背景底色 -->
  <rect class="bg" width="100%" height="100%" rx="16"/>
  <!-- 动态表情主体 -->
{elements}
</svg>"""
        return svg

    @classmethod
    def export_svg(cls, emotion: str, output_path: str) -> str:
        """生成并写入指定路径的 SVG 文件"""
        svg_content = cls.generate_svg(emotion)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        return output_path

    @classmethod
    def export_all(cls, output_dir: str = "data/svg_faces"):
        """一键导出全部 12 款动态 SVG"""
        emotions = [
            "NORMAL", "HAPPY", "BLINK", "CONFUSED", "SAD", "ANGRY",
            "SURPRISED", "LOVE", "WINK", "SLEEPY", "DIZZY", "THINKING"
        ]
        exported = {}
        for emo in emotions:
            path = os.path.join(output_dir, f"{emo.lower()}.svg")
            cls.export_svg(emo, path)
            exported[emo] = path
        return exported
