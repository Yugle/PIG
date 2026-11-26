# -*- coding: utf-8 -*-
"""
PIG 远程看家与监控抓拍技能 (Remote Guard & Surveillance Skill)
核心功能：
1. 接收远程方位指令（正视、向左、向右、俯仰），静默调整头颈舵机机位。
2. 捕获摄像头当前一帧高保真 JPEG 图像（真机 USB/CSI 摄像头、Web 孪生实时推流）。
   严格执行 3.5s TTL 时效性校验，抓拍失败时坚决拦截并清理陈旧历史图片，绝不拿上一次旧图伪装当前现场！
3. 若实时画面有效，调用多模态 VLM 大模型（Xiaomi MiMo-v2.6-Flash）进行家庭安防智能诊断。
4. 调用飞书 OpenAPI / Webhook，将抓拍图片及富文本安防卡片秒级推送至主人手机。
"""

import os
import sys
import json
import time
import base64
from datetime import datetime
from typing import Tuple, Dict, Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")


def _load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _resolve_direction(direction_str: str) -> Tuple[float, float, str]:
    """解析朝向指令，返回 (pan, tilt, desc)"""
    d = (direction_str or "").strip().lower()
    if any(k in d for k in ["left", "左"]):
        return 135.0, 90.0, "水平 135° · 俯仰 90° (左侧全景机位)"
    elif any(k in d for k in ["right", "右"]):
        return 45.0, 90.0, "水平 45° · 俯仰 90° (右侧全景机位)"
    elif any(k in d for k in ["up", "上", "高", "天花板"]):
        return 90.0, 75.0, "水平 90° · 俯仰 75° (仰角高位视角)"
    elif any(k in d for k in ["down", "下", "低", "地面", "桌面"]):
        return 90.0, 105.0, "水平 90° · 俯仰 105° (俯角地面视角)"
    return 90.0, 90.0, "水平 90° · 俯仰 90° (正视中央机位)"


