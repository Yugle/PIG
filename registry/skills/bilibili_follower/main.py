import requests

def run(mid="546195"):
    try:
        url = f"https://api.bilibili.com/x/relation/stat?vmid={mid}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        data = res.get("data", {})
        follower = data.get("follower", 0)
        return f"【B站数据播报】UID {mid} 当前粉丝总数: {follower:,} 位！"
    except Exception as e:
        return f"查询 B 站粉丝数据网络稍有延迟：{e}"
