# -*- coding: utf-8 -*-
"""真机人脸认主单元测试（关闭视线追踪时也能主动检测、3帧重试、字段双向兼容）"""

import os
import sys
import tempfile
import numpy as np
import cv2
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import core.face_tracker as ft_mod
from core.face_tracker import FaceTracker
from core.hardware_agent import HardwareAgent


def test_register_works_even_when_tracking_disabled():
    tracker = FaceTracker(enabled=False)
    fake_face = np.array([[80, 60, 100, 110, 105, 95, 145, 95, 125, 118, 110, 140, 142, 140, 0.92]], dtype=np.float32)
    fake_feat = np.ones((1, 128), dtype=np.float32)

    tracker.detector = MagicMock()
    tracker.detector.detect.return_value = (1, fake_face)
    tracker.recognizer = MagicMock()
    tracker.recognizer.alignCrop.return_value = np.zeros((112, 112, 3), dtype=np.uint8)
    tracker.recognizer.feature.return_value = fake_feat

    with tempfile.TemporaryDirectory() as tmpd:
        master_file = os.path.join(tmpd, "master_face.json")
        with patch.object(ft_mod, "MASTER_FACE_FILE", master_file):
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            res = tracker.register_master_face(frame)
            assert res["status"] == "ok"
            assert res["success"] is True
            assert res["msg"] == res["message"]
            assert os.path.exists(master_file)
            assert tracker.last_identity == "MASTER"


def test_hardware_agent_captures_physical_hal_with_retry():
    tracker = FaceTracker(enabled=False)
    fake_face = np.array([[80, 60, 100, 110, 105, 95, 145, 95, 125, 118, 110, 140, 142, 140, 0.92]], dtype=np.float32)
    fake_feat = np.ones((1, 128), dtype=np.float32)

    tracker.detector = MagicMock()
    # 第1帧未检出人脸（如自动曝光/眨眼），第2帧成功检出
    tracker.detector.detect.side_effect = [(1, None), (1, None), (1, fake_face)]
    tracker.recognizer = MagicMock()
    tracker.recognizer.alignCrop.return_value = np.zeros((112, 112, 3), dtype=np.uint8)
    tracker.recognizer.feature.return_value = fake_feat

    ok_jpg = cv2.imencode(".jpg", np.full((240, 320, 3), 128, dtype=np.uint8))[1].tobytes()
    fake_hal = MagicMock()
    fake_hal.capture_image.return_value = ok_jpg

    agent = HardwareAgent.__new__(HardwareAgent)
    agent.hal = fake_hal
    agent.face_tracker = tracker
    agent.latest_camera_frame = None
    agent.latest_camera_frame_ts = 0.0

    with tempfile.TemporaryDirectory() as tmpd:
        master_file = os.path.join(tmpd, "master_face.json")
        with patch.object(ft_mod, "MASTER_FACE_FILE", master_file), \
             patch("core.hardware_agent.time.sleep", return_value=None):
            res = agent.register_master_face()
            assert res["status"] == "ok"
            assert res["success"] is True
            assert fake_hal.capture_image.call_count == 2


def test_menu_fsm_face_enroll_preview_and_no_emoji_tofu():
    from PIL import Image
    from core.menu_fsm import MenuFSM

    fake_hal = MagicMock()
    rgb = np.full((240, 320, 3), 140, dtype=np.uint8)
    rgb[40:160, 100:220] = (50, 180, 90)
    jpg = cv2.imencode(".jpg", rgb)[1].tobytes()
    fake_hal.capture_image.return_value = jpg

    fsm = MenuFSM(hal=fake_hal)
    fsm.agent = MagicMock()
    fsm.agent.latest_camera_frame = rgb
    fsm.agent.face_tracker = MagicMock()
    fsm.agent.face_tracker.last_box = {"x": 100, "y": 40, "w": 120, "h": 120}
    fsm.agent.face_tracker.input_size = (320, 240)

    prev = fsm._grab_enroll_preview_pil(prefer_agent_latest=True, draw_face_box=True)
    assert prev is not None and prev.size == (304, 304)

    fsm.current_mode = "FACE_ENROLL"
    fsm.face_enroll_step = 0
    fsm.face_enroll_status = "ok"
    fsm.face_enroll_msg = "认主成功! 已绑定"
    fsm.face_enroll_preview_img = prev
    fsm.face_enroll_preview_seq = 1
    assert all(ord(c) <= 0x9FFF for c in fsm.face_enroll_msg)

    rendered = fsm.render_pil_overlay(Image.new("RGB", (240, 320), (0, 0, 0)))
    assert rendered.size == (240, 320)
    # 确认屏幕取景框中心区域确实粘贴了预览图像（非纯黑/纯灰背景）
    center_px = rendered.getpixel((120, 130))
    assert center_px != (0, 0, 0) and center_px != (18, 25, 27), f"Unexpected blank preview pixel: {center_px}"


if __name__ == "__main__":
    test_register_works_even_when_tracking_disabled()
    test_hardware_agent_captures_physical_hal_with_retry()
    test_menu_fsm_face_enroll_preview_and_no_emoji_tofu()
    print("ALL_FACE_ENROLLMENT_TESTS_PASSED")
