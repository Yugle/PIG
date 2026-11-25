"""
PIG 机器人 - 外设驱动与感知层 (drivers)
"""

from .hal_base import BaseRobotHAL
from .hal_physical import PhysicalRobotHAL
from .hal_simulation import SimulationRobotHAL
from .chassis_base import BaseChassis, NoopChassis
from .chassis_sim import SimulationChassis
from .eye_renderer import EyeRenderer
from .asr_engine import BaiduASR
from .tts_engine import TTSEngine

__all__ = [
    "BaseRobotHAL",
    "PhysicalRobotHAL",
    "SimulationRobotHAL",
    "BaseChassis",
    "NoopChassis",
    "SimulationChassis",
    "EyeRenderer",
    "BaiduASR",
    "TTSEngine",
]
