"""
PIG 机器人 - 主动关怀守护中枢 (Proactive Care Engine)
专为树莓派低算力架构设计：
1. 0% 本地模型运算，轻量状态机与时间窗口自检。
2. 晨间初见 (Morning Greeting): 结合昨日记忆反思（如身体不适、失眠、重要行程）进行针对性问候。
3. 工位久坐 (Sedentary Care): 连续工作超过 90 分钟无休息，主动歪头眨眼提醒喝水、看窗外、活动肩颈。
4. 深夜伴睡 (Night Reminder): 深夜 (23:30 ~ 02:30) 屏幕看久提醒护眼早睡。
5. 防打扰机制 (Anti-Annoyance):
   - 全局独立开关 (proactive_care: true/false)
   - 每日频次封顶与冷却间隔 (cooldown_hours)
   - 一键静音防打扰 (snooze)
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional

class ProactiveCareManager:
    def __init__(self, storage_dir=None, memory_manager=None, evolution_engine=None, skill_manager=None, config=None):
        if storage_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_dir = os.path.join(project_root, "data")
        os.makedirs(storage_dir, exist_ok=True)
        self.storage_dir = storage_dir
        self.memory = memory_manager
        self.evolution = evolution_engine
        self.skills = skill_manager
        self.config = config or {}

        self.state_file = os.path.join(storage_dir, "proactive_state.json")
        self._last_mtime = os.path.getmtime(self.state_file) if os.path.exists(self.state_file) else 0.0
        self.state = self._load_state()

        # 连续专注/活动时间跟踪
        self.continuous_active_seconds = 0
        self.last_tick_time = time.time()
        self.quiet_seconds = 0

    def _sync_state(self):
        """如果外部进程（如 CLI 技能或独立指令）修改了状态文件，热重载最新状态"""
        try:
            if os.path.exists(self.state_file):
                mtime = os.path.getmtime(self.state_file)
                if mtime > getattr(self, "_last_mtime", 0.0):
                    self.state = self._load_state()
                    self._last_mtime = mtime
        except Exception:
            pass

    def _load_state(self) -> Dict[str, Any]:
        default = {
            "enabled": bool(self.config.get("proactive_care", True)),
            "cooldown_hours": float(self.config.get("proactive_care_cooldown_hours", 2.0)),
            "last_morning_date": "",
            "last_sedentary_date": "",
            "last_night_date": "",
            "last_care_timestamp": 0.0,
            "snooze_until": 0.0,
            "care_history": []
        }
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    default.update(saved)
            except Exception as e:
                print(f"⚠️ [ProactiveCare] 读取状态异常: {e}")
        return default

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_file)
            self._last_mtime = os.path.getmtime(self.state_file)
        except Exception as e:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
            print(f"⚠️ [ProactiveCare] 保存状态异常: {e}")

    def is_enabled(self) -> bool:
        self._sync_state()
        return self.state.get("enabled", True)

    def set_enabled(self, enabled: bool) -> bool:
        self._sync_state()
        self.state["enabled"] = bool(enabled)
        self._save_state()
        print(f"❤️ [ProactiveCare] 主动关怀守护状态已切换为: {'开启' if enabled else '关闭'}")
        return self.state["enabled"]

    def toggle(self) -> bool:
        return self.set_enabled(not self.is_enabled())

    def snooze(self, minutes: int = 120):
        """一键暂止防打扰（例如主人正在开会或专注）"""
        self._sync_state()
        self.state["snooze_until"] = time.time() + (minutes * 60)
        self._save_state()
        print(f"🔕 [ProactiveCare] 已开启静音防打扰，将在 {minutes} 分钟后恢复主动关怀")

    def unsnooze(self):
        """立即解除防打扰静音"""
        self._sync_state()
        self.state["snooze_until"] = 0.0
        self._save_state()
        print("🔔 [ProactiveCare] 已解除静音防打扰，恢复主动关怀守护")

    def is_snoozed(self) -> bool:
        self._sync_state()
        return time.time() < self.state.get("snooze_until", 0.0)

    def get_status(self) -> Dict[str, Any]:
        self._sync_state()
        return {
            "enabled": self.is_enabled(),
            "is_snoozed": self.is_snoozed(),
            "snooze_remaining_min": max(0, int((self.state.get("snooze_until", 0) - time.time()) / 60)),
            "last_care_timestamp": self.state.get("last_care_timestamp", 0.0),
            "last_morning_date": self.state.get("last_morning_date", ""),
            "continuous_active_min": int(self.continuous_active_seconds / 60)
        }

    def _clean_reflection_text(self, ref_care: str) -> str:
        """
        清洗反思指令中的元指令（如'明天主动询问'、'保持温暖关心的语气'等），转化为自然亲切的第一人称PIG 对话
        """
        if not ref_care:
            return ""
        import re
        text = ref_care.strip()
        # 1. 过滤末尾关于语气/态度/格式的元要求
        text = re.sub(r'[，,、\s]*(?:保持|注意|语气|体现|多用|尽量|减少).*?(?:语气|态度|关怀|陪伴|支持)?$', '', text)
        # 2. 过滤开头的指令性动词
        text = re.sub(r'^(?:明天|次日|今天)?(?:主动|记得|务必|尽量)?(?:询问|关心|问候|跟进|问问|提醒)', '', text)
        # 3. 将“明天”替换为“今天”
        text = text.replace("明天", "今天").strip("，,。！？ \n")
        return text

    # ==========================================
    # 关怀话术与肢体表情生成器
    # ==========================================
    def generate_care_payload(self, scene: str) -> Dict[str, Any]:
        user_name = "主人"
        if self.memory and getattr(self.memory, "long_term_data", None):
            user_name = self.memory.long_term_data.get("user_name", "主人")

        if scene == "morning":
            # 1. 尝试联动昨日记忆反思 (仅使用今天或昨天新鲜反思，避免翻出几周前的旧账当成“昨晚”)
            clean_care = ""
            if self.memory and getattr(self.memory, "long_term_data", None):
                ref = self.memory.long_term_data.get("daily_reflection", {})
                if ref and ref.get("next_day_care"):
                    ref_date_str = ref.get("reflected_date") or str(ref.get("date", ""))[:10]
                    is_fresh = False
                    if ref_date_str:
                        try:
                            ref_d = datetime.strptime(ref_date_str[:10], "%Y-%m-%d").date()
                            is_fresh = (datetime.now().date() - ref_d).days <= 1
                        except Exception:
                            is_fresh = False
                    if is_fresh:
                        clean_care = self._clean_reflection_text(ref.get("next_day_care"))

            # 2. 检查今日待办闹钟
            rem_str = ""
            if self.skills and hasattr(self.skills, "list_active_reminders"):
                try:
                    rems = self.skills.list_active_reminders()
                    if rems:
                        rem_str = "今天有待办事项：" + "、".join([r.get("content", "") for r in rems[:2]])
                except Exception:
                    pass

            if clean_care:
                if "情况" in clean_care or "好些" in clean_care or "恢复" in clean_care:
                    text = f"{user_name}，早呀！昨晚我心里一直挂念着你：{clean_care}，现在感觉好些了吗？空腹记得先喝口温水哦。"
                else:
                    text = f"{user_name}，早安！昨晚我心里一直惦记着你：{clean_care}。新的一天开始啦，空腹记得先喝口温水暖暖胃哦。"
            else:
                text = f"{user_name}，早安！新的一天开始啦，今天天气不错，记得吃个热乎的早饭，今天也要元气满满呀。"

            if rem_str:
                text += f" 另外提醒你，{rem_str}。"

            return {
                "scene": "morning",
                "title": "🌅 晨间初见问候",
                "text": text,
                "emotion": "HAPPY",
                "action": "LOOK_UP",
                "sound": "happy_chirp",
                "pan": 90,
                "tilt": 80
            }

        elif scene == "sedentary":
            text = f"{user_name}，你已经连续伏案忙碌一个半小时啦！站起来伸个懒腰，喝口温水，往窗外远眺放松一下眼睛吧，身体最要紧~"
            return {
                "scene": "sedentary",
                "title": "🫁 工位久坐疲劳关怀",
                "text": text,
                "emotion": "CARE",
                "action": "STRETCH",
                "sound": "confused_boop",
                "pan": 105,
                "tilt": 85
            }

        elif scene == "night":
            text = f"{user_name}，夜已经很深啦，屏幕看久了眼睛会干涩。今天已经很努力了，剩下的事情明天做也没关系，早点上床休息吧，晚安哦。"
            return {
                "scene": "night",
                "title": "🌙 深夜伴睡关怀",
                "text": text,
                "emotion": "SLEEPY",
                "action": "REST",
                "sound": "thinking_hum",
                "pan": 90,
                "tilt": 105
            }

        # 默认兜底
        return {
            "scene": "general",
            "title": "❤️ 主动关怀",
            "text": f"{user_name}，工作辛苦啦，有我在桌面上默默陪着你！",
            "emotion": "HAPPY",
            "action": "NOD",
            "sound": "happy_chirp",
            "pan": 90,
            "tilt": 90
        }

    # ==========================================
    # 状态巡检心跳 (Heartbeat Tick)
    # ==========================================
    def tick(self, energy: float = 0.0, is_talking: bool = False) -> Optional[Dict[str, Any]]:
        """
        每 1~5 秒调用一次的心跳逻辑。
        :param energy: 当前麦克风检测到的环境分贝/能量
        :param is_talking: 当前机器人是否正在与人对话中
        :return: 若满足触发条件，返回关怀 payload；否则返回 None
        """
        now = time.time()
        raw_dt = now - self.last_tick_time
        self.last_tick_time = now

        # 防止长时间休眠或刚启动时 dt 暴增导致久坐误判
        dt = min(10.0, max(0.0, raw_dt))
        if raw_dt > 300.0:
            # 距离上次巡检超过 5 分钟，判定为系统休眠或关机重连，重置连续活动统计
            self.continuous_active_seconds = 0
            self.quiet_seconds = 0

        self._sync_state()

        # 自动巡检夜间海马体反思（即使暂时静音免打扰，也在后台静默完成记忆巩固）
        if self.evolution and hasattr(self.evolution, "maybe_auto_nightly_reflection"):
            try:
                self.evolution.maybe_auto_nightly_reflection(self.config)
            except Exception:
                pass

        if not self.is_enabled() or self.is_snoozed():
            return None

        # 如果当前正处于对话/语音播报中，延后主动打扰
        if is_talking:
            return None

        # 统计主人伏案活动时长 (能量高于 60 视为环境有键盘/人声动静)
        if energy > 60.0:
            self.continuous_active_seconds += dt
            self.quiet_seconds = 0
        else:
            self.quiet_seconds += dt
            # 若安静超过 15 分钟，判定主人已离开工位，重置连续活动时长
            if self.quiet_seconds > 900:
                self.continuous_active_seconds = 0

        # 防打扰冷却检查 (两次主动关怀之间至少间隔 cooldown_hours)
        cooldown_sec = self.state.get("cooldown_hours", 2.0) * 3600
        if (now - self.state.get("last_care_timestamp", 0.0)) < cooldown_sec:
            return None

        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")
        current_hour = now_dt.hour

        # ----------------------------------------------------
        # 1. 晨间初见 (07:30 ~ 11:30 且今天尚未初见问候)
        # 必须检测到桌前有动静 (continuous_active_seconds >= 3)
        # ----------------------------------------------------
        if 7 <= current_hour <= 11:
            if self.state.get("last_morning_date") != today_str:
                if self.continuous_active_seconds >= 3.0 or energy > 80.0:
                    payload = self.generate_care_payload("morning")
                    self.state["last_morning_date"] = today_str
                    self.state["last_care_timestamp"] = now
                    self._record_history(payload)
                    self._save_state()
                    return payload

        # ----------------------------------------------------
        # 2. 工位久坐疲劳 (13:00 ~ 19:00，连续活动超过 90 分钟)
        # ----------------------------------------------------
        if 13 <= current_hour <= 19:
            if self.continuous_active_seconds >= 5400: # 90 分钟
                if self.state.get("last_sedentary_date") != today_str:
                    payload = self.generate_care_payload("sedentary")
                    self.state["last_sedentary_date"] = today_str
                    self.state["last_care_timestamp"] = now
                    self.continuous_active_seconds = 0 # 触发后重置
                    self._record_history(payload)
                    self._save_state()
                    return payload

        # ----------------------------------------------------
        # 3. 深夜作息提醒 (23:30 ~ 02:30，夜深仍在活跃)
        # ----------------------------------------------------
        if current_hour >= 23 or current_hour <= 2:
            if self.state.get("last_night_date") != today_str:
                if self.continuous_active_seconds >= 3.0 or energy > 70.0:
                    payload = self.generate_care_payload("night")
                    self.state["last_night_date"] = today_str
                    self.state["last_care_timestamp"] = now
                    self._record_history(payload)
                    self._save_state()
                    return payload

        return None

    def trigger_test(self, scene: str = "morning") -> Dict[str, Any]:
        """手动测试关怀效果 (跳过所有时间与冷却限制)"""
        payload = self.generate_care_payload(scene)
        self.state["last_care_timestamp"] = time.time()
        self._record_history(payload)
        self._save_state()
        return payload

    def _record_history(self, payload: Dict[str, Any]):
        history = self.state.get("care_history", [])
        history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scene": payload.get("scene"),
            "title": payload.get("title"),
            "text": payload.get("text")
        })
        # 只保留最近 10 次
        self.state["care_history"] = history[-10:]

if __name__ == "__main__":
    print("🧪 测试 ProactiveCareManager...")
    pcm = ProactiveCareManager()
    print("当前开关状态:", pcm.is_enabled())
    morning_test = pcm.trigger_test("morning")
    print("晨间初见问候示例:", morning_test["text"])
    sedentary_test = pcm.trigger_test("sedentary")
    print("久坐疲劳关怀示例:", sedentary_test["text"])
    print("✅ ProactiveCareManager 测试通过！")
