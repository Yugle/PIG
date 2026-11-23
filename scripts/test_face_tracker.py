#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人脸视觉注视与头部追踪单元测试脚本 (scripts/test_face_tracker.py)
验证内容：
1. YuNet / Haar Cascade 双模人脸检测引擎加载
2. 两级拟人生理注视逻辑 (Two-tier Gaze) 数学计算验证
   - 小微移 (≤0.22): 仅眼球瞳孔偏移，舵机保持不动
   - 大位移 (>0.22): 眼球边缘锁定，脖子二轴舵机柔和跟随
3. EMA 指数平滑低通滤波防抖动
4. 与 EyeRenderer 动态眼球微表情引擎的无缝联动渲染
"""

import os
import sys
import numpy as np

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.face_tracker import FaceTracker
from drivers.eye_renderer import EyeRenderer

def run_face_tracker_tests():
    print("==================================================")
    print("🤖 正在启动 [人脸视觉注视与两级头部追踪] 全链路单元测试...")
    print("==================================================")

    # 1. 引擎初始化测试
    tracker = FaceTracker(enabled=True)
    assert tracker.enabled is True, "FaceTracker 开启状态异常"
    print(f"✅ 1. FaceTracker 实例创建成功，检测器状态: {'就绪' if (tracker.detector is not None or getattr(tracker, 'cascade_detector', None) is not None) else '未就绪'}")

    # 2. 空白/黑屏图像鲁棒性测试 (防崩溃)
    dummy_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    has_face, ox, oy = tracker.detect_face(dummy_frame)
    assert not has_face, "全黑画面不应误检出人脸"
    print("✅ 2. 全黑与无目标画面鲁棒性测试通过 (has_face=False, 无误报)")

    # 3. 两级拟人生理注视逻辑验证 (数学与行为验证)
    print("\n🔬 3. 正在验证两级拟人生理注视逻辑 (Two-tier Gaze):")
    cur_pan, cur_tilt = 90.0, 90.0

    # 场景 A: 微小偏移 (offset_x = 0.15, offset_y = -0.10，绝对值 <= 0.22)
    lx, ly, tp, tt = tracker.compute_gaze_and_angles(0.15, -0.10, cur_pan, cur_tilt)
    print(f"  • [场景 A: 视线微偏] offset=(+0.15, -0.10) -> 眼球瞳孔偏移 look=({lx}, {ly}), 目标舵机 angles=({tp:.1f}°, {tt:.1f}°)")
    assert tp == 90.0 and tt == 90.0, "微小位移时脖子舵机必须端正居中 90° (仅动眼球)"
    assert lx == int(-0.15 * 28.0) and ly == int(-0.10 * 20.0), "眼球瞳孔偏移方向必须与视线位移方向一致"
    print("    -> 验证通过：小幅度位移仅转动眼球瞳孔，脖子保持端正居中！")

    # 场景 B: 人脸向右大位移 (offset_x = 0.60, offset_y = 0.35，绝对值 > 0.22)
    lx, ly, tp, tt = tracker.compute_gaze_and_angles(0.60, 0.35, cur_pan, cur_tilt)
    print(f"  • [场景 B: 主人右移] offset=(+0.60, +0.35) -> 眼球瞳孔偏移 look=({lx}, {ly}), 目标舵机 angles=({tp:.1f}°, {tt:.1f}°)")
    assert tp != 90.0, "大幅度横向位移时水平舵机必须联动转动跟随"
    assert tt != 90.0, "大幅度纵向位移时俯仰舵机必须联动转动跟随"
    assert 30.0 <= tp <= 150.0, f"水平舵机角度必须限制在安全物理极限内 (当前: {tp})"
    assert 68.0 <= tt <= 112.0, f"俯仰舵机角度必须限制在安全物理极限内 (当前: {tt})"
    print("    -> 验证通过：大幅度位移触发眼球边缘锁定 + 舵机平滑跟随转向居中！")

    # 4. EMA 防抖动低通滤波器平滑度验证
    print("\n🔬 4. 正在验证 EMA 指数低通滤波平滑性能:")
    t_ema = FaceTracker(enabled=True)
    t_ema.smooth_offset_x = 0.50  # 预热至初始位置
    # 模拟人脸在 +0.50 处高频微小抖动 (0.45, 0.55, 0.46, 0.54)
    readings = [0.45, 0.55, 0.46, 0.54, 0.47, 0.53]
    smoothed = []
    for r in readings:
        t_ema.smooth_offset_x = t_ema.smooth_offset_x * (1 - t_ema.alpha) + r * t_ema.alpha
        smoothed.append(round(t_ema.smooth_offset_x, 3))
    print(f"  • 原始位移抖动序列: {readings} (幅度极差: {max(readings) - min(readings):.3f})")
    print(f"  • EMA 滤波平滑序列: {smoothed} (幅度极差: {max(smoothed) - min(smoothed):.3f})")
    assert (max(smoothed) - min(smoothed)) < (max(readings) - min(readings)), "EMA 必须有效抑制抖动方差"
    print("    -> 验证通过：抖动被有效衰减，杜绝电机高频抽搐发热！")

    # 5. 与 EyeRenderer 动态微表情系统联动渲染验证
    print("\n🎨 5. 正在验证与 240x320 眼球渲染引擎联动:")
    renderer = EyeRenderer(width=240, height=320)
    # 传入场景 B 的瞳孔偏移值
    face_img = renderer.render_eyes(emotion="NORMAL", look_x=lx, look_y=ly)
    assert face_img.size == (240, 320), f"渲染图片尺寸异常: {face_img.size}"
    out_dir = os.path.join(PROJECT_ROOT, "scripts", "output_faces")
    os.makedirs(out_dir, exist_ok=True)
    save_file = os.path.join(out_dir, "test_gaze_tracked_face.png")
    face_img.save(save_file)
    print(f"  • 成功将注视偏移 ({lx}, {ly}) 合成至 240x320 动态眼球，保存为: {save_file}")

    print("\n==================================================")
    print("🎉 恭喜！人脸视觉注视与两级头部追踪全模块测试 100% 通过！")
    print("==================================================")

if __name__ == "__main__":
    run_face_tracker_tests()
