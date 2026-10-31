# -*- coding: utf-8 -*-
"""
具身自主智能体认知与多轮推理中枢 (Embodied ReAct Agent Reasoner)
提供完整的 Reason + Act (思考 -> 动作 -> 观察 -> 下一步推理) 多轮认知闭环，
彻底告别单轮大模型的假口嗨与虚假幻觉，实现跨步骤、跨工具的自主任务编排与执行。
"""
import json
import logging
import re
import requests
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("AgentReasoner")


def parse_llm_json(raw_text: str, reasoning_text: str = "") -> Optional[Dict[str, Any]]:
    """鲁棒的 LLM 结构化输出解析器 (支持 Markdown 包裹与正则提取)"""
    candidate = raw_text.strip() if raw_text else reasoning_text.strip()
    if not candidate:
        return None

    # 1. 尝试直接加载
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 2. 提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", candidate)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 提取首尾大括号
    brace_match = re.search(r"(\{[\s\S]*\})", candidate)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    return None


class AgentReasoner:
    """
    ReAct 认知循环引擎：
    驱动机器人进行多步规划与工具连续调度，支持边观察边决策。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://token.sensenova.cn/v1",
        model: str = "sensenova-6.8-flash-lite",
        max_steps: int = 4
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_steps = max_steps

    def run_react_loop(
        self,
        user_text: str,
        base_sys_prompt: str,
        memory_manager,
        skill_manager,
        evolution_engine=None
    ) -> Dict[str, Any]:
        """
        核心 ReAct 自主执行循环
        :return: {
            "reply": str,                     # 最终对主人说的话
            "behaviors": List[dict],          # 机械头部与眼球动作时序
            "steps": List[dict],              # 每一步的思考链日志
            "final_skill_res": Any            # 最后一步工具的真实返回值
        }
        """
        # 记录交互作息时间戳
        if evolution_engine:
            evolution_engine.record_interaction()
            augmented_sys = evolution_engine.augment_prompt_with_evolution(base_sys_prompt)
        else:
            augmented_sys = base_sys_prompt

        # 注入当前已激活的动态技能列表
        tools_prompt = skill_manager.get_skills_tools_prompt()
        full_sys_prompt = augmented_sys + "\n" + tools_prompt + """

