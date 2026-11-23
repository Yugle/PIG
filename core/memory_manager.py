import os
import json
import time
import re

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
        self.max_history_turns = max_history_turns

        # 短期滑动窗口内存缓冲区
        self.short_term_history = []

        # 长期记忆结构
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

    def load(self):
        """从本地磁盘加载长期记忆"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.long_term_data.update(saved)
            except Exception as e:
                print(f"⚠️ [Memory] 读取 memory.json 失败: {e}")
        self.long_term_data.setdefault("bot_name", "PIG")
        self.long_term_data.setdefault("user_name", "主人")

    def save(self):
        """持久化保存长期记忆到本地磁盘"""
        try:
            self.long_term_data["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.long_term_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [Memory] 保存 memory.json 失败: {e}")

    def clear(self):
        """清空短期与长期记忆"""
        self.short_term_history = []
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

    def get_messages_for_llm(self, user_text: str, base_system_prompt: str) -> list:
        """组装完整的 [System + 短期历史 + 当前用户输入] 消息链"""
        augmented_sys = self.build_prompt_with_memory(base_system_prompt, user_text=user_text)
        messages = [{"role": "system", "content": augmented_sys}]

        # 附带短期滑动窗口对话
        for turn in self.short_term_history:
            messages.append(turn)

        # 加入当前用户提问
        messages.append({"role": "user", "content": user_text})
        return messages

    def record_turn(self, user_text: str, bot_reply_text: str):
        """记录本轮对话，更新亲密度并持久化"""
        # 1. 记入短期上下文
        self.short_term_history.append({"role": "user", "content": user_text})
        self.short_term_history.append({"role": "assistant", "content": bot_reply_text})

        # 滑动窗口截断 (每轮 2 条消息: user + assistant)
        max_messages = self.max_history_turns * 2
        if len(self.short_term_history) > max_messages:
            self.short_term_history = self.short_term_history[-max_messages:]

        # 2. 更新长期画像与亲密度
        self._extract_facts(user_text)
        self.long_term_data["interaction_count"] += 1

        # 每互动 10 次升一级亲密度 (上限 Lv.10)
        new_intimacy = min(10, 1 + self.long_term_data["interaction_count"] // 10)
        self.long_term_data["intimacy_level"] = new_intimacy

        # 3. 持久化到磁盘
        self.save()
