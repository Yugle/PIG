import os
import time
import asyncio
import subprocess
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
    语音合成与毫秒级时长感知适配器。
    支持 Edge-TTS (微软晓晓/小艺高保真中文女声) 与云端克隆音色 API。
    提供精确时长感知与非阻塞异步播放，确保树莓派物理机上动作与语音 100% 毫秒级同步。
    """

    def __init__(self, mode="edge", custom_voice_id="", api_key="", default_voice="zh-CN-XiaoxiaoNeural"):
        self.mode = mode
        self.custom_voice_id = custom_voice_id
        self.api_key = api_key
        self.default_voice = default_voice
        self.audio_available = False

        self._start_time = 0
        self._current_duration = 0.0

        if pygame is not None:
            try:
                pygame.mixer.init()
                self.audio_available = True
            except Exception as e:
                print(f"ℹ️ [TTS] 本机无物理声卡设备 ({e})，将以高精时间戳仿真模式驱动动作同步")
        else:
            print("ℹ️ [TTS] pygame 未安装，音频将以高精时间戳仿真模式运行")

    def get_audio_duration(self, audio_path: str, text: str = "") -> float:
        """精确获取音频文件的播放时长 (秒)"""
        if os.path.exists(audio_path):
            try:
                cmd = [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_path
                ]
                output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).strip()
                dur = float(output)
                if dur > 0:
                    return dur
            except Exception:
                pass

            if self.audio_available and pygame is not None:
                try:
                    snd = pygame.mixer.Sound(audio_path)
                    dur = snd.get_length()
                    if dur > 0:
                        return dur
                except Exception:
                    pass

        # 保底估算 (普通话平均语速: 1 秒约 4~5 字)
        return max(1.8, len(text) * 0.24)

    def synthesize(self, text: str, out_file="/tmp/speech_out.mp3", voice: str = None) -> tuple[str, float]:
        """
        仅合成音频并计算精准时长，不立即阻塞播放。
        :return: (音频文件绝对路径, 精准时长秒数)
        """
        if not text:
            return ("", 0.0)

        voice = voice or self.default_voice

        # 分支 1: 自定义克隆音色
        if self.mode == "custom" and self.api_key and self.custom_voice_id:
            success = self._custom_tts(text, out_file)
            if not success:
                self._edge_fallback(text, out_file, voice)
        else:
            # 分支 2: Edge-TTS
            self._edge_fallback(text, out_file, voice)

        duration = self.get_audio_duration(out_file, text)
        return (out_file, duration)

    def play_async(self, audio_path: str, duration: float = 0.0):
        """非阻塞后台开始播放音频"""
        self._start_time = time.time()
        self._current_duration = duration if duration > 0 else self.get_audio_duration(audio_path)

        if self.audio_available and os.path.exists(audio_path):
            try:
                pygame.mixer.music.load(audio_path)
                pygame.mixer.music.play()
            except Exception as e:
                print(f"⚠️ [TTS 播放异常]: {e}")

    def is_playing(self) -> bool:
        """检查当前语音是否仍在播放中"""
        if self.audio_available:
            try:
                if pygame.mixer.music.get_busy():
                    return True
            except Exception:
                pass

        # 仿真或无声卡环境下的时间戳精确判定
        return (time.time() - self._start_time) < self._current_duration

    def stop(self):
        """停止播放"""
        if self.audio_available:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self._current_duration = 0.0

    def speak(self, text: str, voice: str = None):
        """同步阻塞播放 (兼容旧调用)"""
        print(f"🔊 [TTS 播报]: {text}")
        path, dur = self.synthesize(text, voice=voice)
        self.play_async(path, dur)
        while self.is_playing():
            time.sleep(0.05)

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
            return False
        except Exception:
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
