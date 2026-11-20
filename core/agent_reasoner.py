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
from typing import Dict, Any, List, Optional, Generator

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
        base_url: str = "https://api.xiaomimimo.com/v1",
        model: str = "mimo-v2.6-flash",
        max_steps: int = 4,
        enable_thinking: bool = False
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_steps = max_steps
        self.enable_thinking = enable_thinking

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
        from core.prompt_builder import PromptBuilder
        tools_prompt = skill_manager.get_skills_tools_prompt()
        full_sys_prompt = augmented_sys + "\n" + tools_prompt + "\n" + PromptBuilder.get_react_guidelines_prompt()

        # 构造包含短期上下文的基础消息链
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
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
            if "mimo" in self.model.lower() or "xiaomimimo" in self.base_url:
                if not self.enable_thinking:
                    payload["thinking"] = {"type": "disabled"}

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code != 200:
                    logger.error(f"LLM 调用失败 [{resp.status_code}]: {resp.text}")
                    final_reply = f"认知大模型调用失败 (状态码 {resp.status_code})：{resp.text[:120]}"
                    break

                resp_json = resp.json()
                choice = resp_json.get("choices", [{}])[0]
                msg_obj = choice.get("message", {})
                raw_content = msg_obj.get("content", "")
                reasoning = msg_obj.get("reasoning", "")

                parsed = parse_llm_json(raw_content, reasoning)
                if not parsed:
                    logger.warning(f"无法解析 LLM JSON 输出: {raw_content}")
                    final_reply = raw_content or "大模型输出格式解析失败，未能完成操作。"
                    break

                thought = parsed.get("thought", "")
                skill_call = parsed.get("skill") or parsed.get("skill_call")
                reply_candidate = parsed.get("reply", "")
                behaviors_candidate = parsed.get("behaviors", [])

                if reply_candidate:
                    final_reply = reply_candidate
                if behaviors_candidate:
                    final_behaviors = behaviors_candidate


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

                    # 若为动作自进化或动作表演，动态演出新舞步
                    if tool_name in ["evolve_action", "create_action", "learn_action", "execute_action", "play_action", "do_action", "run_action", "perform_action"]:
                        act_name = tool_args.get("name") or tool_args.get("action_name") or tool_args.get("action") or ""
                        act_obj = skill_manager.get_evolved_action(act_name)
                        if act_obj and act_obj.get("keyframes"):
                            final_behaviors = act_obj["keyframes"]

                    # 若已达到最大步数，无法继续循环推理，直接输出当前真实执行反馈，绝不掩饰
                    if step >= self.max_steps:
                        logger.warning(f"⚠️ ReAct 循环达到最大步数 ({self.max_steps})")
                        is_fail = any(k in str(observation) for k in ["失败", "错误", "未找到", "超时", "异常", "未能", "离线"])
                        if is_fail:
                            final_reply = f"操作未完全达成：{observation}"
                        else:
                            final_reply = str(observation)
                        break

                    # 将本轮的思考和工具执行结果喂回上下文，让大模型继续下一轮推理！
                    assistant_turn_content = json.dumps({
                        "thought": thought,
                        "skill": skill_call,
                        "reply": reply_candidate
                    }, ensure_ascii=False)

                    messages.append({"role": "assistant", "content": assistant_turn_content})
                    observation_prompt = (
                        f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                        f"请根据上述真实工具反馈继续思考：\n"
                        f"- 若工具执行失败或报错，必须在 reply 中直接向主人坦白失败原因，严禁敷衍！\n"
                        f"- 若任务已完成，请立即将 skill 设为 null，并在 reply 中向主人给出最终清晰口语答复。"
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
                final_reply = f"执行过程中发生异常错误：{e}，未能完成操作。"
                break

        # 诚实汇报铁律（失败就直接说出来，严禁任何形式的敷衍掩饰与兜底）：
        # 1. 检查工具真实执行结果是否包含失败报错
        if last_observation and isinstance(last_observation, str):
            is_tool_failure = any(k in last_observation for k in ["失败", "错误", "未找到", "超时", "异常", "未能", "离线", "失效"])
            if is_tool_failure:
                # 若工具明确失败，但大模型未报告失败，直接以真实工具报错为准纠正，绝不掩饰欺骗主人
                if not final_reply or not any(k in str(final_reply) for k in ["失败", "错误", "未能", "未找到", "离线", "异常", "失效"]):
                    final_reply = f"操作未能成功：{last_observation}"

        # 2. 如果最终答复仍然是推脱/未完成的敷衍口嗨词或空值，直接降级展示工具真实反馈
        if not final_reply or any(k in str(final_reply) for k in ["稍等", "正在帮您", "让我先", "正在查询", "正在扫描", "马上为您", "这就去", "我来查", "我在听呢", "有什么问题尽管跟我说"]):
            if last_observation:
                final_reply = str(last_observation)
            else:
                final_reply = "抱歉主人，未能成功执行该操作，请重试。"

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

    def run_react_loop_stream(
        self,
        user_text: str,
        base_sys_prompt: str,
        memory_manager,
        skill_manager,
        evolution_engine=None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        高响应流式 ReAct 自主执行循环：
        当命中最终口语答复时，实时边吐字边断句发射，驱动 TTS 抢先发声；
        当命中工具调用时，无缝衔接执行与下一步观察。
        """
        from core.streaming_pipeline import StreamingJsonReplyExtractor

        if evolution_engine:
            evolution_engine.record_interaction()
            augmented_sys = evolution_engine.augment_prompt_with_evolution(base_sys_prompt)
        else:
            augmented_sys = base_sys_prompt

        from core.prompt_builder import PromptBuilder
        tools_prompt = skill_manager.get_skills_tools_prompt()
        full_sys_prompt = augmented_sys + "\n" + tools_prompt + "\n" + PromptBuilder.get_react_guidelines_prompt()
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
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
                "stream": True,
                "response_format": {"type": "json_object"}
            }
            if "mimo" in self.model.lower() or "xiaomimimo" in self.base_url:
                if not self.enable_thinking:
                    payload["thinking"] = {"type": "disabled"}

            try:
                resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
                if resp.status_code != 200:
                    logger.error(f"LLM 流式调用失败 [{resp.status_code}]: {resp.text}")
                    err_msg = f"大模型连接异常 (状态码 {resp.status_code})，未能完成操作。"
                    final_reply = err_msg
                    yield {"type": "sentence", "text": err_msg}
                    break

                extractor = StreamingJsonReplyExtractor()
                raw_tokens = []

                for line in resp.iter_lines():
                    if line:
                        line_str = line.decode("utf-8")
                        if line_str.startswith("data: ") and line_str != "data: [DONE]":
                            try:
                                chunk_json = json.loads(line_str[6:])
                                choices = chunk_json.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    rc = delta.get("reasoning_content", "")
                                    if rc:
                                        yield {"type": "thought", "text": rc}
                                    delta_content = delta.get("content", "")
                                    if delta_content:
                                        raw_tokens.append(delta_content)
                                        sentences = extractor.feed(delta_content)
                                        for s in sentences:
                                            yield {"type": "sentence", "text": s}
                                        th = extractor.pop_thought_delta()
                                        if th:
                                            yield {"type": "thought", "text": th}
                            except Exception:
                                pass

                for rem in extractor.flush():
                    yield {"type": "sentence", "text": rem}

                raw_content = "".join(raw_tokens)
                parsed = parse_llm_json(raw_content, "")
                if not parsed:
                    final_reply = raw_content or "大模型输出格式解析失败，未能完成操作。"
                    break

                thought = parsed.get("thought", "")
                skill_call = parsed.get("skill") or parsed.get("skill_call")
                reply_candidate = parsed.get("reply", "") or extractor.full_reply_text
                behaviors_candidate = parsed.get("behaviors", [])

                if reply_candidate:
                    final_reply = reply_candidate
                if behaviors_candidate:
                    final_behaviors = behaviors_candidate

                has_valid_tool = (
                    skill_call and
                    isinstance(skill_call, dict) and
                    skill_call.get("name") and
                    str(skill_call.get("name")).lower() not in ["none", "null", "false"]
                )

                if has_valid_tool:
                    tool_name = skill_call.get("name")
                    tool_args = skill_call.get("args") or skill_call.get("params") or {}
                    yield {"type": "tool_start", "name": tool_name, "args": tool_args}

                    observation = skill_manager.dispatch(skill_call)
                    last_observation = observation
                    yield {"type": "observation", "name": tool_name, "result": str(observation)}

                    # 若为动作自进化或动作表演，动态实时演出新学到的动作序列
                    if tool_name in ["evolve_action", "create_action", "learn_action", "execute_action", "play_action", "do_action", "run_action", "perform_action"]:
                        act_name = tool_args.get("name") or tool_args.get("action_name") or tool_args.get("action") or ""
                        act_obj = skill_manager.get_evolved_action(act_name)
                        if act_obj and act_obj.get("keyframes"):
                            final_behaviors = act_obj["keyframes"]

                    steps_log.append({
                        "step": step,
                        "thought": thought,
                        "action": tool_name,
                        "args": tool_args,
                        "observation": str(observation)
                    })

                    if step >= self.max_steps:
                        final_reply = str(observation)
                        yield {"type": "sentence", "text": final_reply}
                        break

                    assistant_turn_content = json.dumps({
                        "thought": thought,
                        "skill": skill_call,
                        "reply": reply_candidate
                    }, ensure_ascii=False)
                    messages.append({"role": "assistant", "content": assistant_turn_content})
                    observation_prompt = (
                        f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                        f"请根据上述真实工具反馈继续思考：\n"
                        f"- 若工具执行失败或报错，必须在 reply 中直接向主人坦白失败原因，严禁敷衍！\n"
                        f"- 若任务已完成，请立即将 skill 设为 null，并在 reply 中向主人给出最终清晰口语答复。"
                    )
                    messages.append({"role": "user", "content": observation_prompt})

                else:
                    steps_log.append({
                        "step": step,
                        "thought": thought or "所有任务与信息已就绪，向主人生成最终答复。",
                        "action": None,
                        "observation": None
                    })
                    break

            except Exception as e:
                logger.error(f"ReAct 流式循环执行异常: {e}")
                err_msg = f"执行过程中发生异常错误：{e}，未能完成操作。"
                final_reply = err_msg
                yield {"type": "sentence", "text": err_msg}
                break

        if last_observation and isinstance(last_observation, str):
            is_tool_failure = any(k in last_observation for k in ["失败", "错误", "未找到", "超时", "异常", "未能", "离线", "失效"])
            if is_tool_failure:
                if not final_reply or not any(k in str(final_reply) for k in ["失败", "错误", "未能", "未找到", "离线", "异常", "失效"]):
                    final_reply = f"操作未能成功：{last_observation}"

        if not final_reply or any(k in str(final_reply) for k in ["稍等", "正在帮您", "让我先", "正在查询", "正在扫描", "马上为您", "这就去", "我来查", "我在听呢", "有什么问题尽管跟我说"]):
            if last_observation:
                final_reply = str(last_observation)
            else:
                final_reply = "抱歉主人，未能成功执行该操作，请重试。"

        known_actions = skill_manager.list_evolved_actions()
        for k, v in known_actions.items():
            short_desc = v.get("description", "").split("：")[0].replace("舞", "")
            if k in user_text.upper() or (short_desc and short_desc in user_text):
                if v.get("keyframes"):
                    final_behaviors = v["keyframes"]
                    break

        memory_manager.record_turn(user_text, final_reply)

        yield {
            "type": "done",
            "reply": final_reply,
            "behaviors": final_behaviors,
            "steps": steps_log,
            "final_skill_res": last_observation
        }

