# -*- coding: utf-8 -*-
"""
PIG 具身动力底盘控制技能 (skills/chassis_control/main.py)
支持语音/对话驱动 3D 仿真底盘或物理 BLE 底盘执行差速运动。
"""
import time


def run(action: str = "forward", speed: float = 0.7, duration: float = 1.2) -> str:
    try:
        from core.hardware_agent import get_hardware_agent
        agent = get_hardware_agent()
    except Exception:
        agent = None

    if agent is None or not getattr(agent, "chassis", None):
        return "当前未连接具身动力底盘实例，请确认主服务已启用底盘驱动。"

    act = str(action or "forward").strip().lower()
    spd = max(0.2, min(1.0, float(speed if speed is not None else 0.7)))
    dur = max(0.2, min(5.0, float(duration if duration is not None else 1.2)))

    if act in ["stop", "brake", "停止", "刹车", "急停"]:
        st = agent.stop_chassis()
        return f"已立即执行底盘紧急刹车，当前坐标 ({st.get('x', 0):.2f}m, {st.get('z', 0):.2f}m)。"

    if act in ["reset", "home", "复位", "回原点"]:
        st = agent.reset_chassis()
        return f"已将小车平滑复位至桌面原点 (0.00m, 0.00m)，航向角 0°。"

    # 动作前将脖子回中并微俯视前方，保证视野与车头对齐
    if getattr(agent, "hal", None):
        try:
            agent.hal.set_angles(90, 82)
            agent.emit_event("pose_sync", {"pan": 90, "tilt": 82, "emotion": "HAPPY"})
        except Exception:
            pass

    if act in ["forward", "fwd", "前进", "向前"]:
        lin, ang = spd, 0.0
        desc = f"向前行驶 {dur:.1f} 秒"
    elif act in ["backward", "back", "后退", "倒车"]:
        lin, ang = -spd, 0.0
        desc = f"向后倒车 {dur:.1f} 秒"
    elif act in ["turn_left", "left", "左转"]:
        lin, ang = 0.0, -spd
        desc = f"原地左转 {dur:.1f} 秒"
    elif act in ["turn_right", "right", "右转"]:
        lin, ang = 0.0, spd
        desc = f"原地右转 {dur:.1f} 秒"
    elif act in ["spin", "circle", "转圈", "转一圈"]:
        lin, ang = 0.0, 0.85
        dur = min(3.0, max(dur, 2.8))
        desc = f"原地开心转一圈 ({dur:.1f} 秒)"
    else:
        return f"未识别的底盘动作: {action}，可选 forward/backward/turn_left/turn_right/spin/stop/reset。"

    agent.move_chassis(linear=lin, angular=ang, duration=dur)
    # 短阻塞观察是否触发小脑防跌落/激光防撞保护
    time.sleep(min(dur + 0.05, 1.6))
    st = agent.get_chassis_state()
    guard = st.get("guard_triggered")
    if guard:
        return (
            f"刚执行【{desc}】途中触发底盘小脑安全拦截：{st.get('guard_message', guard)}！"
            f"当前停驻在 ({st.get('x', 0):.2f}m, {st.get('z', 0):.2f}m)，前向激光测距 {st.get('tof_mm', 2000)}mm。"
        )

    return (
        f"底盘已执行【{desc}】，当前桌面坐标 ({st.get('x', 0):.2f}m, {st.get('z', 0):.2f}m)，"
        f"航向 {st.get('yaw_deg', 0):.1f}°，前向 VL53L0X 测距 {st.get('tof_mm', 2000)}mm。"
    )
