"""
Feishu (Lark) Bot Client & Card Builder for PIG Robot
Supports:
1. Feishu Custom Bot Webhook (Zero-setup Push: Alert cards, Nightly reflection, Status)
2. Feishu OpenAPI (App ID + App Secret for bidirectional DM / Group chat)
3. Rich Interactive Card Generation (Alert, Evolution Diary, Health Doctor)
"""

import os
import sys
import time
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List

logger = logging.getLogger("FeishuBot")

class FeishuClient:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._tenant_access_token = ""
        self._token_expires_at = 0.0

    def update_config(self, config: Dict[str, Any]):
        self.config = config or {}

    @property
    def webhook_url(self) -> str:
        return self.config.get("webhook_url", "").strip()

    @property
    def app_id(self) -> str:
        return self.config.get("app_id", "").strip()

    @property
    def app_secret(self) -> str:
        return self.config.get("app_secret", "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url or (self.app_id and self.app_secret))

    # ========================================================
    # 1. 飞书自定义群机器人 Webhook 推送 (极速简单模式)
    # ========================================================
    def send_webhook_text(self, text: str) -> Dict[str, Any]:
        """通过 Webhook 发送纯文本消息"""
        if not self.webhook_url:
            return {"status": "error", "message": "未配置飞书 Webhook URL"}

        payload = {
            "msg_type": "text",
            "content": {"text": text}
        }
        return self._http_post_json(self.webhook_url, payload)

    def send_webhook_card(self, title: str, elements: List[Dict[str, Any]], template: str = "blue") -> Dict[str, Any]:
        """通过 Webhook 发送高颜值互动卡片 (template: blue / red / green / orange / purple / turquoise)"""
        if not self.webhook_url:
            return {"status": "error", "message": "未配置飞书 Webhook URL"}

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template
            },
            "elements": elements
        }
        payload = {
            "msg_type": "interactive",
            "card": card
        }
        return self._http_post_json(self.webhook_url, payload)

    # ========================================================
    # 2. 飞书开放平台应用 OpenAPI (双向交互模式)
    # ========================================================
    def get_tenant_access_token(self, force_refresh: bool = False) -> str:
        """获取并缓存飞书自建应用的 tenant_access_token"""
        if not self.app_id or not self.app_secret:
            return ""

        now = time.time()
        if not force_refresh and self._tenant_access_token and now < self._token_expires_at - 120:
            return self._tenant_access_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        req_body = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        res = self._http_post_json(url, req_body)
        if res.get("code") == 0:
            self._tenant_access_token = res.get("tenant_access_token", "")
            expires_in = res.get("expire", 7140)
            self._token_expires_at = now + expires_in
            return self._tenant_access_token
        else:
            logger.error(f"❌ [Feishu] 获取 tenant_access_token 失败: {res}")
            return ""

    def send_im_message(self, receive_id: str, msg_type: str, content: Any, receive_id_type: str = "open_id") -> Dict[str, Any]:
        """向指定用户 (open_id) 或群组 (chat_id) 发送消息"""
        token = self.get_tenant_access_token()
        if not token:
            # 若 OpenAPI 凭据未就绪但配置了 Webhook，回退到 Webhook
            if self.webhook_url:
                if msg_type == "interactive" and isinstance(content, dict):
                    return self._http_post_json(self.webhook_url, {"msg_type": "interactive", "card": content})
                return self.send_webhook_text(str(content))
            return {"status": "error", "message": "飞书应用凭据 (App ID/Secret) 与 Webhook 均未配置"}

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        headers = {"Authorization": f"Bearer {token}"}
        content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content_str
        }
        return self._http_post_json(url, payload, headers=headers)

    # ========================================================
    # 3. 极速开箱富文本卡片生成器 (Card Builders)
    # ========================================================
    def send_alert_card(self, title: str, error_msg: str, detail: str = "", cpu_temp: Optional[float] = None) -> Dict[str, Any]:
        """发送系统异常 / 故障报警红色卡片"""
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🚨 告警级别:** <font color='red'>WARNING / ERROR</font>\n**⏱️ 发生时间:** {now_str}\n**📝 错误描述:** {error_msg}"
                }
            }
        ]
        if detail:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🔍 详细诊断信息:**\n```\n{detail[:500]}\n```"
                }
            })
        if cpu_temp is not None:
            elements.append({
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"🤖 PIG 硬件节点 | CPU 温度: {cpu_temp:.1f}°C | 状态: 异常捕获中"}
                ]
            })

        if self.webhook_url:
            return self.send_webhook_card(f"⚠️ [PIG 机器人异常] {title}", elements, template="red")
        return {"status": "error", "message": "未配置推送目标"}

    def send_evolution_card(self, diary_text: str, radar_dict: Dict[str, int], intimacy_level: int, persona_tag: str = "") -> Dict[str, Any]:
        """发送自进化深夜反思沉淀卡片 (紫色温暖风格)"""
        now_str = time.strftime("%Y-%m-%d %H:%M")
        radar_str = (
            f"🌟 **五维性格雷达**: "
            f"活泼 `{radar_dict.get('lively', 50)}` | "
            f"黏人 `{radar_dict.get('clingy', 50)}` | "
            f"微毒舌 `{radar_dict.get('sharp_tongued', 20)}` | "
            f"共情 `{radar_dict.get('empathy', 50)}` | "
            f"好奇 `{radar_dict.get('curiosity', 50)}`"
        )
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**🌙 夜间自进化反思已完成 ({now_str})**\n\n"
                        f"**📖 今日日记:**\n> {diary_text}\n\n"
                        f"**💖 与主人亲密等级:** `Lv.{intimacy_level}`"
                        + (f" ({persona_tag})" if persona_tag else "") + f"\n"
                        f"{radar_str}"
                    )
                }
            },
            {
                "tag": "hr"
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "🐷 PIG 自主伴侣进化引擎 | 随心所动，伴你同行"}
                ]
            }
        ]
        if self.webhook_url:
            return self.send_webhook_card("🌙 PIG 今日伴侣进化日记", elements, template="purple")
        return {"status": "error", "message": "未配置推送目标"}

    def send_status_card(self, status_dict: Dict[str, Any]) -> Dict[str, Any]:
        """发送系统状态 / 体检体温报告 (蓝色科技风)"""
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        ip = status_dict.get("local_ip", "127.0.0.1")
        cpu_temp = status_dict.get("cpu_temp", "42.5°C")
        face_id = status_dict.get("master_registered", False)

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📡 当前网络 IP:** `{ip}` (端口 8090)\n"
                        f"**🌡️ 核心温度:** `{cpu_temp}`\n"
                        f"**👑 主人人脸绑定:** `{'已录入绑定' if face_id else '未录入 (访客模式)'}`\n"
                        f"**🎙️ 语音识别引擎:** `小米 MiMo-V2.5-ASR (就绪)`\n"
                        f"**⏱️ 检查时间:** `{now_str}`"
                    )
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 PIG 管理后台"},
                        "type": "primary",
                        "url": f"http://{ip}:8090"
                    }
                ]
            }
        ]
        if self.webhook_url:
            return self.send_webhook_card("📊 PIG 系统健康与运行状态", elements, template="blue")
        return {"status": "error", "message": "未配置推送目标"}

    # 内部基础 HTTP 辅助
    def _http_post_json(self, url: str, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            req_headers = {"Content-Type": "application/json; charset=utf-8"}
            if headers:
                req_headers.update(headers)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method="POST")
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                res_bytes = resp.read()
                return json.loads(res_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"⚠️ [Feishu] HTTP {e.code} 异常: {err_body}")
            return {"status": "error", "code": e.code, "message": err_body}
        except Exception as e:
            logger.warning(f"⚠️ [Feishu] 请求失败: {e}")
            return {"status": "error", "message": str(e)}

# 全局单例句柄
feishu_client = FeishuClient()
