# -*- coding: utf-8 -*-
"""
小米米家 (Xiaomi / Mijia) 智能家居生态控制技能
支持：
1. 账号云端免 Token 自动发现模式 (MiCloud)
2. 局域网本地直连模式 (MiIO Token / IP)
3. 自动引导生成 data/xiaomi_config.json 配置文件
"""
import os
import json
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "data", "xiaomi_config.json")

logger = logging.getLogger("XiaomiSkill")

def _ensure_config():
    """确保配置文件存在，若无则初始化引导模板"""
    if not os.path.exists(CONFIG_PATH):
        template = {
            "_help": "这是小米米家控制配置文件。你可以选择【云端账号登录】或【局域网IP直连】任意一种方式。",
            "cloud": {
                "enabled": False,
                "username": "13800000000",
                "password": "your_xiaomi_password",
                "country": "cn"
            },
            "lan_devices": {
                "客厅灯": {"ip": "192.168.1.105", "token": "在这里填入32位Token"},
                "智能插座": {"ip": "192.168.1.106", "token": "在这里填入32位Token"}
            }
        }
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(template, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"创建小米配置模板失败: {e}")
            return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取小米配置文件失败: {e}")
        return None

def run(device="台灯", action="打开", value=""):
    """
    技能执行入口：
    由大模型意图提取 device、action 与 value 后动态调用
    """
    config = _ensure_config()
    if not config:
        return "无法加载小米配置文件，请检查 data/xiaomi_config.json 是否存在。"

    cloud_cfg = config.get("cloud", {})
    lan_devs = config.get("lan_devices", {})

    dev_name = (device or "").strip()
    act = (action or "").strip()
    val = (value or "").strip()

    # 0. 判断是否为「米家 App 扫码登录/绑定」意图
    is_qr_login = any(w in act or w in dev_name for w in ["扫码", "扫一扫", "二维码", "绑定", "登录小米", "绑定米家"])
    if is_qr_login:
        try:
            from skills.xiaomi_iot.xiaomi_qr_auth import qr_auth_manager
        except Exception:
            import sys
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            if cur_dir not in sys.path:
                sys.path.insert(0, cur_dir)
            from xiaomi_qr_auth import qr_auth_manager

        # 若已有刚扫码成功的设备
        if qr_auth_manager.status == "success" and qr_auth_manager.synced_devices:
            names = [d.get("name") for d in qr_auth_manager.synced_devices if d.get("name")]
            return f"米家 App 扫码授权已成功！当前已为你自动同步 {len(names)} 个设备：{', '.join(names[:5])}。所有局域网 Token 与 IP 均已就绪，可直接语音控制！"

        res = qr_auth_manager.start_qr_session()
        if res.get("status") == "waiting":
            qr_url = res.get("qr_url")
            login_url = res.get("login_url")
            return (
                f"【米家 App 一键扫码授权】\n\n"
                f"已为你生成米家登录二维码，请打开手机【米家 App】右上角「+」->「扫一扫」直接扫描下方二维码完成授权：\n\n"
                f"![米家扫码登录]({qr_url})\n\n"
                f"👉 授权完成后系统将自动同步全家米家设备局域网 Token！\n"
                f"（备用网页登录链接：{login_url}）"
            )
        else:
            return f"获取米家扫码二维码失败: {res.get('error', '未知错误')}"

    # 1. 判断是否为「扫描/查询设备列表」意图 (排除将设备名“扫地机”误判为查询)
    is_query = (
        any(w in act for w in ["查", "看", "列出", "列表", "有哪些", "什么设备", "清单", "list"]) or
        dev_name.lower() in ["all", "所有", "全部", "所有设备", "设备列表", "设备清单"]
    )

    if is_query:
        if lan_devs:
            dev_names = list(lan_devs.keys())
            return f"米家设备扫描完毕！你账号下当前已绑定的设备有：{', '.join(dev_names)}。请问需要我帮你开或关哪一个？"
        elif cloud_cfg.get("enabled") and cloud_cfg.get("username") and cloud_cfg.get("username") != "13800000000":
            try:
                from micloud import MiCloud
                mc = MiCloud(cloud_cfg.get("username"), cloud_cfg.get("password"))
                if mc.login():
                    devs = mc.get_devices(country=cloud_cfg.get("country", "cn"))
                    names = [d.get("name") for d in devs if d.get("name")]
                    if names:
                        return f"米家设备扫描完毕！你账号下当前绑定的设备有：{', '.join(names)}。请问需要我帮你开或关哪一个？"
                    return "米家账号登录成功，但当前账号下未发现绑定的智能设备。"
                return "米家云端登录失败，请检查 data/xiaomi_config.json 中的账号和密码是否正确。"
            except Exception as e:
                return f"米家云端扫描出现异常: {e}"
        else:
            return "当前尚未检测到绑定的真实小米设备。请在 Web 工作台点击「📱 米家扫码绑定」完成授权同步！"

    # 1. 匹配目标设备
    matched_dev = None
    for k, v in lan_devs.items():
        aliases = v.get("aliases", []) if isinstance(v, dict) else []
        name = v.get("name", "") if isinstance(v, dict) else ""
        if dev_name in k or k in dev_name or any(dev_name in a or a in dev_name for a in aliases) or dev_name in name:
            matched_dev = (k, v)
            break

    if not matched_dev:
        all_devs = list(lan_devs.keys())
        dev_list_str = "、".join(all_devs) if all_devs else "暂无"
        return f"未找到名为【{dev_name}】的小米设备。当前已绑定的设备有：{dev_list_str}。请确认设备名称或先在米家 App 中查看。"

    k_name, v_info = matched_dev
    did = v_info.get("did")
    model = v_info.get("model", "")
    combined_str = f"{act} {val}".strip()

    # 2. 动态接入通用 MIoT 物模型驱动引擎 (Universal MIoT Spec Engine)
    # 不针对单个设备硬编码，全生态设备通过官方物模型自适应调度
    try:
        from miot_spec import MiotSpecManager
    except ImportError:
        from skills.xiaomi_iot.miot_spec import MiotSpecManager

    spec_type = v_info.get("spec_type", "")
    spec_data = MiotSpecManager.get_spec(spec_type, model) if spec_type else None

    payload_params, action_call, actions_done = MiotSpecManager.parse_universal_intent(
        spec=spec_data,
        combined_str=combined_str,
        did=str(did),
        model=model
    )

    if not payload_params and not action_call:
        return f"未能识别对【{k_name}】的操作意图【{combined_str}】。请指明具体的开关或控制指令。"

    # 优先使用云端官方 API 执行 (跨子网 100% 连通)
    if cloud_cfg.get("enabled") and cloud_cfg.get("serviceToken") and cloud_cfg.get("ssecurity") and did:
        try:
            try:
                from xiaomi_qr_auth import XiaomiQRAuth
            except ImportError:
                from skills.xiaomi_iot.xiaomi_qr_auth import XiaomiQRAuth
            auth = XiaomiQRAuth()
            auth.userId = cloud_cfg.get("userId")
            auth._serviceToken = cloud_cfg.get("serviceToken")
            auth._ssecurity = cloud_cfg.get("ssecurity")

            if action_call:
                # 执行 MIoT 专用 Action (/miotspec/action，如扫地机回充、启停)
                url = auth.get_api_url(cloud_cfg.get("country", "cn")) + "/miotspec/action"
                req_params = {"data": json.dumps({"params": {"did": str(did), "siid": action_call["siid"], "aiid": action_call["aiid"], "in": []}})}
                resp = auth.execute_api_call_encrypted(url, req_params)

                if resp is None:
                    return f"控制【{k_name}】失败：米家云端未返回响应，请检查网络连接。"
                if resp.get("code") == 0:
                    return f"已通过米家云端成功向【{k_name}】下达指令：【{action_call['desc']}】！"
                elif resp.get("code") in [401, 10001, -9999]:
                    return f"控制【{k_name}】失败：米家云端凭证已过期 (code: {resp.get('code')})。请在 Web 工作台重新扫码授权。"
                else:
                    err_msg = resp.get("message") or resp.get("error") or "未知错误"
                    return f"控制【{k_name}】失败：米家云端返回错误 (code: {resp.get('code')}, 原因: {err_msg})。"

            # 执行 MIoT 属性设置 (/miotspec/prop/set)
            url = auth.get_api_url(cloud_cfg.get("country", "cn")) + "/miotspec/prop/set"
            req_params = {"data": json.dumps({"params": payload_params})}
            resp = auth.execute_api_call_encrypted(url, req_params)

            if resp is None:
                return f"控制【{k_name}】失败：米家云端未返回响应，请检查网络连接。"

            code = resp.get("code")
            if code == 0:
                res_list = resp.get("result", [])
                failed_items = [it for it in res_list if isinstance(it, dict) and it.get("code", 0) != 0]
                if failed_items:
                    codes = [f"siid:{it.get('siid')}/code:{it.get('code')}" for it in failed_items]
                    return f"控制【{k_name}】部分指令执行失败 ({', '.join(codes)})，设备可能已离线或当前模式不支持该参数。"
                actions_desc = "并将" + "、".join(actions_done[1:]) if len(actions_done) > 1 else actions_done[0]
                return f"已通过米家云端成功为你将【{k_name}】{actions_done[0] if len(actions_done) == 1 else actions_done[0] + '并' + '、'.join(actions_done[1:])}！"
            elif code in [401, 10001, -9999]:
                return f"控制【{k_name}】失败：米家云端授权凭证已过期 (code: {code})。请在 Web 工作台点击「📱 米家扫码绑定」重新扫码刷新授权。"
            else:
                err_msg = resp.get("message") or resp.get("error") or "未知错误"
                return f"控制【{k_name}】失败：米家云端返回错误 (code: {code}, 原因: {err_msg})。"

        except Exception as e:
            logger.warning(f"米家云端控制异常: {e}")
            return f"控制【{k_name}】失败：云端指令下发异常 ({e})。"

    # 回退到局域网直连模式 (需同一局域网并具备有效 Token)
    ip = v_info.get("ip")
    token = v_info.get("token")
    if ip and token and len(token) == 32 and "填入" not in token:
        try:
            from miio import Device
            dev = Device(ip, token)

            if action_call:
                lan_cmd = action_call.get("lan_cmd", "app_start")
                dev.send(lan_cmd)
                return f"已通过局域网直连向【{k_name} ({ip})】发送指令成功执行【{action_call['desc']}】！"

            power_val = any(p.get("value") is True for p in payload_params if p.get("piid") == 1)
            power_cmd = "on" if power_val else "off"

            if "yeelink" in model or "lamp" in model:
                dev.send("set_power", [power_cmd, "smooth", 500])
            else:
                dev.send("set_power", [power_cmd])
            return f"已通过局域网直连向【{k_name} ({ip})】发送指令成功！"
        except Exception as e:
            return f"控制【{k_name}】失败：局域网连接超时或通信异常 ({e})。"

    return f"控制【{k_name}】失败：未配置云端授权凭证或有效局域网 Token。请先扫码绑定米家。"
