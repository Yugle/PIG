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

    dev_name = device.strip()
    act = action.strip()
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
        if dev_name in k or k in dev_name:
            matched_lan = (k, v)
            break

    if matched_lan:
        k_name, v_info = matched_lan
        ip = v_info.get("ip")
        token = v_info.get("token")
        if ip and token and len(token) == 32 and "填入" not in token:
            try:
                from miio import Device
                dev = Device(ip, token)
                if power_on:
                    dev.send("set_power", ["on"])
                else:
                    dev.send("set_power", ["off"])
                return f"已通过局域网直接向【{k_name} ({ip})】发送指令成功执行【{act}】！"
            except Exception as e:
                return f"局域网连接【{k_name}】失败: {e}"

    # 3. 尚未配置真实凭据，给出友好引导与模拟确认
    return (
        f"【小米米家技能就绪】指令已识别为【{dev_name}】->【{act}】！\n"
        f"提示：若要真正控制你家里的实体米家电器，请打开项目中的 data/xiaomi_config.json ，"
        f"将 cloud.enabled 设为 true 并填入小米账号密码，或填入局域网设备 IP 与 Token 即可立即生效！"
    )
