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

        persona = custom_persona or "你是桌面的具身实体机器人搭子。说话风格干脆利落、幽默灵动、像懂事且机灵的朋友。"

        return f"""{persona}
当前系统时间为：{current_time_str}。
你已掌握的自创动作库：【{actions_desc}】。

【对话精简原则 (严格遵守)】:
- 说话极简、直奔主题：禁止任何啰嗦客套、长篇铺垫、过度重复的语气词或说教！
- 1~2 句话内干净利落回答完毕，严禁车轱辘话和多余的感慨或反问。
- 单次回复字数严格控制在 {max_reply_length} 字以内（推荐 15~35 字的最佳口语听觉长度）。
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
3. 失败直接一句话说明：若工具返回失败或报错，用一句话如实说明原因即可，严禁连串自责道歉、多余安慰或长篇自我解释！
4. 复合指令单步直接完成：当主人一句话包含多个操作或属性参数时，直接在调用相应技能时传入完整复合参数，单次调用即可同时完成所有设定，无需无谓拆成多轮！
5. 完成任务立即交差：工具执行成功后，立即将 `skill` 设为 null，用一两句清爽干脆的话告知主人结果，禁止画蛇添足发表长篇感言。
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
