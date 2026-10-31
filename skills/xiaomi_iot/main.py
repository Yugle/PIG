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

    # 1. 判断是否为「扫描/查询设备列表」意图
    is_query = any(w in act or w in dev_name for w in ["查", "扫", "看", "列出", "列表", "什么设备", "有哪些", "list", "all", "所有"])

    if is_query:
        # A. 云端模式真实扫描
        if cloud_cfg.get("enabled") and cloud_cfg.get("username") != "13800000000":
            try:
                from micloud import MiCloud
                mc = MiCloud(cloud_cfg.get("username"), cloud_cfg.get("password"))
                if mc.login():
                    devs = mc.get_devices(country=cloud_cfg.get("country", "cn"))
                    names = [d.get("name") for d in devs if d.get("name")]
                    if names:
                        return f"米家真实设备扫描完毕！你账号下当前绑定的设备有：{', '.join(names)}。请问需要我帮你开或关哪一个？"
                    else:
                        return "米家账号登录成功，但当前账号下未发现绑定的智能设备。"
                else:
                    return "米家云端登录失败，请检查 data/xiaomi_config.json 中的账号和密码是否正确。"
            except ImportError:
                return "树莓派尚未安装 micloud 库，安装后即可自动扫描云端设备。"
            except Exception as e:
                return f"米家云端扫描出现异常: {e}"

        # B. 局域网模式设备扫描
        valid_lan = [k for k, v in lan_devs.items() if v.get("token") and "填入" not in v.get("token")]
        if valid_lan:
            return f"局域网米家设备扫描完毕！当前已配置的本地设备有：{', '.join(valid_lan)}。需要我帮你控制哪一个？"

        # C. 尚未配置真实凭据，真实如实告知（绝不瞎编幻觉）
        return (
            "【米家设备扫描结果】报告主人，当前尚未检测到绑定的真实小米设备！\n"
            "因为 data/xiaomi_config.json 里的账号和局域网 Token 目前仍为默认初始模板。"
            "只要在配置文件中填入你的小米账号密码，我就能真正扫出你家里的全部电器啦！"
        )

    act_lower = act.lower()
    power_on = any(w in act_lower for w in ["开", "启动", "运行", "亮", "on", "open", "true", "1"])

    # 1. 匹配目标设备
    matched_dev = None
    for k, v in lan_devs.items():
        aliases = v.get("aliases", []) if isinstance(v, dict) else []
        name = v.get("name", "") if isinstance(v, dict) else ""
        if dev_name in k or k in dev_name or any(dev_name in a or a in dev_name for a in aliases) or dev_name in name:
            matched_dev = (k, v)
            break

    # 2. 优先尝试：使用扫码授权获取的官方云端凭证 (跨局域网/跨子网 100% 连通)
    if cloud_cfg.get("enabled") and cloud_cfg.get("serviceToken") and cloud_cfg.get("ssecurity"):
        if matched_dev:
            k_name, v_info = matched_dev
            did = v_info.get("did")
            if did:
                try:
                    try:
                        from xiaomi_qr_auth import XiaomiQRAuth
                    except ImportError:
                        from skills.xiaomi_iot.xiaomi_qr_auth import XiaomiQRAuth
                    auth = XiaomiQRAuth()
                    auth.userId = cloud_cfg.get("userId")
                    auth._serviceToken = cloud_cfg.get("serviceToken")
                    auth._ssecurity = cloud_cfg.get("ssecurity")

                    # 智能解析 MIoT 物模型高级属性：色温、亮度、水温、开关
                    import re
                    payload_params = []
                    detail_msg = act
                    combined_str = f"{act} {val}"

                    # 1. 色温调节 (siid 2, piid 3, 2600K ~ 5000K)
                    if any(w in combined_str for w in ["色温", "暖光", "黄光", "冷光", "白光", "阅读", "温馨", "护眼", "暖"]):
                        ct_val = 3000
                        if any(w in combined_str for w in ["暖", "黄", "温馨", "夜间"]):
                            ct_val = 2700
                        elif any(w in combined_str for w in ["冷", "白", "亮白"]):
                            ct_val = 4800
                        elif any(w in combined_str for w in ["阅读", "办公", "看书", "护眼"]):
                            ct_val = 4000
                        m = re.search(r"(\d{4})", combined_str)
                        if m:
                            ct_val = max(2600, min(5000, int(m.group(1))))
                        payload_params.append({"did": str(did), "siid": 2, "piid": 3, "value": ct_val})
                        payload_params.append({"did": str(did), "siid": 2, "piid": 1, "value": True})
                        detail_msg = f"色温调节至 {ct_val}K (暖光模式)" if ct_val < 3500 else f"色温调节至 {ct_val}K"

                    # 2. 亮度调节 (siid 2, piid 2, 1 ~ 100)
                    elif any(w in combined_str for w in ["亮度", "调亮", "调暗", "亮一点", "暗一点", "暗", "亮"]):
                        br_val = 70
                        if "暗" in combined_str:
                            br_val = 25
                        elif "亮" in combined_str:
                            br_val = 85
                        m = re.search(r"(\d{1,3})", combined_str)
                        if m:
                            br_val = max(1, min(100, int(m.group(1))))
                        payload_params.append({"did": str(did), "siid": 2, "piid": 2, "value": br_val})
                        payload_params.append({"did": str(did), "siid": 2, "piid": 1, "value": True})
                        detail_msg = f"亮度调节至 {br_val}%"

                    # 3. 水温/恒温调节 (电水壶 siid 3, piid 4)
                    elif any(w in combined_str for w in ["水温", "恒温", "度", "保温"]):
                        temp_val = 55
                        m = re.search(r"(\d{1,2})", combined_str)
                        if m:
                            temp_val = max(40, min(90, int(m.group(1))))
                        payload_params.append({"did": str(did), "siid": 3, "piid": 4, "value": temp_val})
                        detail_msg = f"目标水温设定至 {temp_val}°C"

                    # 4. 常规开关指令
                    else:
                        payload_params.append({"did": str(did), "siid": 2, "piid": 1, "value": power_on})
                        detail_msg = "打开" if power_on else "关闭"

                    url = auth.get_api_url(cloud_cfg.get("country", "cn")) + "/miotspec/prop/set"
                    req_params = {"data": json.dumps({"params": payload_params})}
                    resp = auth.execute_api_call_encrypted(url, req_params)

                    if resp and resp.get("code") == 0:
                        return f"已通过米家云端成功为你将【{k_name}】执行【{detail_msg}】！"
                    elif resp and "result" in resp and resp["result"] and resp["result"][0].get("code") == 0:
                        return f"已成功为你将【{k_name}】执行【{detail_msg}】！"
                except Exception as e:
                    logger.warning(f"米家云端控制异常，尝试局域网回退: {e}")

    # 3. 局域网直连模式 (同一 WiFi / 子网毫秒级低延迟控制)
    if matched_dev:
        k_name, v_info = matched_dev
        ip = v_info.get("ip")
        token = v_info.get("token")
        model = v_info.get("model", "")
        if ip and token and len(token) == 32 and "填入" not in token:
            try:
                from miio import Device
                dev = Device(ip, token)
                if "yeelink" in model or "lamp" in model:
                    if power_on:
                        dev.send("set_power", ["on", "smooth", 500])
                    else:
                        dev.send("set_power", ["off", "smooth", 500])
                else:
                    cmd_val = ["on"] if power_on else ["off"]
                    try:
                        dev.send("set_power", cmd_val)
                    except Exception:
                        dev.send("set_properties", [{"did": v_info.get("name", "dev"), "siid": 2, "piid": 1, "value": power_on}])
                return f"已通过局域网直连向【{k_name} ({ip})】发送指令成功执行【{act}】！"
            except Exception as e:
                return f"控制【{k_name}】失败: 局域网连接超时 ({e})，且云端控制未就绪。"

    # 4. 尚未配置真实凭据，给出友好引导
    return (
        f"【小米米家技能就绪】指令已识别为【{dev_name}】->【{act}】！\n"
        f"提示：若要真正控制实体电器，请在 Web 工作台点击「📱 米家扫码绑定」完成授权即可立即生效！"
    )
