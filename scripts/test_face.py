import os
import sys

# 将上一级目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eye_renderer import EyeRenderer

def test_all_emotions():
    renderer = EyeRenderer(width=240, height=320)
    output_dir = os.path.join(os.path.dirname(__file__), "output_faces")
    os.makedirs(output_dir, exist_ok=True)

    emotions = ["NORMAL", "BLINK", "HAPPY", "CONFUSED", "SAD", "ANGRY", "SURPRISED", "LOVE", "WINK", "SLEEPY", "DIZZY", "THINKING"]

    print("🎨 开始测试表情渲染引擎...")
    for emo in emotions:
        img = renderer.render_eyes(emotion=emo)
        save_path = os.path.join(output_dir, f"face_{emo.lower()}.png")
        img.save(save_path)
        print(f"  ✅ 表情 [{emo}] 渲染成功 -> {save_path}")

    # 测试带注视角度偏移
    img_look = renderer.render_eyes(emotion="NORMAL", look_x=20, look_y=-10)
    save_path = os.path.join(output_dir, "face_look_right_up.png")
    img_look.save(save_path)
    print(f"  ✅ 注视偏移 [NORMAL (look_x=20, look_y=-10)] 渲染成功 -> {save_path}")

    print("\n✨ 所有表情测试生成完成！文件保存在:", output_dir)

if __name__ == "__main__":
    test_all_emotions()
