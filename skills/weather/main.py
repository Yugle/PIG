# -*- coding: utf-8 -*-
"""
实时天气与自动定位关怀技能 (skills/weather/main.py)
特性：
1. 城市自动感知：当未指定城市时，优先结合用户记忆与 IP 精准自动定位所在城市。
2. 气象语义汉化：支持常见英文天气现象自动转换为中文（晴朗、多云、小雨等）。
3. 具身穿衣与出行关怀：根据气温、湿度给出贴心生活关怀。
"""
import os
import json
import re
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_PATH = os.path.join(PROJECT_ROOT, "data", "memory.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

WEATHER_DESC_CN = {
    "sunny": "晴朗",
    "clear": "晴",
    "partly cloudy": "多云",
    "cloudy": "阴天",
    "overcast": "阴天",
    "mist": "薄雾",
    "fog": "大雾",
    "light rain": "小雨",
    "patchy rain possible": "局部可能有小雨",
    "moderate rain": "中雨",
    "heavy rain": "大雨",
    "thunderstorm": "雷阵雨",
    "light snow": "小雪",
    "moderate snow": "中雪",
    "heavy snow": "大雪",
    "blizzard": "暴风雪"
}

def _auto_detect_city():
    # 1. 尝试从长期记忆提取用户城市（若用户曾说过“我在苏州”、“住在青岛”等）
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                mem = json.load(f)
                facts = mem.get("facts", [])
                for fact in facts:
                    m = re.search(r"(?:在|住在|坐标|来自)\s*([\u4e00-\u9fa5]{2,6})", fact)
                    if m:
                        return m.group(1).replace("市", "")
        except Exception:
            pass

    # 2. 尝试从 config.json 提取默认城市配置
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if cfg.get("city") and cfg.get("city") != "auto":
                    return cfg["city"]
        except Exception:
            pass

    # 3. 尝试国内高精度 IP 地理位置检测 (毫秒级)
    try:
        r = requests.get("http://myip.ipip.net", timeout=2)
        if r.status_code == 200:
            m = re.search(r"来自于：\s*(\S+)\s+(\S+)\s+(\S+)", r.text)
            if m:
                cand = m.group(3).replace("市", "").strip()
                if cand:
                    return cand
    except Exception:
        pass

    # 4. 尝试国际 IP 定位回退
    try:
        r = requests.get("http://ip-api.com/json/?lang=zh-CN", timeout=2).json()
        if r.get("city"):
            return r["city"].replace("市", "").strip()
    except Exception:
        pass

    return "北京"

def run(city="auto"):
    target_city = (city or "").strip()
    is_auto = not target_city or target_city.lower() in ["auto", "none", "null", "本地", "当前", "这里", "所在城市", "当地"]

    if is_auto:
        target_city = _auto_detect_city()

    try:
        url = f"https://wttr.in/{target_city}?format=j1"
        res = requests.get(url, timeout=5).json()
        current = res["current_condition"][0]
        raw_desc = current.get("weatherDesc", [{}])[0].get("value", "晴朗")
        desc = WEATHER_DESC_CN.get(raw_desc.lower(), raw_desc)
        temp = current.get("temp_C", "--")
        feels = current.get("FeelsLikeC", temp)
        humidity = current.get("humidity", "--")

        tip = "气温适宜，祝你今天好心情！"
        try:
            t_int = int(temp)
            if t_int < 10:
                tip = "天气较冷，出门记得添厚衣保暖防感冒哦！"
            elif t_int > 28:
                tip = "气温偏热，紫外线较强，出门注意防晒并多喝水防暑。"
        except Exception:
            pass

        return f"{target_city}当前天气：{desc}，实时气温 {temp}°C（体感 {feels}°C），空气湿度 {humidity}%。{tip}"
    except Exception as e:
        return f"查询【{target_city}】天气时网络稍有延迟，不过别担心，出门前记得随时看下窗外～"
