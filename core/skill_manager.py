"""
PIG 机器人 - 统一技能与插件中枢 (skill_manager.py)
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

# 默认 Gitee 线上开源社区技能集散中心 (Online Skills Registry)
DEFAULT_ONLINE_REGISTRY_URL = "https://gitee.com/yugle159357/PIG/raw/main/registry/skills_registry.json"

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
        self.last_triggered_reminder: Optional[Dict[str, Any]] = None
        self.register_face_callback: Optional[Callable[[], Any]] = None
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
        从 Gitee 线上开源社区 Registry 真实获取技能市场清单。
        纯在线真实拉取：如果网络连不上或服务器异常，严格返回空字典 {}，绝不使用离线假数据。
        """
        now = time.time()
        if not force_refresh and self._online_registry_cache is not None and (now - self._online_registry_last_fetch < 60):
            return self._online_registry_cache

        import requests
        try:
            resp = requests.get(self.registry_url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                skills = data.get("skills", {})
                if isinstance(skills, list):
                    skills = {s["id"]: s for s in skills if "id" in s}
                if isinstance(skills, dict):
                    self._online_registry_cache = skills
                    self._online_registry_last_fetch = now
                    return self._online_registry_cache
            else:
                print(f"⚠️ [OnlineRegistry] Gitee 仓库返回 HTTP {resp.status_code}")
        except Exception as e:
            print(f"⚠️ [OnlineRegistry] 从 Gitee 开源社区拉取失败 ({self.registry_url}): {e}")

        # 纯在线模式：连不上直接置空，绝不使用离线假数据
        self._online_registry_cache = {}
        self._online_registry_last_fetch = now
        return {}

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

    def _is_protected_plugin(self, meta: Dict[str, Any]) -> bool:
        return bool(meta.get("protected", False) or meta.get("builtin", False))

    def resolve_plugin_id(self, name: str) -> Optional[str]:
        """根据精确 ID 或各插件 skill.json 中声明的 aliases 解析插件 ID"""
        raw = (name or "").strip()
        if not raw:
            return None
        if raw in self.installed_plugins:
            return raw
        low = raw.lower()
        with self.lock:
            plugins_snapshot = list(self.installed_plugins.items())
        for sid, obj in plugins_snapshot:
            if low == sid.lower():
                return sid
        for sid, obj in plugins_snapshot:
            meta = obj.get("meta") or {}
            for alias in (meta.get("aliases") or []):
                if str(alias).lower() in low:
                    return sid
        return None

    def resolve_plugin_meta(self, name: str) -> Dict[str, Any]:
        sid = self.resolve_plugin_id(name)
        if not sid:
            return {}
        with self.lock:
            obj = self.installed_plugins.get(sid)
            return dict(obj.get("meta", {})) if obj else {}

    def emit_hook(self, hook_name: str, **kwargs) -> List[Any]:
        """向所有声明了 hook_name 回调函数的已安装插件广播生命周期事件"""
        results: List[Any] = []
        with self.lock:
            plugins_snapshot = list(self.installed_plugins.items())
        for sid, obj in plugins_snapshot:
            fn = getattr(obj.get("module"), hook_name, None)
            if callable(fn):
                try:
                    res = fn(**kwargs)
                    if res is not None:
                        results.append(res)
                except Exception as e:
                    print(f"⚠️ [SkillHook] 插件 [{sid}] 执行 {hook_name} 异常: {e}")
        return results

    def invoke_plugin_hook(self, skill_id: str, hook_name: str, **kwargs) -> Any:
        """调用指定插件模块上的生命周期钩子函数"""
        with self.lock:
            obj = self.installed_plugins.get(skill_id)
        if not obj:
            return None
        fn = getattr(obj.get("module"), hook_name, None)
        if callable(fn):
            try:
                return fn(**kwargs)
            except Exception as e:
                print(f"⚠️ [SkillHook] 插件 [{skill_id}].{hook_name} 异常: {e}")
        return None

    def dispatch_http(self, method: str, path: str, handler: Any, ctx: Optional[Dict[str, Any]] = None) -> bool:
        """按插件声明的 http_prefixes 分发 HTTP 请求到对应插件的 handle_http 钩子"""
        req_path = (path or "").split("?", 1)[0]
        with self.lock:
            plugins_snapshot = list(self.installed_plugins.items())
        for sid, obj in plugins_snapshot:
            meta = obj.get("meta") or {}
            prefixes = meta.get("http_prefixes") or ([] if not meta.get("http_prefix") else [meta["http_prefix"]])
            matched = any(
                req_path == p or req_path.startswith(p.rstrip("/") + "/")
                for p in prefixes if isinstance(p, str) and p
            )
            if not matched:
                continue
            fn = getattr(obj.get("module"), "handle_http", None)
            if callable(fn):
                try:
                    handled = fn(method=method, path=path, handler=handler, ctx=ctx or {})
                    if handled:
                        return True
                except Exception as e:
                    print(f"⚠️ [SkillHTTP] 插件 [{sid}] 处理 {method} {req_path} 异常: {e}")
                    try:
                        handler.send_error(500, str(e))
                    except Exception:
                        pass
                    return True
        return False

    def collect_status_payload(self) -> Dict[str, Any]:
        """聚合所有插件通过 get_status_payload() 上报的运行状态字段"""
        merged: Dict[str, Any] = {}
        for item in self.emit_hook("get_status_payload"):
            if isinstance(item, dict):
                merged.update(item)
        return merged

    def collect_screen_items(self) -> List[Dict[str, Any]]:
        """聚合所有声明了 get_screen_item() 的插件卡片，供 2.4 寸屏幕菜单统一展示"""
        items: List[Dict[str, Any]] = []
        with self.lock:
            plugins_snapshot = list(self.installed_plugins.items())
        for sid, obj in plugins_snapshot:
            fn = getattr(obj.get("module"), "get_screen_item", None)
            if callable(fn):
                try:
                    entry = fn()
                    if isinstance(entry, dict) and entry.get("name"):
                        entry = dict(entry)
                        entry.setdefault("skill_id", sid)
                        entry.setdefault("screen_order", 50)
                        items.append(entry)
                except Exception as e:
                    print(f"⚠️ [SkillScreen] 插件 [{sid}].get_screen_item 异常: {e}")
        items.sort(key=lambda x: int(x.get("screen_order", 50)))
        return items

    def list_installed_skills(self) -> List[Dict[str, Any]]:
        """获取当前已安装激活的技能列表 (由各技能 skill.json 声明保护与配置能力)"""
        with self.lock:
            res = []
            for sid, obj in self.installed_plugins.items():
                m = dict(obj.get("meta", {}))
                m["id"] = sid
                is_builtin = self._is_protected_plugin(m)
                is_self_evolved = m.get("source") == "self_evolved" or "自进化" in str(m.get("author", ""))
                m["is_builtin"] = is_builtin
                m["is_self_evolved"] = is_self_evolved
                m["skill_type"] = "builtin" if is_builtin else ("self_evolved" if is_self_evolved else "community")
                m["type_label"] = "官方内置本能" if is_builtin else ("✨ AI自创本能" if is_self_evolved else "🌐 社区扩展")
                m["can_uninstall"] = not is_builtin
                m["has_config"] = bool(isinstance(m.get("config_schema"), dict))
                res.append(m)
            return res

    def list_configurable_skills(self, cur_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """返回所有声明了 config_schema 的已安装技能及当前生效配置值"""
        items: List[Dict[str, Any]] = []
        skills_cfg = cur_cfg.get("skills_config") if isinstance(cur_cfg.get("skills_config"), dict) else {}
        with self.lock:
            plugins_snapshot = list(self.installed_plugins.items())
        for sid, obj in plugins_snapshot:
            meta = dict(obj.get("meta", {}))
            schema = meta.get("config_schema")
            if not isinstance(schema, dict):
                continue
            section = schema.get("config_section") or f"skills_config.{sid}"
            if section == "root":
                sec_data = cur_cfg
            elif section.startswith("skills_config."):
                sub_key = section.split(".", 1)[1]
                sec_data = skills_cfg.get(sub_key, {}) if isinstance(skills_cfg.get(sub_key), dict) else {}
            elif section == "none":
                sec_data = {}
            else:
                sec_data = cur_cfg.get(section, {}) if isinstance(cur_cfg.get(section), dict) else {}

            values: Dict[str, Any] = {}
            for field in schema.get("fields", []):
                fk = field.get("key")
                if fk:
                    values[fk] = sec_data.get(fk, field.get("default", ""))

            items.append({
                "id": sid,
                "name": meta.get("name", sid),
                "category": meta.get("category", "综合"),
                "description": meta.get("description", ""),
                "schema": schema,
                "values": values,
            })
        return items

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

        # 从 Gitee 在线社区远程下载 main.py 代码入口
        code = ""
        code_url = item.get("code_url")
        if code_url:
            try:
                import requests
                r = requests.get(code_url, timeout=8)
                if r.status_code == 200 and len(r.text) > 10:
                    code = r.text
            except Exception as e:
                print(f"⚠️ [OnlineRegistry] 从 Gitee 社区 ({code_url}) 下载代码失败: {e}")

        if not code:
            code = item.get("code_template", "")

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
        meta = self.resolve_plugin_meta(skill_id)
        if self._is_protected_plugin(meta):
            print(f"🔒 [SkillStore] 核心系统技能 [{skill_id}] 受系统级保护，禁止卸载")
            return False
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

    # ------------------ PIG 自进化现场编写 (LLM Self-Evolution) ------------------
    def create_custom_skill(self, skill_id: str, name: str, description: str, code: str, parameters: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        方案3：PIG 自我编码自进化，现场动态编写并热加载一个全新的 Python 技能
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
            "author": "AI Self-Evolved (PIG 自进化)",
            "category": "自创本能",
            "description": description or f"由桌面PIG 自主进化的专属技能：{name}",
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
                "message": f"🎉 PIG 自进化成功！专属新技能【{meta['name']} ({clean_sid})】已现场编写并成功安装至 skills/{clean_sid}/！无需重启，即可立刻调用执行！"
            }
        except Exception as e:
            return {"status": "error", "message": f"持久化技能文件失败: {e}"}

    def execute_skill(self, skill_id: str, timeout_seconds: float = 12.0, **params) -> str:
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
        """生成精简且高信息密度的技能清单与调用规范，控制 System Prompt Token 开销"""
        installed = self.list_installed_skills()
        if not installed:
            return ""

        lines = ["\n【可用技能插件】:"]
        for s in installed:
            sid = s["id"]
            desc = (s.get("prompt_desc") or s.get("description") or "").replace("\n", " ").strip()
            # 精简超长描述（保留关键调用约定前 80 字，若含重要参数提示如 action=/device= 则保留至覆盖提示）
            if len(desc) > 78:
                has_hint = ("action=" in desc or "device=" in desc)
                cutoff = desc.rfind("。", 0, 82)
                if cutoff >= 28 and "action=" not in desc[cutoff:] and "device=" not in desc[cutoff:]:
                    desc = desc[:cutoff + 1]
                elif has_hint:
                    if len(desc) > 135:
                        desc = desc[:135].rstrip("，； ") + "…"
                else:
                    desc = desc[:76].rstrip("，； ") + "…"
            params = s.get("parameters", {})
            param_str = ", ".join(params.keys())
            lines.append(f"- {sid}({param_str}): {desc}")

        lines.append(
            "\n【内置指令】:"
            "\n- set_bot_name(name): 改机器人自己的名字。"
            "\n- set_user_name(name): 改对主人的称呼。"
            "\n- create_skill(skill_id, name, description, code, parameters): 自创热加载技能 (code 须含 def run(**kwargs))。"
            "\n- install_skill(skill_id) / uninstall_skill(skill_id): 安装或卸载插件。"
            "\n- evolve_action(name, description, keyframes): 自创动作序列，keyframes 为 [{\"pan\":0~180,\"tilt\":0~180,\"emotion\":\"HAPPY|WINK|LOVE\",\"delay\":300}]。"
            "\n- play_action(action): 执行自创动作库里的动作。"
            "\n- register_master_face(): 拍照录入主人人脸特征。"
        )
        return "\n".join(lines)

    # ---------------- 闹钟与日程提醒 (Alarms & Timers) ----------------
    def register_trigger_callback(self, cb: Callable[[Dict[str, Any]], None]):
        """注册闹钟/提醒触发时的抢占回调函数"""
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

    def snooze_reminder(self, reminder_id: str = "", minutes: float = 5.0, content: str = "") -> Optional[Dict[str, Any]]:
        """将刚响过的或指定的闹钟延后 N 分钟稍后再响（不影响 daily 原重复周期）"""
        mins = max(0.05, float(minutes or 5.0))
        target_ts = time.time() + mins * 60.0
        target_dt = datetime.fromtimestamp(target_ts)

        with self.lock:
            source = None
            rid = (reminder_id or "").strip()
            kw = (content or "").strip()
            if rid:
                for r in reversed(self.reminders):
                    if r.get("id") == rid:
                        source = r
                        break
            if source is None and kw:
                for r in reversed(self.reminders):
                    if kw in str(r.get("content", "")):
                        source = r
                        break
            if source is None and self.last_triggered_reminder:
                source = self.last_triggered_reminder
            if source is None:
                completed = [r for r in self.reminders if r.get("completed", False)]
                if completed:
                    completed.sort(key=lambda x: x.get("target_time", 0))
                    source = completed[-1]

            if source is None and not kw:
                return None

            base_content = (source.get("content") if source else None) or kw or "日程提醒"
            prev_count = int((source or {}).get("snooze_count", 0) or 0)
            snooze_item = {
                "id": f"snooze_{uuid.uuid4().hex[:6]}",
                "type": "alarm",
                "content": base_content,
                "target_time": target_ts,
                "target_time_str": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "display_str": f"稍后再响 [{target_dt.strftime('%H:%M')}]: {base_content}",
                "repeat": "none",
                "is_snooze": True,
                "snooze_count": prev_count + 1,
                "completed": False,
                "created_at": time.time(),
            }
            self.reminders.append(snooze_item)
            self._save_reminders()

        print(f"💤 [SkillManager] 闹钟已贪睡延后 {mins:g} 分钟: {snooze_item['display_str']}")
        return snooze_item

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
                        self.last_triggered_reminder = dict(r)
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
        elif name in ["snooze_alarm", "snooze_reminder"]:
            return self.snooze_reminder(
                args.get("id", "") or args.get("keyword", ""),
                args.get("minutes", 5),
                args.get("content", ""),
            )
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
        elif name in ["register_master_face", "register_face", "record_face", "bind_master_face", "face_id"]:
            if hasattr(self, "register_face_callback") and self.register_face_callback:
                res = self.register_face_callback()
                if isinstance(res, dict) and res.get("status") == "ok":
                    return "👑 主人面容 128 维特征已成功录入并持久化！以后我只要一眼看到您，就能立刻认出是主人！"
                elif isinstance(res, dict) and res.get("message"):
                    return f"人脸录入未完成：{res.get('message')}，请正对摄像头保持不动再试一次~"
                return str(res)
            return "已触发人脸录入流程，请正视摄像头保持静止。"
        elif name in ["create_skill", "create_custom_skill", "new_skill", "develop_skill"]:
            sid = args.get("skill_id") or args.get("id") or f"custom_{int(time.time())}"
            s_name = args.get("name") or sid
            desc = args.get("description") or ""
            code = args.get("code") or "def run(**kwargs):\n    return '新技能运行成功！'"
            params = args.get("parameters") or {}
            res = self.create_custom_skill(sid, s_name, desc, code, params)
            return res.get("message", "技能创建完成")
        else:
            sid = self.resolve_plugin_id(name)
            if sid:
                meta = self.installed_plugins[sid].get("meta") or {}
                dq = meta.get("default_query_args")
                if isinstance(dq, dict):
                    trigs = [str(t).lower() for t in (dq.get("triggers") or [])]
                    low_name = name.lower()
                    low_args = str(args).lower()
                    if not args or any(t in low_name or t in low_args for t in trigs):
                        for k, v in (dq.get("defaults") or {}).items():
                            args.setdefault(k, v)
                return self.execute_skill(sid, **args)
        return f"未识别的技能指令: {name}"

    def close(self):
        self._running = False
