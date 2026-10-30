import os
import asyncio
import requests

try:
    import pygame
except ImportError:
    pygame = None

try:
    import edge_tts
except ImportError:
    edge_tts = None


class TTSEngine:
    """
    语音合成适配器。
    支持 Edge-TTS (免费高保真，默认 XiaoyiNeural 甜美女声)
    及云端克隆音色 API (如 Fish Audio / GPT-SoVITS 等)。
    支持声卡不可用时的静音/模拟回退。
    """

    def __init__(self, mode="edge", custom_voice_id="", api_key="", default_voice="zh-CN-XiaoyiNeural"):
        self.mode = mode
        self.custom_voice_id = custom_voice_id
        self.api_key = api_key
        self.default_voice = default_voice
        self.audio_available = False

        if pygame is not None:
            try:
                # 尝试初始化混音器
                pygame.mixer.init()
                self.audio_available = True
            except Exception as e:
                print(f"⚠️ [TTS] 音频播放设备初始化失败 ({e})，将以打印文本模式运行")
        else:
            print("⚠️ [TTS] pygame 未安装，音频输出已关闭")

    def speak(self, text: str, voice: str = None):
        """
        合成并朗读文本。
        """
        if not text:
            return

        print(f"🔊 [TTS 播报]: {text}")
        out_file = "/tmp/speech_out.mp3"
        voice = voice or self.default_voice

        # 分支 1: 云端音色克隆
        if self.mode == "custom" and self.api_key and self.custom_voice_id:
            success = self._custom_tts(text, out_file)
            if not success:
                self._edge_fallback(text, out_file, voice)
        else:
            # 分支 2: Edge-TTS
            self._edge_fallback(text, out_file, voice)

        # 播放音频文件
        if self.audio_available and os.path.exists(out_file):
            try:
                pygame.mixer.music.load(out_file)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
            except Exception as e:
                print(f"⚠️ [TTS 播放失败]: {e}")

    def _custom_tts(self, text: str, path: str) -> bool:
        try:
            url = "https://api.fish.audio/v1/tts"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "text": text,
                "reference_id": self.custom_voice_id,
                "format": "mp3"
            }
            res = requests.post(url, json=data, headers=headers, timeout=12)
            if res.status_code == 200:
                with open(path, "wb") as f:
                    f.write(res.content)
                return True
            else:
                print(f"⚠️ [TTS] 自定义音色接口返回码: {res.status_code}")
                return False
        except Exception as e:
            print(f"⚠️ [TTS] 自定义音色请求异常: {e}")
            return False

    def _edge_fallback(self, text: str, path: str, voice: str):
        if edge_tts is None:
            return

        async def _run():
            try:
                c = edge_tts.Communicate(text, voice)
                await c.save(path)
            except Exception as e:
                print(f"⚠️ [TTS] Edge-TTS 合成失败: {e}")

        try:
            asyncio.run(_run())
        except Exception as e:
            print(f"⚠️ [TTS] 异步事件循环错误: {e}")
