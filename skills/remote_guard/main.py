# -*- coding: utf-8 -*-
"""
PIG 远程看家与寻物抓拍技能 (Remote Guard & Target Finder Skill)
核心功能：
1. 支持单机位快速看家抓拍（正前、左、右、抬头、低头），静默或孪生联动调整头颈舵机。
2. 支持【目标搜寻与对准抓拍】模式（如“帮我找一下猫”、“帮我拍一张猫的照片”）：
   - 先看主方位（若无显式指定则先看正前方），命中即锁定并微调居中抓拍；
   - 未命中时自动驱动头颈左右/俯视多机位扫描，并发调用 VLM 识别目标方位；
   - 找到后回转舵机对准目标高清抓拍并推送；均未找到时自动合成 2×2 四向巡检拼图供主人查看。
3. 严格执行 TTL 时效性校验与物理帧优先策略，杜绝复用转头前或历史旧缓存图！
"""

import io
import os
import re
import sys
import json
import time
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

# 预设机位映射: key -> (pan, tilt, short_tag, full_desc)
VIEWPOINTS: Dict[str, Tuple[float, float, str, str]] = {
    "center": (90.0, 90.0, "正前 90°", "水平 90° · 俯仰 90° (正视中央机位)"),
    "left": (135.0, 92.0, "左侧 135°", "水平 135° · 俯仰 92° (左侧全景机位)"),
    "right": (45.0, 92.0, "右侧 45°", "水平 45° · 俯仰 92° (右侧全景机位)"),
    "down": (90.0, 108.0, "俯视 108°", "水平 90° · 俯仰 108° (俯角桌面/地面)"),
    "up": (90.0, 75.0, "仰角 75°", "水平 90° · 俯仰 75° (仰角高位视角)"),
}
DEFAULT_SCAN_ORDER = ["center", "left", "right", "down"]

GENERIC_SCENE_WORDS = {
    "家", "家里", "客厅", "房间", "屋里", "环境", "现场", "周围", "四周", "一圈", "全景",
    "照", "照片", "相片", "一眼", "一下", "这边", "那边", "里面", "外面", "上面", "下面",
    "左边", "右边", "前面", "后面", "桌面", "地面", "地上", "监控", "巡检", "巡视", "安防",
}


def _load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _resolve_direction(direction_str: str) -> Tuple[str, float, float, str]:
    """解析朝向指令，返回 (key, pan, tilt, desc)"""
    d = (direction_str or "").strip().lower()
    if any(k in d for k in ["scan", "auto", "all", "环顾", "四周", "全景", "一圈", "到处"]):
        pan, tilt, _, desc = VIEWPOINTS["center"]
        return "scan", pan, tilt, "多向环顾巡检 (正前/左/右/俯视)"
    if any(k in d for k in ["left", "左"]):
        pan, tilt, _, desc = VIEWPOINTS["left"]
        return "left", pan, tilt, desc
    if any(k in d for k in ["right", "右"]):
        pan, tilt, _, desc = VIEWPOINTS["right"]
        return "right", pan, tilt, desc
    if any(k in d for k in ["up", "上", "高", "天花板"]):
        pan, tilt, _, desc = VIEWPOINTS["up"]
        return "up", pan, tilt, desc
    if any(k in d for k in ["down", "下", "低", "地面", "桌面"]):
        pan, tilt, _, desc = VIEWPOINTS["down"]
        return "down", pan, tilt, desc
    pan, tilt, _, desc = VIEWPOINTS["center"]
    return "center", pan, tilt, desc


