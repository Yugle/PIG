# -*- coding: utf-8 -*-
"""
网易云音乐检索、可播校验与登录客户端 (skills/netease_music/netease_client.py)
支持：
1. 原生 WEAPI (AES+RSA) 扫码登录 + Cookie (MUSIC_U) 备选登录 + 会话持久化 (data/netease_session.json)
2. 公网检索兜底 + 账号带 Cookie 高码率音轨获取 + 外链 CDN 302 真实可播性校验
"""

import os
import io
import json
import time
import base64
import random
import string
import logging
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger("NeteaseClient")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSION_PATH = os.path.join(PROJECT_ROOT, "data", "netease_session.json")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

_WEAPI_MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbd"
    "a92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
_WEAPI_PUBKEY = "010001"
_WEAPI_NONCE = b"0CoJUm6Qyw8W8jud"


def _weapi_encrypt(payload: Dict[str, Any]) -> Dict[str, str]:
    """构造网易云 Web 版 WEAPI 加密请求参数"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    def _aes_cbc(plain: str, key: bytes) -> str:
        cipher = AES.new(key, AES.MODE_CBC, b"0102030405060708")
        return base64.b64encode(cipher.encrypt(pad(plain.encode("utf-8"), 16))).decode("utf-8")

    sec = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    text = json.dumps(payload)
    enc_text = _aes_cbc(_aes_cbc(text, _WEAPI_NONCE), sec.encode("utf-8"))
    rs = pow(int(sec[::-1].encode("utf-8").hex(), 16), int(_WEAPI_PUBKEY, 16), int(_WEAPI_MODULUS, 16))
    return {"params": enc_text, "encSecKey": format(rs, "x").zfill(256)}


try:
    import pyncm
    from pyncm import apis as ncm_apis
    HAS_PYNCM = True
except ImportError:
    pyncm = None
    ncm_apis = None
    HAS_PYNCM = False


class NeteaseClient:
    """网易云音乐接口封装 (内置 WEAPI 扫码 + MUSIC_U Cookie 登录，不依赖 pyncm)"""

    def __init__(self):
        self.logged_in = False
        self.nickname = ""
        self.vip_type = 0
        self._qr_unikey = ""
        self._http = requests.Session()
        self._http.headers.update(_HEADERS)
        self._init_client_cookies()
        self._load_saved_session()

    def _init_client_cookies(self):
        self._http.cookies.set("os", "pc", domain=".music.163.com")
        self._http.cookies.set("appver", "3.0.18.203152", domain=".music.163.com")
        self._http.cookies.set("__remember_me", "true", domain=".music.163.com")

    def _fetch_http_profile(self) -> Dict[str, Any]:
        try:
            r = self._http.post(
                "https://music.163.com/weapi/nuser/account/get",
                data=_weapi_encrypt({"csrf_token": self._http.cookies.get("__csrf", "")}),
                timeout=6,
            )
            if r.status_code == 200:
                prof = (r.json() or {}).get("profile") or {}
                if prof.get("userId"):
                    return prof
        except Exception:
            pass
        try:
            r = self._http.get("https://music.163.com/api/nuser/account/get", timeout=6)
            if r.status_code == 200:
                return (r.json() or {}).get("profile") or {}
        except Exception:
            pass
        return {}

    def _apply_cookie_header_str(self, cookie_str: str):
        for seg in (cookie_str or "").split(";"):
            part = seg.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            if k.lower() in {"path", "domain", "expires", "max-age", "samesite"}:
                continue
            if k:
                self._http.cookies.set(k, v.strip(), domain=".music.163.com")

    def _load_saved_session(self):
        if not os.path.exists(SESSION_PATH):
            return
        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

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

            cookies = data.get("cookies")
            if isinstance(cookies, dict) and cookies:
                for ck, cv in cookies.items():
                    self._http.cookies.set(ck, cv, domain=".music.163.com")
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
        self._init_client_cookies()
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

    def login_with_cookie(self, cookie_input: str) -> Dict[str, Any]:
        """使用网页版 MUSIC_U Cookie 登录（当扫码遇到网易云 8821 风控时最稳方案）"""
        raw = (cookie_input or "").strip()
        if not raw:
            return {"status": "error", "message": "MUSIC_U Cookie 不能为空"}

        if "MUSIC_U=" in raw:
            self._apply_cookie_header_str(raw)
        else:
            self._http.cookies.set("MUSIC_U", raw, domain=".music.163.com")

        prof = self._fetch_http_profile()
        if not prof.get("userId"):
            return {"status": "error", "message": "Cookie 无效或已过期，请重新从 music.163.com 复制完整的 MUSIC_U 值"}

        self.logged_in = True
        self.nickname = prof.get("nickname", "网易云会员")
        self.vip_type = int(prof.get("vipType", 0))
        self._save_session()
        logger.info(f"🎵 [Netease] Cookie 登录成功: {self.nickname} (VIP={self.vip_type})")
        return {
            "status": "ok",
            "logged_in": True,
            "nickname": self.nickname,
            "vip_type": self.vip_type,
            "message": f"登录成功！欢迎 {self.nickname}",
        }

    def start_qr_login(self) -> Dict[str, Any]:
        """生成网易云手机端扫码登录二维码 (WEAPI 优先 + 零依赖)"""
        try:
            unikey = ""
            login_url = ""
            if HAS_PYNCM:
                resp = ncm_apis.login.LoginQrcodeUnikey()
                unikey = (resp or {}).get("unikey", "")
                if unikey:
                    login_url = ncm_apis.login.GetLoginQRCodeUrl(unikey)

            if not unikey:
                r = self._http.post(
                    "https://music.163.com/weapi/login/qrcode/unikey",
                    data=_weapi_encrypt({"type": 1}),
                    timeout=6,
                )
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
        """轮询网易云二维码扫码状态 (800过期 / 801待扫 / 802待确认 / 803成功 / 8821风控)"""
        if not self._qr_unikey:
            return {"code": 0, "status": "idle", "logged_in": self.logged_in, "nickname": self.nickname}
        try:
            if HAS_PYNCM:
                resp = ncm_apis.login.LoginQrcodeCheck(self._qr_unikey) or {}
            else:
                r = self._http.post(
                    "https://music.163.com/weapi/login/qrcode/client/login",
                    data=_weapi_encrypt({"key": self._qr_unikey, "type": 1}),
                    timeout=6,
                )
                resp = r.json() or {}

            code = int(resp.get("code", 0))
            if code == 803:
                if resp.get("cookie"):
                    self._apply_cookie_header_str(str(resp["cookie"]))
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
                8821: ("blocked", "触发网易云风控(8821)，请用 MUSIC_U Cookie 登录"),
            }
            st_key, msg = status_map.get(code, ("error" if code >= 400 else "unknown", resp.get("message", "检测中")))
            return {"code": code, "status": st_key, "message": msg, "logged_in": False}
        except Exception as e:
            return {"code": -1, "status": "error", "message": str(e)}

    def _resolve_track_url(self, song_id: int) -> Optional[str]:
        """获取单首歌曲可播放音频流 URL (pyncm / WEAPI 带 Cookie 优先，其次外链 302 校验)"""
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
                "https://music.163.com/weapi/song/enhance/player/url/v1",
                data=_weapi_encrypt({"ids": [int(song_id)], "level": "exhigh", "encodeType": "mp3"}),
                timeout=5,
            )
            if r.status_code == 200:
                items = (r.json() or {}).get("data") or []
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

    def _batch_resolve_urls(self, song_ids: List[int]) -> Dict[int, str]:
        """批量获取音频流 URL（单次 WEAPI 请求搞定整个歌单，避免逐首请求耗时）"""
        urls: Dict[int, str] = {}
        clean_ids = [int(x) for x in song_ids if x]
        if not clean_ids:
            return urls
        try:
            r = self._http.post(
                "https://music.163.com/weapi/song/enhance/player/url/v1",
                data=_weapi_encrypt({"ids": clean_ids, "level": "exhigh", "encodeType": "mp3"}),
                timeout=8,
            )
            if r.status_code == 200:
                for it in (r.json() or {}).get("data") or []:
                    sid = it.get("id")
                    u = it.get("url")
                    if sid and u:
                        urls[int(sid)] = u
        except Exception:
            pass
        return urls

    def _songs_to_playable(self, raw_songs: List[Dict[str, Any]], max_tracks: int = 20) -> List[Dict[str, Any]]:
        """将网易云原始歌曲对象列表批量转化为可播队列"""
        candidates = [s for s in (raw_songs or []) if s.get("id")][:max_tracks * 2]
        if not candidates:
            return []
        url_map = self._batch_resolve_urls([int(s["id"]) for s in candidates])
        playable: List[Dict[str, Any]] = []
        for s in candidates:
            sid = int(s["id"])
            url = url_map.get(sid) or self._resolve_track_url(sid)
            if not url:
                continue
            artists = s.get("ar") or s.get("artists") or []
            artist_name = " / ".join(a.get("name", "") for a in artists if a.get("name")) or "未知歌手"
            playable.append({
                "id": sid,
                "title": s.get("name", "未知歌曲"),
                "artist": artist_name,
                "duration_sec": max(0, int(s.get("dt") or s.get("duration") or 0) // 1000),
                "url": url,
            })
            if len(playable) >= max_tracks:
                break
        return playable

    def get_user_playlists(self, limit: int = 25) -> List[Dict[str, Any]]:
        """获取已登录账号创建和收藏的歌单（第 1 个固定为『我喜欢的音乐』）"""
        if not self.logged_in:
            return []
        prof = self._fetch_http_profile()
        uid = prof.get("userId")
        if not uid:
            return []
        try:
            r = self._http.post(
                "https://music.163.com/weapi/user/playlist",
                data=_weapi_encrypt({"uid": uid, "limit": limit, "offset": 0, "includeVideo": True}),
                timeout=8,
            )
            raw_pls = (r.json() or {}).get("playlist") or []
            result: List[Dict[str, Any]] = []
            for idx, p in enumerate(raw_pls):
                creator = (p.get("creator") or {}).get("nickname", "")
                result.append({
                    "id": int(p["id"]),
                    "name": p.get("name", "未命名歌单"),
                    "track_count": int(p.get("trackCount", 0)),
                    "creator": creator,
                    "is_liked": (idx == 0),
                    "cover_url": p.get("coverImgUrl", ""),
                })
            return result
        except Exception as e:
            logger.warning(f"获取用户歌单失败: {e}")
            return []

    def search_playlists(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """搜索公开网易云歌单"""
        kw = (query or "").strip()
        if not kw:
            return []
        try:
            r = self._http.get(
                "https://music.163.com/api/search/get/web",
                params={"s": kw, "type": 1000, "offset": 0, "limit": limit},
                timeout=6,
            )
            if r.status_code == 200:
                pls = (r.json() or {}).get("result", {}).get("playlists") or []
                return [
                    {
                        "id": int(p["id"]),
                        "name": p.get("name", "精选歌单"),
                        "track_count": int(p.get("trackCount", 0)),
                        "creator": (p.get("creator") or {}).get("nickname", ""),
                        "cover_url": p.get("coverImgUrl", ""),
                    }
                    for p in pls if p.get("id")
                ]
        except Exception as e:
            logger.warning(f"搜索歌单失败: {e}")
        return []

    def get_playlist_tracks(self, playlist_id: int, limit: int = 20) -> Dict[str, Any]:
        """拉取指定歌单内的可播曲目"""
        try:
            r = self._http.post(
                "https://music.163.com/weapi/v6/playlist/detail",
                data=_weapi_encrypt({"id": int(playlist_id), "n": max(20, limit * 2), "s": 0}),
                timeout=8,
            )
            pl = (r.json() or {}).get("playlist") or {}
            pl_name = pl.get("name", "")
            raw_songs = pl.get("tracks") or []
            if not raw_songs:
                track_ids = [t.get("id") for t in (pl.get("trackIds") or []) if t.get("id")][:limit * 2]
                if track_ids:
                    dr = self._http.post(
                        "https://music.163.com/weapi/v3/song/detail",
                        data=_weapi_encrypt({"c": json.dumps([{"id": int(tid)} for tid in track_ids])}),
                        timeout=8,
                    )
                    raw_songs = (dr.json() or {}).get("songs") or []
            return {"name": pl_name, "tracks": self._songs_to_playable(raw_songs, max_tracks=limit)}
        except Exception as e:
            logger.warning(f"拉取歌单曲目失败: {e}")
            return {"name": "", "tracks": []}

    def get_liked_tracks(self, limit: int = 20) -> Dict[str, Any]:
        """拉取已登录用户的『我喜欢的音乐』"""
        pls = self.get_user_playlists(limit=5)
        if not pls:
            return {"name": "我喜欢的音乐", "tracks": []}
        res = self.get_playlist_tracks(pls[0]["id"], limit=limit)
        if not res.get("name"):
            res["name"] = pls[0]["name"]
        return res

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

        if self.logged_in:
            try:
                if HAS_PYNCM:
                    rec = ncm_apis.recommend.GetRecommendSongs()
                    raw_songs = (rec or {}).get("data", {}).get("dailySongs") or []
                else:
                    r = self._http.post(
                        "https://music.163.com/weapi/v3/discovery/recommend/songs",
                        data=_weapi_encrypt({"offset": 0, "limit": 20}),
                        timeout=5,
                    )
                    if r.status_code == 200:
                        raw_songs = (r.json() or {}).get("data", {}).get("dailySongs") or []
            except Exception:
                pass

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
