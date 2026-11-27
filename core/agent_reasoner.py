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
import threading
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
# 核心系统内置动作指令 (非 skills/ 插件) —— 不需要第2轮大模型总结直接闭环
CORE_ACTION_TOOLS = {
    "set_timer", "set_alarm", "cancel_reminder",
    "evolve_action", "execute_action", "play_action", "do_action", "run_action", "perform_action",
    "set_bot_name", "rename_bot", "change_bot_name",
    "set_user_name", "change_name", "update_name",
    "install_skill", "uninstall_skill", "create_skill",
    "register_master_face", "register_face", "record_face", "bind_master_face", "face_id",
}


def is_action_skill(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    """判断是否为可单轮直接闭环的执行类动作技能（插件行为由各 skill.json 的 fast_path / query_keywords 驱动）"""
    if tool_name in CORE_ACTION_TOOLS:
        return True

    from core.skill_manager import get_skill_manager
    sm = get_skill_manager()
    meta = sm.resolve_plugin_meta(tool_name) if sm else {}
    if not meta or not meta.get("fast_path"):
        return False

    q_kws = [str(w).lower() for w in (meta.get("query_keywords") or [])]
    if q_kws and isinstance(tool_args, dict):
        combined = " ".join(str(v).lower() for v in tool_args.values())
        if any(w in combined for w in q_kws):
            return False
    return True

def is_skill_failure(obs: Any) -> bool:
    """判断技能执行结果是否包含明确失败/无结果语义"""
    obs_str = str(obs).lower()
    return any(k in obs_str for k in [
        "失败", "错误", "未能", "异常", "未找到", "没有找到", "没找到", "暂时没有", "暂时没", "暂未", "没拉到",
        "未登录", "无效", "error", "fail", "invalid", "超时", "离线", "未识别的技能"
    ])


# 本地零大模型直通表：完全确定且高频的短控制指令，直接调技能并秒回（0ms LLM 等待）
_FAST_LOCAL_RULES = (
    ({"停止播放", "别放了", "关掉音乐", "把歌关了", "别唱了", "关闭音乐", "停止放歌"}, "netease_music", {"action": "stop"}, "好嘞，已停止播放！", "NORMAL"),
    ({"暂停", "暂停播放", "先停一下", "暂停音乐"}, "netease_music", {"action": "pause"}, "好嘞，已暂停播放。", "NORMAL"),
    ({"继续播放", "继续放歌", "恢复播放", "接着放"}, "netease_music", {"action": "resume"}, "好嘞，继续给你播放！", "HAPPY"),
    ({"下一首", "换一首", "切歌", "下首歌", "放下一首"}, "netease_music", {"action": "next"}, "好嘞，切下一首！", "HAPPY"),
    ({"上一首", "上首歌", "放上一首"}, "netease_music", {"action": "prev"}, "好嘞，切回上一首！", "HAPPY"),
    ({"音量调大", "音量大点", "声音大点", "音量调高", "声音调大", "大声点"}, "system_config", {"action": "volume", "value": "+"}, "", "HAPPY"),
    ({"音量调小", "音量小点", "声音小点", "音量调低", "声音调小", "小声点"}, "system_config", {"action": "volume", "value": "-"}, "", "NORMAL"),
    ({"静音", "打开静音"}, "system_config", {"action": "volume", "value": "mute"}, "", "NORMAL"),
    ({"取消静音", "关闭静音", "恢复声音"}, "system_config", {"action": "volume", "value": "60"}, "", "HAPPY"),
    ({"开启人脸追踪", "打开人脸追踪", "开启人脸跟随", "打开人脸跟随"}, "system_config", {"action": "face_tracking", "value": "true"}, "好嘞，已开启人脸追踪！", "HAPPY"),
    ({"关闭人脸追踪", "关掉人脸追踪", "关闭人脸跟随", "关掉人脸跟随"}, "system_config", {"action": "face_tracking", "value": "false"}, "好的，已关闭人脸追踪。", "NORMAL"),
)


def try_fast_local_intent(
    user_text: str,
    skill_manager,
    memory_manager=None,
    evolution_engine=None,
    session_id: str = "device",
    silent: bool = False,
) -> Optional[Dict[str, Any]]:
    """命中高频确定性短指令时本地直接执行并返回，彻底跳过云端 LLM 耗时"""
    clean = re.sub(r"[\s，。！？,.!?~～呀啊哦呢啦吧哈嘛]+", "", str(user_text or "")).lower()
    if not clean or len(clean) > 12 or not skill_manager:
        return None

    installed = getattr(skill_manager, "installed_plugins", {}) or {}
    for triggers, skill_name, skill_args, fixed_reply, emo in _FAST_LOCAL_RULES:
        if clean not in triggers or skill_name not in installed:
            continue

        tool_call = {"name": skill_name, "args": dict(skill_args)}
        observation = skill_manager.dispatch(tool_call)
        if is_skill_failure(observation):
            return None

        if skill_name == "system_config" and skill_args.get("action") == "face_tracking":
            try:
                from core.hardware_agent import get_hardware_agent
                ha = get_hardware_agent()
                if ha:
                    ha.set_face_tracking(skill_args.get("value") == "true")
            except Exception:
                pass

        obs_msg = observation.get("message", "") if isinstance(observation, dict) else str(observation)
        reply = fixed_reply or obs_msg or "好嘞，已经帮你办妥啦！"
        behaviors = [] if silent else [{"emotion": emo, "action": "NOD", "pan": 90, "tilt": 88, "delay": 500}]
        if evolution_engine:
            try:
                evolution_engine.record_interaction()
            except Exception:
                pass
        if memory_manager:
            try:
                memory_manager.record_turn(user_text, reply, session_id=session_id)
            except Exception:
                pass
        logger.info(f"⚡ [FastLocal] 本地直通命中 '{clean}' -> {skill_name}({skill_args}) [0ms LLM]")
        return {
            "reply": reply,
            "behaviors": behaviors,
            "steps": [{"step": 1, "thought": "本地高频控制指令直通", "action": skill_name, "args": dict(skill_args), "observation": str(observation)}],
            "final_skill_res": observation,
        }
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
        self._thread_local = threading.local()

    def _get_session(self) -> requests.Session:
        """每线程复用 Keep-Alive Session，减少冷 TCP/TLS 握手开销"""
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = requests.Session()
            self._thread_local.session = sess
        return sess

    @staticmethod
    def _assemble_system_prompt(base_sys_prompt: str, skill_manager, evolution_engine=None) -> str:
        """组装静态前缀优先的 System Prompt（静态人设+技能+规则前置，动态时间/进化/记忆后置以命中服务端 Prefix Cache）"""
        from core.prompt_builder import PromptBuilder
        tools_prompt = skill_manager.get_skills_tools_prompt()
        react_rules = PromptBuilder.get_react_guidelines_prompt()

        marker = "\n【实时环境上下文】"
        if marker in base_sys_prompt:
            static_base, dynamic_tail = base_sys_prompt.split(marker, 1)
            dynamic_tail = marker + dynamic_tail
        else:
            static_base, dynamic_tail = base_sys_prompt, ""

        static_prefix = static_base + "\n" + tools_prompt + "\n" + react_rules
        assembled = static_prefix + dynamic_tail
        if evolution_engine:
            evolution_engine.record_interaction()
            assembled = evolution_engine.augment_prompt_with_evolution(assembled)
        return assembled

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
        fast_res = try_fast_local_intent(
            user_text, skill_manager, memory_manager=memory_manager,
            evolution_engine=evolution_engine, session_id=session_id, silent=silent,
        )
        if fast_res is not None:
            return fast_res

        full_sys_prompt = self._assemble_system_prompt(base_sys_prompt, skill_manager, evolution_engine)

        # 构造包含短期上下文的基础消息链 (支持多 Session 隔离)
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt, session_id=session_id)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
        final_behaviors = [] if silent else [{"emotion": "NORMAL", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]
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
                resp = self._get_session().post(url, headers=headers, json=payload, timeout=60)
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
                if not behaviors_candidate and parsed.get("emotion"):
                    behaviors_candidate = [{"emotion": str(parsed["emotion"]).upper(), "action": "NOD", "pan": 90, "tilt": 88, "delay": 500}]

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

        fast_res = try_fast_local_intent(
            user_text, skill_manager, memory_manager=memory_manager,
            evolution_engine=evolution_engine, session_id=session_id, silent=silent,
        )
        if fast_res is not None:
            s0 = fast_res["steps"][0]
            yield {"type": "tool_start", "name": s0["action"], "args": s0["args"]}
            yield {"type": "observation", "name": s0["action"], "result": s0["observation"]}
            if fast_res["behaviors"]:
                yield {"type": "behaviors", "behaviors": fast_res["behaviors"]}
            yield {"type": "text_delta", "text": fast_res["reply"]}
            yield {"type": "sentence", "text": fast_res["reply"]}
            yield {
                "type": "done",
                "reply": fast_res["reply"],
                "behaviors": fast_res["behaviors"],
                "steps": fast_res["steps"],
                "final_skill_res": fast_res["final_skill_res"],
            }
            return

        full_sys_prompt = self._assemble_system_prompt(base_sys_prompt, skill_manager, evolution_engine)
        messages = memory_manager.get_messages_for_llm(user_text, full_sys_prompt, session_id=session_id)

        steps_log: List[Dict[str, Any]] = []
        final_reply = None
        final_behaviors = [] if silent else [{"emotion": "NORMAL", "action": "NOD", "pan": 90, "tilt": 90, "delay": 700}]
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
                resp = self._get_session().post(url, headers=headers, json=payload, stream=True, timeout=60)
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
                                        bh = extractor.pop_behaviors()
                                        if bh and not silent:
                                            final_behaviors = bh
                                            yield {"type": "behaviors", "behaviors": bh}
                                        td = extractor.pop_reply_delta()
                                        if td:
                                            yield {"type": "text_delta", "text": td}
                                        for s in sentences:
                                            yield {"type": "sentence", "text": s}
                                        th = extractor.pop_thought_delta()
                                        if th:
                                            yield {"type": "thought", "text": th}
                            except Exception:
                                pass

                bh_tail = extractor.pop_behaviors()
                if bh_tail and not silent:
                    final_behaviors = bh_tail
                    yield {"type": "behaviors", "behaviors": bh_tail}
                td_tail = extractor.pop_reply_delta()
                if td_tail:
                    yield {"type": "text_delta", "text": td_tail}
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
                if not behaviors_candidate and parsed.get("emotion"):
                    behaviors_candidate = [{"emotion": str(parsed["emotion"]).upper(), "action": "NOD", "pan": 90, "tilt": 88, "delay": 500}]

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

