# -*- coding: utf-8 -*-
"""
统一系统提示词工厂 (prompt_builder.py)
集中收敛桌面伴侣机器人的实时时间、人设风格、自进化动作库感知、ReAct 铁律与行为规则，
彻底消除各入口与服务端的提示词重复硬编码与格式漂移。
"""

from datetime import datetime
from typing import Optional, Dict, Any, List

class PromptBuilder:
    """
    桌面具身伴侣机器人统一提示词构建器
    """

    @staticmethod
    def get_current_time_str() -> str:
        """获取包含星期的人性化当前时间"""
        now_dt = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return f"{now_dt.strftime('%Y-%m-%d %H:%M:%S')} ({weekdays[now_dt.weekday()]})"

    @classmethod
    def build_base_prompt(
        cls,
        max_reply_length: int = 150,
        skill_manager = None,
        custom_persona: Optional[str] = None
    ) -> str:
        """
        构建核心人设与上下文基底提示词
        """
        current_time_str = cls.get_current_time_str()

        actions_desc = "暂无自创动作"
        if skill_manager and hasattr(skill_manager, "list_evolved_actions"):
            try:
                known_actions = skill_manager.list_evolved_actions()
                if known_actions:
                    actions_desc = ", ".join([f"{k}({v.get('description', '')})" for k, v in known_actions.items()])
            except Exception:
                pass

        persona = custom_persona or "你是桌面的具身实体机器人搭子。根据主人的话，输出富有真实生命体征、连续起伏的肢体语言与回答。"

        return f"""{persona}
当前系统时间为：{current_time_str}。
你已永久掌握的自进化动作库包括：【{actions_desc}】。
回答风格要求：口语化、亲切真诚，富有生命体征感与陪伴感，回复字数控制在 {max_reply_length} 字以内。
"""

    @staticmethod
    def get_react_guidelines_prompt() -> str:
        """
        获取 ReAct 自主执行与诚实反馈铁律
        """
        return """
【ReAct 多步自主执行与诚实汇报铁律】:
1. 你是一个具备多步动作规划能力的桌面实体机器人。分析主人意图后，如果你需要通过工具获取信息或控制硬件，必须在 `thought` 中说明思考，并在 `skill` 字段中输出具体调用。
2. 严禁口嗨欺骗主人！绝不能在没调用工具时就假装说“让我去查/稍等我去扫描”；调用工具后系统会将真实的执行结果作为【Observation 观察反馈】立即追加给你进行下一步思考！
3. 失败就直接说出来，严禁敷衍掩饰兜底！若工具返回执行失败、报错、超时、离线或不支持，必须在 reply 中直接如实告诉主人具体原因与错误详情，绝不能使用“我在听呢”、“我们重试一下吧”等任何掩饰失败的无意义套话！
4. 复合指令单步直接完成：当主人一句话包含多个操作或属性参数时，直接在调用相应技能时传入完整复合参数，单次调用即可同时完成所有设定，无需无谓拆成多轮！
5. 一旦工具返回已成功完成目标，下一步必须立即将 `skill` 设为 null，并在 `reply` 中给出直接面向主人的清晰答复，严禁重复多次调用相同工具！
6. 严格输出标准 JSON 格式：
{
  "thought": "你的心路思考：分析当前进展，决定下一步动作或准备最终回复",
  "skill": { "name": "工具名称", "args": { "参数名": "参数值" } } 或 null,
  "reply": "此时你想对主人说的话 (若本轮已完成所有任务或发生失败，请给出最终结论答复)",
  "behaviors": [
    { "emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 700 }
  ]
}
"""
