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

# 3. 更新 APT 源并安装底层音视频、摄像头与 GPIO 核心依赖
echo -e "\n${CYAN}[2/8] 安装底层系统音视频、DMA PWM 与摄像头依赖...${NC}"
$SUDO apt-get update -y -qq
$SUDO apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    python3-setuptools \
    pigpio \
    python3-pigpio \
    libportaudio2 \
    libasound2-dev \
    portaudio19-dev \
    ffmpeg \
    alsa-utils \
    fswebcam \
    libatlas-base-dev \
    libjpeg-dev \
    zlib1g-dev

echo -e "${GREEN}✓ 底层系统依赖安装完毕！${NC}"

# 4. 激活并自启 pigpiod 硬件定时守护进程
echo -e "\n${CYAN}[3/8] 配置并启动 pigpiod 硬件 DMA PWM 守护服务...${NC}"
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

# 6. 安装 Python 核心生态依赖
echo -e "\n${CYAN}[5/8] 安装 Python 核心依赖清单 (requirements.txt)...${NC}"
pip3 install -r requirements.txt --quiet --break-system-packages 2>/dev/null || pip3 install -r requirements.txt --quiet
echo -e "${GREEN}✓ Python 依赖库安装成功！${NC}"

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
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/web_sim_server.py
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
