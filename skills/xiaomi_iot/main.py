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

def run(device="客厅灯", action="打开"):
    """
    技能执行入口：
    由大模型意图提取 device 与 action 后动态调用
    """
    config = _ensure_config()
    if not config:
        return "无法加载小米配置文件，请检查 data/xiaomi_config.json 是否存在。"

    cloud_cfg = config.get("cloud", {})
    lan_devs = config.get("lan_devices", {})

    dev_name = (device or "").strip()
    act = (action or "").strip()

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

    power_on = any(w in act for w in ["开", "启动", "运行", "亮"])

    # 1. 尝试云端账号模式
    if cloud_cfg.get("enabled") and cloud_cfg.get("username") != "13800000000":
        user = cloud_cfg.get("username")
        pwd = cloud_cfg.get("password")
        country = cloud_cfg.get("country", "cn")
        try:
            from micloud import MiCloud
            mc = MiCloud(user, pwd)
            if mc.login():
                devices = mc.get_devices(country=country)
                target = None
                for d in devices:
                    if dev_name in d.get("name", ""):
                        target = d
                        break
                if target:
                    did = target["did"]
                    # MIoT Spec 标准开关: siid 2, piid 1
                    mc.send_iot_spec_request(
                        "/miotspec/prop/set",
                        {"params": [{"did": did, "siid": 2, "piid": 1, "value": power_on}]},
                        country=country
                    )
                    return f"已通过米家云端成功为你将【{target.get('name')}】执行【{act}】操作！"
                else:
                    known = [d.get("name") for d in devices if d.get("name")]
                    return f"在你的小米账号下未找到【{dev_name}】，已知绑定的设备有: {', '.join(known[:5])}等。"
            else:
                return "小米云端登录失败，请检查 data/xiaomi_config.json 中的账号和密码是否正确。"
        except ImportError:
            return "树莓派尚未安装 micloud 库，可通过局域网配置或安装 pip install micloud 启用云端。"
        except Exception as e:
            return f"连接米家云端控制出现异常: {e}"

    # 2. 尝试局域网直连模式 (根据配置文件中的匹配设备)
    matched_lan = None
    for k, v in lan_devs.items():
        aliases = v.get("aliases", []) if isinstance(v, dict) else []
        name = v.get("name", "") if isinstance(v, dict) else ""
        if dev_name in k or k in dev_name or any(dev_name in a or a in dev_name for a in aliases) or dev_name in name:
            matched_lan = (k, v)
            break

    if matched_lan:
        k_name, v_info = matched_lan
        ip = v_info.get("ip")
        token = v_info.get("token")
        model = v_info.get("model", "")
        if ip and token and len(token) == 32 and "填入" not in token:
            try:
                from miio import Device
                dev = Device(ip, token)
                # 针对常见设备发送开关指令
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
                        # 尝试 miot spec 指令
                        dev.send("set_properties", [{"did": v_info.get("name", "dev"), "siid": 2, "piid": 1, "value": power_on}])
                return f"已通过局域网直接向【{k_name} ({ip})】发送指令成功执行【{act}】！"
            except Exception as e:
                return f"局域网连接【{k_name} ({ip})】发送指令: {e}"

    # 3. 尚未配置真实凭据，给出友好引导与模拟确认
    return (
        f"【小米米家技能就绪】指令已识别为【{dev_name}】->【{act}】！\n"
        f"提示：若要真正控制你家里的实体米家电器，请打开项目中的 data/xiaomi_config.json ，"
        f"将 cloud.enabled 设为 true 并填入小米账号密码，或填入局域网设备 IP 与 Token 即可立即生效！"
    )
