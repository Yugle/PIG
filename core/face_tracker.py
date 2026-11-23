"""
PIG 机器人 - 人脸视觉注视与头部追踪中枢 (Gaze & Face Tracker)
基于轻量级 OpenCV YuNet 深度学习人脸模型 (仅 228KB，树莓派 3B CPU 纯软解 30~50ms)。
具备两级拟人生理注视逻辑 (Two-tier Gaze Mechanism)：
1. 小幅度偏移: 仅转动眼球瞳孔 (look_x, look_y)，脖子不动；
2. 大幅度偏移: 眼球先盯住边缘，二轴舵机平滑跟随转向居中。
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(PROJECT_ROOT, "models", "face_detection_yunet_2023mar.onnx")
RECOGNIZER_MODEL_PATH = os.path.join(PROJECT_ROOT, "models", "face_recognition_sface_2021dec.onnx")
MASTER_FACE_FILE = os.path.join(PROJECT_ROOT, "data", "master_face.json")

class FaceTracker:
    def __init__(self, enabled: bool = False, input_size=(320, 240)):
        self.enabled = enabled
        self.input_size = input_size
        self.detector = None
        self.cascade_detector = None
        self._init_detector()

        # SFace 离线人脸识别与认主中枢 (128-d 特征向量)
        self.recognizer = None
        self._init_recognizer()
        self.master_embedding: Optional[np.ndarray] = self._load_master_face()
        self.last_identity = "MASTER" if self.master_embedding is not None else "UNREGISTERED"
        self.last_similarity = 0.0
        self.last_recognition_time = 0.0
        self.last_raw_face_row = None
        self.last_frame_resized = None

        # 视线平滑低通滤波器 (EMA) 与防抖机制
        self.smooth_offset_x = 0.0
        self.smooth_offset_y = 0.0
        self.alpha = 0.20  # 平滑阻尼系数
        self.noise_threshold = 0.025  # 像素级微噪死区过滤
        self.last_box = None  # 最近一次检测到的人脸边界框 {"x", "y", "w", "h"}

        # 九向离散防抖状态 (9-Sector Discretized Gaze)
        self.cur_sector = "CENTER"
        self.last_target_pan = 90.0
        self.last_target_tilt = 90.0
        self.last_look_x = 0
        self.last_look_y = 0

    def _init_recognizer(self):
        if cv2 is None:
            return
        if os.path.exists(RECOGNIZER_MODEL_PATH) and hasattr(cv2, "FaceRecognizerSF"):
            try:
                self.recognizer = cv2.FaceRecognizerSF.create(RECOGNIZER_MODEL_PATH, "")
                print("👁️ [FaceTracker] OpenCV SFace 离线人脸识别引擎已就绪 (128-d 认主)")
            except Exception as e:
                print(f"⚠️ [FaceTracker] 初始化 SFace 异常: {e}")

    def _load_master_face(self) -> Optional[np.ndarray]:
        if os.path.exists(MASTER_FACE_FILE):
            try:
                with open(MASTER_FACE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    emb = data.get("embedding")
                    if emb and len(emb) == 128:
                        return np.array(emb, dtype=np.float32).reshape(1, 128)
            except Exception as e:
                print(f"⚠️ [FaceTracker] 读取主人人脸特征库异常: {e}")
        return None

    def register_master_face(self, frame=None) -> Dict[str, Any]:
        """将当前画面中的人脸特征录入为唯一的【主人特征向量】"""
        if self.recognizer is None or cv2 is None:
            return {"success": False, "msg": "SFace 人脸识别引擎未就绪"}

        target_frame = frame if frame is not None else getattr(self, "last_frame_resized", None)
        target_face = getattr(self, "last_raw_face_row", None)
        if target_frame is None or target_face is None:
            return {"success": False, "msg": "未在镜头中检测到人脸，请正对摄像头"}

        try:
            if target_frame.shape[1] != self.input_size[0] or target_frame.shape[0] != self.input_size[1]:
                target_frame = cv2.resize(target_frame, self.input_size)
            aligned = self.recognizer.alignCrop(target_frame, target_face)
            feat = self.recognizer.feature(aligned)  # shape: (1, 128)
            self.master_embedding = feat

            master_data = {
                "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "embedding": feat.flatten().tolist()
            }
            with open(MASTER_FACE_FILE, "w", encoding="utf-8") as f:
                json.dump(master_data, f, indent=2)

            self.last_identity = "MASTER"
            self.last_similarity = 1.0
            print("👑 [FaceTracker] 主人人脸 128-d 特征向量录入成功！已持久化至 data/master_face.json")
            return {"success": True, "msg": "主人面容录入成功！PIG 已经记住你了~"}
        except Exception as e:
            return {"success": False, "msg": f"录入失败: {e}"}

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
                    self.last_raw_face_row = face.copy()
                    self.last_frame_resized = frame_resized.copy()

                    # 离线人脸认主识别 (抽样执行：每 3.0 秒或首次检测到人脸时提取 128-d 向量比对，绝不增加每帧负担)
                    now_ts = time.time()
                    if self.recognizer is not None and (now_ts - self.last_recognition_time > 3.0):
                        self.last_recognition_time = now_ts
                        try:
                            aligned = self.recognizer.alignCrop(frame_resized, face)
                            feat = self.recognizer.feature(aligned)
                            if self.master_embedding is not None:
                                sim = float(self.recognizer.match(feat, self.master_embedding, cv2.FaceRecognizerSF_FR_COSINE))
                                self.last_similarity = round(max(0.0, sim), 2)
                                if sim >= 0.38:
                                    self.last_identity = "MASTER"
                                else:
                                    self.last_identity = "GUEST"
                            else:
                                self.last_identity = "UNREGISTERED"
                                self.last_similarity = 0.0
                        except Exception:
                            pass

                    box = face[0:4]
                    self.last_box = {
                        "x": int(box[0]),
                        "y": int(box[1]),
                        "w": int(box[2]),
                        "h": int(box[3])
                    }
                    cx = float((box[0] + box[2] / 2.0) / self.input_size[0])
                    cy = float((box[1] + box[3] / 2.0) / self.input_size[1])
                    # 镜像映射到用户镜像视口空间 (Mirror View Space):
                    # 用户向右移动 (屏幕向右) -> cx < 0.5 -> raw_offset_x 为正
                    # 用户向左移动 (屏幕向左) -> cx > 0.5 -> raw_offset_x 为负
                    raw_offset_x = -float((cx - 0.5) * 2.0)
                    raw_offset_y = float((cy - 0.5) * 2.0)

                    # 敏捷自适应滤波 (动态响应: 动作发生时 85% 权重即刻跟进，静止时死区完全锁定防抖)
                    diff_x = abs(raw_offset_x - self.smooth_offset_x)
                    if diff_x > 0.035:
                        self.smooth_offset_x = float(self.smooth_offset_x * 0.15 + raw_offset_x * 0.85)
                    elif diff_x > 0.015:
                        self.smooth_offset_x = float(self.smooth_offset_x * 0.70 + raw_offset_x * 0.30)

                    diff_y = abs(raw_offset_y - self.smooth_offset_y)
                    if diff_y > 0.035:
                        self.smooth_offset_y = float(self.smooth_offset_y * 0.15 + raw_offset_y * 0.85)
                    elif diff_y > 0.015:
                        self.smooth_offset_y = float(self.smooth_offset_y * 0.70 + raw_offset_y * 0.30)

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
                    raw_offset_x = -float((cx - 0.5) * 2.0)
                    raw_offset_y = float((cy - 0.5) * 2.0)

                    diff_x = abs(raw_offset_x - self.smooth_offset_x)
                    if diff_x > 0.035:
                        self.smooth_offset_x = float(self.smooth_offset_x * 0.15 + raw_offset_x * 0.85)
                    elif diff_x > 0.015:
                        self.smooth_offset_x = float(self.smooth_offset_x * 0.70 + raw_offset_x * 0.30)

                    diff_y = abs(raw_offset_y - self.smooth_offset_y)
                    if diff_y > 0.035:
                        self.smooth_offset_y = float(self.smooth_offset_y * 0.15 + raw_offset_y * 0.85)
                    elif diff_y > 0.015:
                        self.smooth_offset_y = float(self.smooth_offset_y * 0.70 + raw_offset_y * 0.30)

                    return True, self.smooth_offset_x, self.smooth_offset_y
            except Exception:
                pass

        self.last_box = None
        return False, 0.0, 0.0

    def compute_gaze_and_angles(self, offset_x: float, offset_y: float, cur_pan: float = 90.0, cur_tilt: float = 90.0, is_moving_camera: bool = False, mode: str = "9grid"):
        """
        两级拟人生理注视逻辑与防抖中枢：
        支持九向离散防抖注视 (9grid，极度平稳零抖动) 与连续平滑防抖注视 (continuous，3度迟滞过滤)
        :param offset_x: 人脸相对画面中心偏移 [-1.0, 1.0]
        :param offset_y: 人脸相对画面中心纵向偏移 [-1.0, 1.0]
        :param cur_pan: 当前水平舵机角度
        :param cur_tilt: 当前俯仰舵机角度
        :param is_moving_camera: 摄像头是否随头部舵机转动 (真机为 True，固定摄像头为 False)
        :param mode: 跟踪模式 "9grid" (九向离散，推荐) 或 "continuous" (连续防抖)
        :return: (look_x, look_y, target_pan, target_tilt)
        """
        # 1. 瞳孔视线注视偏移 (灵敏零延迟跟进，1px 死区过滤像素噪点)
        # offset_x > 0 (用户在右侧) -> 眼珠向右 (look_x > 0)
        # offset_x < 0 (用户在左侧) -> 眼珠向左 (look_x < 0)
        raw_lx = int(offset_x * 24.0)
        raw_ly = int(offset_y * 18.0)
        if abs(raw_lx - self.last_look_x) >= 1:
            self.last_look_x = raw_lx
        if abs(raw_ly - self.last_look_y) >= 1:
            self.last_look_y = raw_ly
        look_x = self.last_look_x
        look_y = self.last_look_y

        # 2. 动架摄像头 (真机额头摄像头闭环 P 控制，带 1.5 度死区)
        if is_moving_camera:
            target_pan = cur_pan
            target_tilt = cur_tilt
            if abs(offset_x) > 0.15:
                delta_pan = -offset_x * 3.5
                if abs(delta_pan) >= 1.5:
                    target_pan = max(30.0, min(150.0, cur_pan + delta_pan))
            if abs(offset_y) > 0.15:
                delta_tilt = offset_y * 2.5
                if abs(delta_tilt) >= 1.5:
                    target_tilt = max(68.0, min(112.0, cur_tilt + delta_tilt))
            return look_x, look_y, target_pan, target_tilt

        # 3. 定架摄像头 (电脑屏幕 / 网页仿真 Sim-to-Real)
        if mode == "9grid":
            # 方案一：九向离散注视 (9-Sector Discretized Gaze)
            # 敏捷紧凑迟滞死区 (Hysteresis):
            # 离开 CENTER 只需偏离 > 0.18 (极度灵敏响应)，返回 CENTER 需回到 < 0.10 (稳定不跳频)
            is_center_x = abs(offset_x) < (0.10 if self.cur_sector != "CENTER" else 0.18)
            is_center_y = abs(offset_y) < (0.10 if self.cur_sector != "CENTER" else 0.18)

            if is_center_x and is_center_y:
                self.cur_sector = "CENTER"
                target_pan, target_tilt = 90.0, 90.0
            else:
                # 水平向左向右判定 (偏右凝视 64°，偏左凝视 116°)
                # 用户在右侧 (offset_x > 0.15) -> RIGHT, 舵机向右转动至 64°
                # 用户在左侧 (offset_x < -0.15) -> LEFT, 舵机向左转动至 116°
                if offset_x > 0.15:
                    pan = 64.0
                    dir_x = "RIGHT"
                elif offset_x < -0.15:
                    pan = 116.0
                    dir_x = "LEFT"
                else:
                    pan = 90.0
                    dir_x = "MID"

                # 垂直向上向下判定 (上方抬头 76°，下方低头 104°)
                # 用户在上方 (offset_y < -0.15) -> UP, 舵机抬头仰望 76°
                # 用户在下方 (offset_y > 0.15) -> DOWN, 舵机低头俯视 104°
                if offset_y < -0.15:
                    tilt = 76.0
                    dir_y = "UP"
                elif offset_y > 0.15:
                    tilt = 104.0
                    dir_y = "DOWN"
                else:
                    tilt = 90.0
                    dir_y = "MID"

                self.cur_sector = f"{dir_y}_{dir_x}"
                target_pan, target_tilt = pan, tilt

            self.last_target_pan = target_pan
            self.last_target_tilt = target_tilt
            return look_x, look_y, target_pan, target_tilt
        else:
            # 方案二：连续平滑注视带 1.5° 角度死区滤波 (高灵敏迅捷)
            if abs(offset_x) > 0.15:
                calc_pan = round(max(35.0, min(145.0, 90.0 - offset_x * 38.0)), 1)
            else:
                calc_pan = 90.0

            if abs(offset_y) > 0.15:
                calc_tilt = round(max(68.0, min(112.0, 90.0 + offset_y * 24.0)), 1)
            else:
                calc_tilt = 90.0

            if abs(calc_pan - self.last_target_pan) >= 1.5:
                self.last_target_pan = calc_pan
            if abs(calc_tilt - self.last_target_tilt) >= 1.5:
                self.last_target_tilt = calc_tilt

            return look_x, look_y, self.last_target_pan, self.last_target_tilt

if __name__ == "__main__":
    tracker = FaceTracker(enabled=True)
    print("FaceTracker initialized:", tracker.detector is not None)
    # 测试空图像推理
    dummy_img = np.zeros((240, 320, 3), dtype=np.uint8)
    found, ox, oy = tracker.detect_face(dummy_img)
    print("Dummy test result:", found, ox, oy)