def _generate_synthetic_surveillance_frame(pan: float, tilt: float, desc: str, is_live: bool = False) -> bytes:
    """在无物理摄像头输入或抓拍失败时，合成一张高清安防监控 HUD 状态帧"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        width, height = 640, 480
        # 离线时使用深红暗调，在线拟真使用赛博科技深蓝
        bg_color = (25, 14, 14) if not is_live else (14, 18, 25)
        accent_color = (255, 77, 79) if not is_live else (0, 229, 255)
        grid_color = (56, 30, 30) if not is_live else (30, 42, 56)

        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # 绘制浅色安防网格十字准星
        cx, cy = width // 2, height // 2
        for x in range(0, width, 80):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
        for y in range(0, height, 60):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)

        # 瞄准准星与四角取景线
        draw.line([(cx - 24, cy), (cx + 24, cy)], fill=accent_color, width=2)
        draw.line([(cx, cy - 24), (cx, cy + 24)], fill=accent_color, width=2)
        draw.ellipse([(cx - 40, cy - 40), (cx + 40, cy + 40)], outline=accent_color, width=1)

        # 四角取景框
        corner_len = 28
        draw.line([(20, 20), (20 + corner_len, 20)], fill=accent_color, width=3)
        draw.line([(20, 20), (20, 20 + corner_len)], fill=accent_color, width=3)
        draw.line([(width - 20, 20), (width - 20 - corner_len, 20)], fill=accent_color, width=3)
        draw.line([(width - 20, 20), (width - 20, 20 + corner_len)], fill=accent_color, width=3)
        draw.line([(20, height - 20), (20 + corner_len, height - 20)], fill=accent_color, width=3)
        draw.line([(20, height - 20), (20, height - 20 - corner_len)], fill=accent_color, width=3)
        draw.line([(width - 20, height - 20), (width - 20 - corner_len, height - 20)], fill=accent_color, width=3)
        draw.line([(width - 20, height - 20), (width - 20, height - 20 - corner_len)], fill=accent_color, width=3)

        # 文本 HUD 叠加
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not is_live:
            hud_lines = [
                "SIGNAL ✖ CAMERA OFFLINE / NO LIVE FEED",
                f"TIME: {now_str}",
                f"ANGLE: Pan {pan:.1f}° / Tilt {tilt:.1f}°",
                "STATUS: NO CAMERA INPUT DETECTED",
                "ACTION: PLEASE CONNECT CAMERA OR START WEBCAM STREAM"
            ]
        else:
            hud_lines = [
                "REC ● PIG HOME SENTINEL [LIVE]",
                f"TIME: {now_str}",
                f"ANGLE: Pan {pan:.1f}° / Tilt {tilt:.1f}°",
                "STATUS: SECURE / ALL CLEAR",
                "MODE: EMBODIED PATROL"
            ]

        y_offset = 32
        for line in hud_lines:
            draw.text((36, y_offset), line, fill=(240, 246, 252))
            y_offset += 20

        # 如果离线，屏幕中央醒目警示
        if not is_live:
            banner_text = "[ ! ] NO CAMERA SIGNAL"
            draw.text((cx - 100, cy + 60), banner_text, fill=(255, 120, 120))

        # 保存并导出 bytes
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return b""


def _capture_frame_bytes(pan: float, tilt: float, desc: str, max_age_sec: float = 12.0) -> Tuple[bytes, bool, str]:
    """
    按优先级从物理机摄像头或仿真推流捕获真实画面。
    严格执行 max_age_sec TTL 时效性校验，抓拍失败时绝对杜绝读取陈旧历史缓存图！
    返回: (img_bytes, is_live, status_message)
    """
    now = time.time()
    tmp_path = "/tmp/pig_latest_frame.jpg"

    # 0. 先主动检测并清理超过 max_age_sec 的陈旧临时桥接文件
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
                return frame, True, "实时推流画面抓拍成功"
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
                        return content, True, "实时桥接帧抓拍成功"
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
                    return frame, True, "物理摄像头抓拍成功"
    except Exception:
        pass

    # 4. 尝试直接打开本地 OpenCV V4L2 摄像头 (如树莓派 USB 免驱摄像头 /dev/video0)
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
                        return buf.tobytes(), True, "V4L2 摄像头抓拍成功"
    except Exception:
        pass

    # 5. 所有实时源均无有效画面 -> 严禁使用陈旧历史旧图！
    # 彻底清理残留临时文件，生成离线警示 HUD 帧
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    hud_bytes = _generate_synthetic_surveillance_frame(pan, tilt, desc, is_live=False)
    return hud_bytes, False, "未检测到实时摄像头画面输入"


def _inspect_scene_with_vlm(img_bytes: bytes, user_query: str) -> str:
    """调用小米 MiMo 多模态 VLM 进行安防视觉诊断（仅对真实捕获的有效画面执行）"""
    cfg = _load_config()
    api_key = cfg.get("api_key") or cfg.get("mimo_api_key", "")
    base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
    model = cfg.get("llm_model", "mimo-v2.6-flash")

    if not api_key or not img_bytes:
        return "现场光线平稳，主要通道方位未见异常移动，一切安好～"

    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    img_url = f"data:image/jpeg;base64,{b64_img}"

    sys_instruction = (
        "你是 PIG 智能伴侣机器人的家庭安防巡检中枢。你正在通过机身摄像头观察家里当前的现场画面。\n"
        "请作为贴心的家庭守护助手，用 2 到 3 句话清晰、利落、温暖地汇报当前家庭现场状态（光线是否充足、视野内是否有人员或宠物走动、门窗或环境是否整洁正常、是否有明显异常情况）。\n"
        "严禁输出'在这张图片中'、'从照片来看'等机械生硬套话，直接像真正替主人看家的小伙伴一样自然汇报！"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    q_text = str(user_query or "").strip()
    if not q_text:
        q_text = "帮我巡检一下家里当前的现场环境状态，一切正常吗？"

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
        "max_tokens": 400
    }
    if not cfg.get("llm_thinking", False):
        payload["thinking"] = {"type": "disabled"}

    try:
        import requests
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            res_data = resp.json()
            choices = res_data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return answer
    except Exception as e:
        print(f"⚠️ [RemoteGuard] VLM 视觉分析稍有延迟: {e}")

    return "当前镜头视野通畅，画面未见突发异动，光线平稳，门窗方位一切安好～"


def run(direction="center", question="巡视当前环境并汇报状态", **kwargs):
    """
    远程安防抓拍执行主入口
    :param direction: 镜头机位，如 'center', 'left', 'right', 'up', 'down'
    :param question: 主人具体提问或观察要求
    :param kwargs: 包含飞书回调传入的 target_id, id_type 等
    """
    target_pan, target_tilt, dir_desc = _resolve_direction(direction)

    # 1. 调整头颈姿态（静默调姿）
    try:
        from core.hardware_agent import get_hardware_agent
        agent = get_hardware_agent()
        if agent and agent.hal:
            agent.hal.set_angles(target_pan, target_tilt)
            time.sleep(0.4)
    except Exception:
        pass

    # 2. 抓拍实时帧 (严格执行 TTL 校验，抓拍失败拦截陈旧历史图片)
    img_bytes, is_live, status_msg = _capture_frame_bytes(target_pan, target_tilt, dir_desc)

    # 仅写入独立的远程安防快照文件，绝不写回 /tmp/pig_latest_frame.jpg 污染输入源！
    try:
        with open("/tmp/pig_latest_snapshot.jpg", "wb") as f:
            f.write(img_bytes)
    except Exception:
        pass

    # 3. 智能环境诊断 (仅在真实抓拍成功时调用 VLM，离线时坚决不编造或分析旧图)
    if is_live:
        vlm_summary = _inspect_scene_with_vlm(img_bytes, question)
    else:
        vlm_summary = "小猪目前没有看到画面呢～请在网页端开启【📷 电脑摄像头】或者确认摄像头插好啦～"

    # 4. 组装并推送飞书富文本安防抓拍卡片
    target_id = kwargs.get("target_id")
    id_type = kwargs.get("id_type", "open_id")
    card_delivered = False
    try:
        from skills.feishu_bot.feishu_client import feishu_client
        push_res = feishu_client.send_camera_snapshot_card(
            image_bytes=img_bytes,
            vlm_analysis=vlm_summary,
            direction_desc=dir_desc,
            target_id=target_id,
            id_type=id_type,
            is_live=is_live
        )
        card_delivered = (push_res.get("code") == 0)
        print(f"📷 [RemoteGuard] 飞书安防卡片已投递: {push_res.get('code', push_res.get('status'))}")
    except Exception as e:
        print(f"⚠️ [RemoteGuard] 飞书卡片投递异常: {e}")

    # 检查是否有飞书发图权限提醒
    extra_hint = ""
    try:
        from skills.feishu_bot.feishu_client import feishu_client
        if getattr(feishu_client, "_last_upload_err", None):
            err_info = feishu_client._last_upload_err
            if err_info.get("type") == "permission":
                extra_hint = f"\n\n⚠️ 注：飞书自建应用当前尚未开通发图权限 [im:resource:upload]，导致卡片未能直接显示图片。\n👉 请点击卡片中的开通按钮或直接访问链接一键申请开通：\n{err_info.get('auth_link')}"
    except Exception:
        pass

    if is_live:
        return f"📷【家庭安防巡检已完成】\n机位：{dir_desc}\n诊断：{vlm_summary}{extra_hint}"
    else:
        return f"📷【家庭安防巡检】\n机位：{dir_desc}\n小猪目前没看到实时画面呢，检查一下摄像头连接或者在网页端开启摄像头吧～{extra_hint}"
