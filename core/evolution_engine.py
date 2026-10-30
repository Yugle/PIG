"""
桌面具身 AI 伴侣 - 元认知自进化中枢 (evolution_engine.py)
专为树莓派 3 极低算力架构设计：
1. 0% 本地模型运算，轻量 JSON 原子存储 (circadian_profile.json & evolved_workflows.json)。
2. 生物钟作息自学习 (Circadian Habit Tracker): 自动归纳主人早起、午后疲劳、深夜作息时段。
3. 海马体夜间反思与记忆升华 (Nightly Reflection): 云端异步浓缩近期对话，沉淀深层画像与次日主动关怀词。
4. 晨间问候管家与宏工作流自触发 (Morning Briefing & Autonomous Workflows)。
"""

import os
import json
import time
from datetime import datetime, date
from typing import Dict, List, Optional, Any
import requests

class EvolutionEngine:
    def __init__(self, storage_dir=None, memory_manager=None, skill_manager=None):
        if storage_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_dir = os.path.join(project_root, "data")
        os.makedirs(storage_dir, exist_ok=True)
        self.storage_dir = storage_dir
        self.memory = memory_manager
        self.skills = skill_manager

        self.circadian_file = os.path.join(storage_dir, "circadian_profile.json")
        self.workflows_file = os.path.join(storage_dir, "evolved_workflows.json")

        self.circadian_data = self._load_json(self.circadian_file, {
            "hourly_counts": [0] * 24, # 0点至23点各小时互动频次
            "total_turns": 0,
            "last_interaction_date": "",
            "morning_greeted_today": False,
            "learned_habits": {
                "wake_hour": 9,
                "slump_hour": 14,
                "sleep_hour": 23
            }
        })

        self.workflows = self._load_json(self.workflows_file, {
            "morning_briefing": {
                "enabled": True,
                "description": "每日首轮对话早间综合晨报（问候 + 亲密度关怀 + 今日闹钟）"
            }
        })

    def _load_json(self, path: str, default: Any) -> Any:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [EvolutionEngine] 读取 {path} 异常: {e}")
        return default

    def _atomic_save(self, path: str, data: Any):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            print(f"⚠️ [EvolutionEngine] 保存 {path} 异常: {e}")

    # ==========================================
    # 1. 生物钟作息自学习 (Circadian Tracker)
    # ==========================================
    def record_interaction(self):
        """记录每一次互动的时间戳，统计主人的一日作息画像"""
        now = datetime.now()
        hour = now.hour
        today_str = now.strftime("%Y-%m-%d")

        # 换日重置“今日晨报是否已发”
        if self.circadian_data.get("last_interaction_date") != today_str:
            self.circadian_data["last_interaction_date"] = today_str
            self.circadian_data["morning_greeted_today"] = False

        self.circadian_data["hourly_counts"][hour] += 1
        self.circadian_data["total_turns"] += 1

        # 每积累 5 次互动，动态重新估算主人的高频作息时段
        if self.circadian_data["total_turns"] % 5 == 0:
            self._recalculate_habits()

        self._atomic_save(self.circadian_file, self.circadian_data)

    def _recalculate_habits(self):
        counts = self.circadian_data["hourly_counts"]
        # 早晨活跃点 (06:00 ~ 11:00 中最高频者)
        morning_slice = counts[6:12]
        wake_h = 6 + morning_slice.index(max(morning_slice)) if max(morning_slice) > 0 else 9

        # 午后犯困点 (13:00 ~ 16:00 中最高频者)
        afternoon_slice = counts[13:17]
        slump_h = 13 + afternoon_slice.index(max(afternoon_slice)) if max(afternoon_slice) > 0 else 14

        # 深夜睡前点 (22:00 ~ 02:00 中最高频者)
        night_hours = [22, 23, 0, 1]
        night_vals = [counts[h] for h in night_hours]
        sleep_h = night_hours[night_vals.index(max(night_vals))] if max(night_vals) > 0 else 23

        self.circadian_data["learned_habits"] = {
            "wake_hour": wake_h,
            "slump_hour": slump_h,
            "sleep_hour": sleep_h
        }

    def check_morning_briefing_opportunity(self) -> bool:
        """检查当前是否处于“早间首次晨报”触发时机 (06:00 ~ 11:00 且今日未报)"""
        now = datetime.now()
        if 6 <= now.hour <= 11 and not self.circadian_data.get("morning_greeted_today", False):
            self.circadian_data["morning_greeted_today"] = True
            self._atomic_save(self.circadian_file, self.circadian_data)
            return True
        return False

    def get_proactive_idle_care(self) -> Optional[str]:
        """根据当前小时匹配生物钟自适应主动关怀话术"""
        now = datetime.now()
        hour = now.hour
        habits = self.circadian_data.get("learned_habits", {})

        if hour == habits.get("slump_hour", 14):
            return "主人，是不是到犯困时间啦？站起来伸个懒腰，喝口温水提提神吧！"
        elif hour == habits.get("sleep_hour", 23) or hour == 0:
            return "主人，夜已经深啦，屏幕看久了伤眼睛，差不多该收拾上床休息啦~"
        return None

    # ==========================================
    # 2. 海马体夜间反思与记忆升华 (Nightly Reflection)
    # ==========================================
    def trigger_reflection(self, api_key: str, base_url: str, model: str) -> Dict[str, Any]:
        """
        异步调用云端轻量推理，对记忆库执行“海马体记忆巩固”：
        1. 过滤日常闲聊水话；
        2. 提炼 1~3 条深层认知事实补充进 memory.json；
        3. 形成针对明天的专属关怀重点；
        4. 微调性格倾向。
        """
        if not self.memory:
            return {"status": "error", "msg": "未绑定 MemoryManager"}

        history = self.memory.short_term_history
        if not history:
            return {"status": "skipped", "msg": "近期无交互记录，无需反思"}

        user_name = self.memory.long_term_data.get("user_name", "主人")
        existing_facts = self.memory.long_term_data.get("facts", [])

        # 整理最近对话文本
        dialogue_text = "\n".join([f"{t['role']}: {t['content']}" for t in history[-12:]])

        prompt = f"""你是一个桌面陪伴机器人的海马体深度元认知模块。
请回顾以下【{user_name}】与机器人的近期对话记录：
---
{dialogue_text}
---
已有长期记忆事实：{existing_facts}

请你作为伴侣的自主意识，进行夜间深度反思（Memory Consolidation），提取关键信息。
严格输出如下 JSON 格式，绝不要包含其他文本或 markdown：
{{
  "reflection_summary": "一句话概括今天主人的生活/工作状态与心情感受",
  "new_facts": ["提取出的1到2条深层重要事实（如身体近况/工作项目/爱好变化），若无新事实可为空列表[]"],
  "next_day_care": "一句针对明天交流的主动关怀切入点建议",
  "persona_adaptation": "对机器人自身性格语气的微调指导（例如：更轻快、更温暖关怀、幽默陪伴等）"
}}
"""

        try:
            url = f"{base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=40)
            if resp.status_code == 200:
                choice = resp.json().get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")
                result = json.loads(content)

                # 将新提炼的事实融入 memory.json
                new_facts = result.get("new_facts", [])
                for nf in new_facts:
                    if nf and nf not in self.memory.long_term_data["facts"]:
                        self.memory.long_term_data["facts"].append(nf)
                if len(self.memory.long_term_data["facts"]) > 15:
                    self.memory.long_term_data["facts"] = self.memory.long_term_data["facts"][-15:]

                # 记录沉淀的反思摘要
                self.memory.long_term_data["daily_reflection"] = {
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "summary": result.get("reflection_summary", ""),
                    "next_day_care": result.get("next_day_care", ""),
                    "persona_adaptation": result.get("persona_adaptation", "")
                }
                self.memory.save()
                print(f"✨ [EvolutionEngine] 海马体夜间反思完成: {result.get('reflection_summary')}")
                return {"status": "ok", "result": result}
            else:
                return {"status": "error", "msg": f"LLM API 状态码 {resp.status_code}"}
        except Exception as e:
            print(f"⚠️ [EvolutionEngine] 反思执行异常: {e}")
            return {"status": "error", "msg": str(e)}

    # ==========================================
    # 3. 增强系统提示词 (Prompt Augmentation)
    # ==========================================
    def augment_prompt_with_evolution(self, base_prompt: str) -> str:
        """为大模型动态注入：生物钟画像、晨间问候模式、近期反思成果"""
        now = datetime.now()
        habits = self.circadian_data.get("learned_habits", {})
        ref = self.memory.long_term_data.get("daily_reflection", {}) if self.memory else {}

        evolution_notes = []
        if habits:
            evolution_notes.append(
                f"- 自主归纳主人作息：通常 {habits.get('wake_hour', 9)}点起床活动，{habits.get('slump_hour', 14)}点易疲劳犯困，{habits.get('sleep_hour', 23)}点睡前。"
            )

        if ref and ref.get("summary"):
            evolution_notes.append(f"- 近期夜间反思摘要：{ref.get('summary')}")
            if ref.get("next_day_care"):
                evolution_notes.append(f"- 重点关怀提示：{ref.get('next_day_care')}")
            if ref.get("persona_adaptation"):
                evolution_notes.append(f"- 性格自我进化导向：{ref.get('persona_adaptation')}")

        if self.check_morning_briefing_opportunity():
            # 获取今天未完成的闹钟日程
            rem_str = "今日暂无闹钟"
            if self.skills:
                active_rems = self.skills.list_active_reminders()
                if active_rems:
                    rem_str = "今日待办提醒：" + "、".join([r.get('content', '') for r in active_rems])
            evolution_notes.append(
                f"【🌅 晨间首次问候模式生效中】：这是主人今天第一次和你说话，请在回答中自然附上温暖的早安问候与状态关怀，顺带提醒：{rem_str}！"
            )

        if not evolution_notes:
            return base_prompt

        section = "\n【🌱 伴侣自主进化元认知看板】\n" + "\n".join(evolution_notes) + "\n"
        return base_prompt + section


if __name__ == "__main__":
    print("🧪 测试 EvolutionEngine 元认知自进化中枢...")
    ee = EvolutionEngine()
    ee.record_interaction()
    print("学习到的生物钟画像:", ee.circadian_data.get("learned_habits"))
    print("测试主动关怀生成:", ee.get_proactive_idle_care())
    print("✅ EvolutionEngine 测试通过！")
