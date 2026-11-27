"""
WiFi Manager & Provisioning Module for PIG (Raspberry Pi & Simulation)
Handles:
- Network status detection (SSID, IP, signal, online check)
- WiFi scanning (nmcli / iwlist on Linux, realistic mock fallback in simulation)
- WiFi connection & credential persistence
- SoftAP (Hotspot) provisioning mode for first-boot headless setup
"""

import os
import sys
import time
import json
import socket
import logging
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger("WiFiManager")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WIFI_CFG_PATH = DATA_DIR / "wifi_config.json"

def _is_real_lan_ip(ip: str) -> bool:
    """判断是否为有效局域网/公网 IP（排除回环 127.x 与链路本地 169.254.x）"""
    if not ip:
        return False
    ip = ip.strip()
    if ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
        return False
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def get_local_ip() -> str:
    """获取本机局域网通信真实 IP（离线时返回 127.0.0.1，避免误判 127.0.1.1 为在线）"""
    for probe_ip in ("8.8.8.8", "223.5.5.5"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            s.connect((probe_ip, 80))
            ip = s.getsockname()[0]
            s.close()
            if _is_real_lan_ip(ip):
                return ip
        except Exception:
            pass

    try:
        res = subprocess.run(["hostname", "-I"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.0)
        if res.returncode == 0:
            for tok in res.stdout.strip().split():
                if _is_real_lan_ip(tok):
                    return tok
    except Exception:
        pass

    return "127.0.0.1"


class WiFiManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.ap_mode = False
        self.ap_ssid = "PIG-Setup-8090"
        self.ap_password = "88888888"
        self.ap_ip = "192.168.4.1"
        self.ap_error = ""
        self.connected_ssid = ""
        self.is_connected = False
        self.current_ip = get_local_ip()
        self.signal_strength = 85
        self._last_refresh_ts = 0.0

        self._load_config()
        self._refresh_state(force=True)

    @staticmethod
    def _nmcli_path() -> str:
        return "/usr/bin/nmcli" if os.path.exists("/usr/bin/nmcli") else "nmcli"

    def _run_nmcli(self, args: List[str], timeout: float = 4.0, allow_sudo: bool = False):
        """执行 nmcli；遇到权限不足时自动回退尝试 sudo -n"""
        nmcli = self._nmcli_path()
        cmd = [nmcli, *args]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res.returncode != 0 and allow_sudo and hasattr(os, "geteuid") and os.geteuid() != 0:
            sudo_res = subprocess.run(
                ["sudo", "-n", nmcli, *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
            )
            if sudo_res.returncode == 0 or sudo_res.stderr:
                return sudo_res
        return res

    def _get_wifi_iface(self) -> str:
        """动态查找当前系统的无线网卡设备名 (例如 wlan0)"""
        try:
            res = subprocess.run(
                [self._nmcli_path(), "-t", "-f", "DEVICE,TYPE", "dev"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0,
            )
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    parts = line.split(":")
                    if len(parts) >= 2 and parts[1].strip() == "wifi" and parts[0].strip():
                        return parts[0].strip()
        except Exception:
            pass
        return ""

    def _read_iface_ipv4(self, iface: str) -> str:
        """读取指定网卡的当前 IPv4 地址"""
        if not iface:
            return ""
        try:
            res = subprocess.run(
                [self._nmcli_path(), "-g", "IP4.ADDRESS", "dev", "show", iface],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0,
            )
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    ip = line.split("/")[0].strip()
                    if _is_real_lan_ip(ip):
                        return ip
        except Exception:
            pass
        return ""

    def _load_config(self):
        """读取保存的 Wi-Fi 配置"""
        if WIFI_CFG_PATH.exists():
            try:
                with open(WIFI_CFG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.connected_ssid = cfg.get("ssid", "")
            except Exception as e:
                logger.warning(f"读取 wifi_config.json 异常: {e}")

    def _save_config(self, ssid: str, password: str):
        """原子化持久化已配置的 Wi-Fi 凭证"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = WIFI_CFG_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({
                    "ssid": ssid,
                    "password": password,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, WIFI_CFG_PATH)
        except Exception as e:
            logger.warning(f"保存 wifi_config.json 异常: {e}")

    def _refresh_state(self, force: bool = False):
        """实时检测当前物理网络与热点连通状态 (带 3s TTL 避免高频轮询阻塞)"""
        now = time.time()
        if not force and (now - self._last_refresh_ts) < 3.0:
            return
        self._last_refresh_ts = now

        ip = get_local_ip()
        self.current_ip = ip
        nmcli = self._nmcli_path()

        # 1. 检测 Linux NetworkManager (树莓派官方网络栈)
        try:
            # 1a. 若正在 AP 模式或存在 PIG-Hotspot 活动连接，优先核验真实热点状态
            wifi_iface = self._get_wifi_iface()
            if wifi_iface:
                con_res = subprocess.run(
                    [nmcli, "-t", "-f", "NAME", "con", "show", "--active"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0,
                )
                if con_res.returncode == 0:
                    active_cons = [c.strip() for c in con_res.stdout.splitlines() if c.strip()]
                    if "PIG-Hotspot" in active_cons:
                        self.ap_mode = True
                        self.is_connected = False
                        real_ap_ip = self._read_iface_ipv4(wifi_iface)
                        if real_ap_ip:
                            self.ap_ip = real_ap_ip
                        return
                    elif self.ap_mode:
                        self.ap_mode = False

            # 1b. 检测当前是否真正关联到 Wi-Fi 路由器
            res = subprocess.run(
                [nmcli, "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.5,
            )
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    if line.startswith("yes:"):
                        parts = line.split(":")
                        if len(parts) >= 2:
                            self.connected_ssid = parts[1].strip() or self.connected_ssid or "Wi-Fi"
                            self.is_connected = _is_real_lan_ip(ip) or bool(wifi_iface and self._read_iface_ipv4(wifi_iface))
                            if len(parts) >= 3 and parts[2].isdigit():
                                self.signal_strength = int(parts[2])
                            return

                # 1c. 没有活跃 Wi-Fi，再检查是否有有线以太网连接并拿到真实 LAN IP
                dev_res = subprocess.run(
                    [nmcli, "-t", "-f", "TYPE,STATE", "dev"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0,
                )
                if dev_res.returncode == 0:
                    has_wired = any(
                        line.strip() == "ethernet:connected"
                        for line in dev_res.stdout.splitlines()
                    )
                    if has_wired and _is_real_lan_ip(ip):
                        self.is_connected = True
                        self.connected_ssid = "有线以太网"
                        return

                # nmcli 正常返回但既无 Wi-Fi 也无有线 IP => 真实离线
                self.is_connected = False
                self.connected_ssid = ""
                return
        except Exception:
            pass

        # 2. 回退检测 iwgetid (无 NetworkManager 的轻量 Linux)
        try:
            res = subprocess.run(["iwgetid", "-r"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5)
            if res.returncode == 0 and res.stdout.strip():
                self.connected_ssid = res.stdout.strip()
                self.is_connected = _is_real_lan_ip(ip)
                return
        except Exception:
            pass

        # 3. 非 NetworkManager 环境 (仿真容器/桌面测试)：严格按真实非回环 IP 判断
        if _is_real_lan_ip(ip):
            self.is_connected = True
            if not self.connected_ssid:
                self.connected_ssid = "PIG-WiFi-Net"
        else:
            self.is_connected = False
            self.connected_ssid = ""

    def get_status(self, force: bool = False) -> Dict[str, Any]:
        """获取 Wi-Fi 综合状态"""
        with self._lock:
            self._refresh_state(force=force)
            return {
                "connected": self.is_connected,
                "ssid": self.connected_ssid or (self.ap_ssid if self.ap_mode else "未连接"),
                "ip": self.ap_ip if self.ap_mode else self.current_ip,
                "ap_mode": self.ap_mode,
                "ap_ssid": self.ap_ssid,
                "ap_password": self.ap_password,
                "ap_ip": self.ap_ip,
                "ap_error": self.ap_error,
                "signal": self.signal_strength if self.is_connected else (100 if self.ap_mode else 0),
                "wifi_qr": f"WIFI:T:WPA;S:{self.ap_ssid};P:{self.ap_password};;",
                "settings_url": f"http://{self.ap_ip if self.ap_mode else self.current_ip}:8090/settings"
            }

    def scan_networks(self) -> List[Dict[str, Any]]:
        """扫描周围 Wi-Fi 列表"""
        networks = []
        # 1. 尝试真实 Linux nmcli
        nmcli_path = "/usr/bin/nmcli" if os.path.exists("/usr/bin/nmcli") else "nmcli"
        try:
            res = subprocess.run([nmcli_path, "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                seen = set()
                for line in res.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ssid = parts[0].strip()
                        if not ssid or ssid in seen:
                            continue
                        seen.add(ssid)
                        sig = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 70
                        sec = parts[2] if len(parts) > 2 and parts[2] else "WPA2"
                        networks.append({
                            "ssid": ssid,
                            "signal": sig,
                            "security": sec,
                            "is_current": (ssid == self.connected_ssid)
                        })
                if networks:
                    networks.sort(key=lambda x: x["signal"], reverse=True)
                    return networks
        except Exception:
            pass

        # 2. 仿真环境真实模拟列表 (提供丰富真实感体验)
        cur = self.connected_ssid or "Home-WiFi-5G"
        mock_list = [
            {"ssid": cur, "signal": 96, "security": "WPA2/WPA3", "is_current": self.is_connected},
            {"ssid": "Xiaomi_AX6000_Pro", "signal": 88, "security": "WPA2", "is_current": False},
            {"ssid": "ChinaNet-WorkStudio", "signal": 75, "security": "WPA2", "is_current": False},
            {"ssid": "TP-LINK_LivingRoom_2.4G", "signal": 64, "security": "WPA2", "is_current": False},
            {"ssid": "Mijia-IoT-Private", "signal": 52, "security": "WPA2", "is_current": False},
            {"ssid": "Guest-Free-WiFi", "signal": 40, "security": "Open", "is_current": False}
        ]
        return mock_list

    def connect(self, ssid: str, password: str = "") -> Dict[str, Any]:
        """连接指定 Wi-Fi 热点"""
        if not ssid:
            return {"status": "error", "message": "SSID 不能为空"}

        logger.info(f"📶 正在连接目标 Wi-Fi: {ssid} ...")

        wifi_iface = self._get_wifi_iface()
        if wifi_iface:
            try:
                self._run_nmcli(["con", "down", "PIG-Hotspot"], timeout=6.0, allow_sudo=True)
                self._run_nmcli(["con", "delete", "PIG-Hotspot"], timeout=4.0, allow_sudo=True)
            except Exception:
                pass

            cmd = ["dev", "wifi", "connect", ssid]
            if password:
                cmd.extend(["password", password])
            try:
                res = self._run_nmcli(cmd, timeout=15.0, allow_sudo=True)
                if res.returncode != 0:
                    err_msg = (res.stderr or res.stdout or "Wi-Fi 连接失败").strip()
                    logger.warning(f"❌ 连接 Wi-Fi 失败: {err_msg}")
                    self._refresh_state(force=True)
                    return {"status": "error", "message": err_msg}
            except Exception as e:
                logger.warning(f"nmcli 连接异常: {e}")
                return {"status": "error", "message": str(e)}

        # 更新状态机与持久化
        with self._lock:
            self.connected_ssid = ssid
            self.is_connected = True
            self.ap_mode = False
            self.ap_error = ""
            self.signal_strength = 92
            self.current_ip = get_local_ip()
            self._last_refresh_ts = time.time()
            self._save_config(ssid, password)

        return {
            "status": "ok",
            "message": f"成功连接至 {ssid}",
            "ssid": ssid,
            "ip": self.current_ip,
            "settings_url": f"http://{self.current_ip}:8090/settings"
        }

    def start_softap(self) -> Dict[str, Any]:
        """开启 SoftAP 配网热点（真实树莓派通过 NetworkManager nmcli 拉起热点）"""
        with self._lock:
            self.ap_error = ""
            wifi_iface = self._get_wifi_iface()
            if wifi_iface:
                try:
                    self._run_nmcli(["con", "delete", "PIG-Hotspot"], timeout=4.0, allow_sudo=True)
                    res = self._run_nmcli(
                        [
                            "dev", "wifi", "hotspot",
                            "ifname", wifi_iface,
                            "con-name", "PIG-Hotspot",
                            "ssid", self.ap_ssid,
                            "password", self.ap_password,
                        ],
                        timeout=12.0,
                        allow_sudo=True,
                    )
                    if res.returncode != 0:
                        err = (res.stderr or res.stdout or "nmcli 启动热点失败").strip()
                        self.ap_mode = False
                        self.ap_error = err
                        logger.warning(f"❌ SoftAP 启动失败 ({wifi_iface}): {err}")
                        st = self.get_status(force=True)
                        st["status"] = "error"
                        st["message"] = err
                        return st

                    real_ap_ip = self._read_iface_ipv4(wifi_iface)
                    if real_ap_ip:
                        self.ap_ip = real_ap_ip
                except Exception as e:
                    self.ap_mode = False
                    self.ap_error = str(e)
                    logger.warning(f"❌ SoftAP 启动异常: {e}")
                    st = self.get_status(force=True)
                    st["status"] = "error"
                    st["message"] = str(e)
                    return st

            self.ap_mode = True
            self.is_connected = False
            self._last_refresh_ts = time.time()
            logger.info(f"📡 已开启配网热点: {self.ap_ssid} (IP: {self.ap_ip})")
            st = self.get_status(force=False)
            st["status"] = "ok"
            return st

    def stop_softap(self) -> Dict[str, Any]:
        """关闭配网热点并尝试恢复 Wi-Fi 自动连接"""
        with self._lock:
            wifi_iface = self._get_wifi_iface()
            if wifi_iface:
                try:
                    self._run_nmcli(["con", "down", "PIG-Hotspot"], timeout=8.0, allow_sudo=True)
                    self._run_nmcli(["con", "delete", "PIG-Hotspot"], timeout=5.0, allow_sudo=True)
                    self._run_nmcli(["dev", "connect", wifi_iface], timeout=10.0, allow_sudo=True)
                except Exception as e:
                    logger.warning(f"关闭 SoftAP 清理异常: {e}")

            self.ap_mode = False
            self.ap_error = ""
            st = self.get_status(force=True)
            st["status"] = "ok"
            return st


# 单例实例
wifi_manager = WiFiManager()