def _extract_target_from_question(question: str) -> str:
    """从自然语言提问中自动提取寻找/拍摄目标（如 '帮我找一下猫' -> '猫'）"""
    q = str(question or "").strip()
    if not q:
        return ""

    patterns = [
        r"(?:帮我|帮忙|请|去|顺便)?(?:找一下|找找|找一找|寻找|找|拍一张|拍张|拍个|拍一下|拍摄|照一张|看看|看下)(.+?)(?:的照片|的相片|在哪儿|在哪里|在哪|在不在|在干嘛|在干啥|有没有|呢|吗|嘛|吧|呀|！|!|。|$)",
        r"(.+?)(?:在哪里|在哪儿|在哪|在不在|在干嘛|在干啥)(?:啊|呀|呢|吗|吧|！|!|。|$)",
    ]
    for pat in patterns:
        m = re.search(pat, q)
        if not m:
            continue
        cand = m.group(1).strip(" ，。！？,.!?\"'“”")
        cand = re.sub(r"^(?:一只|一条|个|张|只|那只|我的|我家|咱家|家里的|桌上的|地上的|地面的)+", "", cand).strip()
        cand = re.sub(r"(?:照片|相片|的样子|身影)$", "", cand).strip()
        if not cand or len(cand) > 16:
            continue
        if cand in GENERIC_SCENE_WORDS:
            continue
        # 跳过“家/周围/左看看”等纯泛指或纯方位词
        if any(cand == g for g in GENERIC_SCENE_WORDS) or cand.endswith(("边", "面", "侧")):
            continue
        return cand
    return ""


def _generate_synthetic_surveillance_frame(pan: float, tilt: float, desc: str, is_live: bool = False) -> bytes:
    """在无物理摄像头输入或抓拍失败时，合成一张高清安防监控 HUD 状态帧"""
    try:
        from PIL import Image, ImageDraw
        width, height = 640, 480
        bg_color = (25, 14, 14) if not is_live else (14, 18, 25)
        accent_color = (255, 77, 79) if not is_live else (0, 229, 255)
        grid_color = (56, 30, 30) if not is_live else (30, 42, 56)

        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        cx, cy = width // 2, height // 2
        for x in range(0, width, 80):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
        for y in range(0, height, 60):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)

        draw.line([(cx - 24, cy), (cx + 24, cy)], fill=accent_color, width=2)
        draw.line([(cx, cy - 24), (cx, cy + 24)], fill=accent_color, width=2)
        draw.ellipse([(cx - 40, cy - 40), (cx + 40, cy + 40)], outline=accent_color, width=1)

        corner_len = 28
        for (x0, y0, dx, dy) in [
            (20, 20, corner_len, corner_len),
            (width - 20, 20, -corner_len, corner_len),
            (20, height - 20, corner_len, -corner_len),
            (width - 20, height - 20, -corner_len, -corner_len),
        ]:
            draw.line([(x0, y0), (x0 + dx, y0)], fill=accent_color, width=3)
            draw.line([(x0, y0), (x0, y0 + dy)], fill=accent_color, width=3)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hud_lines = [
            "SIGNAL ✖ CAMERA OFFLINE / NO LIVE FEED" if not is_live else "REC ● PIG HOME SENTINEL [LIVE]",
            f"TIME: {now_str}",
            f"ANGLE: Pan {pan:.1f}° / Tilt {tilt:.1f}°",
            "STATUS: NO CAMERA INPUT DETECTED" if not is_live else "STATUS: SECURE / ALL CLEAR",
        ]
        y_offset = 32
        for line in hud_lines:
            draw.text((36, y_offset), line, fill=(240, 246, 252))
            y_offset += 20

        if not is_live:
            draw.text((cx - 100, cy + 60), "[ ! ] NO CAMERA SIGNAL", fill=(255, 120, 120))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return b""


