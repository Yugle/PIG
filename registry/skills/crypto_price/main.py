import requests

def run(symbol="BTC"):
    try:
        s = (symbol or "BTC").strip().upper()
        res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={s}USDT", timeout=4).json()
        price = float(res.get("price", 0))
        return f"【加密货币实时行情】{s}/USDT 当前价格为: "
    except Exception as e:
        return f"查询 {symbol} 行情网络稍有延迟，稍后再试～"
