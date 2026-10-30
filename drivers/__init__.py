"""
桌面具身 AI 伴侣 - 外设驱动与感知层 (drivers)
"""

from .hal_base import BaseRobotHAL
from .hal_physical import PhysicalRobotHAL
from .hal_simulation import SimulationRobotHAL
from .eye_renderer import EyeRenderer
from .asr_engine import BaiduASR
from .tts_engine import TTSEngine

__all__ = [
    "BaseRobotHAL",
    "PhysicalRobotHAL",
    "SimulationRobotHAL",
    "EyeRenderer",
    "BaiduASR",
    "TTSEngine",
]
