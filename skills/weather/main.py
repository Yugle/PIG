import requests

def run(city="北京"):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        res = requests.get(url, timeout=5).json()
        current = res["current_condition"][0]
        desc = current.get("weatherDesc", [{}])[0].get("value", "晴")
        temp = current.get("temp_C", "--")
        feels = current.get("FeelsLikeC", temp)
        humidity = current.get("humidity", "--")

        tip = "气温适宜，祝你今天好心情！"
        try:
            t_int = int(temp)
            if t_int < 10:
                tip = "天气较冷，出门记得添厚衣保暖防感冒哦！"
            elif t_int > 28:
                tip = "气温偏热，记得多喝水防暑降温。"
        except Exception:
            pass

        return f"{city}当前天气：{desc}，实时气温 {temp}°C（体感 {feels}°C），空气湿度 {humidity}%。{tip}"
    except Exception as e:
        return f"查询{city}天气时网络稍有延迟，不过别担心，出门前记得随时看下窗外～"
