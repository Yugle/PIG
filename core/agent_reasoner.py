# -*- coding: utf-8 -*-
"""
PIG 自主智能体认知与多轮推理中枢 (Embodied ReAct Agent Reasoner)
提供完整的 Reason + Act (思考 -> 动作 -> 观察 -> 下一步推理) 多轮认知闭环，
彻底告别单轮大模型的假口嗨与虚假幻觉，实现跨步骤、跨工具的自主任务编排与执行。
"""
import json
import logging
import re
import time
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


# 常见的执行类动作技能集合 (高确定性、状态变更类，支持单轮极速闭环直出)
ACTION_SKILLS = {
    "system_config", "config", "sys_config", "shezhi",
    "set_bot_name", "rename_bot", "change_bot_name",
    "set_user_name", "change_name", "update_name",
    "reminder", "set_alarm", "set_timer", "set_reminder", "add_reminder", "delete_reminder",
    "evolve_action", "create_action", "learn_action",
    "play_action", "execute_action", "do_action", "run_action", "perform_action",
    "pomodoro", "xiaomi_iot", "med_tracker", "dice_roll", "netease_music",
    "install_skill", "uninstall_skill", "create_skill",
    "register_master_face", "register_face", "record_face", "bind_master_face", "face_id"
}

def is_action_skill(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    """判断是否为可单轮直接闭环的执行类动作技能（排除查询类意图）"""
    t_name = str(tool_name).lower()
    # 小米生态：如果是查设备列表，则是查询类技能，必须走第2轮获取真实数据并汇总
    if any(w in t_name for w in ["xiaomi", "mijia", "miot", "xiaoai"]):
        act = str(tool_args.get("action", "")).lower()
        dev = str(tool_args.get("device", "")).lower()
        val = str(tool_args.get("value", "")).lower()
        combined = f"{act} {dev} {val}"
        if any(w in combined for w in ["查", "看", "列表", "有哪些", "什么设备", "清单", "query", "list", "all", "所有"]):
            return False
        return True

    if any(w in t_name for w in ["netease", "music"]):
        act = str(tool_args.get("action", "")).lower()
        q = str(tool_args.get("query", "")).lower()
        combined = f"{act} {q}"
        if any(w in combined for w in ["playlists", "list", "status", "info", "有哪些", "什么歌", "查歌单", "我的歌单", "当前"]):
            return False
        return True

    return tool_name in ACTION_SKILLS

def is_skill_failure(obs: Any) -> bool:
    """判断技能执行结果是否包含明确失败语义"""
    obs_str = str(obs).lower()
    return any(k in obs_str for k in ["失败", "错误", "未能", "异常", "未找到", "无效", "error", "fail", "invalid", "超时", "离线", "未识别的技能"])


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
        evolution_engine=None,
        session_id: str = "device",
        silent: bool = False,
        on_step_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        核心 ReAct 自主执行循环
        :param session_id: 会话通道 ("device" 树莓派实体, "feishu_xxx" 飞书私聊/群聊, "web" 网页端)
        :param silent: 是否静默执行 (远程会话禁用实体舵机动作)
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

        # 构造包含短期上下文的基础消息链 (支持多 Session 隔离)
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt, session_id=session_id)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
        final_behaviors = [] if silent else [{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]
        last_observation = None
        first_turn_ack = ""

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

                    if on_step_callback:
                        try:
                            on_step_callback({
                                "stage": "tool_calling",
                                "step": step,
                                "thought": thought,
                                "tool_name": tool_name,
                                "tool_args": tool_args
                            })
                        except Exception as cb_err:
                            logger.warning(f"on_step_callback 异常: {cb_err}")

                    # 真正执行工具
                    observation = skill_manager.dispatch(skill_call)
                    last_observation = observation
                    logger.info(f"🔍 [ReAct Step {step}] Observation: {observation}")

                    if on_step_callback:
                        try:
                            on_step_callback({
                                "stage": "tool_done",
                                "step": step,
                                "tool_name": tool_name,
                                "observation": observation
                            })
                        except Exception:
                            pass

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

                    # ⚡ 优化 1：执行类动作技能单轮极速闭环 (Speculative Execution)
                    if is_action_skill(tool_name, tool_args) and not is_skill_failure(observation):
                        final_reply = reply_candidate or str(observation)
                        logger.info(f"⚡ [ReAct FastPath] 执行类技能【{tool_name}】单轮极速闭环完成，跳过第2轮LLM。")
                        break

                    # 若已达到最大步数，无法继续循环推理，直接输出当前真实执行反馈，绝不掩饰
                    if step >= self.max_steps:
                        logger.warning(f"⚠️ ReAct 循环达到最大步数 ({self.max_steps})")
                        is_fail = is_skill_failure(observation)
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
                    if is_skill_failure(observation):
                        observation_prompt = (
                            f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                            f"⚠️ 工具执行遇到了异常或未查询到数据！\n"
                            f"请根据此真实反馈如实向主人说明情况，【严禁】凭空编造不存在的数据、设备或内容！\n"
                            f"- 必须将 skill 设为 null。\n"
                            f"- 在 reply 中直接向主人说明实际情况。"
                        )
                    else:
                        observation_prompt = (
                            f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                            f"真实数据现已就绪，请给出面向主人的最终口语答复：\n"
                            f"- 必须将 skill 设为 null。\n"
                            f"- 必须严格依据上述真实反馈中的内容，在 reply 中向主人清晰汇报结果，绝不编造虚假信息！"
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

        # 静默模式 (如飞书等远程通信通道) 不触发本地实体舵机与眼球动作
        if silent:
            final_behaviors = []
        else:
            # 检查是否匹配用户自创的已知舞蹈动作
            known_actions = skill_manager.list_evolved_actions()
            for k, v in known_actions.items():
                short_desc = v.get("description", "").split("：")[0].replace("舞", "")
                if k in user_text.upper() or (short_desc and short_desc in user_text):
                    if v.get("keyframes"):
                        final_behaviors = v["keyframes"]
                        break

        # 写入持久化长短期记忆 (支持多通道 Session 隔离)
        memory_manager.record_turn(user_text, final_reply, session_id=session_id)

        has_visual_qa = any(s.get("action") == "visual_qa" for s in steps_log) or (last_observation and "【小猪视觉观察】" in str(last_observation))
        ret = {
            "reply": final_reply,
            "behaviors": final_behaviors,
            "steps": steps_log,
            "final_skill_res": last_observation
        }
        if has_visual_qa:
            ret["visual_snapshot"] = f"/api/vision/snapshot?t={int(time.time() * 1000)}"
        return ret

    def run_react_loop_stream(
        self,
        user_text: str,
        base_sys_prompt: str,
        memory_manager,
        skill_manager,
        evolution_engine=None,
        session_id: str = "device",
        silent: bool = False
    ) -> Generator[Dict[str, Any], None, None]:
        """
        高响应流式 ReAct 自主执行循环：
        当命中最终口语答复时，实时边吐字边断句发射，驱动 TTS 抢先发声；
        当命中工具调用时，无缝衔接执行与下一步观察。
        :param session_id: 会话通道 ("device" 树莓派实体, "feishu_xxx" 飞书私聊/群聊, "web" 网页端)
        :param silent: 是否静默模式 (不驱动物理动作)
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
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt, session_id=session_id)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
        final_behaviors = [] if silent else [{"emotion": "HAPPY", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]
        last_observation = None
        first_turn_ack = ""

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

                    # ⚡ 优化 1：执行类动作技能单轮极速闭环直出 (Speculative Execution)
                    if is_action_skill(tool_name, tool_args) and not is_skill_failure(observation):
                        if not reply_candidate:
                            reply_candidate = str(observation)
                            yield {"type": "sentence", "text": reply_candidate}
                        final_reply = reply_candidate
                        logger.info(f"⚡ [ReAct FastPath] 执行类技能【{tool_name}】单轮极速闭环，跳过第2轮LLM。")
                        break

                    if step >= self.max_steps:
                        final_reply = reply_candidate or str(observation)
                        break

                    # ⚡ 优化 2：查询类技能首轮口头垫话无缝衔接第二轮
                    if step == 1 and reply_candidate:
                        first_turn_ack = reply_candidate

                    assistant_turn_content = json.dumps({
                        "thought": thought,
                        "reply": reply_candidate,
                        "skill": skill_call
                    }, ensure_ascii=False)
                    messages.append({"role": "assistant", "content": assistant_turn_content})

                    if is_skill_failure(observation):
                        observation_prompt = (
                            f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                            f"⚠️ 工具执行遇到了异常或未查询到数据！\n"
                            f"请根据此真实反馈如实向主人说明情况，【绝对严禁】凭空编造不存在的数据、设备或内容！\n"
                            f"- 必须将 skill 设为 null。\n"
                            f"- 在 reply 中直接向主人说明实际情况。"
                        )
                    else:
                        ack_hint = f"你刚才已经向主人给出了先行口头垫话：“{reply_candidate}”。\n" if reply_candidate else ""
                        observation_prompt = (
                            f"【系统工具执行反馈 (Observation)】:\n{observation}\n\n"
                            f"{ack_hint}"
                            f"真实数据现已就绪，请给出面向主人的最终口语答复：\n"
                            f"- 必须将 skill 设为 null。\n"
                            f"- 必须严格依据上述真实反馈中的内容，在 reply 中向主人清晰汇报结果，绝不编造虚假信息；自然衔接前文，无需重复刚才的垫话。"
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

        if first_turn_ack and first_turn_ack not in str(final_reply):
            final_reply = f"{first_turn_ack} {final_reply}".strip()

        if last_observation and isinstance(last_observation, str):
            is_tool_failure = is_skill_failure(last_observation)
            if is_tool_failure:
                if not final_reply or not any(k in str(final_reply) for k in ["失败", "错误", "未能", "未找到", "离线", "异常", "失效"]):
                    final_reply = f"操作未能成功：{last_observation}"

        if not final_reply or (first_turn_ack and final_reply == first_turn_ack):
            if last_observation:
                final_reply = f"{final_reply} {last_observation}".strip()
            else:
                final_reply = "抱歉主人，未能成功执行该操作，请重试。"

        if silent:
            final_behaviors = []
        else:
            known_actions = skill_manager.list_evolved_actions()
            for k, v in known_actions.items():
                short_desc = v.get("description", "").split("：")[0].replace("舞", "")
                if k in user_text.upper() or (short_desc and short_desc in user_text):
                    if v.get("keyframes"):
                        final_behaviors = v["keyframes"]
                        break

        memory_manager.record_turn(user_text, final_reply, session_id=session_id)

        has_visual_qa = any(s.get("action") == "visual_qa" for s in steps_log) or (last_observation and "【小猪视觉观察】" in str(last_observation))
        done_payload = {
            "type": "done",
            "reply": final_reply,
            "behaviors": final_behaviors,
            "steps": steps_log,
            "final_skill_res": last_observation
        }
        if has_visual_qa:
            done_payload["visual_snapshot"] = f"/api/vision/snapshot?t={int(time.time() * 1000)}"

        yield done_payload

