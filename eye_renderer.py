from PIL import Image, ImageDraw

class EyeRenderer:
    """
    轻量级桌面机器人面部/眼球渲染引擎。
    分辨率为 240 x 320（对应 2.4 寸竖屏 ST7789）。
    纯 Python (PIL) 高性能绘制，无重型 UI 库依赖，适合树莓派 1GB 内存。
    """

    def __init__(self, width=240, height=320):
        self.width = width
        self.height = height

    def render_eyes(self, emotion="NORMAL", look_x=0, look_y=0) -> Image.Image:
        """
        绘制眼睛并返回 PIL 图像。
        :param emotion: NORMAL | BLINK | HAPPY | CONFUSED | SAD | ANGRY | SURPRISED
        :param look_x: 水平注视偏移量 [-30, 30]
        :param look_y: 垂直注视偏移量 [-20, 20]
        """
        # 深色科技蓝底背景
        img = Image.new("RGB", (self.width, self.height), (10, 14, 20))
        draw = ImageDraw.Draw(img)

        # 默认亮青色 (Cyan)
        eye_color = (0, 220, 255)
        highlight_color = (255, 255, 255)

        # 眼睛中心基准点
        cy = self.height // 2 + look_y
        lx = self.width // 4 + look_x
        rx = self.width * 3 // 4 + look_x

        emotion = (emotion or "NORMAL").upper()

        if emotion == "NORMAL":
            for cx in [lx, rx]:
                # 圆角矩形大眼
                draw.rounded_rectangle(
                    [cx - 36, cy - 50, cx + 36, cy + 50],
                    radius=22,
                    fill=eye_color
                )
                # 左上方高光小白点
                draw.ellipse(
                    [cx - 20, cy - 36, cx - 8, cy - 24],
                    fill=highlight_color
                )

        elif emotion == "BLINK":
            # 眨眼闭眼横线
            for cx in [lx, rx]:
                draw.rounded_rectangle(
                    [cx - 36, cy - 4, cx + 36, cy + 4],
                    radius=3,
                    fill=eye_color
                )

        elif emotion == "HAPPY":
            # 向上弯曲的笑眼弧线
            for cx in [lx, rx]:
                draw.arc(
                    [cx - 40, cy - 40, cx + 40, cy + 30],
                    start=195,
                    end=345,
                    fill=eye_color,
                    width=12
                )

        elif emotion == "CONFUSED":
            # 一大一小好奇/困惑眼
            # 左眼大
            draw.rounded_rectangle(
                [lx - 36, cy - 50, lx + 36, cy + 50],
                radius=22,
                fill=eye_color
            )
            draw.ellipse(
                [lx - 20, cy - 36, lx - 8, cy - 24],
                fill=highlight_color
            )
            # 右眼小
            draw.rounded_rectangle(
                [rx - 25, cy - 25, rx + 25, cy + 25],
                radius=16,
                fill=eye_color
            )

        elif emotion == "SAD":
            # 沮丧委屈眼：下垂弧度
            for cx in [lx, rx]:
                draw.arc(
                    [cx - 40, cy - 20, cx + 40, cy + 50],
                    start=15,
                    end=165,
                    fill=eye_color,
                    width=12
                )

        elif emotion == "ANGRY":
            # 愤怒/认真斜眼
            for cx, is_left in [(lx, True), (rx, False)]:
                draw.rounded_rectangle(
                    [cx - 36, cy - 45, cx + 36, cy + 45],
                    radius=18,
                    fill=(255, 60, 60)  # 怒目转红
                )
                # 遮盖斜角营造怒意
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
                draw.ellipse(
                    [cx - 45, cy - 55, cx + 45, cy + 55],
                    fill=eye_color
                )
                draw.ellipse(
                    [cx - 20, cy - 30, cx + 20, cy + 10],
                    fill=(10, 14, 20)
                )

        else:
            # 默认兜底
            return self.render_eyes("NORMAL", look_x, look_y)

        return img
