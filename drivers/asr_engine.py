import os
import time
import base64
import threading
import requests


class MimoASR:
    """
    小米 MiMo 原生高精语音识别 (ASR) 适配器 (基于 mimo-v2.5-asr)。
    统一使用小米 API Key，支持标准 OpenAI chat completions 格式原生转写。
    告别百度多凭据管理与 Token 刷新，全栈单一 Key 即走天下。
    """

    def __init__(self, api_key: str, base_url: str = "https://api.xiaomimimo.com/v1", model: str = "mimo-v2.5-asr"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._thread_local = threading.local()

    def _get_session(self) -> requests.Session:
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = requests.Session()
            self._thread_local.session = sess
        return sess

    def recognize(self, audio_path: str, rate: int = 16000) -> str:
        """
        将音频文件 (WAV / MP3) 转写为文本。
        :param audio_path: 音频文件绝对路径
        :param rate: 采样率 (默认 16000)
        :return: 识别出的中文文本，若失败返回空字符串
        """
        if not os.path.exists(audio_path):
            print(f"⚠️ [Mimo ASR] 音频文件不存在: {audio_path}")
            return ""

        if not self.api_key:
            print("⚠️ [Mimo ASR] 未配置 api_key")
            return ""

        try:
            with open(audio_path, "rb") as f:
                speech_bytes = f.read()

            if len(speech_bytes) == 0:
                return ""

            b64_audio = base64.b64encode(speech_bytes).decode("utf-8")
            ext = os.path.splitext(audio_path)[1].lower().replace(".", "") or "wav"
            fmt = "mp3" if ext == "mp3" else "wav"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": b64_audio,
                                    "format": fmt
                                }
                            }
                        ]
                    }
                ]
            }

            url = f"{self.base_url}/chat/completions"
            resp = self._get_session().post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code == 200:
                res_data = resp.json()
                choices = res_data.get("choices", [])
                if choices:
                    text = choices[0].get("message", {}).get("content", "").strip()
                    print(f"🎙️ [Mimo ASR 识别成功]: {text}")
                    return text
            else:
                print(f"⚠️ [Mimo ASR 识别失败 {resp.status_code}]: {resp.text[:120]}")
                return ""

        except Exception as e:
            print(f"⚠️ [Mimo ASR 转写异常]: {e}")
            return ""


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


def get_asr_engine(cfg: dict):
    """
    统一 ASR 引擎工厂方法：
    1. 默认优先采用小米原生 MimoASR (统一使用 api_key，零多余凭据)
    2. 若显式指定 asr_provider='baidu' 且配置了百度凭据，则回退兼容 BaiduASR
    """
    provider = str(cfg.get("asr_provider", "mimo")).lower()

    if provider == "baidu" and cfg.get("baidu_api_key") and cfg.get("baidu_secret_key"):
        print("🎙️ [ASR] 正在加载百度智能云 ASR 引擎...")
        return BaiduASR(cfg["baidu_api_key"], cfg["baidu_secret_key"])

    mimo_key = cfg.get("api_key") or cfg.get("mimo_api_key", "")
    base_url = cfg.get("base_url", "https://api.xiaomimimo.com/v1")
    if mimo_key:
        print("🎙️ [ASR] 正在加载小米 MiMo-V2.5-ASR 原生语音识别引擎 (全栈统一单一 Key)...")
        return MimoASR(api_key=mimo_key, base_url=base_url)

    if cfg.get("baidu_api_key") and cfg.get("baidu_secret_key"):
        print("🎙️ [ASR] 回退加载百度智能云 ASR 引擎...")
        return BaiduASR(cfg["baidu_api_key"], cfg["baidu_secret_key"])

    print("⚠️ [ASR] 未检测到可用的 ASR 凭据")
    return None
