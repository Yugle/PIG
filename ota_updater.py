import os
import subprocess
import sys

def auto_upgrade(repo_dir=None):
    """
    检查远程 Git 仓库更新，自动拉取并重启 systemd 守护服务
    """
    if repo_dir is None:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

    os.chdir(repo_dir)
    print(f"📡 [OTA] 检查远端代码更新 (工作目录: {repo_dir})...")

    # 检查是否为 git 仓库
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        print("⚠️ [OTA] 当前目录并非 Git 仓库，跳过自动升级。")
        return False

    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=True, capture_output=True)
        loc = subprocess.check_output(["git", "rev-parse", "HEAD"]).strip()
        rem = subprocess.check_output(["git", "rev-parse", "origin/main"]).strip()

        if loc != rem:
            print(f"📦 [OTA] 发现新版本代码 ({loc.decode()[:7]} -> {rem.decode()[:7]})，正在拉取...")
            subprocess.run(["git", "pull", "origin", "main"], check=True)

            req_file = os.path.join(repo_dir, "requirements.txt")
            if os.path.exists(req_file):
                print("🔄 [OTA] 更新 Python 依赖包...")
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"])

            print("🔁 [OTA] 正在重启 systemd robot.service 服务...")
            subprocess.run(["sudo", "systemctl", "restart", "robot.service"], check=False)
            print("✅ [OTA] 升级完成并已重启主服务！")
            return True
        else:
            print("✨ [OTA] 当前已是最新版本，无需更新。")
            return False
    except Exception as e:
        print(f"❌ [OTA] 更新过程中遇到错误: {e}")
        return False

if __name__ == "__main__":
    auto_upgrade()