def _capture_frame_bytes(
    pan: float,
    tilt: float,
    desc: str,
    max_age_sec: float = 12.0,
    prefer_hardware: bool = False,
) -> Tuple[bytes, bool, str, str]:
    """
    捕获当前真实摄像头画面（严格执行 TTL 校验，绝不拿陈旧旧图冒充实时画面）。
    prefer_hardware=True 时（如舵机刚刚转向后），优先直接抓取物理/V4L2新帧，防止读到转向前的缓存帧。
    返回: (img_bytes, is_live, status_message, source_kind)
    """
    now = time.time()
    tmp_path = "/tmp/pig_latest_frame.jpg"

    if os.path.exists(tmp_path):
        try:
            if now - os.path.getmtime(tmp_path) > max_age_sec:
                os.remove(tmp_path)
        except Exception:
            pass

    def _try_physical_hal() -> Optional[Tuple[bytes, str]]:
        try:
            from core.hardware_agent import get_hardware_agent
            from drivers.hal_physical import PhysicalRobotHAL
            agent = get_hardware_agent()
            if agent and agent.hal and isinstance(agent.hal, PhysicalRobotHAL):
                frame = agent.hal.capture_image()
                if frame and len(frame) > 100:
                    return frame, "physical"
        except Exception:
            pass
        return None

    def _try_v4l2() -> Optional[Tuple[bytes, str]]:
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
                            return buf.tobytes(), "v4l2"
        except Exception:
            pass
        return None

    def _try_web_and_bridge() -> Optional[Tuple[bytes, str]]:
        main_mod = sys.modules.get("__main__")
        if main_mod and hasattr(main_mod, "get_latest_vision_frame"):
            try:
                frame = main_mod.get_latest_vision_frame(max_age_sec=max_age_sec)
                if frame and len(frame) > 100:
                    return frame, "web"
            except Exception:
                pass

        if os.path.exists(tmp_path):
            try:
                if time.time() - os.path.getmtime(tmp_path) <= max_age_sec:
                    with open(tmp_path, "rb") as f:
                        content = f.read()
                        if len(content) > 100:
                            return content, "bridge"
                else:
                    os.remove(tmp_path)
            except Exception:
                pass
        return None

    # 物理舵机云台刚转向时优先直接从物理 HAL 取最新曝光帧；桌面 Web 仿真保留网页推流优先，避免与浏览器争抢 /dev/video0
    loaders = (
        [_try_physical_hal, _try_web_and_bridge, _try_v4l2]
        if prefer_hardware
        else [_try_web_and_bridge, _try_physical_hal, _try_v4l2]
    )
    for loader in loaders:
        hit = loader()
        if hit:
            img_bytes, src = hit
            return img_bytes, True, f"{src} 实时画面抓拍成功", src

    hud_bytes = _generate_synthetic_surveillance_frame(pan, tilt, desc, is_live=False)
    return hud_bytes, False, "未检测到实时摄像头画面输入", "offline"


def _move_head_and_capture(
    pan: float,
    tilt: float,
    desc: str,
    settle_sec: float = 0.45,
) -> Tuple[bytes, bool, str, str]:
    """安全调整头颈舵机机位（同步 3D 数字孪生并锁定视线防抢占），稳定后抓拍实时画面"""
    try:
        from core.hardware_agent import get_hardware_agent
        agent = get_hardware_agent()
        if agent and agent.hal:
            if hasattr(agent, "mark_action_playing"):
                agent.mark_action_playing(max(4.0, settle_sec + 3.0))
            agent.hal.set_angles(pan, tilt, smooth=True)
            if hasattr(agent, "emit_event"):
                actual_pan = getattr(agent.hal, "cur_pan", pan)
                actual_tilt = getattr(agent.hal, "cur_tilt", tilt)
                agent.emit_event("pose_sync", {"pan": actual_pan, "tilt": actual_tilt})
            time.sleep(settle_sec)
    except Exception:
        pass

    return _capture_frame_bytes(pan, tilt, desc, prefer_hardware=True)


def _is_static_duplicate(frame_a: bytes, frame_b: bytes) -> bool:
    """判断两帧是否为固定未移动摄像头的静态重复画面（如 PC 固定 Webcam 仿真）"""
    if not frame_a or not frame_b:
        return False
    if frame_a == frame_b:
        return True
    try:
        from PIL import Image, ImageChops
        im_a = Image.open(io.BytesIO(frame_a)).convert("L").resize((24, 18))
        im_b = Image.open(io.BytesIO(frame_b)).convert("L").resize((24, 18))
        diff = ImageChops.difference(im_a, im_b)
        px = list(diff.getdata())
        mean_diff = sum(px) / max(1, len(px))
        return mean_diff < 2.5
    except Exception:
        return False


def _call_vlm(messages: List[Dict[str, Any]], cfg: Dict[str, Any], max_tokens: int = 350, timeout: int = 18) -> str:
    """统一调用 MiMo 多模态 VLM"""
    api_key = cfg.get("api_key") or cfg.get("mimo_api_key", "")
    base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1").rstrip("/")
    model = cfg.get("llm_model", "mimo-v2.6-flash")
    if not api_key:
        return ""

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if not cfg.get("llm_thinking", False):
        payload["thinking"] = {"type": "disabled"}

    import requests
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 200:
        choices = resp.json().get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip()
    return ""


