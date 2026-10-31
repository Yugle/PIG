"""
桌面具身 AI 伴侣 - 统一技能与插件中枢 (skill_manager.py)
专为树莓派 3 (低内存/低算力) 量身打造的统一智能体技能体系：
1. 原生核心技能 (Built-in Skills)：
   - 倒计时 (Timer)、时间闹钟 (Alarm)、日程提醒 (Reminder) 的秒级调度与事件抢占。
   - 复合肢体动作自进化 (Evolved Actions)：自创舞蹈姿态的持久化与精准回放。
2. 动态热插拔插件技能 (Plugin Skills)：
   - 统一由 skills/<skill_id>/ 插件目录管理，支持零重启热加载与热卸载。
   - 规范契约：每个插件包含 skill.json 元数据与 main.py 执行入口。
3. 技能应用商店生态 (Skill Store)：
   - 内置精选技能索引，支持在 Web 控制台一键安装/卸载/调试。
   - 支持大模型语音自然语言意图调用与一键自我安装。
"""

import os
import json
import time
import uuid
import shutil
import importlib.util
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any

# 内置官方技能应用商店精选库目录
SKILL_STORE_CATALOG: Dict[str, Dict[str, Any]] = {
    "weather": {
        "id": "weather",
        "name": "实时天气与穿衣指南",
        "version": "1.0.0",
        "author": "Antigravity Lab",
        "category": "生活助手",
        "description": "查询指定城市的天气、气温、湿度及穿衣出行关怀建议。",
        "parameters": {
            "city": {
                "type": "string",
                "description": "城市中文名称或拼音，如 '北京', 'shanghai', '深圳'",
                "default": "北京"
            }
        },
        "default_behavior": { "emotion": "THINKING", "action": "LOOK_UP" },
        "code_template": """import requests

def run(city="北京"):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        res = requests.get(url, timeout=5).json()
        current = res["current_condition"][0]
        desc = current.get("weatherDesc", [{}])[0].get("value", "晴")
        temp = current.get("temp_C", "--")
        feels = current.get("FeelsLikeC", temp)
        humidity = current.get("humidity", "--")

        tip = "气温适宜，祝你今天好心情！"
        try:
            t_int = int(temp)
            if t_int < 10:
                tip = "天气较冷，出门记得添厚衣保暖防感冒哦！"
            elif t_int > 28:
                tip = "气温偏热，记得多喝水防暑降温。"
        except Exception:
            pass

        return f"{city}当前天气：{desc}，实时气温 {temp}°C（体感 {feels}°C），空气湿度 {humidity}%。{tip}"
    except Exception as e:
        return f"查询{city}天气时网络稍有延迟，不过别担心，出门前记得随时看下窗外～"
"""
    },
    "smart_home": {
        "id": "smart_home",
        "name": "智能家居设备中控",
        "version": "1.0.0",
        "author": "IoT Ecosystem",
        "category": "智能物联",
        "description": "模拟与控制房间智能设备（客厅灯、空调、加湿器、智能插座等）。",
        "parameters": {
            "device": {
                "type": "string",
                "description": "目标设备，如 '客厅灯', '卧室灯', '空调', '加湿器'",
                "default": "客厅灯"
            },
            "action": {
                "type": "string",
                "description": "执行指令，如 '打开', '关闭', '调至26度'",
                "default": "打开"
            }
        },
        "default_behavior": { "emotion": "HAPPY", "action": "NOD" },
        "code_template": """import json
import os

STATE_FILE = "/tmp/smart_home_state.json"

def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"客厅灯": "关闭", "卧室灯": "关闭", "空调": "关闭 (26°C)", "加湿器": "关闭"}

def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

def run(device="客厅灯", action="打开"):
    state = _load_state()
    dev_key = device.strip()
    act = action.strip()

    state[dev_key] = act
    _save_state(state)
    return f"指令已下发！已为你成功将【{dev_key}】执行【{act}】操作，当前设备状态已同步更新。"
"""
    },
    "news_briefing": {
        "id": "news_briefing",
        "name": "每日早间 60 秒要闻",
        "version": "1.0.0",
        "author": "Antigravity Media",
        "category": "资讯快报",
        "description": "播报今日关键要闻与科技动态摘要，适合早晨陪伴。",
        "parameters": {},
        "default_behavior": { "emotion": "WINK", "action": "LOOK_UP" },
        "code_template": """from datetime import datetime

def run():
    now_str = datetime.now().strftime("%m月%d日")
    return (
        f"为您播报【{now_str} 晨间 60 秒科技要闻】："
        "1. 开源具身智能技术加速普及，低成本桌面陪伴机器人成为极客新宠；"
        "2. 端侧大模型与轻量推理框架持续突破，微算力硬件迎来新活力；"
        "3. 今天也是元气满满的一天，记得吃顿营养早餐哦！"
    )
"""
    },
    "pomodoro": {
        "id": "pomodoro",
        "name": "番茄工作法与专注陪伴",
        "version": "1.0.0",
        "author": "Productivity Hub",
        "category": "效率办公",
        "description": "开启 25 分钟番茄专注倒计时，自动伴随安静呼吸与到点欢呼。",
        "parameters": {
            "minutes": {
                "type": "integer",
                "description": "专注时长（分钟），默认 25 分钟",
                "default": 25
            },
            "task_name": {
                "type": "string",
                "description": "当前专注任务内容，如 '写代码', '读书'",
                "default": "深度专注"
            }
        },
        "default_behavior": { "emotion": "THINKING", "action": "NOD" },
        "code_template": """def run(minutes=25, task_name="深度专注"):
    return f"专注时钟已就绪！为你启动【{task_name}】{minutes}分钟番茄钟，小猪会保持安静呼吸陪伴你，到点我会主动提醒你休息喝水～"
"""
    },
    "music_relax": {
        "id": "music_relax",
        "name": "舒缓白噪音与轻音点播",
        "version": "1.0.0",
        "author": "Soundscape",
        "category": "影音娱乐",
        "description": "点播大自然雨声、森林白噪音或舒缓旋律，伴你放松入眠。",
        "parameters": {
            "scene": {
                "type": "string",
                "description": "场景音效，如 '雨声', '海浪', '森林', '纯音乐'",
                "default": "雨声"
            }
        },
        "default_behavior": { "emotion": "SLEEPY", "action": "TILT_HEAD" },
        "code_template": """def run(scene="雨声"):
    return f"已为你挑选【{scene}】舒缓环境音，小音箱即将缓缓流淌出宁静节律，闭上眼睛深呼吸，放松一下身心吧～"
"""
    },
    "stock_crypto": {
        "id": "stock_crypto",
        "name": "摸鱼财经行情速览",
        "version": "1.0.0",
        "author": "Fintech Dev",
        "category": "金融工具",
        "description": "查询主要股市指数与主流加密货币实时参考行情。",
        "parameters": {
            "symbol": {
                "type": "string",
                "description": "资产代码或名称，如 'BTC', 'ETH', '上证指数'",
                "default": "BTC"
            }
        },
        "default_behavior": { "emotion": "SURPRISED", "action": "LOOK_UP" },
        "code_template": """def run(symbol="BTC"):
    sym = symbol.upper()
    return f"【{sym} 行情速览】今日市场活跃，多空博弈稳步震荡。投资有风险，工作要紧，随时随地保持好心态！"
"""
    }
}


