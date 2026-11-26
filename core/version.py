"""
Real Git Tag-based Firmware Version & OTA Update Manager
Zero-mock implementation:
- Reads genuine SemVer version tags from Git repository.
- Checks remote Git tags (via git ls-remote) across remotes (origin / github).
- Compares SemVer versions dynamically.
"""

import json
import os
import re
import subprocess
import threading
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DIRECTORY = Path(__file__).resolve().parent.parent


def normalize_ota_url(raw_url: str) -> str:
    """
    规范化 OTA 地址：允许用户直接填写域名（如 ota.example.com 或 https://ota.example.com），
    自动补全 https:// 与 /version.json；若已包含具体路径则保持原样。
    """
    url = str(raw_url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.path or parsed.path == "/":
        url = url.rstrip("/") + "/version.json"
    return url


def get_ota_url() -> str:
    """
    获取自定义 OTA 版本检查 URL（如部署在 Vercel 的 ota.yourdomain.com）。
    优先级: 环境变量 PIG_OTA_URL > config.json 中的 ota_url。
    """
    env_url = os.environ.get("PIG_OTA_URL", "").strip()
    if env_url:
        return normalize_ota_url(env_url)
    cfg_file = DIRECTORY / "config.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            url = str(data.get("ota_url", "")).strip()
            if url:
                return normalize_ota_url(url)
        except Exception:
            pass
    return ""


def parse_semver(tag_str: str) -> Tuple[int, int, int]:
    """
    解析语义化版本字符串 (SemVer: Major.Minor.Patch)
    支持 'v0.1.0', '0.1.0', 'v1.2.3-beta' 等
    """
    if not tag_str:
        return (0, 0, 0)
    clean = tag_str.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", clean)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3) or 0)
        return (major, minor, patch)
    return (0, 0, 0)


def get_current_tag() -> Optional[str]:
    """
    获取本地最近的 Git Tag (如 'v0.1.0')
    """
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=DIRECTORY
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def get_current_version() -> str:
    """
    获取当前系统固件真实版本号:
    1. 优先读取 Git Release Tag (如 v0.1.0)
    2. 若未打 Tag，动态读取当前 commit hash 生成开发态版本号 (如 dev-115ec49)
    绝不使用虚假硬编码 mock 数据！
    """
    # 1. 尝试读取最近 Tag
    tag = get_current_tag()
    if tag:
        return tag

    # 2. 读取当前 Git Commit Hash 构造开发版本
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=DIRECTORY
        )
        if res.returncode == 0 and res.stdout.strip():
            return f"dev-{res.stdout.strip()}"
    except Exception:
        pass

    return "v0.1.0-dev"


def _build_check_result(current_ver: str, latest_tag: str, changelog: str = "") -> Dict:
    """根据当前版本与远端最新版本构造统一返回结构"""
    current_tuple = parse_semver(current_ver)
    latest_tuple = parse_semver(latest_tag)
    if latest_tuple > current_tuple:
        res = {
            "has_update": True,
            "current_version": current_ver,
            "latest_version": latest_tag,
            "status": f"新版 {latest_tag}",
        }
    else:
        res = {
            "has_update": False,
            "current_version": current_ver,
            "latest_version": latest_tag or current_ver,
            "status": "已是最新",
        }
    if changelog:
        res["changelog"] = changelog
    return res