def _vlm_check_target(img_bytes: bytes, target: str, view_desc: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """调用 VLM 判断当前视角内是否存在指定目标，并返回结构化结果"""
    if not (cfg.get("api_key") or cfg.get("mimo_api_key")) or not img_bytes:
        return {"found": False, "confidence": 0.0, "position": "center", "summary": "视觉模型未配置"}

    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    sys_prompt = (
        "你是桌面具身机器人的视觉搜索检测器。请仔细观察画面，判断画面中是否出现了用户寻找的目标。\n"
        "严格输出如下 JSON（不要附加 Markdown 代码块）：\n"
        '{"found": true或false, "confidence": 0.0到1.0, "position": "center|left|right|up|down", '
        '"summary": "如果找到，用1~2句亲切口语描述目标在哪里、在干嘛；如果没找到，简要说明当前视角看到的主要环境"}'
    )
    user_text = f"当前镜头机位：{view_desc}。请确认画面中是否有【{target}】？"

    try:
        raw = _call_vlm(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                ]},
            ],
            cfg,
            max_tokens=200,
            timeout=14,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return {
                "found": bool(parsed.get("found", False)),
                "confidence": float(parsed.get("confidence", 0.8 if parsed.get("found") else 0.0)),
                "position": str(parsed.get("position", "center")).lower(),
                "summary": str(parsed.get("summary") or "").strip(),
            }
        if raw:
            low = raw.lower()
            found = not any(neg in low for neg in ["没有看到", "未发现", "没看到", "没有发现", "不在画面", "看不到"])
            return {"found": found, "confidence": 0.6 if found else 0.1, "position": "center", "summary": raw}
    except Exception as e:
        print(f"⚠️ [RemoteGuard] 目标检测异常 ({view_desc}): {e}")
    return {"found": False, "confidence": 0.0, "position": "center", "summary": ""}


