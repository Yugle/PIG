# -*- coding: utf-8 -*-
"""
米家全生态百万设备通用物模型驱动引擎 (Universal MIoT Spec Engine)
基于小米官方开放 MIoT Spec 规范 (miot-spec-v2)，彻底告别“用户说一个设备代码硬编码一个”的落后模式。
支持对任意米家设备（灯具、空调、扫地机、水壶、饮水机、风扇、净化器、窗帘、插座等）进行通用语义解析与驱动。
"""

import os
import re
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("MiotSpec")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "miot_specs")
os.makedirs(CACHE_DIR, exist_ok=True)


class MiotSpecManager:
    """
    通用 MIoT 物模型管理与指令解析器
    """
    _cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_spec(cls, spec_type: str, model: str = "") -> Optional[Dict[str, Any]]:
        """获取并缓存指定设备的 MIoT 物模型定义 (本地持久化缓存 + 官方源更新)"""
        if not spec_type:
            return None

        clean_type = spec_type.strip()
        safe_name = (model or clean_type.split(":")[-2] if ":" in clean_type else "generic").replace("/", "_").replace(".", "_")
        cache_file = os.path.join(CACHE_DIR, f"{safe_name}.json")

        # 1. 内存缓存
        if clean_type in cls._cache:
            return cls._cache[clean_type]

        # 2. 本地文件缓存
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cls._cache[clean_type] = data
                    return data
            except Exception as e:
                logger.warning(f"读取本地物模型缓存失败 [{cache_file}]: {e}")

        # 3. 在线向官方 miot-spec.org 查询
        url = f"https://miot-spec.org/miot-spec-v2/instance?type={clean_type}"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
                spec_data = resp.json()
                cls._cache[clean_type] = spec_data
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(spec_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return spec_data
        except Exception as e:
            logger.warning(f"获取官方 MIoT 物模型失败 [{clean_type}]: {e}")

        return None

    @classmethod
    def parse_universal_intent(cls, spec: Optional[Dict[str, Any]], combined_str: str, did: str, model: str = "") -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
        """
        基于物模型动态语义映射生成标准指令：
        :return: (payload_params, action_call, actions_done)
        """
        payload_params = []
        action_call = None
        actions_done = []
        combined = combined_str.strip()

        # 1. 若无官方物模型（如离线状态），提供高容错标准通用物模型骨架
        if not spec:
            return cls._fallback_standard_spec(combined, did, model)

        # 2. 提取该设备物模型拥有的核心属性和动作字典
        props_by_type = {}
        actions_by_type = {}

        for s in spec.get("services", []):
            siid = s.get("iid")
            s_type = s.get("type", "").lower()
            s_desc = s.get("description", "").lower()
            if "device information" in s_desc:
                continue

            for p in s.get("properties", []):
                piid = p.get("iid")
                p_type = p.get("type", "").lower()
                p_desc = p.get("description", "").lower()
                p_fmt = p.get("format", "")
                p_rng = p.get("value-range")

                info = {"siid": siid, "piid": piid, "format": p_fmt, "range": p_rng, "desc": p_desc}

                # 常用语义槽自动分类
                if ":on:" in p_type or "switch status" in p_desc or (p_desc == "switch" and p_fmt == "bool"):
                    props_by_type.setdefault("switch", []).append(info)
                elif ":brightness:" in p_type or "brightness" in p_desc:
                    props_by_type["brightness"] = info
                elif ":color-temperature:" in p_type or "color temperature" in p_desc or "色温" in p_desc:
                    props_by_type["color_temp"] = info
                elif "target-temperature" in p_type or "target temperature" in p_desc or "水温" in p_desc or "目标温度" in p_desc:
                    props_by_type["target_temp"] = info
                elif ":fan-level:" in p_type or "fan level" in p_desc or "speed" in p_desc or "风速" in p_desc or "档位" in p_desc:
                    props_by_type["fan_level"] = info
                elif ":mode:" in p_type or p_desc == "mode":
                    props_by_type.setdefault("mode", info)

            for a in s.get("actions", []):
                aiid = a.get("iid")
                a_type = a.get("type", "").lower()
                a_desc = a.get("description", "").lower()
                act_info = {"siid": siid, "aiid": aiid, "desc": a.get("description", "")}

                if "start-sweep" in a_type or "start sweep" in a_desc or "扫" in a_desc:
                    actions_by_type["start_sweep"] = act_info
                elif "stop-sweep" in a_type or "stop sweep" in a_desc or "停止清扫" in a_desc:
                    actions_by_type["stop_sweep"] = act_info
                elif "start-charge" in a_type or "charge" in a_desc or "回充" in a_desc:
                    actions_by_type["start_charge"] = act_info
                elif "toggle" in a_type or "toggle" in a_desc:
                    actions_by_type["toggle"] = act_info

        # 3. 语义解析意图与参数匹配 (中英文与常见编程风格指令兼容)
        combined_lower = combined.lower()
        is_turn_off = any(w in combined_lower for w in ["关", "关闭", "熄灭", "断开", "停止", "off", "close", "turn_off", "turn-off", "power_off"]) and not any(w in combined for w in ["不关", "别关"])
        is_turn_on = any(w in combined_lower for w in ["开", "打开", "启动", "开启", "运行", "点亮", "开灯", "on", "open", "turn_on", "turn-on", "power_on"])

        # A. 动作类设备 (如扫地机)
        if any(w in combined_lower for w in ["回充", "基站", "充电", "回家", "charge", "dock", "start_charge"]) and "start_charge" in actions_by_type:
            target_act = actions_by_type["start_charge"]
            action_call = {"siid": target_act["siid"], "aiid": target_act["aiid"], "desc": "返回充电基站", "lan_cmd": "app_charge"}
            actions_done.append("返回充电基站")
            return payload_params, action_call, actions_done

        if any(w in combined_lower for w in ["停", "暂停", "别扫", "别擦", "stop", "pause", "stop_sweep"]) and "stop_sweep" in actions_by_type:
            target_act = actions_by_type["stop_sweep"]
            action_call = {"siid": target_act["siid"], "aiid": target_act["aiid"], "desc": "暂停清扫", "lan_cmd": "app_stop"}
            actions_done.append("暂停清扫")
            return payload_params, action_call, actions_done

        if any(w in combined_lower for w in ["扫", "开始扫地", "扫地", "清洁", "拖地", "扫拖", "sweep", "clean", "start_sweep"]) and "start_sweep" in actions_by_type:
            target_act = actions_by_type["start_sweep"]
            action_call = {"siid": target_act["siid"], "aiid": target_act["aiid"], "desc": "开始全屋清扫", "lan_cmd": "app_start"}
            actions_done.append("开始全屋清扫")
            return payload_params, action_call, actions_done

        # B. 亮度调节 (若设备支持 brightness)
        br_val = None
        if "brightness" in props_by_type:
            b_info = props_by_type["brightness"]
            has_br_intent = any(w in combined_lower for w in ["亮度", "调亮", "调暗", "亮一点", "暗一点", "最亮", "最暗", "%", "brightness", "bright", "level", "dim"])
            if has_br_intent:
                if "最亮" in combined:
                    br_val = 100
                elif "最暗" in combined:
                    br_val = 10
                elif "亮一点" in combined or "调亮" in combined:
                    br_val = 80
                elif "暗一点" in combined or "调暗" in combined:
                    br_val = 25
                else:
                    m = (
                        re.search(r"(?:brightness|bright|level|亮度)\s*[:=\s]?\s*(\d{1,3})", combined_lower) or
                        re.search(r"(\d{1,3})\s*%", combined_lower) or
                        re.search(r"调[到为成]\s*(\d{1,3})", combined) or
                        re.search(r"(\d{1,3})", combined)
                    )
                    if m:
                        br_val = int(m.group(1))
            elif re.search(r"(\d{1,3})\s*%", combined):
                br_val = int(re.search(r"(\d{1,3})\s*%", combined).group(1))

            if br_val is not None:
                rng = b_info.get("range") or [1, 100]
                br_val = max(int(rng[0]), min(int(rng[1]), br_val))
                payload_params.append({"did": str(did), "siid": b_info["siid"], "piid": b_info["piid"], "value": br_val})
                actions_done.append(f"亮度设为 {br_val}%")

        # C. 色温调节 (若设备支持 color_temp)
        ct_val = None
        if "color_temp" in props_by_type:
            c_info = props_by_type["color_temp"]
            has_ct_intent = any(w in combined_lower for w in ["色温", "暖光", "黄光", "冷光", "白光", "阅读", "温馨", "护眼", "自然", "color_temp", "color-temp", "colortemp", "cct", "kelvin", "warm", "cool"])
            if has_ct_intent:
                m_k = (
                    re.search(r"(?:color_temp|cct|kelvin|色温)\s*[:=\s]?\s*(\d{4})", combined_lower) or
                    re.search(r"(\d{4})", combined)
                )
                if m_k:
                    ct_val = int(m_k.group(1))
                elif any(w in combined for w in ["暖", "黄", "温馨", "夜间", "warm"]):
                    ct_val = 2700
                elif any(w in combined for w in ["冷", "白", "cool"]):
                    ct_val = 4800
                elif any(w in combined for w in ["阅读", "办公", "护眼", "自然"]):
                    ct_val = 4000

            if ct_val is not None:
                rng = c_info.get("range") or [2600, 5000]
                ct_val = max(int(rng[0]), min(int(rng[1]), ct_val))
                payload_params.append({"did": str(did), "siid": c_info["siid"], "piid": c_info["piid"], "value": ct_val})
                desc = "暖光" if ct_val < 3500 else ("冷光" if ct_val > 4500 else "自然光")
                actions_done.append(f"色温设为 {ct_val}K ({desc})")

        # D. 目标温度调节 (空调、电水壶、电暖器、热水器等)
        if "target_temp" in props_by_type:
            t_info = props_by_type["target_temp"]
            if any(w in combined for w in ["温度", "度", "水温", "设定温度", "保温", "°c", "°C", "c"]):
                m_t = re.search(r"(\d{1,2}(?:\.\d)?)\s*(?:度|°c|°C|c)?", combined)
                if m_t:
                    t_val = float(m_t.group(1))
                    rng = t_info.get("range") or [16, 32]
                    t_val = max(float(rng[0]), min(float(rng[1]), t_val))
                    val_out = int(t_val) if t_info.get("format") in ["uint8", "uint16", "int32"] else round(t_val, 1)
                    payload_params.append({"did": str(did), "siid": t_info["siid"], "piid": t_info["piid"], "value": val_out})
                    actions_done.append(f"目标温度设为 {val_out}°C")

        # E. 风速调节 (风扇、空调、空气净化器)
        if "fan_level" in props_by_type:
            fl_info = props_by_type["fan_level"]
            if any(w in combined for w in ["风速", "档位", "档", "风力", "强风", "弱风", "中风"]):
                fl_val = 1
                if "强" in combined or "大" in combined or "最" in combined:
                    fl_val = 3
                elif "中" in combined:
                    fl_val = 2
                elif "弱" in combined or "小" in combined:
                    fl_val = 1
                m_fl = re.search(r"(\d)\s*档", combined)
                if m_fl:
                    fl_val = int(m_fl.group(1))
                payload_params.append({"did": str(did), "siid": fl_info["siid"], "piid": fl_info["piid"], "value": fl_val})
                actions_done.append(f"风速设为 {fl_val} 档")

        # F. 开关机逻辑：若设置了参数（如亮度、温度），自动开机；或明确指定开关
        sw_list = props_by_type.get("switch", [])
        if sw_list:
            main_sw = sw_list[0]
            if is_turn_off and not (br_val or ct_val):
                payload_params.append({"did": str(did), "siid": main_sw["siid"], "piid": main_sw["piid"], "value": False})
                actions_done.insert(0, "关闭")
            elif is_turn_on or br_val is not None or ct_val is not None:
                payload_params.insert(0, {"did": str(did), "siid": main_sw["siid"], "piid": main_sw["piid"], "value": True})
                if is_turn_on:
                    actions_done.insert(0, "打开")

        return payload_params, action_call, actions_done

    @classmethod
    def _fallback_standard_spec(cls, combined: str, did: str, model: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
        """当无法连接官方物模型时的标准 MIoT Spec 兜底规则"""
        payload_params = []
        action_call = None
        actions_done = []

        is_off = any(w in combined for w in ["关", "关闭", "断开", "停止", "off", "close"])
        is_on = any(w in combined for w in ["开", "打开", "启动", "开启", "运行", "on", "open"])

        if is_off:
            payload_params.append({"did": str(did), "siid": 2, "piid": 1, "value": False})
            actions_done.append("关闭")
        elif is_on:
            payload_params.append({"did": str(did), "siid": 2, "piid": 1, "value": True})
            actions_done.append("打开")

        return payload_params, action_call, actions_done
