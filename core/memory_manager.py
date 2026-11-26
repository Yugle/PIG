import os
import json
import time
import re
import threading
from datetime import datetime, timedelta

class MemoryManager:
    """
    轻量级两级记忆中枢 (专为树莓派低内存设计)。
    1. 短期记忆 (Short-Term): 滑动窗口维持最近 N 轮上下文对话，保障多轮追问连贯性。
    2. 长期记忆 (Long-Term): 本地持久化 memory.json，记住主人昵称、职业、喜好与情感亲密度。
    """

    def __init__(self, memory_file=None, max_history_turns=6):
        if memory_file is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            memory_file = os.path.join(project_root, "data", "memory.json")
        self.memory_file = memory_file
        self.daily_log_file = os.path.join(os.path.dirname(self.memory_file), "daily_dialogue.json")
        self.max_history_turns = max_history_turns
        self._io_lock = threading.Lock()

        # 多通道会话管理 (Multi-Session: "device" 为树莓派实体, "feishu_xxx" 为飞书私聊/群聊)
        self.sessions = {
            "device": {"history": [], "last_active": time.time()}
        }
        self.session_timeout = 7200  # 2小时无交互的 Session 自动重置短期对话窗口
        self.daily_turns = []        # 按天滚动落盘的近两日对话流水 (专供海马体反思，重启/超2小时不丢失)

        # 长期记忆结构 (跨通道全局共享灵魂)
        self.long_term_data = {
            "bot_name": "PIG",
            "user_name": "主人",
            "facts": [],
            "interaction_count": 0,
            "intimacy_level": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        self.load()

    @property
    def short_term_history(self):
        """兼容老代码单 Session 调用"""
        return self.get_session_history("device")

    @short_term_history.setter
    def short_term_history(self, val):
        self.set_session_history("device", val)

    def get_session_history(self, session_id: str = "device") -> list:
        """获取指定通道的短期上下文 (超时自动清理)"""
        sid = session_id or "device"
        now = time.time()
        if sid not in self.sessions:
            self.sessions[sid] = {"history": [], "last_active": now}
        else:
            if now - self.sessions[sid].get("last_active", now) > self.session_timeout:
                self.sessions[sid] = {"history": [], "last_active": now}
        self.sessions[sid]["last_active"] = now
        return self.sessions[sid]["history"]

    def set_session_history(self, session_id: str, history: list):
        """设置指定通道的短期上下文"""
        sid = session_id or "device"
        self.sessions[sid] = {"history": history, "last_active": time.time()}

    def clear_session(self, session_id: str = "device"):
        """清空指定通道的短期上下文"""
        sid = session_id or "device"
        if sid in self.sessions:
            self.sessions[sid] = {"history": [], "last_active": time.time()}

    def list_active_sessions(self) -> dict:
        """列出当前所有活跃通道及其轮数"""
        return {
            sid: {
                "turns": len(data["history"]) // 2,
                "last_active": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["last_active"]))
            }
            for sid, data in self.sessions.items()
        }

    def _prune_daily_turns(self):
        """只保留今天和昨天最多 60 轮有效对话，过期自动滚动淘汰"""
        now_dt = datetime.now()
        keep_dates = {
            now_dt.strftime("%Y-%m-%d"),
            (now_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        self.daily_turns = [t for t in self.daily_turns if isinstance(t, dict) and t.get("date") in keep_dates][-60:]

    def _load_daily_log(self):
        if os.path.exists(self.daily_log_file):
            try:
                with open(self.daily_log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.daily_turns = data
                elif isinstance(data, dict):
                    self.daily_turns = data.get("turns", [])
            except Exception as e:
                print(f"⚠️ [Memory] 读取 daily_dialogue.json 失败: {e}")
                self.daily_turns = []
        self._prune_daily_turns()

    def _save_daily_log(self):
        self._prune_daily_turns()
        os.makedirs(os.path.dirname(os.path.abspath(self.daily_log_file)), exist_ok=True)
        tmp = self.daily_log_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"turns": self.daily_turns}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.daily_log_file)
        except Exception as e:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            print(f"⚠️ [Memory] 保存 daily_dialogue.json 失败: {e}")

    def get_dialogue_for_reflection(self, max_turns: int = 30) -> list:
        """
        为海马体反思获取按天滚动的持久化对话记录：
        优先返回今天的全部对话（跨网页/真机语音/飞书），若跨过零点今天尚无新对话则读取昨天的；
        绝不因 2 小时短期上下文超时而丢失。
        """
        with self._io_lock:
            self._prune_daily_turns()
            if self.daily_turns:
                today_str = datetime.now().strftime("%Y-%m-%d")
                today_turns = [t for t in self.daily_turns if t.get("date") == today_str]
                chosen = today_turns if today_turns else self.daily_turns
                return list(chosen[-max_turns:])

            # 兜底兼容：仅内存尚存的旧对话 (不触发 2 小时超时清理)
            raw_hist = self.sessions.get("device", {}).get("history", [])
            return list(raw_hist[-(max_turns * 2):])

    def load(self):
        """从本地磁盘加载长期记忆与按天滚动对话流水"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.long_term_data.update(saved)
            except Exception as e:
                print(f"⚠️ [Memory] 读取 memory.json 失败: {e}")
        self.long_term_data.setdefault("bot_name", "PIG")
        self.long_term_data.setdefault("user_name", "主人")
        self._load_daily_log()

    def save(self):
        """持久化保存长期记忆到本地磁盘"""
        try:
            self.long_term_data["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = self.memory_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.long_term_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.memory_file)
        except Exception as e:
            print(f"⚠️ [Memory] 保存 memory.json 失败: {e}")

    def clear(self):
        """清空所有通道短期会话、按天对话日志与长期记忆"""
        with self._io_lock:
            self.sessions = {
                "device": {"history": [], "last_active": time.time()}
            }
            self.daily_turns = []
            self._save_daily_log()
        self.long_term_data = {
            "bot_name": "PIG",
            "user_name": "主人",
            "facts": [],
            "interaction_count": 0,
            "intimacy_level": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.save()
        print("🧹 [Memory] 记忆中枢已完全重置与清空。")

    def set_bot_name(self, name: str) -> str:
        """显式修改机器人自身的名字/昵称"""
        name = (name or "").strip()
        if name and name not in ["你", "我", "什么", "谁", "无", "null", "none"]:
            old_name = self.long_term_data.get("bot_name", "PIG")
            self.long_term_data["bot_name"] = name
            self.save()
            return f"太好啦！我的名字已正式更改为【{name}】！以后我就叫【{name}】啦～"
        return "无效的名字，未能修改。"

    def set_user_name(self, name: str) -> str:
        """显式修改主人称呼"""
        name = (name or "").strip()
        if name and name not in ["你", "我", "什么", "谁", "主人", "null", "none"]:
            old_name = self.long_term_data.get("user_name", "主人")
            self.long_term_data["user_name"] = name
            self.save()
            return f"主人称呼已由【{old_name}】正式更改为【{name}】！"
        return "无效的称呼，未能修改。"

    def _extract_facts(self, text: str):
        """自动轻量提取主人的核心画像事实与机器人自身命名 (无需额外消耗 LLM Token)"""
        # 0. 匹配机器人自身的名字/昵称 (如“你叫 PIG”、“以后叫你阿强”、“给你起名叫小搭”)
        bot_name_match = re.search(r"(?:你叫|你的名字是|以后你的名字叫|以后叫你|给你起名叫|给你取名叫)\s*([a-zA-Z0-9\u4e00-\u9fa5]{2,10})", text)
        if bot_name_match:
            cand = bot_name_match.group(1).strip()
            if cand not in ["什么", "谁", "主人", "可以", "怎么", "无效"]:
                self.long_term_data["bot_name"] = cand
                self.save()

        # 1. 匹配主人名字/昵称更名与识别 (排除“我是不是”、“我是想”、“我是说”等日常疑问/副词情境)
        name_match = re.search(r"(?:我叫|我是(?!不是|想|觉得|说|在|要|看)|叫我|名字是|名字改成|改成|改名叫|换成|称呼我为|以后叫我)\s*([a-zA-Z\u4e00-\u9fa5]{2,10})", text)
        if name_match:
            cand = name_match.group(1).strip()
            if cand not in ["PIG", "机器人", "搭子", "什么", "主人", "可以", "怎么"]:
                self.long_term_data["user_name"] = cand
                self.save()

        # 2. 匹配喜好
        like_match = re.search(r"(?:我喜欢|我最喜欢|平时喜欢|我爱|爱好是)\s*([^\s,，。!！]{2,15})", text)
        if like_match:
            fact = f"喜欢{like_match.group(1).strip()}"
            if fact not in self.long_term_data["facts"]:
                self.long_term_data["facts"].append(fact)

        # 3. 匹配厌恶
        dislike_match = re.search(r"(?:我讨厌|我不喜欢|我最讨厌|我受不了)\s*([^\s,，。!！]{2,15})", text)
        if dislike_match:
            fact = f"讨厌{dislike_match.group(1).strip()}"
            if fact not in self.long_term_data["facts"]:
                self.long_term_data["facts"].append(fact)

        # 4. 匹配职业/身份
        job_match = re.search(r"(?:我是做|我是一名|我工作是|我是个)\s*([^\s,，。!！]{2,12})", text)
        if job_match:
            fact = f"职业是{job_match.group(1).strip()}"
            if fact not in self.long_term_data["facts"]:
                self.long_term_data["facts"].append(fact)

        # 限制长期事实库最多持久化 30 条核心画像
        if len(self.long_term_data["facts"]) > 30:
            self.long_term_data["facts"] = self.long_term_data["facts"][-30:]

    def get_relevant_facts(self, query: str = "", max_facts: int = 5) -> list:
        """
        基于当前用户表达，轻量打分筛选 Top-K 最相关的长期事实与偏好，
        避免所有事实盲目塞入 Prompt 导致注意力涣散与 Token 浪费。
        """
        all_facts = self.long_term_data.get("facts", [])
        if not all_facts:
            return []
        if len(all_facts) <= max_facts or not query:
            return all_facts[-max_facts:]

        scored = []
        q_lower = query.lower()
        q_chars = set(q_lower)

        for idx, fact in enumerate(all_facts):
            f_clean = fact.lower()
            score = 0.0

            # 核心喜好实体词命中（权重最高）
            for prefix in ["喜欢", "讨厌", "爱", "职业是", "是个", "做"]:
                core_val = f_clean.replace(prefix, "")
                if core_val and core_val in q_lower:
                    score += 6.0

            # 字符交集打分
            overlap = len(set(f_clean) & q_chars)
            score += overlap

            # 越靠近当下的记忆附带微小时效分加权
            score += (idx / len(all_facts)) * 0.5
            scored.append((score, idx, fact))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item[2] for item in scored[:max_facts]]

    def build_prompt_with_memory(self, base_system_prompt: str, user_text: str = "") -> str:
        """将检索后的最相关长期记忆档案注入到系统提示词中"""
        bot_name = self.long_term_data.get("bot_name", "PIG")
        user_name = self.long_term_data.get("user_name", "主人")
        intimacy = self.long_term_data.get("intimacy_level", 1)
        count = self.long_term_data.get("interaction_count", 0)
        facts = self.get_relevant_facts(user_text, max_facts=5)

        facts_str = "、".join(facts) if facts else "暂无特殊喜好记录"

        memory_section = f"""

【PIG 身份与主人档案】
- 你的名字：【{bot_name}】 (主人问你叫什么名字或称呼你时，必须以此名字自称，如“我是{bot_name}”)
- 主人称呼：【{user_name}】 (日常对话请自然亲切地称呼主人)
- 亲密指数：Lv.{intimacy} (总互动已达 {count} 次)
- 当前关联偏好与事实：{facts_str}
- 记忆原则：请自然融入这些记忆，如果主人之前聊过某件事，在回答时要表现出连贯的记忆感和默契！
"""
        return base_system_prompt + memory_section

    def get_messages_for_llm(self, user_text: str, base_system_prompt: str, session_id: str = "device") -> list:
        """组装完整的 [System + 指定通道短期历史 + 当前用户输入] 消息链"""
        augmented_sys = self.build_prompt_with_memory(base_system_prompt, user_text=user_text)
        messages = [{"role": "system", "content": augmented_sys}]

        # 附带当前 Session 短期滑动窗口对话
        history = self.get_session_history(session_id)
        for turn in history:
            messages.append(turn)

        # 加入当前用户提问
        messages.append({"role": "user", "content": user_text})
        return messages

    def record_turn(self, user_text: str, bot_reply_text: str, session_id: str = "device"):
        """记录指定通道的本轮对话，更新按天滚动流水、亲密度与事实画像并持久化"""
        # 1. 记入当前 Session 的短期上下文
        sid = session_id or "device"
        history = self.get_session_history(sid)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": bot_reply_text})

        # 滑动窗口截断 (每轮 2 条消息: user + assistant)
        max_messages = self.max_history_turns * 2
        if len(history) > max_messages:
            self.sessions[sid]["history"] = history[-max_messages:]

        # 2. 追加至按天滚动持久化对话流水 (供夜间海马体反思，重启/刷新/跨2小时不丢失)
        now_dt = datetime.now()
        with self._io_lock:
            self.daily_turns.append({
                "date": now_dt.strftime("%Y-%m-%d"),
                "ts": now_dt.strftime("%H:%M:%S"),
                "channel": sid,
                "user": str(user_text or "").strip(),
                "assistant": str(bot_reply_text or "").strip(),
            })
            self._save_daily_log()

        # 3. 更新长期画像与亲密度 (跨通道全局共享灵魂累积)
        self._extract_facts(user_text)
        self.long_term_data["interaction_count"] += 1

        # 每互动 10 次升一级亲密度 (上限 Lv.10)
        new_intimacy = min(10, 1 + self.long_term_data["interaction_count"] // 10)
        self.long_term_data["intimacy_level"] = new_intimacy

        # 4. 持久化到磁盘
        self.save()
