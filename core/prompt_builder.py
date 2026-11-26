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
        """获取包含星期的人性化当前时间"""
        now_dt = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return f"{now_dt.strftime('%Y-%m-%d %H:%M:%S')} ({weekdays[now_dt.weekday()]})"

    @classmethod
    def build_base_prompt(
        cls,
        max_reply_length: int = 200,
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

        persona = custom_persona or "你是桌面的 PIG 机器人搭子。你的名字叫 PIG。说话风格干脆利落、幽默灵动、像懂事且机灵的朋友。"

        return f"""{persona}
当前系统时间为：{current_time_str}。
你已掌握的自创动作库：【{actions_desc}】。

【场景自适应表达与字数准则 (核心铁律)】:
你必须根据用户的意图类型，智能自适应切换回复长度与叙事深度，严禁死板一刀切：

1. 操作与指令型场景 (极简克制，直奔主题):
   - 典型场景：改名、定闹钟/设提醒、控制设备(IoT)、动作表演、查时间/天气/行情、硬件状态确认、简单打招呼等。
   - 表达准则：说话极简、干净利落！严格用 1~2 句话直接回答或确认（推荐 15~35 字的最佳口语听觉长度），严禁任何多余的客套、过度铺垫或说教！

2. 内容生成与娱乐叙事型场景 (生动展开，自适应字数，严禁生硬截短):
   - 典型场景：讲笑话、幽默段子、讲故事、科普原理解析、深度长问答、情感倾诉与陪伴等。
   - 表达准则：彻底解除字数枷锁，字数自适应展开（通常 80~{max_reply_length} 字），确保内容完整精彩：
     * 讲笑话时：必须有完整的场景铺垫、情绪反转和爆笑包袱，生动风趣，绝不能为了省字数搞成一两句干瘪尴尬的废话！
     * 讲故事时：必须情节起承转合，生动传神，娓娓道来；
     * 科普解答时：通俗幽默、深入浅出，把原理脉络解释清晰。

3. 思考极简：thought 字段必须极其精炼（1句话以内快速直奔核心决策），切勿在 thought 中冗长发散导致主人等待。
"""

    @staticmethod
    def infer_emotion_for_text(text: str, seq: int = 0) -> str:
        """
        根据单句语义与句序自动匹配生动微表情（避免说话时永远僵硬在单一 HAPPY）
        """
        t = str(text or "")
        tl = t.lower()
        if any(k in tl for k in ["哈哈", "笑死", "搞笑", "幽默", "段子", "嘿嘿", "嘻嘻", "🤣", "😆", "😄", "笑话"]):
            return "LAUGH"
        if any(k in tl for k in ["爱你", "喜欢", "抱抱", "晚安", "想你", "心疼", "贴心", "么么", "❤️", "🥰", "😘", "甜蜜"]):
            return "LOVE"
        if any(k in tl for k in ["害羞", "不好意思", "脸红", "讨厌", "哎呀", "撒娇", "人家"]):
            return "SHY"
        if any(k in tl for k in ["太棒", "搞定", "完成", "成功", "厉害", "牛", "冲呀", "出发", "🎉", "✨", "恭喜", "耶"]):
            return "EXCITED"
        if any(k in tl for k in ["包在我身上", "当然", "小菜一碟", "拿捏", "轻松", "自信", "酷", "😎"]):
            return "PROUD"
        if any(k in tl for k in ["竟然", "居然", "天哪", "哇", "真的吗", "震撼", "没想到", "omg", "吓"]):
            return "SURPRISED"
        if any(k in tl for k in ["难过", "委屈", "抱歉", "对不起", "辛苦", "心累", "生病", "疼", "呜", "可惜", "未能"]):
            return "SAD"
        if any(k in tl for k in ["警告", "不行", "生气", "愤怒", "危险", "过分", "哼"]):
            return "ANGRY"
        if any(k in tl for k in ["难道", "为什么", "怎么", "好奇", "是不是", "猜猜", "？", "?"]):
            return "CURIOUS" if (seq % 2 == 0) else "CONFUSED"
        if any(k in tl for k in ["悄悄", "秘密", "提示", "建议", "其实", "😉"]):
            return "WINK"
        if any(k in tl for k in ["惬意", "舒服", "晒太阳", "慢慢", "安静", "治愈", "温柔", "放松"]):
            return "COZY"
        if any(k in tl for k in ["困", "睡觉", "早点休息", "打盹", "晚安啦"]):
            return "SLEEPY"
        if any(k in tl for k in ["晕", "头疼", "迷糊", "转圈"]):
            return "DIZZY"

        # 常规叙述句：按句序在灵动表情间自然轮换，告别面瘫
        palette = ["CURIOUS", "HAPPY", "WINK", "PROUD", "NORMAL", "COZY"]
        return palette[max(0, seq) % len(palette)]

    @classmethod
    def infer_behaviors_for_reply(cls, reply_text: str) -> List[Dict[str, Any]]:
        """当大模型未给出丰富关键帧时，按回复句意自动拆解 2~3 段表情与动作节拍"""
        import re
        raw = str(reply_text or "").strip()
        if not raw:
            return [{"emotion": "NORMAL", "action": "NOD", "pan": 90, "tilt": 88, "delay": 700}]
        parts = [p.strip() for p in re.split(r"[。！？!?；;\n]+", raw) if p.strip()]
        if not parts:
            parts = [raw]
        actions = ["NOD", "TILT_LEFT", "TILT_RIGHT", "NOD"]
        keyframes = []
        for idx, part in enumerate(parts[:4]):
            emo = cls.infer_emotion_for_text(part, idx + 1)
            pan = 90 if idx % 3 == 0 else (78 if idx % 2 == 1 else 102)
            tilt = 82 if idx % 2 == 0 else 92
            keyframes.append({
                "emotion": emo,
                "action": actions[idx % len(actions)],
                "pan": pan,
                "tilt": tilt,
                "delay": 850
            })
        return keyframes

    @staticmethod
    def get_react_guidelines_prompt() -> str:
        """
        获取 ReAct 自主执行与单轮极速响应铁律
        """
        return """
【ReAct 多步自主执行与单轮极速响应铁律】:
1. 极速口头响应与垫话 (边做边说，彻底消除延迟)：
   - 执行类动作技能（如 register_master_face, set_bot_name, set_user_name, set_alarm, set_timer, evolve_action, play_action, pomodoro, 开关/调节类的 xiaomi_iot 等）：
     必须在第 1 轮的 `reply` 字段中【直接给出面向主人的最终口语确认】（如：“好嘞胖虎！从现在起我就叫 PIG 啦～” 或 “明天早上8点的起床闹钟已经定好啦！”）。系统会在执行技能后直接以该回复交付主人，实现单轮极速闭环，无需第 2 轮！
   - 信息查询类技能（如 查询设备列表的 xiaomi_iot, weather, crypto_price, news_briefing, bilibili_follower 等）：
     必须在第 1 轮的 `reply` 字段中【直接给出一句极简口头垫话】（如：“好嘞，我来给你查一下！”、“稍等我看一下米家设备～”），系统会立刻朗读垫话，同时在后台查询数据并在第 2 轮给出真实结果汇报，消除等待感！【严禁在第 1 轮凭空编造虚假查询结果】！
2. 名字区分铁律 (严禁混淆)：
   - 主人给你改名/起名（如“你叫 PIG”、“以后叫你小搭”、“给你起名叫阿强”）：必须调用 `set_bot_name(name="...")`！这是修改你自己的名字！
   - 主人修改对主人的称呼（如“我叫张三”、“以后叫我阿珍”、“叫我胖虎”）：必须调用 `set_user_name(name="...")`！这是修改主人的称呼！
3. 真实性铁律 (严禁无中生有、编造数据)：
   - 所有智能设备、天气、行情、闹钟等外部数据查询，必须完全基于系统工具返回的真实 Observation 内容向主人汇报！
   - 【严禁凭空臆造虚假的设备名单】（例如禁止凭空编造电脑、插座、门铃等不存在的设备）！
   - 若工具报错或未查询到，在第 2 轮直接如实说明“未能成功查询到设备”，严禁自作主张编造虚假数据！
4. 复合指令单步直接完成：当主人一句话包含多个操作或属性参数时，直接在调用相应技能时传入完整复合参数，单次调用即可同时完成所有设定，无需无谓拆成多轮！
5. 严禁开空头支票与未结悬念 (单轮真实闭环)：
   - 严禁在没有调用实际技能的情况下，口头敷衍说“我这就去查”、“我查查后台记录/日志”、“稍后告诉你”、“等我处理好回复你”等虚假承诺！
   - 因为当前对话为单轮即时回复闭环，一旦回复后本次对话就已结束，不会再有第二条消息发出；口头托词会导致主人误以为你正在后台排查并一直在原地苦等！
   - 若主人对之前的消息或系统行为产生疑问（如问“刚才为啥发过来了”、“怎么突然推送了”），必须在当轮直接坦诚说明原因（例如：“哥，刚才那条是我后台的定时成长日记/测试自检误触了推送，吓到你了吧～”），当场闭环讲清楚，绝不留悬念！
6. 严格输出标准 JSON 格式 (必须按以下字段顺序输出，reply 紧随 thought 后以便流式即刻发声)：
   `behaviors` 须根据每句话的情绪起伏输出 2~3 个不同表情与动作关键帧（严禁每次都只写 HAPPY），可用表情包括：
   NORMAL, HAPPY, LAUGH, LOVE, EXCITED, WINK, SHY, PROUD, CURIOUS, CONFUSED, THINKING, SURPRISED, COZY, SLEEPY, DIZZY, SAD, ANGRY。
{
  "thought": "极简决策思路 (10字以内快速直奔核心决策)",
  "reply": "面向主人的口头话语 (操作类给出口语确认，查询类给出口头垫话，笑话/故事/科普等内容类自适应展开生动叙述)",
  "skill": { "name": "工具名称", "args": { "参数名": "参数值" } } 或 null,
  "behaviors": [
    { "emotion": "CURIOUS", "action": "NOD", "pan": 84, "tilt": 84, "delay": 650 },
    { "emotion": "EXCITED", "action": "NOD", "pan": 96, "tilt": 90, "delay": 750 }
  ]
}
"""
