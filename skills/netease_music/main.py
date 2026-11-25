# -*- coding: utf-8 -*-
"""
网易云音乐点歌台技能入口 (skills/netease_music/main.py)
"""

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.netease_music.netease_client import netease_client
from skills.netease_music.player import music_player

_PUNCT_RE = re.compile(r"[，。！？、,.!?\s~～\"'“”‘’]+")

_STOP_ACT_EXACT = {"停", "关"}
_STOP_ACT_TOKENS = ("stop", "close", "mute", "shut", "停止", "停下", "停掉", "停播", "停放", "关掉", "关闭", "关了", "别放", "不要放", "不放", "不听", "闭嘴")
_STOP_KW_EXACT = {"停", "停吧", "停下", "停止", "别放", "别放了", "不要放", "不要放了", "不放", "不放了", "不听了", "别播了", "不要播了", "关闭", "关掉", "关了", "闭嘴", "安静点", "太吵了"}
_STOP_KW_PHRASES = ("停止播放", "停止音乐", "停掉音乐", "关掉音乐", "关闭音乐", "停止放歌", "别放了", "不要放了", "别播了", "不要播了", "stop playing", "stop music")

_PAUSE_ACT_TOKENS = ("pause", "暂停", "挂起", "等一下", "等下", "先停")
_PAUSE_KW_EXACT = {"暂停", "先暂停", "暂停一下", "等一下", "先停一下", "pause"}
_PAUSE_KW_PHRASES = ("暂停播放", "暂停音乐", "先暂停", "pause music")

_RESUME_ACT_TOKENS = ("resume", "continue", "unpause", "继续", "恢复", "接着放")
_RESUME_KW_EXACT = {"继续", "恢复", "继续放", "接着放", "继续播", "恢复播放", "resume"}
_RESUME_KW_PHRASES = ("继续播放", "恢复播放", "继续放歌", "接着播放")

_NEXT_ACT_TOKENS = ("next", "skip", "switch", "下一", "切歌", "换一首", "换歌")
_NEXT_KW_PHRASES = ("下一首", "下一曲", "切歌", "换一首", "换首歌", "换下一首")

_PREV_ACT_TOKENS = ("prev", "previous", "上一")
_PREV_KW_PHRASES = ("上一首", "上一曲")

_STATUS_ACT_TOKENS = ("status", "info", "current", "状态", "正在放", "什么歌")
_STATUS_KW_PHRASES = ("什么歌", "正在放什么", "现在放的什么", "播放状态")

_VOLUME_ACT_TOKENS = ("volume", "vol", "声音", "音量")
_DAILY_ACT_TOKENS = ("daily", "recommend", "hot", "推荐", "热歌", "每日")


def _match_intent(act: str, kw: str) -> str:
    """将大模型传入的 action / query 归一化为标准控制指令。"""
    kw_clean = _PUNCT_RE.sub("", kw.lower())
    kw_lower = kw.lower()

    if any(t in act for t in _PAUSE_ACT_TOKENS) or kw_clean in _PAUSE_KW_EXACT or any(p in kw_lower for p in _PAUSE_KW_PHRASES):
        return "pause"
    if act in _STOP_ACT_EXACT or any(t in act for t in _STOP_ACT_TOKENS) or kw_clean in _STOP_KW_EXACT or any(p in kw_lower for p in _STOP_KW_PHRASES):
        return "stop"
    if any(t in act for t in _RESUME_ACT_TOKENS) or kw_clean in _RESUME_KW_EXACT or any(p in kw_lower for p in _RESUME_KW_PHRASES):
        return "resume"
    if any(t in act for t in _NEXT_ACT_TOKENS) or any(p in kw_lower for p in _NEXT_KW_PHRASES):
        return "next"
    if any(t in act for t in _PREV_ACT_TOKENS) or any(p in kw_lower for p in _PREV_KW_PHRASES):
        return "prev"
    if any(t in act for t in _STATUS_ACT_TOKENS) or any(p in kw_lower for p in _STATUS_KW_PHRASES):
        return "status"
    if any(t in act for t in _VOLUME_ACT_TOKENS) or (("音量" in kw_lower or "声音" in kw_lower) and any(k in kw_lower for k in ("调", "大", "小", "高", "低", "设", "%"))):
        return "volume"
    if any(t in act for t in _DAILY_ACT_TOKENS):
        return "daily"
    if not kw and act in ("", "play", "start", "music", "播放", "放歌", "点歌", "听歌"):
        return "daily"
    if kw:
        return "play"
    return "unknown"


