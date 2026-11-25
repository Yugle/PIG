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
_SHUFFLE_ACT_TOKENS = ("shuffle", "random", "order", "随机", "顺序")
_LIKED_TOKENS = ("liked", "like", "我喜欢", "红心", "收藏的歌")
_LIST_PLAYLISTS_TOKENS = ("playlists", "my_playlists", "list_playlists", "有哪些歌单", "我的歌单", "查歌单", "看歌单")


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
    if any(t in act for t in _SHUFFLE_ACT_TOKENS) or any(p in kw_lower for p in ("随机播放", "开启随机", "顺序播放", "列表循环")):
        return "shuffle"
    if act in ("play_index", "index"):
        return "play_index"
    if any(t in act for t in _VOLUME_ACT_TOKENS) or (("音量" in kw_lower or "声音" in kw_lower) and any(k in kw_lower for k in ("调", "大", "小", "高", "低", "设", "%"))):
        return "volume"
    if any(t in act for t in _LIST_PLAYLISTS_TOKENS) or any(p in kw_lower for p in ("有哪些歌单", "我的歌单", "查看歌单", "看看歌单")):
        return "playlists"
    if any(t in act for t in _LIKED_TOKENS) or any(p in kw_lower for p in ("我喜欢的音乐", "我喜欢的歌", "红心歌单")):
        return "liked"
    if act in ("playlist", "play_playlist", "歌单", "播放歌单") or "歌单" in kw_lower:
        return "playlist"
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
            return "当前没有正在播放的音乐，主人想听哪一首或哪个歌单可以直接告诉我哦～"
        state_txt = "正在播放" if st["playing"] else ("已暂停" if st["paused"] else "已停止")
        pl_txt = f"来自【{st['playlist_name']}】· " if st.get("playlist_name") else ""
        mode_txt = "随机" if st.get("shuffle") else "顺序"
        return f"现在{state_txt}的是《{st['title']}》- {st['artist']}（{pl_txt}{mode_txt} {st['queue_index']}/{st['queue_size']}，音量 {st['volume']}%）。"

    if intent == "pause":
        st = music_player.pause()
        if not st["has_track"]:
            return "当前本来就没有在放歌哦～"
        return f"好哒，已暂停《{st['title']}》，想继续听随时叫我！"

    if intent == "resume":
        tr = music_player.resume()
        if not tr:
            tracks = netease_client.get_recommended_tracks()
            tr = music_player.play_tracks(tracks, playlist_name="每日推荐" if netease_client.logged_in else "热歌推荐")
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
            tr = music_player.play_tracks(tracks, playlist_name="每日推荐" if netease_client.logged_in else "热歌推荐")
        if not tr:
            return "暂时没有切到可播放的下一首歌，主人换个歌名试试？"
        return f"已切到下一首：《{tr['title']}》- {tr['artist']}♪"

    if intent == "prev":
        tr = music_player.prev_track()
        if not tr:
            return "目前队列里没有上一首歌啦～"
        return f"切回上一首：《{tr['title']}》- {tr['artist']}♪"

    if intent == "play_index":
        try:
            idx = int(val or kw or 0)
        except Exception:
            idx = 0
        tr = music_player.play_at_index(idx)
        if not tr:
            return "队列里没有对应的曲目序号哦～"
        return f"已切换播放《{tr['title']}》- {tr['artist']}♪"

    if intent == "shuffle":
        if val in ("on", "true", "1", "开启") or "随机" in kw:
            enabled = music_player.set_shuffle(True)
        elif val in ("off", "false", "0", "关闭") or any(k in kw for k in ("顺序", "循环")):
            enabled = music_player.set_shuffle(False)
        else:
            enabled = music_player.set_shuffle(None)
        return f"已切换为{'🔀 随机播放' if enabled else '🔁 顺序连播'}模式！"

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

    if intent == "playlists":
        if not netease_client.logged_in:
            return "你还没登录网易云账号哦～可以先在 `/skills` 或屏幕「技能服务」扫码登录，登录后就能直接查看并播放你的全部歌单！不过现在我也可以直接帮你搜全网歌单，比如“放个深夜学习歌单”。"
        pls = netease_client.get_user_playlists(limit=15)
        if not pls:
            return "暂时没拉到你的网易云歌单，请稍后再试～"
        summary = "、".join(f"《{p['name']}》({p['track_count']}首)" for p in pls[:8])
        return f"你账号里一共有 {len(pls)} 个歌单，前面的有：{summary}。你想听哪一个可以直接告诉我！"

    if intent == "liked":
        if not netease_client.logged_in:
            return "「我喜欢的音乐」需要先登录网易云账号哦～去 `/skills` 扫码后就能一键播放你收藏的红心歌曲啦！"
        res = netease_client.get_liked_tracks(limit=20)
        tracks = res.get("tracks") or []
        pl_name = res.get("name") or "我喜欢的音乐"
        if not tracks:
            return "暂时没从你的「我喜欢的音乐」里拉到可播放音源，换个歌单试试？"
        tr = music_player.play_tracks(tracks, playlist_name=pl_name)
        return f"正在播放你的【{pl_name}】：首曲《{tr['title']}》- {tr['artist']}（队列共 {len(tracks)} 首）♪"

    if intent == "playlist":
        clean_q = re.sub(r"(播放|打开|来一个|放一个|来个|放个|我的|歌单)", "", kw).strip()
        if netease_client.logged_in and clean_q:
            user_pls = netease_client.get_user_playlists(limit=25)
            matched = next((p for p in user_pls if clean_q.lower() in p["name"].lower()), None)
            if matched:
                res = netease_client.get_playlist_tracks(matched["id"], limit=20)
                tracks = res.get("tracks") or []
                if tracks:
                    tr = music_player.play_tracks(tracks, playlist_name=matched["name"])
                    return f"找到你的歌单【{matched['name']}】，正在播放《{tr['title']}》- {tr['artist']}（队列 {len(tracks)} 首）♪"
        search_kw = clean_q or kw or "轻音乐 治愈"
        pub_pls = netease_client.search_playlists(search_kw, limit=5)
        for p in pub_pls:
            res = netease_client.get_playlist_tracks(p["id"], limit=20)
            tracks = res.get("tracks") or []
            if tracks:
                pl_title = res.get("name") or p["name"]
                tr = music_player.play_tracks(tracks, playlist_name=pl_title)
                return f"为你选了网易云歌单【{pl_title}】：正在播放《{tr['title']}》- {tr['artist']}（队列 {len(tracks)} 首）♪"
        return f"没有找到可播放的「{search_kw}」相关歌单，换个风格或歌名试试？"

    if intent == "daily":
        tracks = netease_client.get_recommended_tracks()
        if not tracks:
            return "网易云暂时没拉到可播推荐歌曲，麻烦检查一下树莓派联网状态～"
        pl_title = "每日推荐" if netease_client.logged_in else "热歌推荐"
        tr = music_player.play_tracks(tracks, playlist_name=pl_title)
        return f"为你挑了{pl_title}：《{tr['title']}》- {tr['artist']}（已加入 {len(tracks)} 首歌连播队列）♪"

    if intent == "unknown":
        return f"没听懂音乐控制指令（action={action}），你可以说：放首歌、放我喜欢的音乐、查看我的歌单、暂停、下一首、随机播放、或停止播放～"

    # 默认搜索并播放目标曲目
    tracks = netease_client.search_playable_tracks(kw)
    if not tracks:
        vip_tip = "" if netease_client.logged_in else "（当前未扫码登录网易云账号，部分 VIP 版权歌曲无法试听完整版，可在后台扫码绑定）"
        return f"在网易云搜了一圈「{kw}」，暂时没有找到可直接播放的版权音源{vip_tip}，换个翻唱或歌名试试？"

    tr = music_player.play_tracks(tracks, playlist_name=f"搜索:{kw}")
    return f"找到啦！正在为你播放《{tr['title']}》- {tr['artist']}（队列 {len(tracks)} 首）♪"


