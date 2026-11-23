import requests

def run(topic="ai"):
    try:
        q = (topic or "ai").strip()
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=3"
        headers = {"User-Agent": "PIG-Robot-Skill"}
        res = requests.get(url, headers=headers, timeout=5).json()
        items = res.get("items", [])
        if not items:
            return f"🔍 未检索到关于 {topic} 的热门项目。"
        lines = [f"🚀 【GitHub Top 热门开源项目 · 领域: {topic}】"]
        for i, it in enumerate(items, 1):
            desc = it.get('description') or '无简介'
            lines.append(f"{i}. 🌟 {it['full_name']} (★ {it['stargazers_count']:,})
   📝 {desc[:70]}...")
        return "
".join(lines)
    except Exception as e:
        return f"获取 GitHub 热榜网络稍有延迟: {e}"
