# -*- coding: utf-8 -*-
"""
PIG 网易云音乐后台非阻塞播放器 (skills/netease_music/player.py)
特性：
1. 智能选择本机播放器 (mpv 优先 -> ffplay 兜底)；在纯网页仿真无物理声卡时仅同步状态推给浏览器播放，绝不阻塞主线程。
2. 支持列表队列自动连播下一首、切歌(上一首/下一首)、音量调节、暂停与恢复(恢复时从断点继续)。
3. 语音唤醒/摸头打断时自动静默让路 (pause_for_voice)，释放 ALSA 声卡给 TTS 语音播报。
"""

import os
import json
import time
import random
import shutil
import socket
import threading
import subprocess
from typing import Dict, Any, List, Optional


def _has_audio_sink() -> bool:
    """检测宿主机是否具备物理音频输出设备 (树莓派或带声卡桌面 Linux)"""
    if os.path.exists("/proc/asound/cards"):
        try:
            txt = open("/proc/asound/cards", "r", encoding="utf-8", errors="ignore").read().strip()
            if txt and "no soundcards" not in txt.lower():
                return True
        except Exception:
            pass
    return False


class MusicPlayer:
    def __init__(self):
        self._lock = threading.RLock()
        self.playlist: List[Dict[str, Any]] = []
        self.playlist_name: str = ""
        self.shuffle_mode: bool = False
        self.index: int = -1
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.volume: int = 75
        self._proc: Optional[subprocess.Popen] = None
        self._play_token: int = 0
        self._elapsed_offset: float = 0.0
        self._segment_start_ts: float = 0.0
        self._mpv_sock: str = "/tmp/pig_music_mpv.sock"

        self.mpv_bin = shutil.which("mpv")
        self.ffplay_bin = shutil.which("ffplay")
        self.has_local_audio = _has_audio_sink()

    def _emit_music_state(self):
        try:
            from core.hardware_agent import get_hardware_agent
            agent = get_hardware_agent()
            if agent:
                agent.emit_event("music_state", {"music": self.get_state()})
        except Exception:
            pass

    def _is_twin_physical(self) -> bool:
        try:
            from core.hardware_agent import get_hardware_agent, IS_RASPBERRY_PI
            agent = get_hardware_agent()
            if agent and agent.mode == "twin" and (IS_RASPBERRY_PI or self.has_local_audio):
                return True
        except Exception:
            pass
        return False

    def _kill_proc_locked(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def _send_mpv_ipc(self, cmd_list: list) -> bool:
        if not self.mpv_bin or not os.path.exists(self._mpv_sock):
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.8)
            s.connect(self._mpv_sock)
            s.sendall((json.dumps({"command": cmd_list}) + "\n").encode("utf-8"))
            s.close()
            return True
        except Exception:
            return False

    def _start_track_locked(self, track: Dict[str, Any], seek_sec: float = 0.0):
        self._kill_proc_locked()
        self._play_token += 1
        token = self._play_token
        self._elapsed_offset = max(0.0, float(seek_sec))
        self._segment_start_ts = time.time()
        self.is_playing = True
        self.is_paused = False

        # 若为纯网页仿真模式或无物理声卡，音频交给前端浏览器 <audio> 播，不拉本地无效 ffplay
        if not self._is_twin_physical() or not (self.mpv_bin or self.ffplay_bin):
            return

        url = track.get("url", "")
        if not url:
            return

        if self.mpv_bin:
            try:
                if os.path.exists(self._mpv_sock):
                    os.remove(self._mpv_sock)
            except Exception:
                pass
            cmd = [
                self.mpv_bin,
                "--no-video",
                "--really-quiet",
                f"--volume={self.volume}",
                f"--input-ipc-server={self._mpv_sock}",
            ]
            if seek_sec > 0.5:
                cmd.append(f"--start={seek_sec:.1f}")
            cmd.append(url)
        else:
            cmd = [
                self.ffplay_bin,
                "-nodisp",
                "-autoexit",
                "-loglevel", "quiet",
                "-volume", str(self.volume),
            ]
            if seek_sec > 0.5:
                cmd.extend(["-ss", f"{seek_sec:.1f}"])
            cmd.append(url)

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc_ref = self._proc

            def _watch():
                rc = proc_ref.wait()
                with self._lock:
                    if token != self._play_token or self.is_paused:
                        return
                    # 正常播完自动切下一首
                    if rc == 0 and self.playlist and len(self.playlist) > 1:
                        self.index = self._next_index_locked(step=1)
                        self._start_track_locked(self.playlist[self.index], seek_sec=0.0)
                    else:
                        self.is_playing = False
                        self._proc = None
                self._emit_music_state()

            threading.Thread(target=_watch, daemon=True, name="NeteaseWatch").start()
        except Exception:
            self._proc = None

    def _next_index_locked(self, step: int = 1) -> int:
        n = len(self.playlist)
        if n <= 1:
            return 0
        if self.shuffle_mode and step > 0:
            choices = [i for i in range(n) if i != self.index]
            return random.choice(choices) if choices else 0
        return (self.index + step) % n

    def play_tracks(self, tracks: List[Dict[str, Any]], playlist_name: str = "", shuffle: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        if not tracks:
            return None
        with self._lock:
            self.playlist = list(tracks)
            self.playlist_name = playlist_name or ""
            if shuffle is not None:
                self.shuffle_mode = bool(shuffle)
            self.index = random.randrange(len(self.playlist)) if (self.shuffle_mode and len(self.playlist) > 1) else 0
            cur = self.playlist[self.index]
            self._start_track_locked(cur, seek_sec=0.0)
        self._emit_music_state()
        return cur

    def play_at_index(self, idx: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.playlist or idx < 0 or idx >= len(self.playlist):
                return None
            self.index = int(idx)
            cur = self.playlist[self.index]
            self._start_track_locked(cur, seek_sec=0.0)
        self._emit_music_state()
        return cur

    def set_shuffle(self, enabled: Optional[bool] = None) -> bool:
        with self._lock:
            self.shuffle_mode = (not self.shuffle_mode) if enabled is None else bool(enabled)
            res = self.shuffle_mode
        self._emit_music_state()
        return res

    def get_queue(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "index": i,
                    "id": tr.get("id"),
                    "title": tr.get("title", ""),
                    "artist": tr.get("artist", ""),
                    "duration_sec": tr.get("duration_sec", 0),
                    "is_current": (i == self.index),
                }
                for i, tr in enumerate(self.playlist)
            ]

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            if not self.is_playing or self.is_paused:
                return self.get_state()
            self._elapsed_offset += max(0.0, time.time() - self._segment_start_ts)
            self.is_paused = True
            self.is_playing = False
            self._play_token += 1
            self._kill_proc_locked()
        self._emit_music_state()
        return self.get_state()

    def pause_for_voice(self):
        """语音唤醒/摸头打断时立即释放音频设备，避免歌词干扰拾音"""
        with self._lock:
            was_playing = self.is_playing
        if was_playing:
            self.pause()

    def resume(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.playlist or self.index < 0:
                return None
            if self.is_playing and not self.is_paused:
                return self.playlist[self.index]
            cur = self.playlist[self.index]
            self._start_track_locked(cur, seek_sec=self._elapsed_offset)
        self._emit_music_state()
        return cur

    def next_track(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.playlist:
                return None
            self.index = self._next_index_locked(step=1)
            cur = self.playlist[self.index]
            self._start_track_locked(cur, seek_sec=0.0)
        self._emit_music_state()
        return cur

    def prev_track(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.playlist:
                return None
            self.index = self._next_index_locked(step=-1)
            cur = self.playlist[self.index]
            self._start_track_locked(cur, seek_sec=0.0)
        self._emit_music_state()
        return cur

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._play_token += 1
            self._kill_proc_locked()
            self.is_playing = False
            self.is_paused = False
            self._elapsed_offset = 0.0
        self._emit_music_state()
        return self.get_state()

    def set_volume(self, vol: int) -> int:
        with self._lock:
            self.volume = max(10, min(100, int(vol)))
            if self.mpv_bin and self._proc and self._send_mpv_ipc(["set_property", "volume", self.volume]):
                pass
            elif self.is_playing and not self.is_paused and self.playlist and self.index >= 0:
                elapsed = self._elapsed_offset + max(0.0, time.time() - self._segment_start_ts)
                self._start_track_locked(self.playlist[self.index], seek_sec=elapsed)
        self._emit_music_state()
        return self.volume

    def current_track(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self.playlist and 0 <= self.index < len(self.playlist):
                return self.playlist[self.index]
            return None

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            cur = self.playlist[self.index] if (self.playlist and 0 <= self.index < len(self.playlist)) else None
            return {
                "playing": self.is_playing,
                "paused": self.is_paused,
                "volume": self.volume,
                "has_track": cur is not None,
                "song_id": cur.get("id") if cur else None,
                "title": cur.get("title", "") if cur else "",
                "artist": cur.get("artist", "") if cur else "",
                "duration_sec": cur.get("duration_sec", 0) if cur else 0,
                "queue_size": len(self.playlist),
                "queue_index": self.index + 1 if cur else 0,
                "playlist_name": self.playlist_name,
                "shuffle": self.shuffle_mode,
                "stream_path": f"/api/netease/audio?id={cur['id']}" if cur else "",
                "play_on_device": self._is_twin_physical(),
            }


music_player = MusicPlayer()
