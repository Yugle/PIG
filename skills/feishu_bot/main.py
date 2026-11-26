"""
PIG Skill: feishu_bot (飞书智能助理与消息中心)
"""

import os
import re
import json
import time
import threading
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


def on_evolution_diary(diary_text: str = "", radar_dict=None, intimacy_level: int = 1, persona_tag: str = ""):
    """响应夜间自进化反思事件，向已配置的飞书端推送日记卡片"""
    if feishu_client.is_configured:
        feishu_client.send_evolution_card(
            diary_text=diary_text or "今日陪伴打卡",
            radar_dict=radar_dict or {},
            intimacy_level=intimacy_level,
            persona_tag=persona_tag,
        )


def get_screen_item() -> dict:
    if feishu_client.ws_connected:
        val = "长连在线"
    elif feishu_client.is_configured:
        val = "已配置"
    else:
        val = "未配置"
    return {
        "id": "feishu",
        "skill_id": "feishu_bot",
        "icon": "chat",
        "name": "飞书网关",
        "val": val,
        "type": "info",
        "screen_order": 30,
    }


def _handle_feishu_incoming_message(ctx: dict, clean_text: str, open_id: str, chat_id: str, msg_id: str = ""):
    """统一处理飞书传入消息 (无论是 WebSocket 长连接还是 HTTP Webhook 回调)"""
    skill_mgr = ctx.get("skill_manager")
    mem = ctx.get("memory_manager")
    evolution_engine = ctx.get("evolution_engine")
    load_config = ctx.get("load_config")

    def _worker():
        try:
            target_id = chat_id if chat_id else open_id
            id_type = "chat_id" if chat_id else "open_id"

            text_lower = clean_text.strip().lower()
            snapshot_triggers = [
                "看看家", "拍照", "拍张照", "拍个照", "看下家", "监控", "看一眼",
                "巡视", "巡检", "安防", "家里安全", "看看客厅", "看看周围",
                "拍张照片", "照一张", "拍一下", "看看现场", "抓拍", "拍一张"
            ]
            if any(k in text_lower for k in snapshot_triggers) and skill_mgr and "remote_guard" in skill_mgr.installed_plugins:
                print(f"📷 [Feishu Bot] 命中远程看家与监控抓拍意图: '{clean_text}'")
                dir_val = "center"
                if any(w in text_lower for w in ["向左", "往左", "左边", "左侧", "左看"]):
                    dir_val = "left"
                elif any(w in text_lower for w in ["向右", "往右", "右边", "右侧", "右看"]):
                    dir_val = "right"
                elif any(w in text_lower for w in ["抬头", "向上", "天花板", "高处", "上看"]):
                    dir_val = "up"
                elif any(w in text_lower for w in ["低头", "向下", "地面", "桌面", "低处", "下看"]):
                    dir_val = "down"
                skill_mgr.execute_skill(
                    "remote_guard",
                    timeout_seconds=25.0,
                    direction=dir_val,
                    question=clean_text,
                    target_id=target_id,
                    id_type=id_type,
                )
                return

            from core.agent_reasoner import AgentReasoner
            from core.prompt_builder import PromptBuilder

            c_cfg = load_config() if callable(load_config) else {}
            reasoner = AgentReasoner(
                api_key=c_cfg.get("api_key") or c_cfg.get("mimo_api_key") or "",
                base_url=c_cfg.get("base_url") or "https://api.xiaomimimo.com/v1",
                model=c_cfg.get("llm_model") or "mimo-v2.6-flash",
                max_steps=4,
                enable_thinking=c_cfg.get("llm_thinking", False),
            )
            sys_prompt = PromptBuilder.build_base_prompt(max_reply_length=300, skill_manager=skill_mgr)
            sid = f"feishu_{chat_id or open_id}"

            init_card = {
                "config": {"wide_screen_mode": True, "update_multi": True},
                "header": {"title": {"tag": "plain_text", "content": "🤖 PIG 正在处理..."}, "template": "blue"},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "⏳ **正在分析意图并规划任务链路...**"}}],
            }
            msg_res = feishu_client.send_im_message(target_id, "interactive", init_card, receive_id_type=id_type)
            msg_id_cur = msg_res.get("data", {}).get("message_id") if msg_res.get("code") == 0 else None

            start_t = time.time()
            skills_used = []

            def _on_feishu_step(ev: dict):
                if not msg_id_cur or ev.get("stage") != "tool_calling":
                    return
                t_name = ev.get("tool_name", "")
                meta = skill_mgr.resolve_plugin_meta(t_name) if skill_mgr else {}
                s_label = meta.get("name") or f"🛠️ {t_name}"
                skills_used.append(s_label)
                thought_text = str(ev.get("thought") or "").strip()
                thought_line = f"💭 **决策思考**：{thought_text}\n" if thought_text else ""
                step_num = ev.get("step", 1)
                call_elements = [{
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"{thought_line}⚙️ **正在调用技能 (第 {step_num} 步)**：`{s_label}`\n⏳ **执行状态**：正在与后端服务交互，请稍候片刻...",
                    },
                }]
                feishu_client.update_im_card(msg_id_cur, "⚙️ PIG 正在执行任务...", call_elements, template="turquoise")

            print(f"🤖 [Feishu Bot] 正在为会话 {sid} 进行思考推演 (动态流式卡片模式): '{clean_text}'...")
            res = reasoner.run_react_loop(
                user_text=clean_text,
                base_sys_prompt=sys_prompt,
                memory_manager=mem,
                skill_manager=skill_mgr,
                evolution_engine=evolution_engine,
                session_id=sid,
                silent=True,
                on_step_callback=_on_feishu_step,
            )
            reply_text = res.get("reply", "小猪收到啦！")
            elapsed = round(time.time() - start_t, 1)

            if msg_id_cur:
                final_elements = [{"tag": "div", "text": {"tag": "lark_md", "content": reply_text}}]
                if skills_used:
                    chain_str = " ➔ ".join(skills_used)
                    final_elements.append({"tag": "hr"})
                    final_elements.append({
                        "tag": "note",
                        "elements": [{"tag": "plain_text", "content": f"⚡ 技能链路：{chain_str} · 总耗时 {elapsed}s · PIG Robot OS"}],
                    })
                card_title = "💬 PIG 回复" if not skills_used else "✅ PIG 执行完毕"
                update_res = feishu_client.update_im_card(msg_id_cur, card_title, final_elements, template="blue")
                if update_res.get("code") == 0:
                    print(f"📤 [Feishu Bot] 已原地动态更新卡片至飞书 {msg_id_cur} (耗时 {elapsed}s)")
                    return

            send_res = feishu_client.send_im_message(target_id, "text", {"text": reply_text}, receive_id_type=id_type)
            print(f"📤 [Feishu Bot] 已回复消息至飞书 {target_id}: '{reply_text[:50]}...' -> {send_res.get('code', send_res.get('status'))}")
        except Exception as e:
            print(f"❌ [Feishu Bot] 处理消息失败: {e}")

    threading.Thread(target=_worker, daemon=True, name="FeishuMsgWorker").start()


