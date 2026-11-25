"""
Xiaomi QR Code Authentication & Automatic Device Sync Module
Supports scanning QR code via Mi Home App to bypass cloud captchas & SMS verification,
then automatically extracts LAN tokens and updates data/xiaomi_config.json.
"""

import os
import sys
import time
import json
import base64
import random
import hashlib
import hmac
import threading
import requests
from pathlib import Path

try:
    from Cryptodome.Cipher import ARC4
except ImportError:
    from Crypto.Cipher import ARC4

SERVERS = ["cn", "de", "us", "ru", "tw", "sg", "in", "i2"]

class XiaomiQRAuth:
    def __init__(self, config_path=None):
        self.config_path = config_path or Path(__file__).resolve().parent.parent.parent / "data" / "xiaomi_config.json"
        self._session = requests.Session()
        self._agent = self.generate_agent()
        self._device_id = self.generate_device_id()
        self._ssecurity = None
        self.userId = None
        self._serviceToken = None
        self._cUserId = None
        self._pass_token = None
        self._location = None
        
        # QR 状态
        self.status = "idle"  # idle, waiting, success, expired, error
        self.qr_url = None
        self.login_url = None
        self.lp_url = None
        self.timeout = 300
        self.error_msg = ""
        self.synced_devices = []
        self._poll_thread = None
        self._session_id = 0

    @staticmethod
    def generate_agent():
        agent_id = "".join(map(lambda i: chr(i), [random.randint(65, 69) for _ in range(13)]))
        random_text = "".join(map(lambda i: chr(i), [random.randint(97, 122) for _ in range(18)]))
        return f"{random_text}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"

    @staticmethod
    def generate_device_id():
        return "".join(map(lambda i: chr(i), [random.randint(97, 122) for _ in range(6)]))

    @staticmethod
    def to_json(response_text):
        return json.loads(response_text.replace("&&&START&&&", ""))

    @staticmethod
    def encrypt_rc4(password, payload):
        r = ARC4.new(base64.b64decode(password))
        r.encrypt(bytes(1024))
        return base64.b64encode(r.encrypt(payload.encode())).decode()

    @staticmethod
    def decrypt_rc4(password, payload):
        r = ARC4.new(base64.b64decode(password))
        r.encrypt(bytes(1024))
        return r.encrypt(base64.b64decode(payload))

    def signed_nonce(self, nonce):
        hash_object = hashlib.sha256(base64.b64decode(self._ssecurity) + base64.b64decode(nonce))
        return base64.b64encode(hash_object.digest()).decode("utf-8")

    @staticmethod
    def generate_nonce(millis):
        nonce_bytes = os.urandom(8) + (int(millis / 60000)).to_bytes(4, byteorder="big")
        return base64.b64encode(nonce_bytes).decode()

    @staticmethod
    def generate_enc_signature(url, method, signed_nonce, params):
        signature_params = [str(method).upper(), url.split("com")[1].replace("/app/", "/")]
        for k, v in params.items():
            signature_params.append(f"{k}={v}")
        signature_params.append(signed_nonce)
        signature_string = "&".join(signature_params)
        return base64.b64encode(hashlib.sha1(signature_string.encode("utf-8")).digest()).decode()

    def generate_enc_params(self, url, method, signed_nonce, nonce, params, ssecurity):
        params["rc4_hash__"] = self.generate_enc_signature(url, method, signed_nonce, params)
        for k, v in params.items():
            params[k] = self.encrypt_rc4(signed_nonce, v)
        params.update({
            "signature": self.generate_enc_signature(url, method, signed_nonce, params),
            "ssecurity": ssecurity,
            "_nonce": nonce,
        })
        return params

    def execute_api_call_encrypted(self, url, params):
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self.userId),
            "yetAnotherServiceToken": str(self._serviceToken),
            "serviceToken": str(self._serviceToken),
            "locale": "zh_CN",
            "timezone": "GMT+08:00",
            "channel": "MI_APP_STORE"
        }
        millis = round(time.time() * 1000)
        nonce = self.generate_nonce(millis)
        signed_nonce = self.signed_nonce(nonce)
        fields = self.generate_enc_params(url, "POST", signed_nonce, nonce, params, self._ssecurity)
        response = self._session.post(url, headers=headers, cookies=cookies, params=fields)
        if response.status_code == 200:
            decoded = self.decrypt_rc4(self.signed_nonce(fields["_nonce"]), response.text)
            return json.loads(decoded)
        return None

    def get_api_url(self, country="cn"):
        return "https://" + ("" if country == "cn" else (country + ".")) + "api.io.mi.com/app"

    def get_homes(self, country="cn"):
        url = self.get_api_url(country) + "/v2/homeroom/gethome"
        params = {"data": '{"fg": true, "fetch_share": true, "fetch_share_dev": true, "limit": 300, "app_ver": 7}'}
        return self.execute_api_call_encrypted(url, params)

    def get_devices(self, country, home_id, owner_id):
        url = self.get_api_url(country) + "/v2/home/home_device_list"
        params = {
            "data": '{"home_owner": ' + str(owner_id) +
            ',"home_id": ' + str(home_id) +
            ', "limit": 200, "get_split_device": true, "support_smart_home": true}'
        }
        return self.execute_api_call_encrypted(url, params)

    def start_qr_session(self):
        """发起米家扫码登录流程，返回二维码图片链接和轮询状态"""
        self.qr_url = ""
        self.login_url = ""
        self.lp_url = ""
        self.local_qr_path = None
        old_qr_file = self.config_path.parent / "xiaomi_qr.png"
        if old_qr_file.exists():
            try:
                old_qr_file.unlink()
            except Exception:
                pass
        url = "https://account.xiaomi.com/longPolling/loginUrl"
        data = {
            "_qrsize": "480",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts",
            "_hasLogo": "false",
            "sid": "xiaomiio",
            "serviceParam": "",
            "_locale": "zh_CN",
            "_dc": str(int(time.time() * 1000))
        }
        try:
            resp = self._session.get(url, params=data, timeout=10)
            if resp.status_code == 200:
                res = self.to_json(resp.text)
                if "qr" in res and "lp" in res:
                    self.qr_url = res["qr"]
                    self.login_url = res["loginUrl"]
                    self.lp_url = res["lp"]
                    self.timeout = res.get("timeout", 300)
                    self.status = "waiting"
                    self.error_msg = ""
                    self.synced_devices = []
                    self._session_id += 1
                    current_sid = self._session_id

                    # 下载并缓存本地二维码图片，供树莓派 2.4 寸 ST7789 屏幕或 Web 实体直接调用
                    try:
                        qr_img_resp = self._session.get(self.qr_url, timeout=6)
                        if qr_img_resp.status_code == 200:
                            qr_file = self.config_path.parent / "xiaomi_qr.png"
                            with open(qr_file, "wb") as f:
                                f.write(qr_img_resp.content)
                            self.local_qr_path = str(qr_file)
                    except Exception:
                        pass

                    # 启动后台监听线程 (传递 session_id，避免旧线程竞争)
                    self._poll_thread = threading.Thread(target=self._background_poll, args=(current_sid,), daemon=True)
                    self._poll_thread.start()

                    return {
                        "status": "waiting",
                        "qr_url": self.qr_url,
                        "login_url": self.login_url,
                        "timeout": self.timeout
                    }
        except Exception as e:
            self.status = "error"
            self.error_msg = str(e)
            return {"status": "error", "error": str(e)}

        self.status = "error"
        self.error_msg = "获取二维码失败"
        return {"status": "error", "error": "获取二维码失败"}

    def _background_poll(self, session_id):
        """后台长轮询米家扫码确认结果"""
        start_time = time.time()
        while self.status == "waiting" and self._session_id == session_id:
            if time.time() - start_time > self.timeout:
                self.status = "expired"
                self.error_msg = "扫码超时，请重新刷新二维码"
                break
            try:
                resp = self._session.get(self.lp_url, timeout=15)
                if self._session_id != session_id:
                    break
                if resp.status_code == 200:
                    data = self.to_json(resp.text)
                    if "userId" in data and "ssecurity" in data:
                        self.userId = data["userId"]
                        self._ssecurity = data["ssecurity"]
                        self._cUserId = data.get("cUserId")
                        self._pass_token = data.get("passToken")
                        self._location = data.get("location")

                        # 获取 serviceToken
                        if self._location:
                            loc_resp = self._session.get(self._location, headers={"content-type": "application/x-www-form-urlencoded"}, timeout=10)
                            if loc_resp.status_code == 200 and "serviceToken" in loc_resp.cookies:
                                self._serviceToken = loc_resp.cookies["serviceToken"]
                        
                        # 登录成功，开始自动拉取并解析设备
                        self._sync_all_devices()
                        self.status = "success"
                        break
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                if self._session_id == session_id:
                    self.status = "error"
                    self.error_msg = str(e)
                break

    def _sync_all_devices(self):
        """通过获取到的凭据扫描所有家庭设备并自动写入 xiaomi_config.json"""
        all_devices = []
        try:
            homes = self.get_homes("cn")
            if homes and "result" in homes and "homelist" in homes["result"]:
                for h in homes["result"]["homelist"]:
                    hid = h.get("id")
                    owner_id = h.get("owner_id", self.userId)
                    dev_resp = self.get_devices("cn", hid, owner_id)
                    if dev_resp and "result" in dev_resp and "device_info" in dev_resp["result"]:
                        all_devices.extend(dev_resp["result"]["device_info"])

            # 整理为 lan_devices 字典格式
            lan_devices = {}
            for d in all_devices:
                name = d.get("name", "未命名设备")
                ip = d.get("localip", "")
                token = d.get("token", "")
                model = d.get("model", "")
                did = d.get("did", "")
                mac = d.get("mac", "")

                # 智能提取别名与中文主键
                key = self._guess_key(name, model)
                aliases = [name, key]
                if "lamp" in model or "light" in model or "灯" in name:
                    aliases.extend(["台灯", "米家台灯", "灯"])
                elif "water" in model or "饮水" in name:
                    aliases.extend(["饮水机", "宠物饮水机", "猫咪饮水机"])
                elif "kettle" in model or "水壶" in name:
                    aliases.extend(["水壶", "电水壶", "烧水壶"])
                elif "tv" in model or "盒子" in name or "电视" in name:
                    aliases.extend(["电视", "电视盒子"])
                elif "camera" in model or "摄像" in name:
                    aliases.extend(["摄像机", "摄像头", "监控"])

                # 去重
                aliases = list(dict.fromkeys([a for a in aliases if a]))

                lan_devices[key] = {
                    "name": name,
                    "did": did,
                    "mac": mac,
                    "ip": ip,
                    "token": token,
                    "model": model,
                    "spec_type": d.get("spec_type", ""),
                    "aliases": aliases
                }

            self.synced_devices = list(lan_devices.values())

            # 写入本地 xiaomi_config.json
            cfg = {
                "_help": "这是小米米家控制配置文件，已由米家 App 扫码一键自动同步（支持毫秒级局域网 0 延迟控制）。",
                "cloud": {
                    "enabled": True,
                    "userId": str(self.userId),
                    "serviceToken": str(self._serviceToken or ""),
                    "ssecurity": str(self._ssecurity or ""),
                    "country": "cn"
                },
                "lan_devices": lan_devices
            }

            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

        except Exception as e:
            self.error_msg = f"扫描设备失败: {e}"

    @staticmethod
    def _guess_key(name, model):
        name_lower = (name or "").lower()
        model_lower = (model or "").lower()
        if "lamp" in model_lower or "light" in model_lower or "灯" in name_lower:
            return "台灯"
        if "water" in model_lower or "饮水" in name_lower:
            return "饮水机"
        if "kettle" in model_lower or "水壶" in name_lower:
            return "水壶"
        if "tv" in model_lower or "盒子" in name_lower or "电视" in name_lower:
            return "电视盒子"
        if "camera" in model_lower or "摄像" in name_lower or "监控" in name_lower:
            return "摄像机"
        return name or "智能设备"

    def get_qr_png_bytes(self) -> bytes:
        """获取米家扫码绑定二维码的 PNG 字节流"""
        # 1. 优先读取已缓存的图片
        qr_file = self.config_path.parent / "xiaomi_qr.png"
        if qr_file.exists():
            try:
                with open(qr_file, "rb") as f:
                    return f.read()
            except Exception:
                pass

        # 2. 从 login_url 或默认 URL 生成
        target_url = self.login_url or self.qr_url or "https://account.xiaomi.com/fe/service/login/qrcode?sid=xiaomiio"
        try:
            import qrcode
            import io
            qr = qrcode.QRCode(box_size=4, border=1)
            qr.add_data(target_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return b""

    def get_qr_base64(self) -> str:
        """获取米家二维码的 Base64 Data-URI"""
        png_bytes = self.get_qr_png_bytes()
        if png_bytes:
            b64 = base64.b64encode(png_bytes).decode("ascii")
            return f"data:image/png;base64,{b64}"
        return ""

# 全局单例
qr_auth_manager = XiaomiQRAuth()
