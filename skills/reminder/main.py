# -*- coding: utf-8 -*-
"""
闹钟与日程提醒技能 (Reminder Skill Plugin)
作为标准 skills/ 插件，统一处理倒计时、特定时间闹钟、日程查询与取消
"""
from typing import Optional

def run(action="list", time_str="", seconds=0, content="日程提醒", repeat="none", keyword=""):
    """
    提醒技能执行入口：
    :param action: 操作类型 ('set_alarm', 'set_timer', 'cancel', 'list')
    :param time_str: 闹钟时间 (如 '08:00', '14:30')
    :param seconds: 倒计时秒数 (如 60, 300)
    :param content: 提醒内容 (如 '喝水', '关火', '起床')
    :param repeat: 重复周期 ('none', 'daily')
    :param keyword: 取消提醒时的关键词或ID
    """
    try:
        from core.skill_manager import get_skill_manager
    except ImportError:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from core.skill_manager import get_skill_manager

    mgr = get_skill_manager()
    if not mgr:
        return "提醒调度器核心服务未就绪，无法设置提醒。"

    act = (action or "").strip().lower()

    if act in ["set_timer", "timer", "倒计时"]:
        sec = float(seconds) if seconds else 60
        reminder = mgr.set_timer(sec, content or "倒计时结束")
        return f"⏰ 倒计时设定成功！将在 {reminder.get('target_time_str', '')} 提醒你：【{reminder.get('content', '')}】。"

    elif act in ["set_alarm", "alarm", "闹钟", "定闹钟"]:
        if not time_str:
            time_str = "08:00"
        reminder = mgr.set_alarm(time_str, content or "起床/日程提醒", repeat=repeat)
        return f"⏰ 闹钟设定成功！已设定在 {reminder.get('target_time_str', '')} 提醒你：【{reminder.get('content', '')}】。"

    elif act in ["cancel", "取消", "删除"]:
        kw = keyword or content or time_str
        cancelled = mgr.cancel_reminder(kw)
        if cancelled:
            return f"已成功为你取消 {len(cancelled)} 个相关提醒任务！"
        else:
            return f"没有找到与【{kw}】相关的待响铃提醒或闹钟。"

    elif act in ["list", "查询", "查看", "列表"]:
        active = mgr.list_active_reminders()
        if not active:
            return "当前没有任何待响铃的闹钟或倒计时哦～"
        items = []
        for r in active:
            items.append(f"- 【{r.get('type')}】{r.get('target_time_str')} : {r.get('content')}")
        return f"当前共有 {len(active)} 个待触发提醒：\n" + "\n".join(items)

    else:
        # 智能根据入参推断意图
        if seconds and float(seconds) > 0:
            reminder = mgr.set_timer(float(seconds), content)
            return f"⏰ 倒计时设定成功！将在 {reminder.get('target_time_str', '')} 提醒你：【{reminder.get('content', '')}】。"
        elif time_str:
            reminder = mgr.set_alarm(time_str, content, repeat=repeat)
            return f"⏰ 闹钟设定成功！已设定在 {reminder.get('target_time_str', '')} 提醒你：【{reminder.get('content', '')}】。"
        else:
            active = mgr.list_active_reminders()
            if not active:
                return "当前没有待触发的闹钟或倒计时。"
            return f"当前待触发提醒列表：\n" + "\n".join([f"- {r.get('display_str')}" for r in active])
