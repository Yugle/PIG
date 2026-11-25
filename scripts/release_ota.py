#!/usr/bin/env python3
"""
一键更新本地 VERSION 与 ota/public/version.json，配合 Vercel 自动部署 OTA 版本清单。
用法:
  python3 scripts/release_ota.py v0.2.0 "优化歌单播放与系统更新"
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
OTA_JSON_FILE = ROOT / "ota" / "public" / "version.json"


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/release_ota.py <版本号，如 v0.2.0> [更新说明]")
        sys.exit(1)

    raw_ver = sys.argv[1].strip()
    tag_ver = raw_ver if raw_ver.lower().startswith("v") else f"v{raw_ver}"
    plain_ver = tag_ver.lstrip("vV")
    changelog = sys.argv[2].strip() if len(sys.argv) >= 3 else f"发布版本 {tag_ver}"

    tz_cn = timezone(timedelta(hours=8))
    now_iso = datetime.now(tz_cn).isoformat(timespec="seconds")

    VERSION_FILE.write_text(plain_ver + "\n", encoding="utf-8")

    OTA_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "latest_version": tag_ver,
        "changelog": changelog,
        "updated_at": now_iso,
    }
    OTA_JSON_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"已更新 VERSION -> {plain_ver}")
    print(f"已更新 {OTA_JSON_FILE.relative_to(ROOT)} -> {tag_ver}")
    print("接下来执行:")
    print(f"  git add VERSION ota/public/version.json && git commit -m 'release: {tag_ver}' && git tag {tag_ver} && git push --follow-tags")


if __name__ == "__main__":
    main()