【ReAct 多步自主执行铁律】:
1. 你是一个具备多步动作规划能力的桌面实体机器人。分析主人意图后，如果你需要通过工具获取信息或控制硬件，必须在 `thought` 中说明思考，并在 `skill` 字段中输出具体调用。
2. 严禁口嗨欺骗主人！绝不能在没调用工具时就假装说“让我去查/稍等我去扫描”；调用工具后系统会将真实的执行结果作为【Observation 观察反馈】立即追加给你进行下一步思考！
3. 当你收集到足够的事实或所有动作都已执行完毕后，将 `skill` 设为 null，并在 `reply` 中给出直接面向主人的温情口语回答。
4. 严格输出标准 JSON 格式：
{
  "thought": "你的心路思考：分析当前进展，决定下一步动作或准备最终回复",
  "skill": { "name": "工具名称", "args": { "参数名": "参数值" } } 或 null,
  "reply": "此时你想对主人说的话 (若本轮已完成所有任务，请给出最终结论答复)",
  "behaviors": [
    { "emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 85, "delay": 700 }
  ]
}
"""

        # 构造包含短期上下文的基础消息链
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt)

        steps_log: List[Dict[str, Any]] = []
        final_reply = "我在听呢，有什么问题尽管跟我说吧！"
        final_behaviors = [{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]
        last_observation = None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        step = 0
        while step < self.max_steps:
            step += 1
            payload = {
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"}
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code != 200:
                    logger.error(f"LLM 调用失败 [{resp.status_code}]: {resp.text}")
                    final_reply = f"网络通信有些延迟 (状态码 {resp.status_code})，我们重试一下吧。"
                    break

                resp_json = resp.json()
                choice = resp_json.get("choices", [{}])[0]
                msg_obj = choice.get("message", {})
                raw_content = msg_obj.get("content", "")
                reasoning = msg_obj.get("reasoning", "")

                parsed = parse_llm_json(raw_content, reasoning)
                if not parsed:
                    logger.warning(f"无法解析 LLM JSON 输出: {raw_content}")
                    final_reply = raw_content or "我脑筋转了一下，请再说一次吧！"
                    break

                thought = parsed.get("thought", "")
                skill_call = parsed.get("skill") or parsed.get("skill_call")
                reply_candidate = parsed.get("reply", "")
                behaviors_candidate = parsed.get("behaviors", [])

                if reply_candidate:
                    final_reply = reply_candidate
                if behaviors_candidate:
                    final_behaviors = behaviors_candidate

                # 智能意图兜底：如果第一步用户明显要求查小米设备或扫码绑定，但模型忘记输出 skill
                if step == 1 and not (skill_call and isinstance(skill_call, dict) and skill_call.get("name")):
                    if any(w in user_text for w in ["扫码", "绑定米家", "绑定小米", "登录小米", "米家扫码"]):
                        skill_call = {"name": "xiaomi_iot", "args": {"device": "all", "action": "扫码登录"}}
                        thought = "自动意图路由：检测到用户扫码绑定米家意图，主动调度 xiaomi_iot 扫码授权工具。"
                    elif any(w in user_text for w in ["小米", "米家"]) and any(w in user_text for w in ["查", "看", "扫", "什么", "哪些", "设备", "完"]):
                        skill_call = {"name": "xiaomi_iot", "args": {"device": "all", "action": "查询设备列表"}}
                        thought = "自动意图路由：检测到用户查询米家设备意图，主动调度 xiaomi_iot 扫描工具。"

                # 检查是否需要调用工具
                has_valid_tool = (
                    skill_call and
                    isinstance(skill_call, dict) and
                    skill_call.get("name") and
                    str(skill_call.get("name")).lower() not in ["none", "null", "false"]
                )

                if has_valid_tool:
                    tool_name = skill_call.get("name")
                    tool_args = skill_call.get("args") or skill_call.get("params") or {}

                    logger.info(f"🤖 [ReAct Step {step}] Thought: {thought} | Action: {tool_name}({tool_args})")

                    # 真正执行工具
                    observation = skill_manager.dispatch(skill_call)
                    last_observation = observation
                    logger.info(f"🔍 [ReAct Step {step}] Observation: {observation}")

                    steps_log.append({
                        "step": step,
                        "thought": thought,
                        "action": tool_name,
                        "args": tool_args,
                        "observation": str(observation)
                    })

                    # 若为动作自进化，动态演出新舞步
                    if tool_name == "evolve_action":
                        act_name = tool_args.get("name", "")
                        act_obj = skill_manager.get_evolved_action(act_name)
                        if act_obj and act_obj.get("keyframes"):
                            final_behaviors = act_obj["keyframes"]

                    # 将本轮的思考和工具执行结果喂回上下文，让大模型继续下一轮推理！
                    assistant_turn_content = json.dumps({
                        "thought": thought,
                        "skill": skill_call,
                        "reply": reply_candidate
                    }, ensure_ascii=False)

                    messages.append({"role": "assistant", "content": assistant_turn_content})
                    observation_prompt = (
                        f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                        f"请根据上述真实工具反馈，继续思考下一步行动；若任务已完成，请将 skill 设为 null，并在 reply 中向主人给出最终口语答复。"
                    )
                    messages.append({"role": "user", "content": observation_prompt})

                else:
                    # 没有工具调用或工具已执行完毕，大模型已产生最终面向主人的回答
                    logger.info(f"✅ [ReAct 闭环完成 (第 {step} 步)] Final Reply: {final_reply}")
                    steps_log.append({
                        "step": step,
                        "thought": thought or "所有任务与信息已就绪，向主人生成最终答复。",
                        "action": None,
                        "observation": None
                    })
                    break

            except Exception as e:
                logger.error(f"ReAct 循环执行异常: {e}")
                final_reply = "执行过程中遇到了一点小意外，我们再试一次吧。"
                break

        # 兜底检查：仅当大模型最终答复依然是推脱/未完成的敷衍口嗨词（如“稍等我去查/马上为您扫描”），
        # 且没有表达完成态词汇（“已/完成/好了/成功/二维码已生成/扫描结果”）时，才智能降级到工具真实观察结果
        if last_observation and isinstance(last_observation, str):
            is_deferral = any(k in final_reply for k in ["稍等", "正在帮您", "让我先", "正在查询", "正在扫描", "马上为您", "这就去", "我来查"])
            has_completion = any(k in final_reply for k in ["已", "完成", "好了", "成功", "二维码", "扫描完毕", "查到", "为你", "打开了", "关闭了"])
            if is_deferral and not has_completion:
                final_reply = last_observation

        # 检查是否匹配用户自创的已知舞蹈动作
        known_actions = skill_manager.list_evolved_actions()
        for k, v in known_actions.items():
            short_desc = v.get("description", "").split("：")[0].replace("舞", "")
            if k in user_text.upper() or (short_desc and short_desc in user_text):
                if v.get("keyframes"):
                    final_behaviors = v["keyframes"]
                    break

        # 写入持久化长短期记忆
        memory_manager.record_turn(user_text, final_reply)

        return {
            "reply": final_reply,
            "behaviors": final_behaviors,
            "steps": steps_log,
            "final_skill_res": last_observation
        }
