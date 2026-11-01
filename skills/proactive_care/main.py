import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.proactive_care import ProactiveCareManager

def execute(action="status", scene="morning", snooze_minutes=120, **kwargs):
    storage_dir = os.path.join(PROJECT_ROOT, "data")
    pcm = ProactiveCareManager(storage_dir=storage_dir)

    act = (action or "status").lower()

    if act == "enable" or act == "on":
        pcm.set_enabled(True)
        return {
            "status": "success",
            "message": "❤️ 主动关怀守护已开启！小猪将在早晨醒来、久坐疲劳或深夜时主动送上贴心关怀与问候~",
            "behavior": {"emotion": "HAPPY", "action": "NOD"}
        }

    elif act == "disable" or act == "off":
        pcm.set_enabled(False)
        return {
            "status": "success",
            "message": "🔕 主动关怀已关闭。小猪将保持安静，只在您呼唤唤醒词时进行应答。",
            "behavior": {"emotion": "NORMAL", "action": "NOD"}
        }

    elif act == "toggle":
        new_state = pcm.toggle()
        msg = "❤️ 主动关怀已开启！" if new_state else "🔕 主动关怀已关闭。"
        return {
            "status": "success",
            "message": msg,
            "behavior": {"emotion": "HAPPY" if new_state else "NORMAL", "action": "NOD"}
        }

    elif act == "snooze" or act == "mute":
        mins = int(snooze_minutes or 120)
        pcm.snooze(minutes=mins)
        return {
            "status": "success",
            "message": f"🤫 收到！小猪已开启免打扰静音模式，接下来的 {mins} 分钟内绝不主动打扰您，请专注忙您的事情吧~",
            "behavior": {"emotion": "NORMAL", "action": "BOW"}
        }

    elif act in ["unsnooze", "unmute"]:
        pcm.unsnooze()
        return {
            "status": "success",
            "message": "🔔 已为您解除免打扰，小猪恢复主动关怀守护！",
            "behavior": {"emotion": "HAPPY", "action": "NOD"}
        }

    elif act == "test":
        sc = scene or "morning"
        payload = pcm.trigger_test(scene=sc)
        return {
            "status": "success",
            "message": f"[{payload.get('title')}] {payload.get('text')}",
            "behavior": {
                "emotion": payload.get("emotion", "CARE"),
                "action": payload.get("action", "LOOK_UP")
            }
        }

    else:
        st = pcm.get_status()
        state_desc = "开启中" if st["enabled"] else "已关闭"
        if st["is_snoozed"]:
            state_desc += f" (静音免打扰中，剩余 {st['snooze_remaining_min']} 分钟)"
        return {
            "status": "success",
            "message": f"❤️ 当前主动关怀守护状态：【{state_desc}】。今日连续活动时间约 {st['continuous_active_min']} 分钟。",
            "behavior": {"emotion": "HAPPY", "action": "NOD"}
        }

def run(action="status", scene="morning", snooze_minutes=120, **kwargs):
    """供 SkillManager 与 ReAct 动态技能调用的标准入口"""
    res = execute(action=action, scene=scene, snooze_minutes=snooze_minutes, **kwargs)
    if isinstance(res, dict):
        return res.get("message", str(res))
    return str(res)

if __name__ == "__main__":
    print(run(action="status"))
