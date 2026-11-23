"""
PIG 机器人 - 人脸视觉注视与头部追踪中枢 (Gaze & Face Tracker)
基于轻量级 OpenCV YuNet 深度学习人脸模型 (仅 228KB，树莓派 3B CPU 纯软解 30~50ms)。
具备两级拟人生理注视逻辑 (Two-tier Gaze Mechanism)：
1. 小幅度偏移: 仅转动眼球瞳孔 (look_x, look_y)，脖子不动；
2. 大幅度偏移: 眼球先盯住边缘，二轴舵机平滑跟随转向居中。
"""

import os
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(PROJECT_ROOT, "models", "face_detection_yunet_2023mar.onnx")

class FaceTracker:
    def __init__(self, enabled: bool = False, input_size=(320, 240)):
        self.enabled = enabled
        self.input_size = input_size
        self.detector = None
        self.cascade_detector = None
        self._init_detector()

        # 视线平滑低通滤波器 (EMA)
        self.smooth_offset_x = 0.0
        self.smooth_offset_y = 0.0
        self.alpha = 0.35  # 平滑系数
        self.last_box = None  # 最近一次检测到的人脸边界框 {"x", "y", "w", "h"}

    def _init_detector(self):
        if cv2 is None:
            return
        # 1. 优先加载 OpenCV YuNet 深度学习轻量模型 (228KB)
        if os.path.exists(MODEL_PATH):
            try:
                self.detector = cv2.FaceDetectorYN_create(
                    MODEL_PATH,
                    "",
                    self.input_size,
                    score_threshold=0.6,
                    nms_threshold=0.3,
                    top_k=1
                )
            except Exception as e:
                print(f"⚠️ [FaceTracker] 初始化 YuNet 模型异常: {e}")
                self.detector = None

        # 2. 备用保障：Haar Cascade 级联分类器
        if self.detector is None:
            cascade_path = os.path.join(PROJECT_ROOT, "models", "haarcascade_frontalface_default.xml")
            if os.path.exists(cascade_path):
                try:
                    self.cascade_detector = cv2.CascadeClassifier(cascade_path)
                    print("👁️ [FaceTracker] YuNet 未就绪，已启用 Haar Cascade 备用人脸检测器")
                except Exception as e:
                    print(f"⚠️ [FaceTracker] 初始化 Haar Cascade 异常: {e}")

    def set_enabled(self, val: bool):
        self.enabled = bool(val)
        print(f"👁️ [FaceTracker] 人脸视线追踪开关已设置为: {'ON (开启)' if self.enabled else 'OFF (关闭)'}")

    def detect_face(self, frame_bgr) -> tuple:
        """
        输入 BGR 图像，输出人脸中心在画面中的相对偏移 [-1.0, 1.0] (0.0 为正中)
        返回: (has_face: bool, offset_x: float, offset_y: float)
        """
        if not self.enabled or frame_bgr is None or cv2 is None:
            return False, 0.0, 0.0

        if self.detector is None and self.cascade_detector is None:
            return False, 0.0, 0.0

        h, w = frame_bgr.shape[:2]
        if (w, h) != self.input_size:
            frame_resized = cv2.resize(frame_bgr, self.input_size)
        else:
            frame_resized = frame_bgr

        # 方案 A: YuNet 深度学习检测
        if self.detector is not None:
            try:
                self.detector.setInputSize(self.input_size)
                _, faces = self.detector.detect(frame_resized)
                if faces is not None and len(faces) > 0:
                    face = faces[0]
                    box = face[0:4]
                    self.last_box = {
                        "x": int(box[0]),
                        "y": int(box[1]),
                        "w": int(box[2]),
                        "h": int(box[3])
                    }
                    cx = float((box[0] + box[2] / 2.0) / self.input_size[0])
                    cy = float((box[1] + box[3] / 2.0) / self.input_size[1])
                    raw_offset_x = float((cx - 0.5) * 2.0)
                    raw_offset_y = float((cy - 0.5) * 2.0)

                    self.smooth_offset_x = float(self.smooth_offset_x * (1 - self.alpha) + raw_offset_x * self.alpha)
                    self.smooth_offset_y = float(self.smooth_offset_y * (1 - self.alpha) + raw_offset_y * self.alpha)
                    return True, self.smooth_offset_x, self.smooth_offset_y
            except Exception:
                pass

        # 方案 B: Haar Cascade 级联检测回退
        if self.cascade_detector is not None:
            try:
                gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
                faces = self.cascade_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
                if len(faces) > 0:
                    x, y, fw, fh = faces[0]
                    self.last_box = {
                        "x": int(x),
                        "y": int(y),
                        "w": int(fw),
                        "h": int(fh)
                    }
                    cx = float((x + fw / 2.0) / self.input_size[0])
                    cy = float((y + fh / 2.0) / self.input_size[1])
                    raw_offset_x = float((cx - 0.5) * 2.0)
                    raw_offset_y = float((cy - 0.5) * 2.0)

                    self.smooth_offset_x = float(self.smooth_offset_x * (1 - self.alpha) + raw_offset_x * self.alpha)
                    self.smooth_offset_y = float(self.smooth_offset_y * (1 - self.alpha) + raw_offset_y * self.alpha)
                    return True, self.smooth_offset_x, self.smooth_offset_y
            except Exception:
                pass

        self.last_box = None
        return False, 0.0, 0.0

    def compute_gaze_and_angles(self, offset_x: float, offset_y: float, cur_pan: float = 90.0, cur_tilt: float = 90.0, is_moving_camera: bool = False):
        """
        两级拟人生理注视逻辑 (Two-tier Gaze & Head Tracking)：
        :param offset_x: 人脸相对画面中心偏移 [-1.0, 1.0]，未镜像时向右移动为负
        :param offset_y: 人脸相对画面中心纵向偏移 [-1.0, 1.0]，向上为负，向下为正
        :param cur_pan: 当前水平舵机角度
        :param cur_tilt: 当前俯仰舵机角度
        :param is_moving_camera: 摄像头是否随头部舵机转动 (真机额头摄像头为 True，电脑固定摄像头为 False)
        :return: (look_x, look_y, target_pan, target_tilt)
        """
        # 1. 瞳孔视线注视偏移 (人向右 -> 镜像对齐后眼珠向右看正方向；人向上 -> 眼珠向上瞄负坐标)
        look_x = int(-offset_x * 28.0)
        look_y = int(offset_y * 20.0)

        # 2. 脖子舵机两级生理驱动机制
        if is_moving_camera:
            # 动架摄像头 (真机闭环 P 控制)
            target_pan = cur_pan
            target_tilt = cur_tilt
            if abs(offset_x) > 0.22:
                delta_pan = offset_x * 3.5
                target_pan = max(30.0, min(150.0, cur_pan + delta_pan))
            if abs(offset_y) > 0.22:
                delta_tilt = offset_y * 2.5
                target_tilt = max(68.0, min(112.0, cur_tilt + delta_tilt))
        else:
            # 定架摄像头 (电脑屏幕 / 网页仿真 Sim-to-Real): 绝对注视角映射
            # 微小位移 (|offset| <= 0.22): 脖子保持端正居中 90°，仅靠眼球转动灵动注视
            # 较大位移 (|offset| > 0.22): 脖子柔顺协同转动，精准将视野朝向主人
            if abs(offset_x) > 0.22:
                target_pan = round(max(35.0, min(145.0, 90.0 + offset_x * 38.0)), 1)
            else:
                target_pan = 90.0

            if abs(offset_y) > 0.22:
                target_tilt = round(max(68.0, min(112.0, 90.0 + offset_y * 24.0)), 1)
            else:
                target_tilt = 90.0

        return look_x, look_y, target_pan, target_tilt

if __name__ == "__main__":
    tracker = FaceTracker(enabled=True)
    print("FaceTracker initialized:", tracker.detector is not None)
    # 测试空图像推理
    dummy_img = np.zeros((240, 320, 3), dtype=np.uint8)
    found, ox, oy = tracker.detect_face(dummy_img)
    print("Dummy test result:", found, ox, oy)
