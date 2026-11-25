# -*- coding: utf-8 -*-
"""
网易云音乐点歌台技能入口 (skills/netease_music/main.py)
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.netease_music.netease_client import netease_client
from skills.netease_music.player import music_player


def run(action: str = "play", query: str = "", value: str = "") -> str:
    act = (action or "play").strip().lower()
    kw = (query or "").strip()
    val = (value or "").strip().lower()

    if act in ["status", "info", "当前", "正在放什么"]:
        st = music_player.get_state()
        if not st["has_track"]:
            return "当前没有正在播放的音乐，主人想听哪一首可以直接告诉我哦～"
        state_txt = "正在播放" if st["playing"] else ("已暂停" if st["paused"] else "已停止")
        return f"现在{state_txt}的是《{st['title']}》- {st['artist']}（队列 {st['queue_index']}/{st['queue_size']}，音量 {st['volume']}%）。"

    if act in ["pause", "暂停"]:
        st = music_player.pause()
        if not st["has_track"]:
            return "当前本来就没有在放歌哦～"
        return f"好哒，已暂停《{st['title']}》，想继续听随时叫我！"

    if act in ["resume", "continue", "继续", "恢复播放"]:
        tr = music_player.resume()
        if not tr:
            tracks = netease_client.get_recommended_tracks()
            tr = music_player.play_tracks(tracks)
        if not tr:
            return "歌单里暂时没有歌，请告诉我你想听什么歌名或歌手吧～"
        return f"继续为你播放《{tr['title']}》- {tr['artist']}♪"

    if act in ["stop", "close", "关闭", "停止", "别放了", "关掉音乐"]:
        music_player.stop()
        return "音乐已关闭，世界安静下来啦～"

    if act in ["next", "下一首", "切歌", "换一首"]:
        tr = music_player.next_track()
        if not tr:
            tracks = netease_client.get_recommended_tracks()
            tr = music_player.play_tracks(tracks)
        if not tr:
            return "暂时没有切到可播放的下一首歌，主人换个歌名试试？"
        return f"已切到下一首：《{tr['title']}》- {tr['artist']}♪"

    if act in ["prev", "上一首"]:
        tr = music_player.prev_track()
        if not tr:
            return "目前队列里没有上一首歌啦～"
        return f"切回上一首：《{tr['title']}》- {tr['artist']}♪"

    if act in ["volume", "音量", "声音"]:
        cur_v = music_player.volume
        if val in ["up", "大", "调大", "+"] or "大" in kw:
            new_v = music_player.set_volume(cur_v + 15)
        elif val in ["down", "小", "调小", "-"] or "小" in kw:
            new_v = music_player.set_volume(cur_v - 15)
        else:
            digits = "".join(c for c in (val or kw) if c.isdigit())
            new_v = music_player.set_volume(int(digits) if digits else cur_v)
        return f"已把音乐音量调整到 {new_v}% 啦！"

    if act in ["daily", "recommend", "推荐", "热歌", "每日推荐"] or not kw:
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
