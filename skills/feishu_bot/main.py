"""
PIG Skill: feishu_bot (飞书智能助理与消息中心)
"""

import os
import json
from skills.feishu_bot.feishu_client import feishu_client

def run(action="send_status", title="PIG 状态汇报", content="系统运行正常", **kwargs):
    """
    飞书机器人技能调用入口
    :param action: "send_alert", "send_diary", "send_status", "send_text"
    :param title: 卡片标题
    :param content: 内容或错误信息
    """
    # 动态载入配置 (优先 data/config.json 或环境变量)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg_path = os.path.join(project_root, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                feishu_cfg = cfg.get("feishu", {})
                if feishu_cfg:
                    feishu_client.update_config(feishu_cfg)
        except Exception:
            pass

    act = (action or "send_status").lower()

    if not feishu_client.is_configured:
        return "⚠️ 未配置飞书 Webhook 或 App 凭据，请在设置中填入飞书机器人 Webhook URL。"

    if act in ["send_alert", "alert", "error", "warn"]:
        res = feishu_client.send_alert_card(title=title, error_msg=content, detail=kwargs.get("detail", ""))
        return f"已向飞书推送【故障告警卡片】：{title}"

    elif act in ["send_diary", "diary", "evolution"]:
        # 读取当前五维性格
        try:
            perso_path = os.path.join(project_root, "data", "personality_profile.json")
            with open(perso_path, "r", encoding="utf-8") as f:
                p_data = json.load(f)
                radar = p_data.get("radar", {})
                intimacy = p_data.get("intimacy_level", 1)
                tag = p_data.get("persona_tag", "")
        except Exception:
            radar = {"lively": 75, "clingy": 66, "sharp_tongued": 22, "empathy": 88, "curiosity": 79}
            intimacy = 2
            tag = "伴侣"

        res = feishu_client.send_evolution_card(content, radar, intimacy, tag)
        return "已向飞书推送【夜间伴侣自进化日记卡片】！"

    elif act in ["send_status", "status"]:
        # 获取状态
        status_dict = {
            "local_ip": kwargs.get("local_ip", "127.0.0.1"),
            "cpu_temp": kwargs.get("cpu_temp", "43.2°C"),
            "master_registered": os.path.exists(os.path.join(project_root, "data", "master_face.json"))
        }
        res = feishu_client.send_status_card(status_dict)
        return "已向飞书推送【系统状态健康卡片】！"

    elif act in ["send_text", "text"]:
        res = feishu_client.send_webhook_text(content)
        return f"已向飞书发送纯文本：{content}"

    return f"未知动作类型：{act}"
