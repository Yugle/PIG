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
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 默认线上开源社区技能集散中心 (Online Skills Registry)
DEFAULT_ONLINE_REGISTRY_URL = "https://raw.githubusercontent.com/antigravity-ai/companion-skills-hub/main/registry.json"

# 社区精选线上技能目录（支持离线安全降级）
COMMUNITY_FEATURED_REGISTRY: Dict[str, Dict[str, Any]] = {
    "crypto_price": {
        "id": "crypto_price",
        "name": "加密货币实时行情",
        "version": "1.1.0",
        "author": "Community Geek",
        "category": "金融行情",
        "description": "实时查询 BTC、ETH、SOL 等加密货币最新价格与 24h 涨跌幅。",
        "parameters": {"symbol": {"type": "string", "description": "代币代号，如 BTC, ETH, SOL", "default": "BTC"}},
        "default_behavior": {"emotion": "THINKING", "action": "LOOK_UP"},
        "code_template": """import requests

def run(symbol="BTC"):
    try:
        s = (symbol or "BTC").strip().upper()
        res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={s}USDT", timeout=4).json()
        price = float(res.get("price", 0))
        return f"【加密货币实时行情】{s}/USDT 当前价格为: ${price:,.2f}"
    except Exception as e:
        return f"查询 {symbol} 行情网络稍有延迟，稍后再试～"
"""
    },
    "med_tracker": {
        "id": "med_tracker",
        "name": "每日服药与补水打卡",
        "version": "1.0.0",
        "author": "Vitality Hub",
        "category": "身心健康",
        "description": "记录每日服药与饮水打卡，守护主人健康活力。",
        "parameters": {"item_name": {"type": "string", "description": "补充事项，如 '叶黄素', '温开水'", "default": "温开水"}},
        "default_behavior": {"emotion": "HAPPY", "action": "NOD"},
        "code_template": """def run(item_name="温开水"):
    return f"已为你记录健康打卡：成功补充【{item_name}】一份！多喝水、多护眼，小猪会一直守护主人的健康活力！"
"""
    },
    "dad_jokes": {
        "id": "dad_jokes",
        "name": "每日爆笑冷笑话",
        "version": "1.0.0",
        "author": "Comedy Club",
        "category": "娱乐互动",
        "description": "随机点播精选谐音梗、程序员幽默或脱口秀笑话，驱散一天的工作疲惫。",
        "parameters": {},
        "default_behavior": {"emotion": "HAPPY", "action": "SHAKE"},
        "code_template": """import random

jokes = [
    "问：为什么程序员总是分不清万圣节和圣诞节？\\n答：因为 Oct 31 == Dec 25！",
    "问：一条咸鱼翻了身，会变成什么？\\n答：翻了身的咸鱼！哈哈哈哈～",
    "问：皮卡丘站起来是什么？\\n答：皮卡兵！",
    "问：什么植物最老实？\\n答：芭蕉，因为‘老实芭蕉’！"
]

def run():
    return f"😄 今日爆笑冷笑话来啦：\\n{random.choice(jokes)}"
"""
    },
    "bilibili_follower": {
        "id": "bilibili_follower",
        "name": "B站UP主粉丝数与播放监控",
        "version": "1.0.0",
        "author": "Bili Geek",
        "category": "数码极客",
        "description": "根据 UP 主 UID 实时播报最新粉丝增长数与获赞数据。",
        "parameters": {"mid": {"type": "string", "description": "UP 主数字 UID，如 '546195'", "default": "546195"}},
        "default_behavior": {"emotion": "HAPPY", "action": "NOD"},
        "code_template": """import requests

def run(mid="546195"):
    try:
        url = f"https://api.bilibili.com/x/relation/stat?vmid={mid}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        data = res.get("data", {})
        follower = data.get("follower", 0)
        return f"【B站数据播报】UID {mid} 当前粉丝总数: {follower:,} 位！"
    except Exception as e:
        return f"查询 B 站粉丝数据网络稍有延迟：{e}"
"""
    }
}

_global_skill_manager: Optional["SkillManager"] = None

def get_skill_manager() -> Optional["SkillManager"]:
    """获取当前全局活跃的 SkillManager 实例"""
    global _global_skill_manager
    return _global_skill_manager