def _sync_feishu_service(feishu_cfg: dict, ctx: dict):
    if not isinstance(feishu_cfg, dict):
        return
    feishu_client.update_config(feishu_cfg)
    if feishu_client.app_id and feishu_client.app_secret:
        feishu_client.start_ws_service(
            lambda txt, oid, cid, mid="": _handle_feishu_incoming_message(ctx, txt, oid, cid, mid)
        )


def on_startup(ctx: dict):
    load_config = ctx.get("load_config")
    cfg = load_config() if callable(load_config) else {}
    if "feishu" in cfg:
        _sync_feishu_service(cfg["feishu"], ctx)


def on_config_update(cfg: dict, updates: dict, ctx: dict):
    if isinstance(updates, dict) and "feishu" in updates and isinstance(cfg.get("feishu"), dict):
        _sync_feishu_service(cfg["feishu"], ctx)


def handle_http(method: str, path: str, handler, ctx: dict) -> bool:
    req_path = path.split("?", 1)[0]
    mem = ctx.get("memory_manager")
    skill_mgr = ctx.get("skill_manager")
    evolution_engine = ctx.get("evolution_engine")

    if method == "GET" and req_path == "/api/feishu/status":
        handler._send_json({
            "configured": feishu_client.is_configured,
            "has_webhook": bool(feishu_client.webhook_url and "demo" not in feishu_client.webhook_url),
            "has_app": bool(feishu_client.app_id and feishu_client.app_secret),
            "ws_connected": feishu_client.ws_connected,
            "last_open_id": feishu_client.last_user_open_id,
            "active_sessions": mem.list_active_sessions() if mem else {},
        })
        return True

    if req_path == "/api/feishu/test_snapshot":
        try:
            if not skill_mgr or "remote_guard" not in skill_mgr.installed_plugins:
                handler._send_json({"status": "error", "message": "未安装 remote_guard 技能"})
                return True
            report = skill_mgr.execute_skill(
                "remote_guard",
                timeout_seconds=25.0,
                direction="center",
                question="前端设置页一键远程巡检抓拍测试",
            )
            handler._send_json({"status": "ok", "message": "现场抓拍与安防卡片已成功触发并推向飞书！", "report": report})
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)})
        return True

    if method == "POST" and req_path == "/api/feishu/webhook":
        try:
            body = handler._read_json_body()
            if body.get("type") == "url_verification":
                handler._send_json({"challenge": body.get("challenge", "")})
                return True
            event = body.get("event", {})
            if event:
                msg = event.get("message", {})
                if msg.get("message_type") == "text":
                    content_json = json.loads(msg.get("content", "{}"))
                    clean_text = re.sub(r"@_user_\d+\s*", "", content_json.get("text", "")).strip()
                    sender = event.get("sender", {}).get("sender_id", {})
                    if clean_text:
                        _handle_feishu_incoming_message(
                            ctx, clean_text, sender.get("open_id", ""), msg.get("chat_id", ""), msg.get("message_id", "")
                        )
            handler._send_json({"status": "ok"})
        except Exception as e:
            handler.send_error(500, str(e))
        return True

    if method == "POST" and req_path == "/api/feishu/test_notify":
        try:
            body = handler._read_json_body()
            msg_type = body.get("type", "alert")
            title = body.get("title", "PIG 机器人测试告警")
            content = body.get("content", "这是一条来自树莓派 PIG 的测试推送！飞书告警与通知通道已打通。")
            if msg_type == "diary":
                p_data = evolution_engine.get_personality() if evolution_engine else {}
                res = feishu_client.send_evolution_card(
                    diary_text=content,
                    radar_dict=p_data.get("radar", {}),
                    intimacy_level=p_data.get("intimacy_level", 2),
                    persona_tag=p_data.get("persona_tag", "元气治愈系桌宠"),
                )
            else:
                res = feishu_client.send_alert_card(title, content, detail="硬件健康正常 | 多 Session 架构就绪")
            if isinstance(res, dict) and res.get("status") == "error":
                handler._send_json({"status": "error", "message": res.get("message", "推送失败"), "result": res})
                return True
            if isinstance(res, dict) and res.get("code", 0) != 0 and "msg" in res:
                handler._send_json({"status": "error", "message": f"飞书返回错误码 {res.get('code')}: {res.get('msg')}", "result": res})
                return True
            handler._send_json({"status": "ok", "result": res})
        except Exception as e:
            handler.send_error(500, str(e))
        return True

    return False
