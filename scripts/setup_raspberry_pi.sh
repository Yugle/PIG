#!/usr/bin/env bash
# ==============================================================================
# PIG 机器人 - 树莓派 3 一键全自动安装、硬件接口使能与 systemd 服务注册脚本
# 适用硬件：Raspberry Pi 3 / 3B / 3B+ (Raspberry Pi OS Bullseye / Bookworm)
# ==============================================================================

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}====================================================================${NC}"
echo -e "${GREEN}🤖 PIG 机器人 - 树莓派全自动一键部署程序${NC}"
echo -e "${CYAN}====================================================================${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

TARGET_USER="${SUDO_USER:-$USER}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
    if id "pi" >/dev/null 2>&1; then
        TARGET_USER="pi"
    else
        TARGET_USER="$(whoami)"
    fi
fi

START_NOW=0
for arg in "$@"; do
    case "$arg" in
        --start) START_NOW=1 ;;
    esac
done

echo -e "📂 工程根目录: ${YELLOW}${PROJECT_DIR}${NC} (运行用户: ${YELLOW}${TARGET_USER}${NC})"

# 1. 检查 root / sudo 权限
if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# 2. 自动配置树莓派硬件 SPI 与内核级安全开关机按钮
echo -e "\n${CYAN}[1/8] 配置 SPI 屏幕接口与硬件电源开关 (dtoverlay=gpio-shutdown)...${NC}"
CONFIG_TXT=""
[ -f "/boot/firmware/config.txt" ] && CONFIG_TXT="/boot/firmware/config.txt"
[ -z "$CONFIG_TXT" ] && [ -f "/boot/config.txt" ] && CONFIG_TXT="/boot/config.txt"

NEED_REBOOT=0
if [ -n "$CONFIG_TXT" ]; then
    if grep -q "^dtparam=spi=on" "$CONFIG_TXT"; then
        echo -e "${GREEN}✓ SPI 接口已处于开启状态 (${CONFIG_TXT})${NC}"
    else
        echo -e "${YELLOW}⚙️ 正在写入 dtparam=spi=on 至 ${CONFIG_TXT}...${NC}"
        echo "dtparam=spi=on" | $SUDO tee -a "$CONFIG_TXT" > /dev/null
        NEED_REBOOT=1
    fi

    if grep -q "^dtoverlay=gpio-shutdown" "$CONFIG_TXT"; then
        echo -e "${GREEN}✓ 内核级按钮安全开关机已开启 (Pin 5/GPIO 3 <-> Pin 6/GND)${NC}"
    else
        echo -e "${YELLOW}⚙️ 正在写入 dtoverlay=gpio-shutdown (Pin 5+6 按钮一键安全关机/开机)...${NC}"
        echo "dtoverlay=gpio-shutdown" | $SUDO tee -a "$CONFIG_TXT" > /dev/null
        NEED_REBOOT=1
    fi
else
    echo -e "${YELLOW}⚠️ 未检测到 /boot/config.txt，跳过树莓派设备树硬件配置${NC}"
fi

# 3. 切换国内清华 APT/PIP 镜像并安装底层系统依赖 (兼容 Debian Trixie)
echo -e "\n${CYAN}[2/8] 配置清华国内源并安装底层音视频、字体与编译依赖...${NC}"
$SUDO sed -i 's|https\?://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g; s|https\?://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g; s|https\?://archive.raspberrypi.com/debian|https://mirrors.tuna.tsinghua.edu.cn/raspberrypi|g' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null || true

$SUDO apt-get -o Acquire::ForceIPv4=true update -y -qq
$SUDO apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends \
    build-essential \
    python3-pip \
    python3-dev \
    python3-pygame \
    python3-pyaudio \
    python3-numpy \
    python3-pil \
    python3-opencv \
    python3-pycryptodome \
    fonts-wqy-microhei \
    libportaudio2 \
    libasound2-dev \
    portaudio19-dev \
    ffmpeg \
    mpv \
    alsa-utils \
    fswebcam \
    dnsmasq-base \
    bzip2 \
    curl \
    wget \
    libjpeg-dev \
    zlib1g-dev

echo -e "${GREEN}✓ 底层系统依赖与中文字体安装完毕！${NC}"