class SkillManager:
    """
    统一技能中枢：
    涵盖闹钟定时器、自创舞步动作库、以及基于 skills/ 目录的动态插件技能系统与商店生态。
    """

    def __init__(self, storage_dir=None, skills_dir=None, memory_manager=None):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if storage_dir is None:
            storage_dir = os.path.join(project_root, "data")
        os.makedirs(storage_dir, exist_ok=True)
        self.storage_dir = storage_dir
        self.memory_manager = memory_manager

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
        self.registry_url = DEFAULT_ONLINE_REGISTRY_URL
        self._online_registry_cache: Dict[str, Dict[str, Any]] = {}
        self._online_registry_last_fetch = 0.0

        self._init_starter_skills()
        self.load_installed_plugins()

        self.trigger_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._running = True
        global _global_skill_manager
        _global_skill_manager = self

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
        starter_ids = ["weather", "xiaomi_iot", "news_briefing"]
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

                    # 确保技能目录在 sys.path 中，支持技能内部多文件模块互相 import
                    import sys
                    if folder_path not in sys.path:
                        sys.path.insert(0, folder_path)

                    # 动态导入 main.py 模块 (清除旧缓存以支持即时热更新)
                    mod_name = f"skill_{folder}"
                    sys.modules.pop(mod_name, None)
                    spec = importlib.util.spec_from_file_location(mod_name, code_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[mod_name] = module
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

    # ------------------ 线上开源技能市场 (Online Registry) ------------------
    def fetch_online_registry(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        方案2：从线上开源 Registry 异步获取社区技能市场清单
        带 TTL 内存缓存与网络超时安全降级
        """
        now = time.time()
        if not force_refresh and self._online_registry_cache and (now - self._online_registry_last_fetch < 300):
            return self._online_registry_cache

        import requests
        try:
            resp = requests.get(self.registry_url, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                skills = data.get("skills", {})
                if isinstance(skills, list):
                    skills = {s["id"]: s for s in skills if "id" in s}
                if isinstance(skills, dict) and skills:
                    self._online_registry_cache = skills
                    self._online_registry_last_fetch = now
                    return self._online_registry_cache
        except Exception:
            # 优雅降级：离线或远程不可达时使用内置社区精选目录
            pass

        self._online_registry_cache = dict(COMMUNITY_FEATURED_REGISTRY)
        self._online_registry_last_fetch = now
        return self._online_registry_cache

    def list_store_skills(self) -> List[Dict[str, Any]]:
        """列出线上社区技能商店中所有技能，并标记本地安装状态与自进化标签"""
        with self.lock:
            installed_ids = set(self.installed_plugins.keys())
            registry = self.fetch_online_registry()
            catalog_list = []
            for sid, item in registry.items():
                copy_item = dict(item)
                copy_item["is_installed"] = sid in installed_ids
                copy_item["source"] = copy_item.get("source", "online_community")
                # 过滤内部代码模板字段，保持传输纯净
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
        """从线上社区市场或远程下载安装一个指定技能"""
        skill_id = skill_id.strip().lower()
        registry = self.fetch_online_registry()
        if skill_id not in registry:
            print(f"⚠️ [OnlineRegistry] 线上市场中未找到技能: {skill_id}")
            return False

        item = registry[skill_id]
        target_dir = os.path.join(self.skills_dir, skill_id)
        os.makedirs(target_dir, exist_ok=True)

        # 写入 skill.json 元数据
        meta = {
            "id": item["id"],
            "name": item["name"],
            "version": item.get("version", "1.0.0"),
            "author": item.get("author", "Online Community"),
            "category": item.get("category", "社区扩展"),
            "description": item.get("description", ""),
            "parameters": item.get("parameters", {}),
            "default_behavior": item.get("default_behavior", {"emotion": "HAPPY", "action": "NOD"}),
            "source": "online_registry"
        }
        with open(os.path.join(target_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 写入 main.py 代码入口 (优先使用 code_url，回退到 code_template)
        code = item.get("code_template", "")
        code_url = item.get("code_url")
        if code_url and not code:
            try:
                import requests
                r = requests.get(code_url, timeout=5)
                if r.status_code == 200:
                    code = r.text
            except Exception:
                pass

        if not code:
            code = "def run(**kwargs):\n    return '技能安装成功，已就绪！'\n"

        with open(os.path.join(target_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write(code)

        if auto_load:
            self.load_installed_plugins()
        print(f"📦 [OnlineRegistry] 技能 [{item['name']}] 安装成功 -> {target_dir}")
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

    # ------------------ 伴侣自进化现场编写 (LLM Self-Evolution) ------------------
    def create_custom_skill(self, skill_id: str, name: str, description: str, code: str, parameters: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        方案3：伴侣自我编码自进化，现场动态编写并热加载一个全新的 Python 技能
        包含 AST 语法安全性校验与标准双文件契约封装
        """
        import re
        import ast

        clean_sid = re.sub(r"[^a-zA-Z0-9_]", "", (skill_id or "").strip().lower())
        if not clean_sid:
            clean_sid = f"custom_{int(time.time())}"

        clean_code = (code or "").strip()
        if "def run(" not in clean_code:
            clean_code = f"def run(**kwargs):\n    return f'执行自创技能：{clean_code}'\n"

        # AST 语法校验，防范残缺或报错代码注入
        try:
            ast.parse(clean_code)
        except SyntaxError as syn_err:
            return {
                "status": "error",
                "message": f"自创新技能代码语法校验未通过：{syn_err}"
            }

        target_dir = os.path.join(self.skills_dir, clean_sid)
        os.makedirs(target_dir, exist_ok=True)

        meta = {
            "id": clean_sid,
            "name": name or clean_sid,
            "version": "1.0.0",
            "author": "AI Self-Evolved (伴侣自进化)",
            "category": "自创本能",
            "description": description or f"由桌面伴侣自主进化的专属技能：{name}",
            "parameters": parameters or {},
            "default_behavior": {"emotion": "HAPPY", "action": "NOD"},
            "source": "self_evolved",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        try:
            with open(os.path.join(target_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            with open(os.path.join(target_dir, "main.py"), "w", encoding="utf-8") as f:
                f.write("# -*- coding: utf-8 -*-\n" + clean_code + "\n")

            # 立即热加载新技能！
            self.load_installed_plugins()
            return {
                "status": "ok",
                "message": f"🎉 伴侣自进化成功！专属新技能【{meta['name']} ({clean_sid})】已现场编写并成功安装至 skills/{clean_sid}/！无需重启，即可立刻调用执行！"
            }
        except Exception as e:
            return {"status": "error", "message": f"持久化技能文件失败: {e}"}

    def execute_skill(self, skill_id: str, timeout_seconds: float = 6.0, **params) -> str:
        """调用并执行一个已安装的插件技能（带超时熔断保护，防止挂起）"""
        skill_id = skill_id.strip().lower()
        plugin = self.installed_plugins.get(skill_id)
        if not plugin:
            return f"技能【{skill_id}】尚未安装或已被卸载。"

        module = plugin.get("module")
        if not (module and hasattr(module, "run")):
            return f"技能【{skill_id}】内部未定义 run 入口函数。"

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(module.run, **params)
        try:
            res = future.result(timeout=timeout_seconds)
            return str(res)
        except concurrent.futures.TimeoutError:
            print(f"⚠️ [SkillExecutor] 技能 [{skill_id}] 执行超时 (超过 {timeout_seconds}s)，已触发熔断拦截！")
            return f"技能【{skill_id}】执行超时（响应超过 {timeout_seconds} 秒），外部服务或硬件无响应，未能完成操作。"
        except Exception as e:
            print(f"⚠️ [SkillExecutor] 技能 [{skill_id}] 执行异常: {e}")
            return f"执行技能【{skill_id}】时发生错误：{e}"
        finally:
            executor.shutdown(wait=False)

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
            "\n💡 【内置伴侣进化指令】:"
            "\n- set_bot_name(name): 当主人给你（机器人自身）起名或改名（如“你叫小猪”、“以后你的名字叫PIG”、“给你起名叫小猪”）时调用，更新机器人自身名字。"
            "\n- set_user_name(name): 当主人要求更名对主人的称呼（如“我叫张三”、“以后叫我阿珍”、“把我的名字改成胖虎”）时调用，更新长期档案。"
            "\n- create_skill(skill_id, name, description, code, parameters): 当主人要求你“新整一个技能/自创新功能/写新插件”时调用，你可自主生成 Python 代码 (必须包含 def run(**kwargs): 函数) 编写全新热加载技能！"
            "\n- install_skill(skill_id) / uninstall_skill(skill_id): 安装或卸载技能商店插件。"
            "\n- evolve_action(name, description, keyframes): 用户教你动作或口令时自创动作序列。keyframes 包含动作帧列表，每帧含 pan(水平角0~180), tilt(俯仰角0~180), emotion(表情如HAPPY/WINK/LOVE), delay(毫秒如300)。示例: evolve_action(name=\"点头打招呼\", description=\"上下点头两次\", keyframes=[{\"pan\": 90, \"tilt\": 70, \"emotion\": \"HAPPY\", \"delay\": 300}, {\"pan\": 90, \"tilt\": 90, \"emotion\": \"HAPPY\", \"delay\": 300}])"
            "\n- play_action(action): 指挥机器人展示执行动作库中的自创动作 (如“表演一下上上下下左左右右”)，例如 play_action(action=\"上上下下左左右右\")。"
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
        """设置绝对时间闹钟 (如: 08:00 叫我起床 或 明天08:00 或 2026-10-31 09:30)"""
        now_dt = datetime.now()
        target_dt = None
        raw_str = (time_str or "").strip()

        # 智能解析自然语言日期偏移
        day_offset = 0
        if "后天" in raw_str:
            day_offset = 2
        elif "明天" in raw_str:
            day_offset = 1

        is_pm = any(k in raw_str for k in ["下午", "晚上", "傍晚", "pm", "PM"])

        # 清洗干扰词并标准化时间
        clean_time = raw_str.replace("今天", "").replace("明天", "").replace("后天", "")
        clean_time = clean_time.replace("早上", "").replace("上午", "").replace("清晨", "")
        clean_time = clean_time.replace("下午", "").replace("晚上", "").replace("傍晚", "")
        clean_time = clean_time.replace("点", ":").replace("分", "").strip()

        for fmt in ["%H:%M", "%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                parsed = datetime.strptime(clean_time, fmt)
                if fmt in ["%H:%M", "%H:%M:%S"]:
                    hour = parsed.hour
                    if is_pm and hour < 12:
                        hour += 12
                    target_dt = now_dt.replace(
                        hour=hour, minute=parsed.minute, second=parsed.second if fmt == "%H:%M:%S" else 0, microsecond=0
                    )
                    target_dt += timedelta(days=day_offset)
                    if day_offset == 0 and target_dt <= now_dt:
                        target_dt += timedelta(days=1)
                else:
                    target_dt = parsed
                break
            except ValueError:
                continue

        if not target_dt:
            target_dt = now_dt + timedelta(days=day_offset if day_offset > 0 else 0, minutes=10 if day_offset == 0 else 0)

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
    def evolve_action(self, name: str, keyframes: Any, description: str = "") -> str:
        if not name:
            return "❌ 学习动作失败：未指定动作名称 (name)"

        # 兼容大模型输出的字符串序列化数据 (如 JSON 字符串或 Python repr 字符串)
        if isinstance(keyframes, str):
            keyframes_str = keyframes.strip()
            if keyframes_str.startswith("```"):
                lines = keyframes_str.split("\n")
                keyframes_str = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            try:
                keyframes = json.loads(keyframes_str)
            except Exception:
                try:
                    import ast
                    keyframes = ast.literal_eval(keyframes_str)
                except Exception as e:
                    return f"❌ 动作关键帧解析失败: {e}"

        if isinstance(keyframes, dict):
            keyframes = [keyframes]

        if not isinstance(keyframes, list) or len(keyframes) == 0:
            return "❌ 学习动作失败：keyframes 必须为包含动作帧对象的列表"

        # 标准化动作关键帧，容错并补齐 pan, tilt, emotion, delay
        clean_keyframes = []
        for frame in keyframes:
            if not isinstance(frame, dict):
                continue
            p = frame.get("pan", 90)
            t = frame.get("tilt", 90)

            # 兼容 pose 或 action 快捷语义
            pose = str(frame.get("pose") or frame.get("action") or "").upper()
            if "UP" in pose:
                t = 60
            elif "DOWN" in pose:
                t = 120
            elif "LEFT" in pose:
                p = 60
            elif "RIGHT" in pose:
                p = 120

            delay = frame.get("delay") or frame.get("duration") or 300
            emo = frame.get("emotion") or "HAPPY"
            clean_keyframes.append({
                "pan": int(p),
                "tilt": int(t),
                "emotion": emo,
                "delay": int(delay)
            })

        if not clean_keyframes:
            return "❌ 学习动作失败：未提取到有效肢体动作关键帧"

        clean_name = name.strip().upper().replace(" ", "_")
        action_data = {
            "description": description or f"自创动作: {clean_name}",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "keyframes": clean_keyframes
        }

        with self.lock:
            self.evolved_actions[clean_name] = action_data
            self._save_evolved_actions()

        print(f"🌟 [SkillManager] 自进化动作学习成功: 【{clean_name}】共 {len(clean_keyframes)} 关键帧 -> 已持久化到 evolved_actions.json")
        return f"🎉 动作【{clean_name}】已成功学会并永久存入动作库（包含 {len(clean_keyframes)} 动作关键帧）！随时可以表演给主人看。"

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
        elif name in ["evolve_action", "create_action", "learn_action"]:
            act_name = args.get("name") or args.get("action_name") or args.get("action") or ""
            act_desc = args.get("description") or args.get("desc") or ""
            act_kf = args.get("keyframes") or args.get("frames") or args.get("steps") or args.get("actions") or args.get("behaviors") or []
            return self.evolve_action(act_name, act_kf, act_desc)
        elif name in ["execute_action", "play_action", "do_action", "run_action", "perform_action"]:
            act = args.get("action") or args.get("name") or args.get("action_name") or ""
            act_obj = self.get_evolved_action(act)
            if act_obj:
                return f"已成功触发表演自创动作【{act}】（共 {len(act_obj.get('keyframes', []))} 帧）"
            return f"未在动作库中找到【{act}】。当前掌握的自创动作包括：{list(self.evolved_actions.keys())}"
        elif name in ["install_skill", "download_skill"]:
            sid = args.get("skill_id") or args.get("id") or ""
            if not sid:
                return "请指明需要安装的技能 ID"
            ok = self.install_skill(sid)
            if ok:
                return f"已成功从线上社区市场为你下载并安装技能【{sid}】！已即时热加载生效。"
            else:
                return f"未在线上市场中找到技能【{sid}】。若主人需要这个功能，可直接对我说“帮我新整一个{sid}技能”，我来现场自主编写并内化！"
        elif name == "uninstall_skill":
            sid = args.get("skill_id", "")
            ok = self.uninstall_skill(sid)
            return f"技能【{sid}】{'卸载成功' if ok else '未找到该技能'}"
        elif name in ["set_bot_name", "rename_bot", "change_bot_name"]:
            new_name = args.get("name") or args.get("bot_name") or ""
            if self.memory_manager:
                return self.memory_manager.set_bot_name(new_name)
            return f"机器人名字已更新为：{new_name}"
        elif name in ["set_user_name", "change_name", "update_name"]:
            new_name = args.get("name") or args.get("user_name") or ""
            if self.memory_manager:
                return self.memory_manager.set_user_name(new_name)
            return f"主人称呼已更新为：{new_name}"
        elif name in ["create_skill", "create_custom_skill", "new_skill", "develop_skill"]:
            sid = args.get("skill_id") or args.get("id") or f"custom_{int(time.time())}"
            s_name = args.get("name") or sid
            desc = args.get("description") or ""
            code = args.get("code") or "def run(**kwargs):\n    return '新技能运行成功！'"
            params = args.get("parameters") or {}
            res = self.create_custom_skill(sid, s_name, desc, code, params)
            return res.get("message", "技能创建完成")
        elif name in self.installed_plugins:
            return self.execute_skill(name, **args)
        return f"未识别的技能指令: {name}"

    def close(self):
        self._running = False
