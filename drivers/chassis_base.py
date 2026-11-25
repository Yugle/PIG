# -*- coding: utf-8 -*-
"""
PIG 具身动力底盘硬件抽象层 (BaseChassis) 与无头/蓝牙工厂
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable


class BaseChassis(ABC):
    """
    轮式动力底盘抽象基类。
    统一 3D 数字孪生仿真底盘 (SimulationChassis)、无线 BLE 底盘 (BLEChassis) 与串口底盘接口。
    """

    @abstractmethod
    def move(self, linear: float, angular: float, duration: float = 0.0) -> Dict[str, Any]:
        """
        下发差速运动指令。
        :param linear: 线速度归一化值 [-1.0, 1.0]，正数为前进，负数为后退
        :param angular: 角速度归一化值 [-1.0, 1.0]，正数为右转(顺时针)，负数为左转(逆时针)
        :param duration: 持续时间(秒)。>0 时到期自动刹车；0 表示持续按当前速度运动直到新指令或看门狗超时
        """
        pass

    @abstractmethod
    def stop(self) -> Dict[str, Any]:
        """紧急制动刹车，所有轮速立即归零。"""
        pass

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """获取当前底盘状态（位姿、轮速、悬崖/激光传感器与报警标志）。"""
        pass

    def reset(self) -> Dict[str, Any]:
        """重置底盘位姿至桌面原点。"""
        return self.stop()

    def set_guards(self, cliff_guard: Optional[bool] = None, tof_guard: Optional[bool] = None) -> Dict[str, Any]:
        """动态开启或关闭小脑硬件防跌落/防撞保护。"""
        return self.get_state()

    def add_state_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """注册底盘状态变化/报警回调。"""
        pass

    def close(self):
        """安全释放底盘资源并刹车。"""
        self.stop()


class NoopChassis(BaseChassis):
    """未接物理底盘且非仿真时的空安全实现。"""

    def move(self, linear: float, angular: float, duration: float = 0.0) -> Dict[str, Any]:
        return {"status": "noop", "linear": 0.0, "angular": 0.0}

    def stop(self) -> Dict[str, Any]:
        return {"status": "noop", "linear": 0.0, "angular": 0.0}

    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": "noop",
            "x": 0.0,
            "z": 0.0,
            "yaw_deg": 0.0,
            "linear": 0.0,
            "angular": 0.0,
            "wheel_l": 0.0,
            "wheel_r": 0.0,
            "tof_mm": 2000,
            "cliff_left": False,
            "cliff_right": False,
            "guard_triggered": None,
        }
