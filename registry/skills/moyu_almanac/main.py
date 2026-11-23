import random
from datetime import datetime

def run(job_role="程序员"):
    yis = ["带薪喝水提神", "重构祖传代码", "准点下班打卡", "去茶水间偶遇咖啡", "戴耳机专注沉思", "优雅闭目养神"]
    jis = ["临下班前发布版本", "直接推 master 分支", "在群里 @所有人", "空腹猛灌冰美式", "承诺周五提测", "开无意义长会"]
    today = datetime.now().strftime("%Y年%m月%d日")
    y_pick = random.sample(yis, 2)
    j_pick = random.sample(jis, 2)
    return f"📅 【PIG 赛博打工黄历 · {today}】
👤 测算角色: {job_role}
🟢 【宜】: {'、'.join(y_pick)}
🔴 【忌】: {'、'.join(j_pick)}
✨ 今日吉神方位: 茶水间！"