def run(action: str = "play", query: str = "", value: str = "") -> str:
    act = (action or "play").strip().lower()
    kw = (query or "").strip()
    val = (value or "").strip().lower()
    intent = _match_intent(act, kw)

    if intent == "status":
        st = music_player.get_state()
        if not st["has_track"]:
            return "当前没有正在播放的音乐，主人想听哪一首可以直接告诉我哦～"
        state_txt = "正在播放" if st["playing"] else ("已暂停" if st["paused"] else "已停止")
        return f"现在{state_txt}的是《{st['title']}》- {st['artist']}（队列 {st['queue_index']}/{st['queue_size']}，音量 {st['volume']}%）。"

    if intent == "pause":
        st = music_player.pause()
        if not st["has_track"]:
            return "当前本来就没有在放歌哦～"
        return f"好哒，已暂停《{st['title']}》，想继续听随时叫我！"

    if intent == "resume":
        tr = music_player.resume()
        if not tr:
            tracks = netease_client.get_recommended_tracks()
            tr = music_player.play_tracks(tracks)
        if not tr:
            return "歌单里暂时没有歌，请告诉我你想听什么歌名或歌手吧～"
        return f"继续为你播放《{tr['title']}》- {tr['artist']}♪"

    if intent == "stop":
        music_player.stop()
        return "音乐已关闭，世界安静下来啦～"

    if intent == "next":
        tr = music_player.next_track()
        if not tr:
            tracks = netease_client.get_recommended_tracks()
            tr = music_player.play_tracks(tracks)
        if not tr:
            return "暂时没有切到可播放的下一首歌，主人换个歌名试试？"
        return f"已切到下一首：《{tr['title']}》- {tr['artist']}♪"

    if intent == "prev":
        tr = music_player.prev_track()
        if not tr:
            return "目前队列里没有上一首歌啦～"
        return f"切回上一首：《{tr['title']}》- {tr['artist']}♪"

    if intent == "volume":
        cur_v = music_player.volume
        if val in ["up", "大", "调大", "+"] or any(k in kw for k in ("大", "高", "响")):
            new_v = music_player.set_volume(cur_v + 15)
        elif val in ["down", "小", "调小", "-"] or any(k in kw for k in ("小", "低", "轻")):
            new_v = music_player.set_volume(cur_v - 15)
        else:
            digits = "".join(c for c in (val or kw) if c.isdigit())
            new_v = music_player.set_volume(int(digits) if digits else cur_v)
        return f"已把音乐音量调整到 {new_v}% 啦！"

    if intent == "daily":
        tracks = netease_client.get_recommended_tracks()
        if not tracks:
            return "网易云暂时没拉到可播推荐歌曲，麻烦检查一下树莓派联网状态～"
        tr = music_player.play_tracks(tracks)
        return f"为你挑了网易云热门歌曲：《{tr['title']}》- {tr['artist']}（已加入 {len(tracks)} 首歌连播队列）♪"

    if intent == "unknown":
        return f"没听懂音乐控制指令（action={action}），你可以说：放首歌、暂停、继续、下一首、或停止播放～"
        tracks = netease_client.get_recommended_tracks()
        if not tracks:
            return "网易云暂时没拉到可播推荐歌曲，麻烦检查一下树莓派联网状态～"
        tr = music_player.play_tracks(tracks)
        return f"为你挑了网易云热门歌曲：《{tr['title']}》- {tr['artist']}（已加入 {len(tracks)} 首歌连播队列）♪"

    # 默认搜索并播放目标曲目
    tracks = netease_client.search_playable_tracks(kw)
    if not tracks:
        vip_tip = "" if netease_client.logged_in else "（当前未扫码登录网易云账号，部分 VIP 版权歌曲无法试听完整版，可在后台扫码绑定）"
        return f"在网易云搜了一圈「{kw}」，暂时没有找到可直接播放的版权音源{vip_tip}，换个翻唱或歌名试试？"

    tr = music_player.play_tracks(tracks)
    return f"找到啦！正在为你播放《{tr['title']}》- {tr['artist']}♪"