# 4. 安装并激活 pigpiod 硬件定时守护进程 (兼容 Debian Trixie 无 apt pigpio 包)
echo -e "\n${CYAN}[3/8] 配置并启动 pigpiod 硬件 DMA PWM 守护服务...${NC}"
if ! command -v pigpiod >/dev/null 2>&1; then
    if $SUDO apt-get -o Acquire::ForceIPv4=true install -y pigpio python3-pigpio 2>/dev/null; then
        echo -e "${GREEN}✓ 已通过 apt 安装 pigpio${NC}"
    else
        echo -e "${YELLOW}⚙️ 当前系统无 apt pigpio 包，正在从清华镜像下载源码编译安装 pigpiod...${NC}"
        rm -rf /tmp/pigpio && mkdir -p /tmp/pigpio
        curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/raspberrypi/pool/main/p/pigpio/pigpio_1.79.orig.tar.gz | tar -xz --strip-components=1 -C /tmp/pigpio
        (cd /tmp/pigpio && make -j4 && ($SUDO make install || true))
        $SUDO ldconfig
        rm -rf /tmp/pigpio
    fi
fi

if [ ! -f /etc/systemd/system/pigpiod.service ] && [ ! -f /lib/systemd/system/pigpiod.service ]; then
    PIGPIOD_BIN="$(command -v pigpiod || echo /usr/local/bin/pigpiod)"
    $SUDO tee /etc/systemd/system/pigpiod.service > /dev/null <<EOF
[Unit]
Description=Pigpio daemon
After=network.target

[Service]
ExecStart=${PIGPIOD_BIN} -l
ExecStop=/bin/systemctl kill pigpiod
Type=forking

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
fi

$SUDO systemctl enable pigpiod
$SUDO systemctl restart pigpiod

if systemctl is-active --quiet pigpiod; then
    echo -e "${GREEN}✓ pigpiod 硬件守护进程已正常运行 (端口 8888 就绪)${NC}"
else
    echo -e "${RED}⚠️ pigpiod 启动异常，请检查 systemctl status pigpiod${NC}"
fi

# 5. 配置外设硬件访问权限
echo -e "\n${CYAN}[4/8] 配置外设用户组权限 (gpio, audio, spi, video, dialout)...${NC}"
$SUDO usermod -a -G gpio,audio,spi,video,dialout "$TARGET_USER" 2>/dev/null || true
echo -e "${GREEN}✓ 用户组权限配置完成！${NC}"

# 6. 安装 Python 核心生态依赖 (走清华源 + 兼容 PEP 668)
echo -e "\n${CYAN}[5/8] 安装 Python 核心依赖与 ST7789 屏幕驱动...${NC}"
python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >/dev/null 2>&1 || true
python3 -m pip install pigpio luma.lcd spidev requests openai fastapi uvicorn qrcode edge-tts pypinyin sherpa-onnx websockets --quiet --break-system-packages 2>/dev/null || \
    python3 -m pip install pigpio luma.lcd spidev requests openai fastapi uvicorn qrcode edge-tts pypinyin sherpa-onnx websockets --quiet
echo -e "${GREEN}✓ Python 核心依赖与屏幕驱动安装成功！${NC}"

