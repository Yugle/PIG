# -*- coding: utf-8 -*-
"""
PIG 动力底盘 2D/3D 数字孪生仿真引擎 (SimulationChassis)

精准模拟硬件 BOM 方案的物理与小脑行为：
1. 93×90mm 四轮差速短轴底盘 (6V 95RPM N20 电机，线速度 ~0.18 m/s，原地转向 ~120°/s)
2. 桌面物理边界 (1.20m × 0.80m)：模拟车头左右 TCRT5000 红外防跌落急刹与安全回退
3. 桌面实体障碍物 (咖啡杯、桌面书架/笔筒)：模拟车头 VL53L0X 激光 ToF (20~2000mm) 测距与 13cm 前向防撞急刹
4. 500ms 无线 BLE 看门狗断连保护
"""

import math
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from .chassis_base import BaseChassis


# 桌面仿真环境内置障碍物 (坐标单位: 米, radius 为圆柱碰撞半径)
# 坐标系约定: Three.js 水平面 X 为左右(+右/-左), Z 为前后(+正前方/朝向用户, -后方), yaw=0 时车头朝向 +Z
DEFAULT_DESK_OBSTACLES = [
    {"id": "mug", "name": "陶瓷马克杯", "x": 0.16, "z": 0.28, "radius": 0.045},
    {"id": "holder", "name": "笔筒方块", "x": -0.24, "z": 0.22, "radius": 0.048},
    {"id": "toy", "name": "桌面摆件", "x": 0.34, "z": -0.16, "radius": 0.042},
]


