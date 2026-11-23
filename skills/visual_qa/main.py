# -*- coding: utf-8 -*-
"""
多模态视觉之眼 (Visual QA) 技能插件
基于小米 MiMo-v2.6-Flash 原生全模态大模型与树莓派/网页数字孪生摄像头单帧捕获，
看懂主人眼前的现实世界，回答穿搭、物品、文字与场景问答。
"""

import os
import sys
import json
import base64
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")


def _load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _capture_frame_bytes() -> bytes:
    """按优先级从不同链路获取当前摄像头一帧 JPEG 字节流"""
    # 1. 尝试从 web_sim_server 抓取最新帧 (支持 Web 仿真端网页摄像头或已绑定的 HAL)
    try:
        import web_sim_server
        if hasattr(web_sim_server, "get_latest_vision_frame"):
            frame = web_sim_server.get_latest_vision_frame()
            if frame and len(frame) > 100:
                return frame
    except Exception:
        pass

    # 2. 尝试从 HardwareAgent 获取 HAL 捕获
    try:
        from core.hardware_agent import get_hardware_agent
        agent = get_hardware_agent()
        if agent and agent.hal:
            frame = agent.hal.capture_image()
            if frame and len(frame) > 100:
                return frame
    except Exception:
        pass

    # 3. 尝试直接打开本地 OpenCV V4L2 摄像头 (如树莓派 USB 免驱裸板 /dev/video0)
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            ret, mat = cap.read()
            cap.release()
            if ret and mat is not None:
                success, buf = cv2.imencode(".jpg", mat)
                if success:
                    return buf.tobytes()
    except Exception:
        pass

    return b""


def run(question="请仔细观察这张照片，告诉我你看到了什么？", **kwargs):
    """
    技能入口执行函数
    :param question: 主人对眼前画面的提问 (如 '看看我手里拿的什么', '帮我看看我穿这身好看吗')
    """
    q_text = str(question or "").strip()
    if not q_text:
        q_text = "请仔细观察眼前的画面，告诉我你看到了什么？"

    cfg = _load_config()
    api_key = cfg.get("api_key") or cfg.get("mimo_api_key", "")
    base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
    model = cfg.get("llm_model", "mimo-v2.6-flash")

    if not api_key:
        return "未能启用多模态视觉问答：系统中未配置小米 MiMo API 凭据。"

    # 1. 抓取摄像头画面
    img_bytes = _capture_frame_bytes()
    if not img_bytes:
        return "未能捕获到摄像头画面：请确认树莓派免驱 USB 摄像头已插好，或在 Web 仿真工作台中打开了摄像头预览开关。"

    # 2. 构造 OpenAI 兼容格式的多模态请求
    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    img_url = f"data:image/jpeg;base64,{b64_img}"

    sys_instruction = (
        "你是桌面具身 AI 伴侣机器人小猪。你正在通过机身摄像头观察眼前的画面。\n"
        "请结合主人的提问，以自然、亲切、生动且口语化的中文（40到90字以内）向主人清晰描述你看到的内容。\n"
        "回答要像真正陪伴在主人身边的伴侣一样直接交流，严禁输出'在这张图片中'、'从照片来看'等机械生硬的套话！"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": q_text},
                    {"type": "image_url", "image_url": {"url": img_url}}
                ]
            }
        ],
        "max_tokens": 200
    }

    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            res_data = resp.json()
            choices = res_data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return f"【小猪视觉观察】{answer}"
            return "小猪眨了眨眼，暂时没能看清眼前的细节，可以把物品拿近一点让我再看一次哦～"
        else:
            return f"视觉多模态分析未成功：云端服务返回状态码 {resp.status_code} ({resp.text[:80]})"
    except Exception as e:
        return f"视觉多模态大模型通信异常：{e}"