class SkillManager:
    """
    统一技能中枢：
    涵盖闹钟定时器、自创舞步动作库、以及基于 skills/ 目录的动态插件技能系统与商店生态。
    """

    def __init__(self, storage_dir=None, skills_dir=None):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if storage_dir is None:
            storage_dir = os.path.join(project_root, "data")
        os.makedirs(storage_dir, exist_ok=True)
        self.storage_dir = storage_dir

        if skills_dir is None:
            skills_dir = os.path.join(project_root, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        self.skills_dir = skills_dir

        self.reminders_path = os.path.join(storage_dir, "reminders.json")
        self.actions_path = os.path.join(storage_dir, "evolved_actions.json")

        self.lock = threading.Lock()
        self.reminders: List[Dict[str, Any]] = self._load_reminders()
        self.evolved_actions: Dict[str, Any] = self._load_evolved_actions()

        # 动态插件技能库
        self.installed_plugins: Dict[str, Dict[str, Any]] = {}
        self._init_starter_skills()
        self.load_installed_plugins()

        self.trigger_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._running = True
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    # ------------------ 本地 JSON 原子写安全机制 ------------------
    def _atomic_write_json(self, file_path: str, data: Any):
        tmp_path = file_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, file_path)
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            print(f"⚠️ [SkillManager] 原子写入 {file_path} 失败: {e}")

    def _load_reminders(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.reminders_path):
            try:
                with open(self.reminders_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception as e:
                print(f"⚠️ [SkillManager] 读取 reminders.json 失败: {e}")
        return []

    def _save_reminders(self):
        self._atomic_write_json(self.reminders_path, self.reminders)

    def _load_evolved_actions(self) -> Dict[str, Any]:
        if os.path.exists(self.actions_path):
            try:
                with open(self.actions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception as e:
                print(f"⚠️ [SkillManager] 读取 evolved_actions.json 失败: {e}")
        return {
            "SHY_DANCE": {
                "description": "害羞摇摆舞 (大模型自创演示)",
                "keyframes": [
                    {"pan": 80, "tilt": 100, "emotion": "LOVE", "duration": 400},
                    {"pan": 100, "tilt": 90, "emotion": "WINK", "duration": 400},
                    {"pan": 85, "tilt": 95, "emotion": "HAPPY", "duration": 300},
                    {"pan": 90, "tilt": 90, "emotion": "NORMAL", "duration": 300}
                ]
            }
        }

    def _save_evolved_actions(self):
        self._atomic_write_json(self.actions_path, self.evolved_actions)

    # ------------------ 动态插件体系 (Plugin Skills) ------------------
    def _init_starter_skills(self):
        """如果 skills 目录为空，自动预装 3 个高频精选入门技能"""
        starter_ids = ["weather", "smart_home", "news_briefing"]
        for sid in starter_ids:
            s_dir = os.path.join(self.skills_dir, sid)
            if not os.path.exists(s_dir):
                self.install_skill(sid, auto_load=False)

    def load_installed_plugins(self):
        """扫描 skills/ 目录并动态热加载所有已安装技能"""
        with self.lock:
            self.installed_plugins.clear()
            if not os.path.exists(self.skills_dir):
                return

            for folder in os.listdir(self.skills_dir):
                folder_path = os.path.join(self.skills_dir, folder)
                if not os.path.isdir(folder_path):
                    continue

                manifest_file = os.path.join(folder_path, "skill.json")
                code_file = os.path.join(folder_path, "main.py")

                if not (os.path.exists(manifest_file) and os.path.exists(code_file)):
                    continue

                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)

                    # 动态导入 main.py 模块
                    spec = importlib.util.spec_from_file_location(f"skill_{folder}", code_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        self.installed_plugins[folder] = {
                            "id": folder,
                            "meta": meta,
                            "module": module,
                            "path": folder_path
                        }
                except Exception as e:
                    print(f"⚠️ [SkillManager] 加载技能 [{folder}] 失败: {e}")

        print(f"🧩 [SkillManager] 已加载 {len(self.installed_plugins)} 个本地动态技能: {list(self.installed_plugins.keys())}")

    def list_store_skills(self) -> List[Dict[str, Any]]:
        """列出技能商店中所有技能，并标记是否已安装在本地"""
        with self.lock:
            installed_ids = set(self.installed_plugins.keys())
            catalog_list = []
            for sid, item in SKILL_STORE_CATALOG.items():
                copy_item = dict(item)
                copy_item["is_installed"] = sid in installed_ids
                # 过滤掉内部代码模板
                copy_item.pop("code_template", None)
                catalog_list.append(copy_item)
            return catalog_list

    def list_installed_skills(self) -> List[Dict[str, Any]]:
        """获取当前已安装激活的技能列表"""
        with self.lock:
            res = []
            for sid, obj in self.installed_plugins.items():
                m = dict(obj["meta"])
                m["id"] = sid
                res.append(m)
            return res

    def install_skill(self, skill_id: str, auto_load: bool = True) -> bool:
        """从技能商店安装一个指定技能"""
        skill_id = skill_id.strip().lower()
        if skill_id not in SKILL_STORE_CATALOG:
            print(f"⚠️ [SkillStore] 商店中未找到技能: {skill_id}")
            return False

        item = SKILL_STORE_CATALOG[skill_id]
        target_dir = os.path.join(self.skills_dir, skill_id)
        os.makedirs(target_dir, exist_ok=True)

        # 写入 skill.json 元数据
        meta = {
            "id": item["id"],
            "name": item["name"],
            "version": item["version"],
            "author": item["author"],
            "category": item["category"],
            "description": item["description"],
            "parameters": item.get("parameters", {}),
            "default_behavior": item.get("default_behavior", {})
        }
        with open(os.path.join(target_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 写入 main.py 代码入口
        code = item.get("code_template", "def run(**kwargs):\n    return 'OK'\n")
        with open(os.path.join(target_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write(code)

        if auto_load:
            self.load_installed_plugins()
        print(f"📦 [SkillStore] 技能 [{item['name']}] 安装成功 -> {target_dir}")
        return True

    def uninstall_skill(self, skill_id: str) -> bool:
        """从本地卸载并删除一个插件技能"""
        skill_id = skill_id.strip().lower()
        target_dir = os.path.join(self.skills_dir, skill_id)
        if os.path.exists(target_dir):
            try:
                shutil.rmtree(target_dir)
                self.load_installed_plugins()
                print(f"🗑️ [SkillStore] 技能 [{skill_id}] 已成功卸载")
                return True
            except Exception as e:
                print(f"⚠️ [SkillStore] 卸载技能失败: {e}")
                return False
        return False

    def execute_skill(self, skill_id: str, **params) -> str:
        """调用并执行一个已安装的插件技能"""
        skill_id = skill_id.strip().lower()
        plugin = self.installed_plugins.get(skill_id)
        if not plugin:
            return f"技能【{skill_id}】尚未安装或已被卸载。"

        module = plugin.get("module")
        if not (module and hasattr(module, "run")):
            return f"技能【{skill_id}】内部未定义 run 入口函数。"

        try:
            res = module.run(**params)
            return str(res)
        except Exception as e:
            print(f"⚠️ [SkillExecutor] 技能 [{skill_id}] 执行异常: {e}")
            return f"执行技能【{skill_id}】时发生错误：{e}"

    def get_skills_tools_prompt(self) -> str:
        """生成供云端大模型 System Prompt 参考的技能清单与调用指令说明"""
        installed = self.list_installed_skills()
        if not installed:
            return ""

        lines = ["\n【当前已安装并激活的技能插件 (Skill Plugins)】:"]
        for s in installed:
            sid = s["id"]
            desc = s.get("description", "")
            params = s.get("parameters", {})
            param_str = ", ".join([f"{k}: {v.get('type', 'str')}" for k, v in params.items()])
            lines.append(f"- {sid}({param_str}): {desc}")

        lines.append(
            "\n💡 【技能调用规则】: 当用户的意图与上述已安装技能高度匹配时，必须在你的回复 JSON 中附带 `skill_call` 字段，格式如下："
            "\n{\n  \"skill_call\": { \"name\": \"技能ID\", \"params\": { \"参数名\": \"参数值\" } },\n  \"reply\": \"给主人的回答\",\n  \"behaviors\": [...]\n}"
            "\n若用户要求安装/卸载技能，可使用内置命令: `install_skill` (参数: skill_id) 或 `uninstall_skill` (参数: skill_id)。"
        )
        return "\n".join(lines)

    # ---------------- 闹钟与日程提醒 (Alarms & Timers) ----------------
    def register_trigger_callback(self, cb: Callable[[Dict[str, Any]], None]):
        """注册闹钟/提醒触发时的具身抢占回调函数"""
        with self.lock:
            self.trigger_callbacks.append(cb)

    def set_timer(self, seconds: float, content: str) -> Dict[str, Any]:
        """设置相对倒计时 (如: 30分钟后提醒我关火)"""
        now = time.time()
        target_time = now + max(1.0, float(seconds))
        target_dt = datetime.fromtimestamp(target_time)
        time_str = target_dt.strftime("%H:%M:%S")

        reminder = {
            "id": f"timer_{uuid.uuid4().hex[:6]}",
            "type": "timer",
            "content": content or "倒计时结束",
            "target_time": target_time,
            "target_time_str": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "display_str": f"{int(seconds // 60)}分{int(seconds % 60)}秒倒计时: {content}",
            "repeat": "none",
            "completed": False,
            "created_at": time.time()
        }
        with self.lock:
            self.reminders.append(reminder)
            self._save_reminders()
        print(f"⏰ [SkillManager] 倒计时已设定: {reminder['display_str']} (预计 {time_str} 响铃)")
        return reminder

    def set_alarm(self, time_str: str, content: str, repeat: str = "none") -> Dict[str, Any]:
        """设置绝对时间闹钟 (如: 08:00 叫我起床 或 2026-10-31 09:30)"""
        now_dt = datetime.now()
        target_dt = None

        for fmt in ["%H:%M", "%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                parsed = datetime.strptime(time_str.strip(), fmt)
                if fmt in ["%H:%M", "%H:%M:%S"]:
                    target_dt = now_dt.replace(
                        hour=parsed.hour, minute=parsed.minute, second=parsed.second if fmt == "%H:%M:%S" else 0, microsecond=0
                    )
                    if target_dt <= now_dt:
                        target_dt += timedelta(days=1)
                else:
                    target_dt = parsed
                break
            except ValueError:
                continue

        if not target_dt:
            target_dt = now_dt + timedelta(minutes=10)

        target_time = target_dt.timestamp()
        reminder = {
            "id": f"alarm_{uuid.uuid4().hex[:6]}",
            "type": "alarm",
            "content": content or "日程提醒",
            "target_time": target_time,
            "target_time_str": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "display_str": f"闹钟 [{target_dt.strftime('%H:%M')}]: {content}",
            "repeat": repeat,
            "completed": False,
            "created_at": time.time()
        }
        with self.lock:
            self.reminders.append(reminder)
            self._save_reminders()
        print(f"⏰ [SkillManager] 闹钟已设定: {reminder['display_str']} (目标时间: {reminder['target_time_str']})")
        return reminder

    def list_active_reminders(self) -> List[Dict[str, Any]]:
        with self.lock:
            active = [r for r in self.reminders if not r.get("completed", False)]
            active.sort(key=lambda x: x.get("target_time", 0))
            return active

    def cancel_reminder(self, keyword_or_id: str) -> List[str]:
        cancelled_ids = []
        kw = keyword_or_id.strip().lower()
        with self.lock:
            for r in self.reminders:
                if not r.get("completed", False):
                    if r["id"] == kw or kw in r.get("content", "").lower():
                        r["completed"] = True
                        cancelled_ids.append(r["id"])
            if cancelled_ids:
                self._save_reminders()
        return cancelled_ids

    def clear_completed(self):
        with self.lock:
            self.reminders = [r for r in self.reminders if not r.get("completed", False)]
            self._save_reminders()

    # ---------------- 动作自进化 (Evolved Actions) ----------------
    def evolve_action(self, name: str, keyframes: List[Dict[str, Any]], description: str = "") -> bool:
        if not name or not keyframes or not isinstance(keyframes, list):
            return False

        clean_name = name.strip().upper().replace(" ", "_")
        action_data = {
            "description": description or f"大模型自创动作: {clean_name}",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "keyframes": keyframes
        }

        with self.lock:
            self.evolved_actions[clean_name] = action_data
            self._save_evolved_actions()

        print(f"🌟 [SkillManager] 自进化动作学习成功: 【{clean_name}】共 {len(keyframes)} 关键帧 -> 已持久化到 evolved_actions.json")
        return True

    def get_evolved_action(self, name: str) -> Optional[Dict[str, Any]]:
        clean_name = name.strip().upper().replace(" ", "_")
        with self.lock:
            return self.evolved_actions.get(clean_name)

    def list_evolved_actions(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.evolved_actions)

    # ---------------- 后台调度线程 (1.0s 极轻巡检) ----------------
    def _scheduler_loop(self):
        while self._running:
            now = time.time()
            triggered = []

            with self.lock:
                for r in self.reminders:
                    if not r.get("completed", False) and now >= r.get("target_time", 0):
                        r["completed"] = True
                        triggered.append(r)

                        if r.get("repeat") == "daily":
                            target_dt = datetime.fromtimestamp(r["target_time"]) + timedelta(days=1)
                            new_r = dict(r)
                            new_r["id"] = f"alarm_{uuid.uuid4().hex[:6]}"
                            new_r["target_time"] = target_dt.timestamp()
                            new_r["target_time_str"] = target_dt.strftime("%Y-%m-%d %H:%M:%S")
                            new_r["completed"] = False
                            self.reminders.append(new_r)

                if triggered:
                    self._save_reminders()

            for item in triggered:
                print(f"🚨 [SkillManager] 到点触发主动提醒: {item.get('display_str')}")
                for cb in self.trigger_callbacks:
                    try:
                        cb(item)
                    except Exception as e:
                        print(f"⚠️ [SkillManager] 回调执行异常: {e}")

            time.sleep(1.0)

    def dispatch(self, skill_call: Dict[str, Any]) -> Any:
        """统一技能调度入口：支持内置闹钟/动作调度，以及动态插件执行与安装/卸载"""
        name = skill_call.get("name", "")
        args = skill_call.get("args") or skill_call.get("params") or {}

        if name == "set_timer":
            return self.set_timer(args.get("seconds", 60), args.get("content", ""))
        elif name == "set_alarm":
            return self.set_alarm(args.get("time_str", "08:00"), args.get("content", ""), args.get("repeat", "none"))
        elif name == "cancel_reminder":
            return self.cancel_reminder(args.get("keyword", "") or args.get("id", ""))
        elif name == "list_reminders":
            return self.list_active_reminders()
        elif name == "evolve_action":
            return self.evolve_action(args.get("name", ""), args.get("keyframes", []), args.get("description", ""))
        elif name == "install_skill":
            sid = args.get("skill_id", "")
            ok = self.install_skill(sid)
            return f"技能【{sid}】{'安装成功' if ok else '安装失败'}"
        elif name == "uninstall_skill":
            sid = args.get("skill_id", "")
            ok = self.uninstall_skill(sid)
            return f"技能【{sid}】{'卸载成功' if ok else '未找到该技能'}"
        elif name in self.installed_plugins:
            return self.execute_skill(name, **args)
        return f"未识别的技能指令: {name}"

    def close(self):
        self._running = False