# 6.5 自动下载超轻量中文离线语音唤醒模型 (sherpa-onnx 3.3M int8)
KWS_DIR="${PROJECT_DIR}/models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
KWS_ENCODER="${KWS_DIR}/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
KWS_VALID=0
if [ -d "$KWS_DIR" ] && [ -f "${KWS_DIR}/tokens.txt" ]; then
    for enc in "${KWS_DIR}"/*encoder*.onnx; do
        if [ -f "$enc" ] && [ "$(wc -c < "$enc" 2>/dev/null || echo 0)" -gt 100000 ]; then
            KWS_VALID=1
            break
        fi
    done
fi

if [ "$KWS_VALID" -eq 1 ]; then
    echo -e "${GREEN}✓ 中文语音唤醒模型已存在且完整 (${KWS_DIR})${NC}"
else
    echo -e "\n${CYAN}[5.5/8] 下载离线中文语音唤醒模型 (~5MB, 支持自定义'你好小猪')...${NC}"
    rm -rf "$KWS_DIR"
    mkdir -p "${PROJECT_DIR}/models"
    KWS_TAR="/tmp/sherpa-kws-3.3M.tar.bz2"
    KWS_URL_GH="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2"
    KWS_MS_BASE="https://www.modelscope.cn/models/pkufool/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/resolve/master"

    if wget -q --timeout=15 --tries=2 -O "$KWS_TAR" "$KWS_URL_GH" || curl -fsSL --connect-timeout 15 -o "$KWS_TAR" "$KWS_URL_GH"; then
        tar -xjf "$KWS_TAR" -C "${PROJECT_DIR}/models" && rm -f "$KWS_TAR"
        chown -R "${TARGET_USER}:${TARGET_USER}" "$KWS_DIR" 2>/dev/null || true
        echo -e "${GREEN}✓ 中文语音唤醒模型下载并解压就绪！${NC}"
    else
        rm -f "$KWS_TAR"
        echo -e "${YELLOW}⚠️ GitHub 下载中文唤醒模型超时，尝试通过 ModelScope 直链拉取 (避免 Git LFS 指针文件)...${NC}"
        mkdir -p "$KWS_DIR"
        MS_OK=1
        for kws_file in tokens.txt \
            encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx \
            decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx \
            joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx; do
            if ! (curl -fsSL --connect-timeout 15 --max-time 60 -o "${KWS_DIR}/${kws_file}" "${KWS_MS_BASE}/${kws_file}" || wget -q --timeout=30 -O "${KWS_DIR}/${kws_file}" "${KWS_MS_BASE}/${kws_file}"); then
                MS_OK=0
                break
            fi
        done
        if [ "$MS_OK" -eq 1 ] && [ "$(wc -c < "$KWS_ENCODER" 2>/dev/null || echo 0)" -gt 100000 ]; then
            chown -R "${TARGET_USER}:${TARGET_USER}" "$KWS_DIR" 2>/dev/null || true
            echo -e "${GREEN}✓ 已从 ModelScope 国内镜像拉取完整中文唤醒模型！${NC}"
        else
            rm -rf "$KWS_DIR"
            echo -e "${YELLOW}⚠️ 中文唤醒模型自动拉取失败 (可手动解压到 models/ 目录)，已回退保留英文/按键唤醒。${NC}"
        fi
    fi
fi

# 7. 初始化 config.json (若不存在则从模板生成)
echo -e "\n${CYAN}[6/8] 检查并初始化 config.json 配置文件...${NC}"
if [ ! -f "${PROJECT_DIR}/config.json" ] && [ -f "${PROJECT_DIR}/config.json.example" ]; then
    cp "${PROJECT_DIR}/config.json.example" "${PROJECT_DIR}/config.json"
    chown "${TARGET_USER}:${TARGET_USER}" "${PROJECT_DIR}/config.json" 2>/dev/null || true
    echo -e "${GREEN}✓ 已生成默认 config.json（请记得填入你的 API Key 或通过网页 /settings 设置）${NC}"
else
    echo -e "${GREEN}✓ config.json 已存在，保留现有配置${NC}"
fi

# 8. 预热生成本地非语言程序化音效
echo -e "\n${CYAN}[7/8] 预热生成 5 款本地非语言程序化萌趣 WAV 音效...${NC}"
python3 -c "import core.sound_effects as se; se.ensure_sound_assets()"
chown -R "${TARGET_USER}:${TARGET_USER}" "${PROJECT_DIR}/assets" 2>/dev/null || true
echo -e "${GREEN}✓ 本地程序化音频预热就绪！${NC}"

# 9. 自动生成并注册 systemd 开机自启守护服务 (robot.service)
echo -e "\n${CYAN}[8/8] 自动注册 systemd 开机自启与崩溃拉起守护服务 (robot.service)...${NC}"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
SERVICE_FILE="/etc/systemd/system/robot.service"

$SUDO tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=PIG Desktop Embodied AI Robot Agent & Twin Server
After=network-online.target pigpiod.service
Wants=network-online.target pigpiod.service

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable robot.service

if [ "$START_NOW" -eq 1 ]; then
    $SUDO systemctl restart robot.service
    echo -e "${GREEN}✓ robot.service 已注册开机自启并立即启动！${NC}"
else
    # 避免服务占用麦克风/SPI导致后续立即运行 test_hardware.py 冲突，先停服务待用户体检或重启拉起
    $SUDO systemctl stop robot.service 2>/dev/null || true
    echo -e "${GREEN}✓ robot.service 已生成并设为开机自启 (User=${TARGET_USER}, Dir=${PROJECT_DIR})${NC}"
fi

echo -e "\n${CYAN}====================================================================${NC}"
echo -e "${GREEN}🎉 恭喜！树莓派全栈环境与守护服务已一键部署完成！${NC}"
echo -e "${CYAN}====================================================================${NC}"
if [ "$NEED_REBOOT" -eq 1 ]; then
    echo -e "${YELLOW}⚠️ 检测到首次开启 SPI 或电源按钮，请先重启一次使设备树生效：sudo reboot${NC}"
fi
echo -e "👉 推荐操作顺序："
echo -e "  1. 首次接好线跑 10 秒硬件体检:  ${YELLOW}python3 scripts/test_hardware.py${NC}"
echo -e "  2. 体检通过后启动后台常驻服务:  ${YELLOW}sudo systemctl start robot.service${NC}"
echo -e "     (或直接 ${YELLOW}sudo reboot${NC}，开机即自动拉起 PIG 服务)"
echo -e "  3. 手机/电脑浏览器访问控制台:    ${YELLOW}http://$(hostname -I 2>/dev/null | awk '{print $1}'):8090${NC}"
echo -e "${CYAN}====================================================================${NC}\n"