def on_voice_interrupt():
    """语音唤醒/摸头打断时立即暂停音乐，释放声卡"""
    music_player.pause_for_voice()


def get_status_payload() -> dict:
    return {"music": music_player.get_state()}


def get_screen_item() -> dict:
    val = (netease_client.nickname or "已登录")[:6] if netease_client.logged_in else "免登录"
    return {
        "id": "netease",
        "skill_id": "netease_music",
        "icon": "sfx",
        "name": "网易云",
        "val": val,
        "type": "info",
        "screen_qr": True,
        "qr_mode": "NETEASE_QR",
        "screen_order": 20,
    }


def start_screen_qr() -> dict:
    import base64
    res = netease_client.start_qr_login()
    if res.get("status") != "ok":
        return {
            "title": "网易云登录",
            "headline": "打开网易云App 扫码登录",
            "foot_tip": "绑定后畅听 VIP 与个人每日推荐",
            "status": res.get("message", "获取二维码失败"),
            "error": True,
        }
    raw_png = b""
    b64 = res.get("qr_base64", "")
    if "," in b64:
        try:
            raw_png = base64.b64decode(b64.split(",", 1)[1])
        except Exception:
            raw_png = b""
    return {
        "title": "网易云登录",
        "headline": "打开网易云App 扫码登录",
        "foot_tip": "绑定后畅听 VIP 与个人每日推荐",
        "status": "打开网易云App 扫一扫",
        "qr_bytes": raw_png,
    }


def poll_screen_qr() -> dict:
    st = netease_client.check_qr_status()
    code = int(st.get("code", 0))
    if code == 803 or st.get("logged_in"):
        return {"state": "success", "status_text": f"绑定成功！{st.get('nickname', '')}"}
    if code == 800:
        return {"state": "expired", "status_text": "二维码已过期，按 [OK] 刷新"}
    if code == 802:
        return {"state": "scanned", "status_text": "已扫码，请在手机确认登录"}
    if code == 8821 or st.get("status") in ("blocked", "error"):
        return {"state": "blocked", "status_text": "风控拦截8821 请在/skills填Cookie"}
    return {"state": "waiting"}


def get_screen_state(active_mode: str = "", active_qr_skill: str = "", qr_status: str = "") -> dict:
    return {
        "netease_qr": {
            "active": (active_mode == "NETEASE_QR" or active_qr_skill == "netease_music"),
            "status": qr_status if active_qr_skill == "netease_music" else "等待网易云App扫码...",
            "logged_in": bool(netease_client.logged_in),
            "nickname": netease_client.nickname or "",
        }
    }
