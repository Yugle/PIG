# -*- coding: utf-8 -*-
"""
小猪系统设置与个性化调节技能 (skills/system_config/main.py)
支持语音动态调整系统参数、开关硬件外设、个性化更名。
"""

import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def _load_config():
    cfg_path = os.path.join(PROJECT_ROOT, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_config(cfg):
    cfg_path = os.path.join(PROJECT_ROOT, "config.json")
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ [system_config] 保存 config.json 失败: {e}")
        return False

def _update_memory(key, value):
    mem_path = os.path.join(PROJECT_ROOT, "data", "memory.json")
    if os.path.exists(mem_path):
        try:
            with open(mem_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data[key] = str(value).strip()
            with open(mem_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [system_config] 更新 memory.json 失败: {e}")

def run(action="query", value="", **kwargs):
    cfg = _load_config()
    act = (action or "query").lower().strip()
    val_str = str(value or "").strip().lower()

    # 1. 人脸视线追踪开关
    if "face" in act or "track" in act or "注视" in act or "人脸" in act or "摄像头" in act:
        enable = val_str in ["true", "1", "on", "yes", "开启", "打开", "开"]
        cfg["face_tracking"] = enable
        _save_config(cfg)
        status_text = "开启" if enable else "关闭"
        return {
            "status": "success",
            "message": f"👁️ 已经为你{status_text}人脸注视追踪啦！{'我现在会一直看着你哦～' if enable else '摄像头已暂停追踪，你可以安心忙别的事。'}",
            "behavior": {"emotion": "LOVE" if enable else "NORMAL", "action": "TILT_HEAD" if enable else "NOD"}
        }

    # 2. 主动关怀开关
    if "care" in act or "关怀" in act or "打扰" in act:
        enable = val_str in ["true", "1", "on", "yes", "开启", "打开", "开"]
        cfg["proactive_care"] = enable
        _save_config(cfg)
        from core.proactive_care import ProactiveCareManager
        pcm = ProactiveCareManager(storage_dir=os.path.join(PROJECT_ROOT, "data"))
        pcm.set_enabled(enable)
        status_text = "开启" if enable else "关闭"
        return {
            "status": "success",
            "message": f"❤️ 主动关怀守护已经为你{status_text}！{'小猪会按时提醒你喝水休息～' if enable else '小猪已保持安静，只在唤醒时回应你。'}",
            "behavior": {"emotion": "HAPPY" if enable else "NORMAL", "action": "NOD"}
        }

    # 3. 设置主人称呼
    if "user" in act or "master" in act or "主人" in act or "称呼" in act:
        new_name = str(value or "").strip()
        if not new_name:
            new_name = "胖虎"
        cfg["user_name"] = new_name
        _save_config(cfg)
        _update_memory("user_name", new_name)
        return {
            "status": "success",
            "message": f"好嘞！以后我就称呼你为「{new_name}」啦，已经深深记在海马体长期记忆里了！",
            "behavior": {"emotion": "HAPPY", "action": "SHAKE_HEAD"}
        }

    # 4. 设置机器人名字
    if "bot" in act or "robot" in act or "名字" in act or "小猪" in act:
        new_name = str(value or "").strip()
        if not new_name:
            new_name = "小猪"
        cfg["robot_name"] = new_name
        _save_config(cfg)
        _update_memory("bot_name", new_name)
        return {
            "status": "success",
            "message": f"收到！从现在起我的名字就是「{new_name}」啦，请多多指教呀！",
            "behavior": {"emotion": "LOVE", "action": "TILT_HEAD"}
        }

    # 5. 设置非语言萌趣音效
    if "sound" in act or "audio" in act or "音效" in act or "呼噜" in act:
        enable = val_str in ["true", "1", "on", "yes", "开启", "打开", "开"]
        cfg["non_verbal_audio"] = enable
        _save_config(cfg)
        status_text = "开启" if enable else "关闭"
        return {
            "status": "success",
            "message": f"🎶 萌趣非语言音效已{status_text}！{'在交互时小猪会发出各种可爱的呼噜与轻鸣声~' if enable else '已进入静音无音效模式。'}",
            "behavior": {"emotion": "HAPPY" if enable else "NORMAL", "action": "NOD"}
        }

    # 默认：查询当前配置状态
    ft_state = "开启" if cfg.get("face_tracking", False) else "关闭"
    care_state = "开启" if cfg.get("proactive_care", True) else "关闭"
    u_name = cfg.get("user_name", "胖虎")
    b_name = cfg.get("robot_name", "小猪")

    return {
        "status": "success",
        "message": f"报告主人！当前我的名字是「{b_name}」，主人称呼是「{u_name}」；人脸注视追踪目前处于【{ft_state}】状态，主动关怀守护处于【{care_state}】状态。",
        "behavior": {"emotion": "NORMAL", "action": "NOD"}
    }

execute = run
