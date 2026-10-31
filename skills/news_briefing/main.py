from datetime import datetime

def run():
    now_str = datetime.now().strftime("%m月%d日")
    return (
        f"为您播报【{now_str} 晨间 60 秒科技要闻】："
        "1. 开源具身智能技术加速普及，低成本桌面陪伴机器人成为极客新宠；"
        "2. 端侧大模型与轻量推理框架持续突破，微算力硬件迎来新活力；"
        "3. 今天也是元气满满的一天，记得吃顿营养早餐哦！"
    )