class SimulationChassis(BaseChassis):
    """50Hz 闭环桌面动力底盘数字孪生仿真器"""

    MAX_LINEAR_MPS = 0.18       # N20 95RPM 实测桌面约 18 cm/s
    MAX_ANGULAR_DPS = 125.0     # 原地差速转向约 125 °/s
    DESK_HALF_W = 0.62          # 桌面宽 1.24m (X: -0.62 ~ +0.62)
    DESK_HALF_D = 0.42          # 桌面深 0.84m (Z: -0.42 ~ +0.42)
    CHASSIS_RADIUS = 0.068      # 93x90mm 底盘等效外接防撞半径
    TOF_STOP_MM = 125           # VL53L0X 前向障碍物急停阈值 (12.5 cm)
    WATCHDOG_TIMEOUT = 1.2      # 持续运动超时保护(秒)，duration>0 时不受短包限制

    def __init__(self, obstacles: Optional[List[Dict[str, Any]]] = None):
        self._lock = threading.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

        # 位姿状态 (X, Z 单位米; yaw_deg=0 面向 +Z)
        self.x = 0.0
        self.z = 0.0
        self.yaw_deg = 0.0

        # 当前归一化控制指令 [-1, 1]
        self.cmd_linear = 0.0
        self.cmd_angular = 0.0
        self.wheel_l = 0.0
        self.wheel_r = 0.0
        self._cmd_expire_at = 0.0
        self._last_cmd_time = 0.0
        self._has_duration = False

        # 小脑硬件保护开关与传感器实时状态
        self.cliff_guard = True
        self.tof_guard = True
        self.tof_mm = 2000
        self.cliff_left = False
        self.cliff_right = False
        self.guard_triggered: Optional[str] = None
        self.guard_message: str = ""
        self._guard_clear_at = 0.0

        self.obstacles = list(obstacles) if obstacles is not None else list(DEFAULT_DESK_OBSTACLES)

        self._running = True
        self._last_broadcast_ts = 0.0
        self._thread = threading.Thread(target=self._sim_loop, daemon=True, name="ChassisSimLoop")
        self._thread.start()

    @staticmethod
    def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, float(val)))

    def add_state_listener(self, callback: Callable[[Dict[str, Any]], None]):
        if callback and callback not in self._listeners:
            self._listeners.append(callback)

    def _emit_state(self, event_type: str = "chassis_state"):
        state = self.get_state()
        payload = {"type": event_type, "chassis": state}
        for cb in list(self._listeners):
            try:
                cb(payload)
            except Exception:
                pass

    def _compute_sensors(self, x: float, z: float, yaw_deg: float) -> Dict[str, Any]:
        """计算车头两个 TCRT5000 悬崖探头与前方 VL53L0X 激光 ToF 读数"""
        rad = math.radians(yaw_deg)
        fwd_x = math.sin(rad)
        fwd_z = math.cos(rad)
        # 在 Y-up 右手系中面向 fwd=(sinθ, cosθ) 时，机器人自身右侧为 (-cosθ, sinθ)
        right_x = -math.cos(rad)
        right_z = math.sin(rad)

        # 车头左/右探头偏移: 前方 5.2cm, 左右各 3.6cm
        lx = x + fwd_x * 0.052 - right_x * 0.036
        lz = z + fwd_z * 0.052 - right_z * 0.036
        rx = x + fwd_x * 0.052 + right_x * 0.036
        rz = z + fwd_z * 0.052 + right_z * 0.036

        cliff_l = abs(lx) > (self.DESK_HALF_W - 0.02) or abs(lz) > (self.DESK_HALF_D - 0.02)
        cliff_r = abs(rx) > (self.DESK_HALF_W - 0.02) or abs(rz) > (self.DESK_HALF_D - 0.02)

        # VL53L0X 激光测距: 沿车头正前方射线检测 ±15° 锥角内的障碍物边缘及桌面假想挡板
        min_dist_m = 2.0
        sensor_x = x + fwd_x * 0.048
        sensor_z = z + fwd_z * 0.048

        for obs in self.obstacles:
            ox = obs["x"] - sensor_x
            oz = obs["z"] - sensor_z
            proj = ox * fwd_x + oz * fwd_z
            if proj <= 0:
                continue
            perp = abs(ox * right_x + oz * right_z)
            # 有效光斑锥宽 (ToF FoV ~25°)
            beam_r = obs["radius"] + 0.015 + proj * 0.18
            if perp <= beam_r:
                surface_dist = max(0.02, proj - obs["radius"])
                if surface_dist < min_dist_m:
                    min_dist_m = surface_dist

        return {
            "cliff_left": cliff_l,
            "cliff_right": cliff_r,
            "tof_mm": int(round(min_dist_m * 1000)),
        }

    def move(self, linear: float, angular: float, duration: float = 0.0) -> Dict[str, Any]:
        lin = self._clamp(linear)
        ang = self._clamp(angular)
        now = time.time()

        with self._lock:
            self.cmd_linear = lin
            self.cmd_angular = ang
            self._last_cmd_time = now
            if duration and float(duration) > 0:
                self._has_duration = True
                self._cmd_expire_at = now + float(duration)
            else:
                self._has_duration = False
                self._cmd_expire_at = now + self.WATCHDOG_TIMEOUT

            # 计算左右轮归一化差速 [-1, 1]
            self.wheel_l = self._clamp(lin + ang)
            self.wheel_r = self._clamp(lin - ang)

            if lin == 0.0 and ang == 0.0:
                self.wheel_l = 0.0
                self.wheel_r = 0.0

        self._emit_state("chassis_state")
        return self.get_state()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self.cmd_linear = 0.0
            self.cmd_angular = 0.0
            self.wheel_l = 0.0
            self.wheel_r = 0.0
            self._has_duration = False
        self._emit_state("chassis_state")
        return self.get_state()

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self.x = 0.0
            self.z = 0.0
            self.yaw_deg = 0.0
            self.cmd_linear = 0.0
            self.cmd_angular = 0.0
            self.wheel_l = 0.0
            self.wheel_r = 0.0
            self._has_duration = False
            self.guard_triggered = None
            self.guard_message = ""
            sensors = self._compute_sensors(0.0, 0.0, 0.0)
            self.cliff_left = sensors["cliff_left"]
            self.cliff_right = sensors["cliff_right"]
            self.tof_mm = sensors["tof_mm"]
        self._emit_state("chassis_state")
        return self.get_state()

    def set_guards(self, cliff_guard: Optional[bool] = None, tof_guard: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            if cliff_guard is not None:
                self.cliff_guard = bool(cliff_guard)
            if tof_guard is not None:
                self.tof_guard = bool(tof_guard)
        self._emit_state("chassis_state")
        return self.get_state()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": "simulation",
                "x": round(self.x, 4),
                "z": round(self.z, 4),
                "yaw_deg": round(self.yaw_deg, 2),
                "linear": round(self.cmd_linear, 3),
                "angular": round(self.cmd_angular, 3),
                "wheel_l": round(self.wheel_l, 3),
                "wheel_r": round(self.wheel_r, 3),
                "tof_mm": int(self.tof_mm),
                "cliff_left": bool(self.cliff_left),
                "cliff_right": bool(self.cliff_right),
                "cliff_guard": bool(self.cliff_guard),
                "tof_guard": bool(self.tof_guard),
                "guard_triggered": self.guard_triggered,
                "guard_message": self.guard_message,
                "obstacles": self.obstacles,
                "desk_bounds": {"half_w": self.DESK_HALF_W, "half_d": self.DESK_HALF_D},
            }

    def _sim_loop(self):
        dt = 0.02  # 50 Hz 小脑控制周期
        while self._running:
            time.sleep(dt)
            now = time.time()
            changed = False
            alert_to_emit = False

            with self._lock:
                if self.guard_triggered and now > self._guard_clear_at:
                    self.guard_triggered = None
                    self.guard_message = ""
                    changed = True

                # 看门狗或定时结束自动归零
                if (self.cmd_linear != 0.0 or self.cmd_angular != 0.0) and now >= self._cmd_expire_at:
                    self.cmd_linear = 0.0
                    self.cmd_angular = 0.0
                    self.wheel_l = 0.0
                    self.wheel_r = 0.0
                    changed = True

                lin = self.cmd_linear
                ang = self.cmd_angular

                # 先根据当前位姿刷新传感器
                sensors = self._compute_sensors(self.x, self.z, self.yaw_deg)
                self.cliff_left = sensors["cliff_left"]
                self.cliff_right = sensors["cliff_right"]
                self.tof_mm = sensors["tof_mm"]

                if lin != 0.0 or ang != 0.0:
                    # 1. 小脑急刹拦截: 向前开时若触发桌面边缘悬崖传感器
                    if self.cliff_guard and lin > 0 and (self.cliff_left or self.cliff_right):
                        rad = math.radians(self.yaw_deg)
                        # 安全回退 2.5cm 并小幅转向躲避桌沿 (左侧悬空向右 -25°, 右侧悬空向左 +25°)
                        self.x -= math.sin(rad) * 0.025
                        self.z -= math.cos(rad) * 0.025
                        self.yaw_deg = (self.yaw_deg + (-25.0 if self.cliff_left else 25.0)) % 360.0
                        self.cmd_linear = 0.0
                        self.cmd_angular = 0.0
                        self.wheel_l = 0.0
                        self.wheel_r = 0.0
                        self.guard_triggered = "CLIFF_BRAKE"
                        self.guard_message = "🛑 TCRT5000 检测到桌沿悬空！10ms 硬件急刹并安全回退"
                        self._guard_clear_at = now + 2.2
                        changed = True
                        alert_to_emit = True
                    # 2. 小脑急刹拦截: 向前开时若 VL53L0X 检测到前方 < 12.5cm 障碍物
                    elif self.tof_guard and lin > 0 and self.tof_mm <= self.TOF_STOP_MM:
                        self.cmd_linear = 0.0
                        self.cmd_angular = 0.0
                        self.wheel_l = 0.0
                        self.wheel_r = 0.0
                        self.guard_triggered = "TOF_OBSTACLE_BRAKE"
                        self.guard_message = f"🚧 VL53L0X 测得前方障碍 ({self.tof_mm}mm)！20ms 自动防撞急刹"
                        self._guard_clear_at = now + 2.0
                        changed = True
                        alert_to_emit = True
                    else:
                        # 执行差速运动学积分
                        # angular > 0 为向右(顺时针), 在 Y-up 右手系(面向+Z)中, X 变大需要 yaw_deg += delta?
                        # 注意: sin(yaw), cos(yaw) 当 yaw 增加时 fwd_x > 0 (+X为视觉右或左? Three.js 相机在+Z看原点时, +X在屏幕右侧!)
                        # Wait: 如果面向 +Z (远离相机/朝向镜头前), 向右侧转意味着转向 -X 还是 +X?
                        # 若以机器人第一视角面向 +Z: 朝 +Z 看去时, 机器人的右手边是 -X 方向!
                        # 当按 D(右转) 时: 机器人应当顺时针转 (从+Y俯视顺时针 = 绕Y轴旋转角度减小), 此时 fwd 从 +Z 转向 -X!
                        d_yaw = -ang * self.MAX_ANGULAR_DPS * dt
                        next_yaw = (self.yaw_deg + d_yaw) % 360.0
                        rad = math.radians(next_yaw)
                        v_mps = lin * self.MAX_LINEAR_MPS
                        next_x = self.x + math.sin(rad) * v_mps * dt
                        next_z = self.z + math.cos(rad) * v_mps * dt

                        # 物理桌沿硬兜底(防止关闭防跌落保护时飞出无限远)
                        if not self.cliff_guard:
                            next_x = max(-self.DESK_HALF_W + 0.03, min(self.DESK_HALF_W - 0.03, next_x))
                            next_z = max(-self.DESK_HALF_D + 0.03, min(self.DESK_HALF_D - 0.03, next_z))

                        # 物理圆柱体硬碰撞校验(即使关掉激光保护也不穿透水杯)
                        blocked_by_obs = False
                        for obs in self.obstacles:
                            dx = next_x - obs["x"]
                            dz = next_z - obs["z"]
                            if math.hypot(dx, dz) < (obs["radius"] + self.CHASSIS_RADIUS):
                                blocked_by_obs = True
                                break

                        if not blocked_by_obs:
                            self.x = next_x
                            self.z = next_z
                        self.yaw_deg = next_yaw

                        sensors = self._compute_sensors(self.x, self.z, self.yaw_deg)
                        self.cliff_left = sensors["cliff_left"]
                        self.cliff_right = sensors["cliff_right"]
                        self.tof_mm = sensors["tof_mm"]
                        changed = True

            # 运动期间每 ~60ms 广播一次位姿同步给多端/HUD；停稳或报警时立即广播
            if alert_to_emit or (changed and (now - self._last_broadcast_ts >= 0.06 or (self.cmd_linear == 0 and self.cmd_angular == 0))):
                self._last_broadcast_ts = now
                self._emit_state("chassis_alert" if alert_to_emit else "chassis_state")

    def close(self):
        self._running = False
        self.stop()
