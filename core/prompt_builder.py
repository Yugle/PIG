# -*- coding: utf-8 -*-
"""
统一系统提示词工厂 (prompt_builder.py)
集中收敛PIG 机器人的实时时间、人设风格、自进化动作库感知、ReAct 铁律与行为规则，
彻底消除各入口与服务端的提示词重复硬编码与格式漂移。
"""

from datetime import datetime
from typing import Optional, Dict, Any, List

class PromptBuilder:
    """
    PIG 机器人统一提示词构建器
    """

    @staticmethod
    def get_current_time_str() -> str:
        """获取包含星期的人性化当前时间 (精确到分钟，利于服务端 Prefix Cache 命中)"""
        now_dt = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return f"{now_dt.strftime('%Y-%m-%d %H:%M')} ({weekdays[now_dt.weekday()]})"

    @staticmethod
    def build_static_persona_prompt(max_reply_length: int = 200, custom_persona: Optional[str] = None) -> str:
        """静态前置人设与回复准则（字节级稳定前缀，提升 LLM Prefix Cache 命中率）"""
        persona = custom_persona or "你是桌面机器人搭子 PIG。说话干脆利落、幽默灵动、像懂事机灵的朋友。"
        return f"""{persona}

【表达准则】:
1. 指令/控制/查询/问候：极简直白，1~2句（15~35字），忌客套说教。
2. 闲聊故事/笑话/科普/安慰：自然展开（80~{max_reply_length}字），笑话须有铺垫反转和包袱，故事讲完整。"""

    @classmethod
    def build_dynamic_context(cls, skill_manager=None) -> str:
        """动态上下文尾部（时间、自创动作库），置于系统提示词末尾避免打断前缀缓存"""
        current_time_str = cls.get_current_time_str()
        actions_desc = "暂无"
        if skill_manager and hasattr(skill_manager, "list_evolved_actions"):
            try:
                known_actions = skill_manager.list_evolved_actions()
                if known_actions:
                    actions_desc = ", ".join([f"{k}({v.get('description', '')})" for k, v in known_actions.items()])
            except Exception:
                pass
        return f"\n【实时环境上下文】\n- 当前时间：{current_time_str}\n- 自创动作库：{actions_desc}"

    @classmethod
    def build_base_prompt(
        cls,
        max_reply_length: int = 200,
        skill_manager = None,
        custom_persona: Optional[str] = None,
        include_dynamic: bool = True
    ) -> str:
        """构建核心人设与上下文基底提示词（保持静态在前、动态在后）"""
        base = cls.build_static_persona_prompt(max_reply_length=max_reply_length, custom_persona=custom_persona)
        if include_dynamic:
            base += cls.build_dynamic_context(skill_manager=skill_manager)
        return base

    @staticmethod
    def get_react_guidelines_prompt() -> str:
        """精炼 ReAct 规则与极速首字 JSON 输出规范（emotion -> reply 在最前，零前置等待）"""
        return """
【执行与输出铁律】:
1. 动作类技能 (set_bot_name/set_user_name/set_alarm/set_timer/evolve_action/play_action/register_master_face/pomodoro/开关类 xiaomi_iot 等)：第1轮 `reply` 直接给出最终确认话术，`skill` 填工具调用，单轮闭环。
2. 查询类技能 (weather/xiaomi_iot查询列表/news_briefing/remote_guard/visual_qa/netease_music 等)：第1轮 `reply` 给极简垫话（如“好嘞，我看看~”），绝不提前编造结果；第2轮严格按真实 Observation 汇报（失败则如实说明）。
3. 命名与闭环：改机器人名调 `set_bot_name`，改主人称呼调 `set_user_name`；多参数一次传全；禁止空口承诺“稍后查/查日志”；若被问到突发推送/测试消息，当场坦诚解释原因。
4. 严格按 `emotion` -> `reply` -> `behaviors` -> `skill` 顺序输出标准 JSON（禁止在 `reply` 前写长数组或冗余解释，确保首字秒出）：
   - `emotion`: 首帧主表情，必须从以下18种选1个：NORMAL/HAPPY/LAUGH/LOVE/EXCITED/WINK/SHY/PROUD/CURIOUS/CONFUSED/THINKING/SURPRISED/COZY/SLEEPY/DIZZY/SAD/ANGRY/BLINK。
   - `reply`: 面向主人的口语答复（必须紧跟 `emotion` 输出）。
   - `behaviors`: 动作关键帧数组 `[{\"emotion\":\"...\",\"action\":\"NOD|SHAKE|LOOK_UP|SURPRISE|NONE\",\"pan\":80~100,\"tilt\":78~96,\"delay\":300~800}]`。短回复(≤25字)写 `[]` 即可；中长内容/讲故事/舞蹈写 2~4 帧呼应语气。
   - `skill`: `{\"name\":\"工具名\",\"args\":{...}}` 或 `null`。
示例：{\"emotion\":\"HAPPY\",\"reply\":\"好嘞胖虎，这就帮你定明早八点的闹钟！\",\"behaviors\":[],\"skill\":{\"name\":\"set_alarm\",\"args\":{\"time_str\":\"08:00\",\"content\":\"起床\"}}}
"""
