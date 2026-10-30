#!/usr/bin/env bash
# ==============================================================================
# 桌面具身 AI 伴侣机器人 - 树莓派 3 一键自动化环境安装与部署脚本
# 适用硬件：Raspberry Pi 3 / 3B / 3B+ (Raspberry Pi OS Bullseye / Bookworm)
# ==============================================================================

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}====================================================================${NC}"
echo -e "${GREEN}🤖 桌面具身 AI 伴侣 - 树莓派 3 一键自动化环境部署程序${NC}"
echo -e "${CYAN}====================================================================${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
echo -e "📂 工程根目录: ${YELLOW}${PROJECT_DIR}${NC}"

# 1. 检查 root / sudo 权限
if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# 2. 检查并开启 SPI 硬件接口
echo -e "\n${CYAN}[1/6] 检查并配置树莓派硬件 SPI 接口...${NC}"
CONFIG_TXT="/boot/config.txt"
[ -f "/boot/firmware/config.txt" ] && CONFIG_TXT="/boot/firmware/config.txt"

if grep -q "^dtparam=spi=on" "$CONFIG_TXT"; then
    echo -e "${GREEN}✓ SPI 接口已处于开启状态 (${CONFIG_TXT})${NC}"
else
    echo -e "${YELLOW}⚙️ 正在向 ${CONFIG_TXT} 写入 dtparam=spi=on...${NC}"
    echo "dtparam=spi=on" | $SUDO tee -a "$CONFIG_TXT" > /dev/null
    echo -e "${GREEN}✓ SPI 接口已成功配置！${NC}"
fi

# 3. 更新 APT 源并安装底层音视频及 GPIO 核心依赖
echo -e "\n${CYAN}[2/6] 安装底层系统音视频、DMA PWM 与编译依赖...${NC}"
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
    libatlas-base-dev \
    libjpeg-dev \
    zlib1g-dev

echo -e "${GREEN}✓ 底层系统依赖安装完毕！${NC}"

# 4. 激活并自启 pigpiod 硬件定时守护进程
echo -e "\n${CYAN}[3/6] 配置并启动 pigpiod 硬件 DMA PWM 守护服务...${NC}"
$SUDO systemctl enable pigpiod
$SUDO systemctl restart pigpiod

if systemctl is-active --quiet pigpiod; then
    echo -e "${GREEN}✓ pigpiod 硬件守护进程已正常运行 (端口 8888 就绪)${NC}"
else
    echo -e "${RED}⚠️ pigpiod 启动异常，请检查 systemctl status pigpiod${NC}"
fi

# 5. 配置当前用户外设硬件访问权限
echo -e "\n${CYAN}[4/6] 配置外设用户组权限 (gpio, audio, spi, dialout)...${NC}"
$SUDO usermod -a -G gpio,audio,spi,dialout "$USER" || true
echo -e "${GREEN}✓ 用户组权限配置完成！${NC}"

# 6. 安装 Python 核心生态依赖
echo -e "\n${CYAN}[5/6] 安装 Python 核心依赖清单 (requirements.txt)...${NC}"
pip3 install -r requirements.txt --quiet --break-system-packages 2>/dev/null || pip3 install -r requirements.txt --quiet

echo -e "${GREEN}✓ Python 依赖库安装成功！${NC}"

# 7. 自动生成非语言萌趣程序化音频
echo -e "\n${CYAN}[6/6] 预热生成 5 款本地非语言程序化萌趣 WAV 音效...${NC}"
python3 -c "import core.sound_effects as se; se.ensure_sound_assets()"
echo -e "${GREEN}✓ 本地程序化音频预热就绪！${NC}"

echo -e "\n${CYAN}====================================================================${NC}"
echo -e "${GREEN}🎉 恭喜！树莓派 3 环境部署已全部完成！${NC}"
echo -e "${CYAN}====================================================================${NC}"
echo -e "👉 下一步推荐操作："
echo -e "  1. 运行硬件接线体检自测:  ${YELLOW}python3 scripts/test_hardware.py${NC}"
echo -e "  2. 启动具身实体主中枢:    ${YELLOW}python3 main_agent.py${NC}"
echo -e "  3. 启动 3D Web 仿真服务:  ${YELLOW}python3 web_sim_server.py${NC}"
echo -e "${CYAN}====================================================================${NC}\n"
