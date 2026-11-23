import os
import time
import asyncio
import subprocess
import requests
import base64
import json
import hashlib
import shutil

TTS_CACHE_DIR = "/tmp/pig_tts_cache"
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

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
    支持 小米 MiMo-V2.5-TTS、Edge-TTS (微软晓晓) 与云端克隆音色 API。
    提供精确时长感知与非阻塞异步播放，确保树莓派物理机上动作与语音 100% 毫秒级同步。
    """

    def __init__(
        self,
        mode="edge",
        custom_voice_id="",
        api_key="",
        default_voice="zh-CN-XiaoxiaoNeural",
        mimo_api_key="",
        mimo_voice="冰糖",
        mimo_clone_audio="",
        mimo_design_prompt=""
    ):
        self.mode = mode
        self.custom_voice_id = custom_voice_id
        self.api_key = api_key
        self.default_voice = default_voice
        self.mimo_api_key = mimo_api_key
        self.mimo_voice = mimo_voice
        self.mimo_clone_audio = mimo_clone_audio
        self.mimo_design_prompt = mimo_design_prompt
        self._clone_audio_b64 = None
        self.audio_available = False

        self._start_time = 0
        self._current_duration = 0.0
        self._player_proc = None

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
        clean_text = (text or "").strip()
        if not clean_text:
            return ("", 0.0)

        # 0. 快速匹配本地磁盘 LRU/MD5 缓存 (0ms 命中即出声)
        cache_key = hashlib.md5(
            f"{self.mode}_{voice or self.mimo_voice}_{self.mimo_clone_audio}_{self.mimo_design_prompt}_{clean_text}".encode("utf-8")
        ).hexdigest()
        cache_file = os.path.join(TTS_CACHE_DIR, f"{cache_key}.mp3")
        if os.path.exists(cache_file):
            try:
                shutil.copyfile(cache_file, out_file)
                dur = self.get_audio_duration(out_file, clean_text)
                return (out_file, dur)
            except Exception:
                pass

        # 分支 1: 小米 MiMo-V2.5-TTS
        if self.mode == "mimo" and self.mimo_api_key:
            success = self._mimo_tts(clean_text, out_file, voice, cache_file=cache_file)
            if not success:
                self._edge_fallback(clean_text, out_file, self.default_voice)
        # 分支 2: 自定义克隆音色 (如 Fish Audio)
        elif self.mode == "custom" and self.api_key and self.custom_voice_id:
            voice = voice or self.default_voice
            success = self._custom_tts(clean_text, out_file)
            if not success:
                self._edge_fallback(clean_text, out_file, voice)
        else:
            # 分支 3: Edge-TTS
            voice = voice or self.default_voice
            self._edge_fallback(clean_text, out_file, voice)

        duration = self.get_audio_duration(out_file, clean_text)
        return (out_file, duration)

    def play_async(self, audio_path: str, duration: float = 0.0):
        """非阻塞后台开始播放音频"""
        self._start_time = time.time()
        self._current_duration = duration if duration > 0 else self.get_audio_duration(audio_path)

        if not os.path.exists(audio_path):
            return

        # 优先使用原生命令行播放器 (Linux / 树莓派 ALSA 直连蓝牙/声卡)
        ext = os.path.splitext(audio_path)[1].lower()
        player_cmd = None
        if ext == ".mp3":
            player_cmd = ["mpg123", "-q", audio_path]
        elif ext in [".wav", ".pcm"]:
            player_cmd = ["aplay", "-q", audio_path]

        if player_cmd:
            try:
                self.stop()
                self._player_proc = subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass

        # 回退到 pygame
        if self.audio_available:
            try:
                pygame.mixer.music.load(audio_path)
                pygame.mixer.music.play()
            except Exception as e:
                print(f"⚠️ [TTS 播放异常]: {e}")

    def is_playing(self) -> bool:
        """检查当前语音是否仍在播放中"""
        proc = getattr(self, "_player_proc", None)
        if proc is not None:
            if proc.poll() is None:
                return True
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
        proc = getattr(self, "_player_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.2)
            except Exception:
                pass
            self._player_proc = None
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

    def _mimo_tts(self, text: str, path: str, voice: str = None, cache_file: str = None) -> bool:
        """
        调用小米 MiMo-V2.5-TTS 语音大模型套件:
        1. 声音克隆 (mimo-v2.5-tts-voiceclone): 当配置了 mimo_clone_audio 参考音频文件时
        2. 音色设计 (mimo-v2.5-tts-voicedesign): 当配置了 mimo_design_prompt 描述词时
        3. 预置音色 (mimo-v2.5-tts): 默认模式，可选音色: 冰糖, 茉莉, 苏打, 白桦等
        """
        if not self.mimo_api_key:
            return False
        try:
            url = "https://api.xiaomimimo.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.mimo_api_key}",
                "Content-Type": "application/json"
            }

            # 判定子模型与参数
            clone_path = self.mimo_clone_audio
            if clone_path and not os.path.exists(clone_path):
                proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                cand = os.path.join(proj_root, clone_path)
                if os.path.exists(cand):
                    clone_path = cand

            if clone_path and os.path.exists(clone_path):
                model = "mimo-v2.5-tts-voiceclone"
                user_prompt = "亲切、自然、真实的对话语气"
                # 读取并缓存克隆参考音频的 base64
                if self._clone_audio_b64 is None:
                    with open(clone_path, "rb") as f:
                        ext = os.path.splitext(clone_path)[1].lower().replace(".", "") or "mp3"
                        mime = "audio/wav" if ext == "wav" else "audio/mp3"
                        b64_str = base64.b64encode(f.read()).decode("utf-8")
                        self._clone_audio_b64 = f"data:{mime};base64,{b64_str}"
                audio_voice = self._clone_audio_b64
            elif self.mimo_design_prompt:
                model = "mimo-v2.5-tts-voicedesign"
                user_prompt = self.mimo_design_prompt
                audio_voice = None
            else:
                model = "mimo-v2.5-tts"
                user_prompt = "自然、亲切、生动的日常对话语气"
                audio_voice = voice or self.mimo_voice or "冰糖"

            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": text}
                ],
                "audio": {
                    "format": "mp3"
                },
                "stream": False
            }
            if audio_voice is not None:
                payload["audio"]["voice"] = audio_voice

            res = requests.post(url, json=payload, headers=headers, timeout=20)
            if res.status_code == 200:
                data = res.json()
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    audio_obj = msg.get("audio", {})
                    b64 = audio_obj.get("data", "")
                    if b64:
                        full_bytes = base64.b64decode(b64)
                        with open(path, "wb") as f:
                            f.write(full_bytes)
                        if cache_file:
                            try:
                                with open(cache_file, "wb") as cf:
                                    cf.write(full_bytes)
                            except Exception:
                                pass
                        return True
            print(f"⚠️ [TTS] 小米 MiMo 调用异常 ({res.status_code}): {res.text[:200]}")
            return False
        except Exception as e:
            print(f"⚠️ [TTS] 小米 MiMo 请求失败: {e}")
            return False

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
