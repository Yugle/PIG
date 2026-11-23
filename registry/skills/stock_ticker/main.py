import requests

def run(symbol="sh000001"):
    try:
        s = (symbol or "sh000001").strip().lower()
        alias_map = {"上证": "sh000001", "大盘": "sh000001", "上证指数": "sh000001", "深证": "sz399001", "深成指": "sz399001", "创业板": "sz399006", "茅台": "sh600519", "贵州茅台": "sh600519", "宁德时代": "sz300750"}
        code = alias_map.get(s, s)
        url = f"https://qt.gtimg.cn/q={code}"
        r = requests.get(url, timeout=4)
        if r.status_code == 200 and "~" in r.text:
            parts = r.text.split("~")
            name = parts[1]
            price = parts[3]
            diff = parts[31]
            ratio = parts[32]
            arrow = "🔴 涨" if float(diff) >= 0 else "🟢 跌"
            return f"📈 【行情播报】{name} ({code}) 当前价: {price} 元 | 涨跌幅: {ratio}% ({arrow}{diff})"
        return f"未能获取股票代码 {symbol} 的实时行情，请确认代码格式（如 sh000001 或 sh600519）。"
    except Exception as e:
        return f"查询股票行情网络稍有延迟: {e}"
