# -*- coding: utf-8 -*-
import random

FORTUNES = [
    ("大吉", "今天你就是天选之子，放手去干，PIG给你兜底！"),
    ("中吉", "稳中有升，小步快跑，别浪就能赢。"),
    ("小吉", "运气在线但不多，靠实力补一补刚刚好。"),
    ("吉", "平平淡淡才是真，摸鱼也摸得心安理得。"),
    ("末吉", "小事顺大事缓，重要决定再等等风向。"),
    ("凶", "今天宜躺平忌折腾，别作死就是赢。"),
    ("大凶", "听PIG的，先吃点好的睡一觉，明天再战！"),
]

def run(**kwargs):
    action = kwargs.get("action", "抽签")
    topic = kwargs.get("topic", "")
    state = kwargs.get("_state", {})
    history = state.setdefault("history", [])

    if action == "查询历史":
        if not history:
            return {"text": "签筒还是空的，快摇一支！"}
        last = history[-1]
        streak = state.get("streak", 0)
        return {"text": f"最近一签：{last['level']} - {last['meaning']}（已连续好运 {streak} 次）"}

    level, meaning = random.choice(FORTUNES)
    history.append({"level": level, "meaning": meaning, "topic": topic})
    if level in ("大吉", "中吉", "小吉"):
        state["streak"] = state.get("streak", 0) + 1
    else:
        state["streak"] = 0

    prefix = f"关于「{topic}」，" if topic else ""
    return {
        "text": f"{prefix}摇签——{level}！{meaning} 当前连续好运 {state['streak']} 次。"
    }
