# -*- coding: utf-8 -*-
"""
多模态视觉之眼 (Visual QA) 技能插件
基于小米 MiMo-v2.6-Flash 原生全模态大模型与树莓派/网页数字孪生摄像头单帧捕获，
看懂主人眼前的现实世界，回答穿搭、物品、文字与场景问答。
"""

import os
import sys
import json
import time
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


def _capture_frame_bytes(max_age_sec: float = 12.0) -> tuple[bytes, str]:
    """按优先级从不同链路获取当前摄像头一帧 JPEG 字节流，严格执行 TTL 时效校验，返回 (img_bytes, err_msg)"""
    now = time.time()
    tmp_path = "/tmp/pig_latest_frame.jpg"

    # 0. 先主动检测并清理超过 TTL 的陈旧临时桥接文件
    if os.path.exists(tmp_path):
        try:
            mtime = os.path.getmtime(tmp_path)
            if now - mtime > max_age_sec:
                os.remove(tmp_path)
        except Exception:
            pass

    # 1. 优先检查主模块内存中的实时画面帧 (get_latest_vision_frame 严格校验 TTL)
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "get_latest_vision_frame"):
        try:
            frame = main_mod.get_latest_vision_frame(max_age_sec=max_age_sec)
            if frame and len(frame) > 100:
                return frame, ""
        except Exception:
            pass

    # 2. 检查实时帧桥接文件 (/tmp/pig_latest_frame.jpg)，必须在 TTL 窗口期内
    if os.path.exists(tmp_path):
        try:
            mtime = os.path.getmtime(tmp_path)
            if now - mtime <= max_age_sec:
                with open(tmp_path, "rb") as f:
                    content = f.read()
                    if len(content) > 100:
                        return content, ""
            else:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            pass

    # 3. 尝试从 HardwareAgent 获取物理真机 HAL 捕获
    try:
        from core.hardware_agent import get_hardware_agent
        agent = get_hardware_agent()
        if agent and agent.hal:
            from drivers.hal_physical import PhysicalHAL
            if isinstance(agent.hal, PhysicalHAL):
                frame = agent.hal.capture_image()
                if frame and len(frame) > 100:
                    return frame, ""
    except Exception:
        pass

    # 4. 尝试直接打开本地 OpenCV V4L2 摄像头 (如树莓派 USB 免驱裸板 /dev/video0)
    try:
        if os.path.exists("/dev/video0"):
            import cv2
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                for _ in range(3):
                    cap.grab()
                ret, mat = cap.read()
                cap.release()
                if ret and mat is not None:
                    success, buf = cv2.imencode(".jpg", mat)
                    if success:
                        return buf.tobytes(), ""
    except Exception:
        pass

    # 清理潜在残留旧文件
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return b"", "小猪目前没看到画面呢～请在网页端开启【📷 电脑摄像头】或者确认摄像头插好啦～"


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
    img_bytes, err_msg = _capture_frame_bytes()
    if not img_bytes:
        return err_msg or "未能捕获到画面：若在 Web 仿真工作台，请开启【📷 电脑摄像头】；若在真机，请确认免驱 USB 摄像头已插好。"

    # 将本次送检大模型的抓拍瞬间同步持久化至快照文件，供前端对话视窗查看
    try:
        with open("/tmp/pig_latest_snapshot.jpg", "wb") as f:
            f.write(img_bytes)
    except Exception:
        pass

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
        "max_tokens": 500
    }
    if not cfg.get("llm_thinking", False):
        payload["thinking"] = {"type": "disabled"}

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
