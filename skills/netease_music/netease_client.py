# -*- coding: utf-8 -*-
"""
网易云音乐检索、可播校验与扫码登录客户端 (skills/netease_music/netease_client.py)
支持：
1. 原生零依赖 HTTP 二维码扫码登录 & Cookie 会话持久化 (data/netease_session.json)，可选配合 pyncm
2. 公网检索兜底 + 账号带 Cookie 高码率音轨获取 + 外链 CDN 302 真实可播性校验
"""

import os
import io
import json
import time
import base64
import logging
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger("NeteaseClient")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSION_PATH = os.path.join(PROJECT_ROOT, "data", "netease_session.json")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

try:
    import pyncm
    from pyncm import apis as ncm_apis
    HAS_PYNCM = True
except ImportError:
    pyncm = None
    ncm_apis = None
    HAS_PYNCM = False


class NeteaseClient:
    """网易云音乐接口封装 (内置原生 HTTP QR/Session 支持，不强依赖 pyncm)"""

    def __init__(self):
        self.logged_in = False
        self.nickname = ""
        self.vip_type = 0
        self._qr_unikey = ""
        self._http = requests.Session()
        self._http.headers.update(_HEADERS)
        self._load_saved_session()

    def _fetch_http_profile(self) -> Dict[str, Any]:
        try:
            r = self._http.get("https://music.163.com/api/nuser/account/get", timeout=6)
            if r.status_code == 200:
                return (r.json() or {}).get("profile") or {}
        except Exception:
            pass
        return {}

    def _load_saved_session(self):
        if not os.path.exists(SESSION_PATH):
            return
        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 1. 优先恢复 pyncm 会话（若存在且已装）
            sess_str = data.get("session", "")
            if HAS_PYNCM and sess_str:
                sess = pyncm.LoadSessionFromString(sess_str)
                pyncm.SetCurrentSession(sess)
                st = ncm_apis.login.GetCurrentLoginStatus()
                prof = (st or {}).get("profile") or {}
                if prof.get("userId"):
                    self.logged_in = True
                    self.nickname = prof.get("nickname", "网易云用户")
                    self.vip_type = int(prof.get("vipType", 0))
                    logger.info(f"🎵 [Netease] 已恢复登录态 (pyncm): {self.nickname} (VIP={self.vip_type})")
                    return

            # 2. 原生 Cookie 会话恢复（零依赖）
            cookies = data.get("cookies")
            if isinstance(cookies, dict) and cookies:
                self._http.cookies.update(cookies)
                prof = self._fetch_http_profile()
                if prof.get("userId"):
                    self.logged_in = True
                    self.nickname = prof.get("nickname", data.get("nickname", "网易云用户"))
                    self.vip_type = int(prof.get("vipType", data.get("vip_type", 0)))
                    logger.info(f"🎵 [Netease] 已恢复登录态 (HTTP): {self.nickname} (VIP={self.vip_type})")
        except Exception as e:
            logger.debug(f"恢复网易云会话跳过: {e}")

    def _save_session(self):
        try:
            os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
            payload: Dict[str, Any] = {
                "cookies": requests.utils.dict_from_cookiejar(self._http.cookies),
                "nickname": self.nickname,
                "vip_type": self.vip_type,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if HAS_PYNCM:
                try:
                    payload["session"] = pyncm.DumpSessionAsString(pyncm.GetCurrentSession())
                except Exception:
                    pass
            with open(SESSION_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存网易云会话失败: {e}")

    def logout(self):
        self.logged_in = False
        self.nickname = ""
        self.vip_type = 0
        self._qr_unikey = ""
        self._http.cookies.clear()
        if os.path.exists(SESSION_PATH):
            try:
                os.remove(SESSION_PATH)
            except Exception:
                pass

    def get_auth_status(self) -> Dict[str, Any]:
        return {
            "has_pyncm": HAS_PYNCM,
            "qr_supported": True,
            "logged_in": self.logged_in,
            "nickname": self.nickname,
            "vip_type": self.vip_type,
        }

    def start_qr_login(self) -> Dict[str, Any]:
        """生成网易云手机端扫码登录二维码 (Base64 PNG，原生 HTTP + pyncm 双模支持)"""
        try:
            unikey = ""
            login_url = ""
            if HAS_PYNCM:
                resp = ncm_apis.login.LoginQrcodeUnikey()
                unikey = (resp or {}).get("unikey", "")
                if unikey:
                    login_url = ncm_apis.login.GetLoginQRCodeUrl(unikey)
            if not unikey:
                r = self._http.get("https://music.163.com/api/login/qrcode/unikey", params={"type": 1}, timeout=6)
                unikey = (r.json() or {}).get("unikey", "")
                if unikey:
                    login_url = f"https://music.163.com/login?codekey={unikey}"

            if not unikey:
                return {"status": "error", "message": "获取网易云二维码 unikey 失败，请检查网络连接"}
            self._qr_unikey = unikey

            qr_b64 = ""
            try:
                import qrcode
                img = qrcode.make(login_url)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                pass

            return {
                "status": "ok",
                "unikey": unikey,
                "login_url": login_url,
                "qr_base64": qr_b64,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def check_qr_status(self) -> Dict[str, Any]:
        """轮询网易云二维码扫码状态 (800过期 / 801待扫 / 802待确认 / 803成功)"""
        if not self._qr_unikey:
            return {"code": 0, "status": "idle", "logged_in": self.logged_in, "nickname": self.nickname}
        try:
            if HAS_PYNCM:
                resp = ncm_apis.login.LoginQrcodeCheck(self._qr_unikey) or {}
            else:
                r = self._http.get(
                    "https://music.163.com/api/login/qrcode/client/login",
                    params={"key": self._qr_unikey, "type": 1},
                    timeout=6,
                )
                resp = r.json() or {}

            code = int(resp.get("code", 0))
            if code == 803:
                if HAS_PYNCM:
                    st = ncm_apis.login.GetCurrentLoginStatus() or {}
                    ncm_apis.login.WriteLoginInfo(st)
                    prof = st.get("profile") or {}
                else:
                    prof = self._fetch_http_profile()
                self.logged_in = True
                self.nickname = prof.get("nickname", "网易云会员")
                self.vip_type = int(prof.get("vipType", 0))
                self._save_session()
                return {
                    "code": 803,
                    "status": "success",
                    "logged_in": True,
                    "nickname": self.nickname,
                    "vip_type": self.vip_type,
                    "message": f"登录成功！欢迎 {self.nickname}",
                }
            status_map = {
                800: ("expired", "二维码已过期，请刷新重试"),
                801: ("waiting", "请打开手机网易云音乐「扫一扫」"),
                802: ("scanned", "已扫码，请在手机上点击确认登录"),
            }
            st_key, msg = status_map.get(code, ("unknown", resp.get("message", "检测中")))
            return {"code": code, "status": st_key, "message": msg, "logged_in": False}
        except Exception as e:
            return {"code": -1, "status": "error", "message": str(e)}

    def _resolve_track_url(self, song_id: int) -> Optional[str]:
        """获取单首歌曲可播放音频流 URL (pyncm / 带 Cookie 官方接口优先，其次校验外链 302 避免 404)"""
        if HAS_PYNCM:
            try:
                res = ncm_apis.track.GetTrackAudio(song_id, bitrate=320000)
                items = (res or {}).get("data") or []
                if items and items[0].get("url"):
                    return items[0]["url"]
            except Exception:
                pass

        try:
            r = self._http.post(
                "https://music.163.com/api/song/enhance/player/url",
                data={"ids": json.dumps([int(song_id)]), "br": 320000},
                timeout=5,
            )
            if r.status_code == 200:
                items = (r.json() or {}).get("data") or []
                if items and items[0].get("url"):
                    return items[0]["url"]
        except Exception:
            pass

        # 公网直链重定向检查: 非 /404 即真实可播音频
        outer = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
        try:
            hr = self._http.head(outer, allow_redirects=False, timeout=4)
            loc = hr.headers.get("Location", "")
            if loc and "/404" not in loc and loc.startswith("http"):
                return loc
            if hr.status_code == 200:
                return outer
        except Exception:
            pass
        return None

    def search_playable_tracks(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """搜索歌曲并过滤出真正可播放的候选列表"""
        kw = (query or "").strip()
        raw_songs: List[Dict[str, Any]] = []

        if HAS_PYNCM and kw:
            try:
                res = ncm_apis.cloudsearch.GetSearchResult(keyword=kw, stype=1, limit=limit)
                raw_songs = (res or {}).get("result", {}).get("songs") or []
            except Exception:
                pass

        if not raw_songs and kw:
            try:
                r = self._http.get(
                    "https://music.163.com/api/search/get/web",
                    params={"s": kw, "type": 1, "offset": 0, "limit": limit},
                    timeout=5,
                )
                if r.status_code == 200:
                    raw_songs = (r.json() or {}).get("result", {}).get("songs") or []
            except Exception as e:
                logger.warning(f"网易云搜索失败: {e}")

        playable: List[Dict[str, Any]] = []
        for s in raw_songs:
            sid = s.get("id")
            if not sid:
                continue
            artists = s.get("ar") or s.get("artists") or []
            artist_name = " / ".join(a.get("name", "") for a in artists if a.get("name")) or "未知歌手"
            title = s.get("name", "未知歌曲")
            duration_ms = int(s.get("dt") or s.get("duration") or 0)

            url = self._resolve_track_url(int(sid))
            if url:
                playable.append({
                    "id": int(sid),
                    "title": title,
                    "artist": artist_name,
                    "duration_sec": max(0, duration_ms // 1000),
                    "url": url,
                })
                if len(playable) >= 5:
                    break
        return playable

    def get_recommended_tracks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取每日推荐或云音乐热歌榜中的可播曲目"""
        raw_songs: List[Dict[str, Any]] = []

        # 1. 已登录时优先尝试每日推荐
        if self.logged_in:
            try:
                if HAS_PYNCM:
                    rec = ncm_apis.recommend.GetRecommendSongs()
                    raw_songs = (rec or {}).get("data", {}).get("dailySongs") or []
                else:
                    r = self._http.get("https://music.163.com/api/v3/discovery/recommend/songs", timeout=5)
                    if r.status_code == 200:
                        raw_songs = (r.json() or {}).get("data", {}).get("dailySongs") or []
            except Exception:
                pass

        # 2. 回退云音乐热歌榜 (id=3778678) / 轻音乐歌单搜索
        if not raw_songs:
            try:
                if HAS_PYNCM:
                    pl = ncm_apis.playlist.GetPlaylistInfo(3778678)
                    raw_songs = (pl or {}).get("playlist", {}).get("tracks") or []
                else:
                    r = self._http.get(
                        "https://music.163.com/api/playlist/detail",
                        params={"id": 3778678},
                        timeout=5,
                    )
                    if r.status_code == 200:
                        raw_songs = (r.json() or {}).get("result", {}).get("tracks") or []
            except Exception:
                pass

        if not raw_songs:
            return self.search_playable_tracks("轻音乐 治愈", limit=limit)

        playable: List[Dict[str, Any]] = []
        for s in raw_songs[:limit * 2]:
            sid = s.get("id")
            if not sid:
                continue
            artists = s.get("ar") or s.get("artists") or []
            artist_name = " / ".join(a.get("name", "") for a in artists if a.get("name")) or "未知歌手"
            url = self._resolve_track_url(int(sid))
            if url:
                playable.append({
                    "id": int(sid),
                    "title": s.get("name", "热门歌曲"),
                    "artist": artist_name,
                    "duration_sec": int(s.get("dt") or s.get("duration") or 0) // 1000,
                    "url": url,
                })
                if len(playable) >= 6:
                    break
        return playable


netease_client = NeteaseClient()
