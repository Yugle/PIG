# 🍓 PIG 桌面机器人 - 树莓派真机联调与一键部署总结

> **更新日期**：2026-11-27  
> **覆盖范围**：全栈表情/反射/音乐/闹钟、真机声卡与摄像头适配、中文语音唤醒与低延迟优化、Wi-Fi 断网与 SoftAP 配网、飞书长连接及一键部署脚本加固。

---

## 一、 核心功能与稳定性升级清单

### 1. 语音唤醒、VAD 收音与低延迟全链路优化
- **离线中文唤醒增强（[drivers/wakeword_engine.py](../drivers/wakeword_engine.py)）**：
  - 自动生成 `n ǐ h ǎo x iǎo zh ū` 与口语连读变调 `n í h ǎo x iǎo zh ū` 双规则；
  - 默认 `sherpa_keywords_score` 提升至 `2.2`（`keywords_threshold=0.20`），修复近距离说话 `mic_gain=2.5` 硬削波失真，改为动态防破音限幅。
- **唤醒后 VAD 抗误截断（[core/hardware_agent.py](../core/hardware_agent.py)）**：
  - 加 `self._audio_lock` 与 `_recording_active` 互斥锁，杜绝唤醒线程 `_run_loop()` 与录音线程 `record_audio_clip()` 并发读 `pyaudio` 导致的底层卡死；
  - 录音前排空旧缓冲帧并屏蔽前 2 帧提示音尾音，按环境底噪自适应阈值 + 连续 2 帧开口判定 + 3 帧前滚保留首字；无人开口 4 秒直接返回空，不再误识别成“嗯。”。
- **语音链路响应与真机优先发声（[core/hardware_agent.py](../core/hardware_agent.py)、[core/streaming_pipeline.py](../core/streaming_pipeline.py)、[simulator/web_simulator.html](../simulator/web_simulator.html)）**：
  - LLM 吐句瞬间即发 `robot_chunk` 纯文字上屏（0ms 等待 TTS）；
  - 后台 `ThreadPoolExecutor` 并发预合成后续句子，`AsyncMiMoTTSStreamer` 按线程复用 `requests.Session()` Keep-Alive 连接池；
  - 跳过播前 `ffprobe` 子进程测时长阻塞，树莓派真机 `play_async()` 先于 Web `robot_audio` 拉起，避免工作台明显抢跑。

### 2. 摄像头视觉并发安全与追踪说明
- **多线程抓拍加锁（[drivers/hal_physical.py](../drivers/hal_physical.py)）**：为 `PhysicalHAL._cam` 增设 `self._cam_lock`，防止开启常驻人脸追踪 `_vision_loop()` 时与 `visual_qa` / `remote_guard` 并发读取 `/dev/video0` 冲突。
- **机制区分**：网页顶部 `📷 摄像头: 关` 与 `📷 电脑摄像头追踪: 关` 控制的是浏览器本机摄像头（受限于非 HTTPS 局域网权限）；树莓派真机 USB 摄像头需在 **⚙️ 系统配置 -> 视觉注视**、设置页 `/settings`、机身设置菜单或直接语音“开启人脸追踪”启用。

### 3. Wi-Fi 状态检测与真机 SoftAP 配网修复
- **真实离线判定（[core/wifi_manager.py](../core/wifi_manager.py)、[core/menu_fsm.py](../core/menu_fsm.py)）**：
  - 过滤 `127.*`（含 Debian 常见 `127.0.1.1`）、`169.254.*`、`0.0.0.0`，`get_status()` 改为带 3s TTL 的实时 `nmcli` 检测，断网不再停留在启动缓存的“已联网”；
  - `QR_CODE` 页面与 Web 模拟器新增红色 `未联网` 状态分支及 `[OK] 开启 AP 热点` 提示。
- **真机热点拉起（[core/wifi_manager.py](../core/wifi_manager.py)）**：
  - `start_softap()` / `stop_softap()` 改为真实调用 `nmcli dev wifi hotspot`（失败自动回退 `sudo -n`），动态读取分配的热点网关 IP（如 `10.42.0.1`），失败时透传真实错误信息（如缺 `dnsmasq-base` 或权限不足）。

### 4. 一键部署脚本加固（[scripts/setup_raspberry_pi.sh](../scripts/setup_raspberry_pi.sh)）
- 补全缺失包：APT 增加 `mpg123`（CLI MP3 播放与 `-f` 音量控制依赖）、`network-manager`、`dnsmasq-base`、`pulseaudio-utils`；用户组追加 `netdev`；PIP 补全 `sherpa-onnx`、`lark-oapi`。
- 解决国内网络与 `piwheels.org` 断流：全局安装时使用 `PIP_CONFIG_FILE=/dev/null` 跳过 `/etc/pip.conf`，直连清华源并配置 `--resume-retries 5` 及阿里云镜像回退；
- 解决 Git LFS 指针假模型问题：优先下载官方 `.tar.bz2`，回退使用 ModelScope 二进制直链拉取并校验 `.onnx` 体积（`> 100KB`）；
- 自动清理旧 `bluealsa` 劫持配置，并在同时检测到 USB 放音与录音设备时，按 ALSA 声卡 ID（`hw:CARD=...,DEV=0`）与 `dmix` 自动写入 `/etc/asound.conf`，防止树莓派重启后 HDMI 抢占序号导致声卡错位。
- 服务入口已对齐主程序 `app.py`。

---

## 二、 树莓派真机排查速查（踩坑备忘）

1. **声卡序号重启漂移 / 无声 / 响度卡死**：
   - 执行 `arecord -l && aplay -l`，不要在 `/etc/asound.conf` 里写死 `hw:2,0`、`hw:3,0`，改为 `hw:CARD=Device,DEV=0` / `hw:CARD=Device_1,DEV=0` 并为放音套一层 `dmix`。
2. **飞书长连接与测试告警**：
   - 若点测试告警提示“未配置有效的飞书群 Webhook URL，且暂无飞书私聊记录”，在确认 `🚀 [Feishu WS] 飞书官方长连接通道已成功建立` 后，先在飞书客户端向机器人私聊发一句 `"你好"` 录入 `last_open_id`。
3. **DNS 偶发解析失败（`[Errno -2] Name or service not known`）**：
   - 切换热点/路由器后若 `/etc/resolv.conf` 丢失 DNS，可临时执行 `echo "nameserver 223.5.5.5" | sudo tee /etc/resolv.conf` 恢复。
