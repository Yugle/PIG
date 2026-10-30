from PIL import Image, ImageDraw

class EyeRenderer:
    """
    轻量级桌面机器人面部/眼球渲染引擎。
    分辨率为 240 x 320（对应 2.4 寸竖屏 ST7789）。
    纯 Python (PIL) 高性能绘制，无重型 UI 库依赖，适合树莓派 1GB 内存。
    支持 12 种丰富具身微表情与视线注视偏移。
    """

    def __init__(self, width=240, height=320):
        self.width = width
        self.height = height

    def render_eyes(self, emotion="NORMAL", look_x=0, look_y=0) -> Image.Image:
        """
        绘制眼睛并返回 PIL 图像。
        :param emotion: NORMAL | BLINK | HAPPY | CONFUSED | SAD | ANGRY | SURPRISED | LOVE | WINK | SLEEPY | DIZZY | THINKING
        :param look_x: 水平注视偏移量 [-30, 30]
        :param look_y: 垂直注视偏移量 [-20, 20]
        """
        # 深色科技蓝底背景
        img = Image.new("RGB", (self.width, self.height), (10, 14, 20))
        draw = ImageDraw.Draw(img)

        # 默认亮青色 (Cyan) 与高光
        eye_color = (0, 220, 255)
        highlight_color = (255, 255, 255)

        # 眼睛中心基准点
        cy = self.height // 2 + look_y
        lx = self.width // 4 + look_x
        rx = self.width * 3 // 4 + look_x

        emotion = (emotion or "NORMAL").upper()

        if emotion == "NORMAL":
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 36, cy - 50, cx + 36, cy + 50], radius=22, fill=eye_color)
                draw.ellipse([cx - 20, cy - 36, cx - 8, cy - 24], fill=highlight_color)

        elif emotion == "BLINK":
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 36, cy - 4, cx + 36, cy + 4], radius=3, fill=eye_color)

        elif emotion == "HAPPY":
            # 向上弯曲的明亮笑眼
            for cx in [lx, rx]:
                draw.arc([cx - 40, cy - 40, cx + 40, cy + 30], start=195, end=345, fill=eye_color, width=14)

        elif emotion == "CONFUSED":
            # 一大一小好奇/困惑眼
            draw.rounded_rectangle([lx - 36, cy - 50, lx + 36, cy + 50], radius=22, fill=eye_color)
            draw.ellipse([lx - 20, cy - 36, lx - 8, cy - 24], fill=highlight_color)
            draw.rounded_rectangle([rx - 25, cy - 25, rx + 25, cy + 25], radius=16, fill=eye_color)

        elif emotion == "SAD":
            # 沮丧委屈下垂眼
            for cx in [lx, rx]:
                draw.arc([cx - 40, cy - 20, cx + 40, cy + 50], start=15, end=165, fill=eye_color, width=12)

        elif emotion == "ANGRY":
            # 愤怒/认真斜红眼
            for cx, is_left in [(lx, True), (rx, False)]:
                draw.rounded_rectangle([cx - 36, cy - 45, cx + 36, cy + 45], radius=18, fill=(255, 60, 60))
                cut_pts = [
                    (cx - 40, cy - 55),
                    (cx + 40, cy - (30 if is_left else 55)),
                    (cx + 40, cy - 40),
                    (cx - 40, cy - (40 if is_left else 25))
                ]
                draw.polygon(cut_pts, fill=(10, 14, 20))

        elif emotion == "SURPRISED":
            # 吃惊睁大圆眼
            for cx in [lx, rx]:
                draw.ellipse([cx - 45, cy - 55, cx + 45, cy + 55], fill=eye_color)
                draw.ellipse([cx - 20, cy - 30, cx + 20, cy + 10], fill=(10, 14, 20))

        elif emotion == "LOVE":
            # 桃心爱心眼 (浪漫粉红)
            pink = (255, 80, 140)
            for cx in [lx, rx]:
                r = 16
                draw.ellipse([cx - r*2 + 2, cy - 26, cx + 2, cy + 6], fill=pink)
                draw.ellipse([cx - 2, cy - 26, cx + r*2 - 2, cy + 6], fill=pink)
                draw.polygon([(cx - r*2 + 4, cy - 4), (cx + r*2 - 4, cy - 4), (cx, cy + 32)], fill=pink)
                draw.ellipse([cx - 16, cy - 18, cx - 8, cy - 10], fill=highlight_color)

        elif emotion == "WINK":
            # 单眼调皮眨眼 (左眼笑眼，右眼大眼睁开)
            draw.arc([lx - 40, cy - 40, lx + 40, cy + 30], start=195, end=345, fill=eye_color, width=14)
            draw.rounded_rectangle([rx - 36, cy - 50, rx + 36, cy + 50], radius=22, fill=eye_color)
            draw.ellipse([rx - 20, cy - 36, rx - 8, cy - 24], fill=highlight_color)

        elif emotion == "SLEEPY":
            # 困倦打瞌睡眼 + 浮动 zZZ
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 32, cy + 8, cx + 32, cy + 16], radius=4, fill=(100, 160, 200))
            # 绘制 zZZ 符号
            draw.text((rx + 24, cy - 60), "z", fill=(0, 220, 255))
            draw.text((rx + 34, cy - 80), "Z", fill=(0, 220, 255))

        elif emotion == "DIZZY":
            # 眩晕蚊香圈圈眼
            orange = (255, 180, 20)
            for cx in [lx, rx]:
                for radius, width in [(36, 6), (24, 5), (12, 4)]:
                    draw.arc([cx - radius, cy - radius, cx + radius, cy + radius], start=45, end=330, fill=orange, width=width)

        elif emotion == "THINKING":
            # 思考/推理扫描眼 (眼球向上望，眼眶微眯)
            for cx in [lx, rx]:
                draw.rounded_rectangle([cx - 36, cy - 32, cx + 36, cy + 42], radius=18, fill=eye_color)
                # 瞳孔高光往右上偏
                draw.ellipse([cx - 10, cy - 28, cx + 18, cy], fill=highlight_color)

        else:
            return self.render_eyes("NORMAL", look_x, look_y)

        return img