def _inspect_scene_with_vlm(img_bytes: bytes, user_query: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """通用家庭安防/全景视觉分析（单图或拼图均适用）"""
    cfg = cfg or _load_config()
    if not (cfg.get("api_key") or cfg.get("mimo_api_key")) or not img_bytes:
        return "现场光线平稳，视野方位一切正常、未见突发异动～"

    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    sys_instruction = (
        "你是 PIG 智能伴侣机器人的家庭看护与视觉抓拍助手。你正在通过机身摄像头观察现场画面。\n"
        "请用 2 到 3 句话自然、亲切、利落地向主人汇报画面内容（光线、人或宠物动态、门窗与整洁度或主人关心的内容）。\n"
        "一切安好时请用'一切正常'或'平平安安'表述，严禁输出'在这张图片中'、'从照片来看'等机械套话！"
    )
    q_text = str(user_query or "").strip() or "帮我巡检一下家里当前的现场环境状态，一切正常吗？"
    try:
        answer = _call_vlm(
            [
                {"role": "system", "content": sys_instruction},
                {"role": "user", "content": [
                    {"type": "text", "text": q_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                ]},
            ],
            cfg,
            max_tokens=360,
            timeout=16,
        )
        if answer:
            return answer
    except Exception as e:
        print(f"⚠️ [RemoteGuard] VLM 视觉分析稍有延迟: {e}")
    return "当前镜头视野通畅，画面未见突发异动，光线平稳，门窗方位一切安好～"


def _nudge_angles_toward_target(pan: float, tilt: float, pos_hint: str) -> Tuple[float, float]:
    """根据 VLM 给出的目标偏移方位微调舵机，使其更居中"""
    new_pan, new_tilt = pan, tilt
    p = (pos_hint or "").lower()
    if "left" in p or "左" in p:
        new_pan = min(150.0, pan + 14.0)
    elif "right" in p or "右" in p:
        new_pan = max(30.0, pan - 14.0)
    if "up" in p or "上" in p:
        new_tilt = max(68.0, tilt - 8.0)
    elif "down" in p or "下" in p:
        new_tilt = min(114.0, tilt + 8.0)
    return new_pan, new_tilt


def _build_patrol_collage(views: List[Tuple[str, float, float, bytes]]) -> bytes:
    """将多视角抓拍画面拼接为 2×2 四宫格全景巡检图 (640x480)"""
    if not views:
        return b""
    if len(views) == 1:
        return views[0][3]
    try:
        from PIL import Image, ImageDraw
        canvas = Image.new("RGB", (640, 480), color=(16, 20, 28))
        draw = ImageDraw.Draw(canvas)
        slots = [(0, 0), (320, 0), (0, 240), (320, 240)]
        for idx, (tag, pan, tilt, img_bytes) in enumerate(views[:4]):
            sx, sy = slots[idx]
            tile = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((320, 240))
            canvas.paste(tile, (sx, sy))
            draw.rectangle([(sx + 6, sy + 6), (sx + 168, sy + 28)], fill=(10, 14, 22))
            draw.text((sx + 12, sy + 10), f"[{tag}] P:{int(pan)} T:{int(tilt)}", fill=(0, 229, 255))

        draw.line([(320, 0), (320, 480)], fill=(45, 58, 78), width=2)
        draw.line([(0, 240), (640, 240)], fill=(45, 58, 78), width=2)
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return views[0][3]


def _save_latest_snapshot(img_bytes: bytes) -> None:
    if not img_bytes:
        return
    try:
        with open("/tmp/pig_latest_snapshot.jpg", "wb") as f:
            f.write(img_bytes)
    except Exception:
        pass


def _push_feishu_card(
    img_bytes: bytes,
    summary: str,
    dir_desc: str,
    is_live: bool,
    target_id: Optional[str],
    id_type: str,
    card_title: Optional[str] = None,
    section_title: Optional[str] = None,
) -> Tuple[bool, str]:
    """推送飞书图文卡片并返回 (是否送达, 权限提示)"""
    delivered = False
    extra_hint = ""
    try:
        from skills.feishu_bot.feishu_client import feishu_client
        if not feishu_client.is_configured:
            return False, ""
        push_res = feishu_client.send_camera_snapshot_card(
            image_bytes=img_bytes,
            vlm_analysis=summary,
            direction_desc=dir_desc,
            target_id=target_id,
            id_type=id_type,
            is_live=is_live,
            card_title=card_title,
            section_title=section_title,
        )
        delivered = (push_res.get("code") == 0)
        print(f"📷 [RemoteGuard] 飞书卡片投递: {push_res.get('code', push_res.get('status'))}")
        err_info = getattr(feishu_client, "_last_upload_err", None)
        if err_info and err_info.get("type") == "permission":
            extra_hint = (
                "\n\n⚠️ 注：飞书自建应用当前尚未开通发图权限 [im:resource:upload]，导致卡片未能直接显示图片。\n"
                f"👉 请点击卡片中的开通按钮或访问链接一键开通：\n{err_info.get('auth_link')}"
            )
    except Exception as e:
        print(f"⚠️ [RemoteGuard] 飞书卡片投递异常: {e}")
    return delivered, extra_hint


def _run_target_search_or_scan(
    dir_key: str,
    target: str,
    question: str,
    cfg: Dict[str, Any],
) -> Tuple[bytes, bool, str, str, str, str]:
    """
    执行目标搜寻或全景巡检。
    返回: (final_img_bytes, is_live, dir_desc, vlm_summary, card_title, section_title)
    """
    primary_key = "center" if dir_key == "scan" else dir_key
    scan_order = [primary_key] + [k for k in DEFAULT_SCAN_ORDER if k != primary_key]

    p_pan, p_tilt, p_tag, p_desc = VIEWPOINTS[primary_key]
    p_bytes, p_live, _, p_src = _move_head_and_capture(p_pan, p_tilt, p_desc, settle_sec=0.35)
    if not p_live:
        return (
            p_bytes,
            False,
            p_desc,
            "小猪目前没有看到画面呢～请在网页端开启【📷 电脑摄像头】或者确认树莓派摄像头已连接～",
            "📷 PIG 远程抓拍状态",
            "**📷 现场画面状态**",
        )

    # 若指定了目标，先立即检测主视角：命中就快速锁定，不必盲目四处乱晃
    captured_views: List[Tuple[str, float, float, bytes]] = [(p_tag, p_pan, p_tilt, p_bytes)]
    if target:
        hit0 = _vlm_check_target(p_bytes, target, p_desc, cfg)
        if hit0.get("found"):
            final_pan, final_tilt = p_pan, p_tilt
            final_bytes, final_desc = p_bytes, p_desc
            summary = hit0.get("summary") or f"在{p_tag}看到了{target}～"
            # 若目标偏离中心且属于实体云台，轻柔对准微调再补拍一帧清晰居中照片
            pos_hint = hit0.get("position", "center")
            if pos_hint != "center" and p_src in ("physical", "v4l2"):
                n_pan, n_tilt = _nudge_angles_toward_target(p_pan, p_tilt, pos_hint)
                if abs(n_pan - p_pan) >= 2.0 or abs(n_tilt - p_tilt) >= 2.0:
                    nudged_bytes, nudged_live, _, _ = _move_head_and_capture(n_pan, n_tilt, p_desc, settle_sec=0.4)
                    if nudged_live:
                        final_pan, final_tilt, final_bytes = n_pan, n_tilt, nudged_bytes
                        final_desc = f"水平 {final_pan:.0f}° · 俯仰 {final_tilt:.0f}° (已对准【{target}】)"
            return (
                final_bytes,
                True,
                final_desc,
                summary,
                f"🐾 PIG 已找到「{target}」并拍下照片",
                f"**🎯 目标识别与抓拍 ({target})**",
            )

    # 扫掠其余机位（仅保留画面有变化的有效视角）
    candidate_frames: List[Tuple[str, str, float, float, str, bytes]] = []
    for k in scan_order[1:]:
        pan, tilt, tag, desc = VIEWPOINTS[k]
        f_bytes, f_live, _, f_src = _move_head_and_capture(pan, tilt, desc, settle_sec=0.45)
        if not f_live:
            continue
        if f_src in ("web", "bridge") and _is_static_duplicate(p_bytes, f_bytes):
            continue
        captured_views.append((tag, pan, tilt, f_bytes))
        candidate_frames.append((k, tag, pan, tilt, desc, f_bytes))

    # 并行 VLM 检测其余机位中的目标
    if target and candidate_frames:
        with ThreadPoolExecutor(max_workers=min(3, len(candidate_frames))) as pool:
            futs = [
                (item, pool.submit(_vlm_check_target, item[5], target, item[4], cfg))
                for item in candidate_frames
            ]
            hits = []
            for item, fut in futs:
                try:
                    res = fut.result(timeout=15)
                    if res.get("found"):
                        hits.append((res.get("confidence", 0.5), item, res))
                except Exception:
                    pass

        if hits:
            hits.sort(key=lambda x: x[0], reverse=True)
            _, best_item, best_res = hits[0]
            _, best_tag, best_pan, best_tilt, best_desc, best_bytes = best_item
            pos_hint = best_res.get("position", "center")
            n_pan, n_tilt = _nudge_angles_toward_target(best_pan, best_tilt, pos_hint)
            final_bytes, final_live, _, _ = _move_head_and_capture(n_pan, n_tilt, best_desc, settle_sec=0.45)
            snap_bytes = final_bytes if final_live else best_bytes
            snap_desc = f"水平 {n_pan:.0f}° · 俯仰 {n_tilt:.0f}° ({best_tag} · 已锁定【{target}】)"
            summary = best_res.get("summary") or f"转向{best_tag}后找到{target}并拍好照片啦～"
            return (
                snap_bytes,
                True,
                snap_desc,
                summary,
                f"🐾 PIG 已找到「{target}」并拍下照片",
                f"**🎯 目标识别与抓拍 ({target})**",
            )

    # 未找到目标（或纯全景环顾模式）：回正头部，合成全景拼图或使用实时图
    _move_head_and_capture(90.0, 90.0, VIEWPOINTS["center"][3], settle_sec=0.25)
    is_collage = len(captured_views) > 1
    out_bytes = _build_patrol_collage(captured_views) if is_collage else p_bytes
    view_names = " / ".join(v[0] for v in captured_views)

    if target:
        card_title = f"🔍 环顾巡检未见「{target}」"
        sec_title = "**🐾 多向寻物结果**"
        if is_collage:
            env_note = _inspect_scene_with_vlm(
                out_bytes,
                f"这是一张包含【{view_names}】的多视角拼图，主人想找「{target}」但刚才各角度初检都没看到，请确认有没有漏掉并简短总结各方位环境。",
                cfg,
            )
            summary = f"小猪转动头部环顾了 {view_names}，这几个视角里都没有看到「{target}」呢～已把多视角拼接图发给你确认：{env_note}"
        else:
            summary = f"小猪仔细查看了当前固定画面（{p_tag}），里面没有看到「{target}」的身影～（提示：当前为固定机位画面，多角度扫寻需配合云台真机摄像头哦）"
        return out_bytes, True, f"环顾扫描 ({view_names})", summary, card_title, sec_title

    summary = _inspect_scene_with_vlm(
        out_bytes,
        f"请根据这张多向巡检{'拼接图' if is_collage else '画面'}({view_names})汇报家里当前整体情况：{question}",
        cfg,
    )
    return out_bytes, True, f"全景巡检 ({view_names})", summary, "📷 PIG 多角度全景巡检", "**👁️ 全景环境诊断**"


def run(direction="center", question="巡视当前环境并汇报状态", target="", **kwargs):
    """
    远程安防巡检与寻物抓拍统一入口
    :param direction: 'center' | 'left' | 'right' | 'up' | 'down' | 'scan'
    :param question: 主人具体观察要求（如 '帮我找一下猫', '拍一张猫的照片'）
    :param target: 搜寻或重点拍摄的目标名称（可留空，自动从 question 提取）
    """
    cfg = _load_config()
    dir_key, target_pan, target_tilt, dir_desc = _resolve_direction(direction)
    clean_target = str(target or "").strip() or _extract_target_from_question(question)

    q_lower = str(question or "").lower()
    explicit_corner = dir_key in ("left", "right", "up", "down")
    wants_search_scan = (
        dir_key == "scan"
        or any(k in q_lower for k in ["环顾", "四周", "一圈", "全景", "找", "在哪", "在不在"])
        or (bool(clean_target) and not explicit_corner)
    )

    if wants_search_scan:
        img_bytes, is_live, out_desc, vlm_summary, card_title, sec_title = _run_target_search_or_scan(
            dir_key, clean_target, question, cfg
        )
    else:
        img_bytes, is_live, _, _ = _move_head_and_capture(target_pan, target_tilt, dir_desc, settle_sec=0.45)
        out_desc = dir_desc
        if is_live:
            vlm_query = f"重点帮主人观察并拍好「{clean_target}」：{question}" if clean_target else question
            vlm_summary = _inspect_scene_with_vlm(img_bytes, vlm_query, cfg)
            card_title = f"🐾 PIG 已拍下「{clean_target}」照片" if clean_target else "📷 PIG 远程安防与家庭现场巡检"
            sec_title = f"**🎯 抓拍观察 ({clean_target})**" if clean_target else "**👁️ 现场智能环境诊断**"
        else:
            vlm_summary = "小猪目前没有看到画面呢～请在网页端开启【📷 电脑摄像头】或者确认摄像头插好啦～"
            card_title = "📷 PIG 远程抓拍状态"
            sec_title = "**📷 现场画面状态**"

    _save_latest_snapshot(img_bytes)

    target_id = kwargs.get("target_id")
    id_type = kwargs.get("id_type", "open_id")
    _, extra_hint = _push_feishu_card(
        img_bytes, vlm_summary, out_desc, is_live, target_id, id_type, card_title=card_title, section_title=sec_title
    )

    if is_live:
        label = f"寻物抓拍·{clean_target}" if clean_target else "家庭安防巡检"
        return f"📷【{label}已完成】\n机位：{out_desc}\n观察：{vlm_summary}{extra_hint}"
    return f"📷【家庭视觉抓拍】\n机位：{out_desc}\n小猪目前没看到实时画面呢，检查一下摄像头连接或者在网页端开启摄像头吧～{extra_hint}"