def check_ota_via_url(ota_url: str, current_ver: str, timeout_sec: float = 4.0) -> Optional[Dict]:
    """
    通过 Vercel 等静态 HTTPS 接口检查 version.json。
    成功返回结果字典，失败返回 None（由调用方自动回退到 Git 探测）。
    """
    try:
        req = urllib.request.Request(
            ota_url,
            headers={"User-Agent": "PIG-Robot-OTA/1.0", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        latest_tag = str(data.get("latest_version") or data.get("version") or "").strip()
        if not latest_tag or parse_semver(latest_tag) <= (0, 0, 0):
            return None
        changelog = str(data.get("changelog") or "").strip()
        res = _build_check_result(current_ver, latest_tag, changelog=changelog)
        res["source"] = "vercel"
        return res
    except Exception:
        return None


def check_ota_update(timeout_sec: float = 8.0) -> Dict:
    """
    真实检查远程 OTA 更新:
    1. 若配置了 ota_url（如 Vercel 自定义域名），优先请求 version.json
    2. 未配置或请求失败时，回退至 git ls-remote --tags (origin / github)
    """
    current_ver = get_current_version()

    ota_url = get_ota_url()
    if ota_url:
        url_res = check_ota_via_url(ota_url, current_ver, timeout_sec=min(timeout_sec, 4.0))
        if url_res is not None:
            return url_res

    remote_tags: List[str] = []
    error_msg = None

    # 依次探测已配置的远程仓库 (origin -> github)
    remotes_to_check = ["origin", "github"]
    for remote in remotes_to_check:
        try:
            res = subprocess.run(
                ["git", "ls-remote", "--tags", remote],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=DIRECTORY
            )
            if res.returncode == 0:
                lines = res.stdout.strip().splitlines()
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        ref = parts[1]
                        if ref.startswith("refs/tags/"):
                            t = ref.replace("refs/tags/", "").replace("^{}", "")
                            if t and t not in remote_tags:
                                remote_tags.append(t)
                if remote_tags:
                    break
        except subprocess.TimeoutExpired:
            error_msg = "网络请求超时"
        except Exception as e:
            error_msg = str(e)

    # 如果无法连接任何远程仓库
    if not remote_tags:
        if error_msg:
            return {
                "has_update": False,
                "current_version": current_ver,
                "latest_version": current_ver,
                "status": "检查失败"
            }
        return {
            "has_update": False,
            "current_version": current_ver,
            "latest_version": current_ver,
            "status": "已是最新"
        }

    # 解析所有合规的语义化 Tag
    valid_tags = []
    for t in remote_tags:
        t_tuple = parse_semver(t)
        if t_tuple > (0, 0, 0):
            valid_tags.append((t_tuple, t))

    if not valid_tags:
        return {
            "has_update": False,
            "current_version": current_ver,
            "latest_version": current_ver,
            "status": "已是最新"
        }

    # 排序找到最高版本
    valid_tags.sort(key=lambda x: x[0], reverse=True)
    _, latest_tag = valid_tags[0]
    return _build_check_result(current_ver, latest_tag)


def perform_ota_update(target_tag: Optional[str] = None) -> Dict:
    """
    真实执行 OTA 代码拉取与版本切换（优先 origin，失败回退 github）
    """
    try:
        # 1. 同步拉取远程 Tags
        active_remote = None
        last_err = ""
        for remote in ("origin", "github"):
            fetch_res = subprocess.run(
                ["git", "fetch", "--tags", remote],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=DIRECTORY
            )
            if fetch_res.returncode == 0:
                active_remote = remote
                break
            last_err = fetch_res.stderr.strip()

        if not active_remote:
            return {"success": False, "error": f"拉取失败: {last_err}"}

        # 2. 检出目标 Tag 或主分支
        if target_tag:
            checkout_res = subprocess.run(
                ["git", "checkout", target_tag],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=DIRECTORY
            )
            if checkout_res.returncode != 0:
                return {"success": False, "error": f"检出标签失败: {checkout_res.stderr.strip()}"}
        else:
            pull_res = subprocess.run(
                ["git", "pull", "--ff-only", active_remote, "main"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=DIRECTORY
            )
            if pull_res.returncode != 0:
                return {"success": False, "error": f"代码拉取失败: {pull_res.stderr.strip()}"}

        new_ver = get_current_version()
        return {
            "success": True,
            "message": f"OTA 升级完成，当前版本: {new_ver}",
            "version": new_ver
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


class VersionManager:
    """全局单例版本管理器"""
    def __init__(self):
        self._lock = threading.RLock()
        self._current_version = get_current_version()
        self._update_status = "已是最新"
        self._has_update = False
        self._latest_version = self._current_version

    @property
    def current_version(self) -> str:
        with self._lock:
            self._current_version = get_current_version()
            return self._current_version

    @property
    def update_status(self) -> str:
        with self._lock:
            return self._update_status

    def check_update_sync(self) -> Dict:
        res = check_ota_update()
        with self._lock:
            self._update_status = res["status"]
            self._has_update = res["has_update"]
            self._latest_version = res["latest_version"]
        return res

    def perform_ota_update(self, target_tag: Optional[str] = None) -> Dict:
        res = perform_ota_update(target_tag)
        if res.get("success"):
            with self._lock:
                self._current_version = get_current_version()
                self._update_status = "已是最新"
                self._has_update = False
                self._latest_version = self._current_version
        return res


version_manager = VersionManager()
