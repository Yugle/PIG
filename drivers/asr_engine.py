import os
import time
import base64
import requests

class BaiduASR:
    """
    百度智能云短语音识别 (ASR) 适配器。
    国内直连，毫秒级响应，支持自动 Token 缓存与鉴权刷新。
    """

    def __init__(self, api_key: str, secret_key: str, cuid="pig_desktop_robot"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.cuid = cuid
        self.token = None
        self.token_expire_time = 0

    def _get_token(self) -> str:
        """获取或复用未过期的 Access Token (有效期 30 天)"""
        now = time.time()
        if self.token and now < self.token_expire_time - 3600:
            return self.token

        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key
        }
        try:
            resp = requests.post(url, params=params, timeout=10)
            data = resp.json()
            if "access_token" in data:
                self.token = data["access_token"]
                expires_in = data.get("expires_in", 2592000)
                self.token_expire_time = now + expires_in
                return self.token
            else:
                print(f"⚠️ [Baidu ASR] 获取 Token 失败: {data}")
                return ""
        except Exception as e:
            print(f"⚠️ [Baidu ASR] Token 请求网络异常: {e}")
            return ""

    def recognize(self, audio_path: str, rate: int = 16000) -> str:
        """
        将 16kHz WAV 或 PCM 音频转写为文本。
        :param audio_path: 音频文件路径
        :param rate: 采样率 (默认 16000)
        :return: 识别出的中文文本，若失败返回空字符串
        """
        if not os.path.exists(audio_path):
            print(f"⚠️ [Baidu ASR] 音频文件不存在: {audio_path}")
            return ""

        token = self._get_token()
        if not token:
            return ""

        try:
            with open(audio_path, "rb") as f:
                speech_bytes = f.read()

            audio_len = len(speech_bytes)
            if audio_len == 0:
                return ""

            b64_speech = base64.b64encode(speech_bytes).decode("utf-8")

            # 百度语音识别接口
            url = "https://vop.baidu.com/server_api"
            payload = {
                "format": "wav",
                "rate": rate,
                "channel": 1,
                "cuid": self.cuid,
                "token": token,
                "speech": b64_speech,
                "len": audio_len
            }

            headers = {"Content-Type": "application/json"}
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            result_json = res.json()

            if result_json.get("err_no") == 0:
                results = result_json.get("result", [])
                text = "".join(results).strip()
                print(f"🎙️ [Baidu ASR 识别成功]: {text}")
                return text
            else:
                err_code = result_json.get("err_no")
                err_msg = result_json.get("err_msg")
                print(f"⚠️ [Baidu ASR 识别错误码 {err_code}]: {err_msg}")
                return ""

        except Exception as e:
            print(f"⚠️ [Baidu ASR 转写异常]: {e}")
            return ""
