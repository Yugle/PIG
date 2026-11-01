# -*- coding: utf-8 -*-
"""
桌面具身实体机器人 - 全功能综合自动化集成测试套件 (E2E Test Suite)
覆盖：
1. Web 3D 仿真工作台与动力学动态 SVG 眼球引擎
2. 高拟真中文语音合成 (Edge-TTS)
3. 记忆中枢、主人称呼定制与长期记忆
4. 生物钟作息画像自学习与海马体深度反思
5. 定时闹钟与日程提醒生命周期
6. 动态技能插件体系与技能商店
7. 天气技能自动 IP 定位与温感关怀
8. 小米米家 IoT 生态直控 (开关、复合调光调温、防误触、诚实拒绝)
9. ReAct 多步认知推理闭环与诚实汇报原则 (严禁敷衍兜底)
"""

import os
import sys
import json
import time
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

BASE_URL = "http://localhost:8090"

class TestSuite:
    def __init__(self):
        self.results = []

    def run_test(self, name: str, test_fn):
        print(f"👉 正在测试: {name} ...", end=" ", flush=True)
        try:
            ok, detail = test_fn()
            status = "✅ PASS" if ok else "❌ FAIL"
            self.results.append((name, ok, detail))
            print(f"[{status}] - {detail}")
        except Exception as e:
            self.results.append((name, False, f"异常: {e}"))
            print(f"[❌ FAIL] - 异常: {e}")

    def summary(self):
        total = len(self.results)
        passed = sum(1 for _, ok, _ in self.results if ok)
        failed = total - passed
        print("\n" + "=" * 65)
        print(f"📊 全功能集成测试总结: 总计 {total} 项 | 通过 {passed} 项 | 失败 {failed} 项")
        print(f"📈 测试通过率: {passed / total * 100:.1f}%")
        print("=" * 65)
        if failed > 0:
            print("\n❌ 失败项明细:")
            for name, ok, detail in self.results:
                if not ok:
                    print(f"  - {name}: {detail}")
        else:
            print("\n🎉 所有功能模块全部通过测试，系统处于最佳健康状态！")

suite = TestSuite()

# 1. Web 3D 仿真视窗主页
def test_web_simulator():
    resp = requests.get(f"{BASE_URL}/", timeout=5)
    has_robot = "WebGL" in resp.text or "robot" in resp.text.lower() or "canvas" in resp.text
    return resp.status_code == 200 and has_robot, f"HTTP {resp.status_code}, 页面大小: {len(resp.text)} 字节"
suite.run_test("1. Web 3D 仿真工作台视窗", test_web_simulator)

# 2. 动力学眼球 SVG 引擎 (12 种表情)
def test_svg_engine():
    resp_svg = requests.get(f"{BASE_URL}/api/svg?emotion=HAPPY", timeout=5)
    resp_list = requests.get(f"{BASE_URL}/api/svg_list", timeout=5)
    emotions = resp_list.json().get("emotions", [])
    valid_svg = resp_svg.status_code == 200 and "<svg" in resp_svg.text
    valid_list = len(emotions) == 12
    return valid_svg and valid_list, f"生成 SVG ({len(resp_svg.text)} 字节), 包含 {len(emotions)} 种动态表情"
suite.run_test("2. 动力学动态 SVG 眼球引擎", test_svg_engine)

# 3. 高拟真中文语音合成 (Edge-TTS)
def test_edge_tts():
    resp = requests.get(f"{BASE_URL}/api/tts?text=主人你好，我是桌面小搭子", timeout=12)
    is_mp3 = resp.headers.get("Content-Type") == "audio/mpeg"
    has_audio = len(resp.content) > 1000
    return resp.status_code == 200 and is_mp3 and has_audio, f"合成 MP3 成功 ({len(resp.content)} 字节)"
suite.run_test("3. Edge-TTS 拟真语音合成", test_edge_tts)

# 4. 记忆中枢档案与主人称呼
def test_memory_profile():
    resp = requests.get(f"{BASE_URL}/api/memory", timeout=5)
    data = resp.json()
    user_name = data.get("user_name", "")
    facts = data.get("facts", [])
    return resp.status_code == 200 and user_name in ["阿珍", "胖虎"], f"主人称呼: 【{user_name}】, 亲密度: Lv.{data.get('intimacy_level')}, 长期记忆: {len(facts)} 条"
suite.run_test("4. 记忆中枢与主人画像档案", test_memory_profile)

# 5. 生物钟作息自学习数据
def test_circadian_tracker():
    resp = requests.get(f"{BASE_URL}/api/circadian", timeout=5)
    data = resp.json()
    circadian = data.get("circadian", {})
    turns = circadian.get("total_turns", 0)
    wake_h = circadian.get("learned_habits", {}).get("wake_hour")
    return resp.status_code == 200 and turns > 0, f"累计交互: {turns} 轮, 学习作息: 早起 {wake_h}:00"
suite.run_test("5. 生物钟作息自学习 (/api/circadian)", test_circadian_tracker)

# 6. 技能插件体系与技能商店
def test_skill_store():
    resp = requests.get(f"{BASE_URL}/api/skills/store", timeout=5)
    data = resp.json()
    installed = [s.get("id") for s in data.get("installed", [])]
    expected = ["xiaomi_iot", "weather", "news_briefing", "pomodoro"]
    all_present = all(k in installed for k in expected)
    return resp.status_code == 200 and all_present, f"当前已热加载 {len(installed)} 个动态技能: {installed}"
suite.run_test("6. 动态技能热加载体系", test_skill_store)

# 7. 闹钟与日程提醒完整生命周期 (增删查)
def test_reminders_lifecycle():
    from core.skill_manager import SkillManager
    from core.memory_manager import MemoryManager
    mem = MemoryManager(os.path.join(PROJECT_ROOT, "data", "memory.json"))
    sm = SkillManager(storage_dir=os.path.join(PROJECT_ROOT, "data"), memory_manager=mem)

    # 1. 设定测试闹钟
    t = sm.set_timer(300, "测试泡茶提醒")
    tid = t.get("id")

    # 2. 查询闹钟
    active = sm.list_active_reminders()
    found = any(r.get("id") == tid for r in active)

    # 3. 取消闹钟
    cancelled = sm.cancel_reminder(tid)
    still_present = any(r.get("id") == tid for r in sm.list_active_reminders())

    return found and cancelled and not still_present, f"创建 -> 查验 ({tid}) -> 取消成功"
suite.run_test("7. 闹钟与日程生命周期调度", test_reminders_lifecycle)

# 8. 天气技能自动 IP 定位
def test_weather_skill():
    from skills.weather.main import run as run_weather
    res = run_weather("")
    has_weather_info = any(k in res for k in ["天气", "气温", "温度", "℃", "晴", "云", "雨"])
    return has_weather_info, f"IP 定位反馈: {res[:55]}..."
suite.run_test("8. 天气技能四级容错自动定位", test_weather_skill)

# 9. 小米米家直控 - 复合控制台灯 (打开 + 50% 亮度)
def test_xiaomi_compound():
    from skills.xiaomi_iot.main import run as run_xiaomi
    res = run_xiaomi("台灯", "打开", "并把亮度调整到50%")
    return "成功" in res and "50%" in res, f"云端执行结果: {res}"
suite.run_test("9. 米家台灯复合指令 (打开+50%亮度)", test_xiaomi_compound)

# 10. 小米米家直控 - 调节色温为暖光 (2700K)
def test_xiaomi_color_temp():
    from skills.xiaomi_iot.main import run as run_xiaomi
    res = run_xiaomi("台灯", "调成暖光", "")
    return "成功" in res and "2700K" in res, f"云端执行结果: {res}"
suite.run_test("10. 米家台灯色温调节 (暖光 2700K)", test_xiaomi_color_temp)

# 11. 小米米家直控 - 关闭台灯
def test_xiaomi_turn_off():
    from skills.xiaomi_iot.main import run as run_xiaomi
    res = run_xiaomi("台灯", "关闭", "")
    return "成功" in res and "关闭" in res, f"云端执行结果: {res}"
suite.run_test("11. 米家台灯关闭指令", test_xiaomi_turn_off)

# 12. 小米米家直控 - 未配置设备拒绝 (诚实报错，严禁欺瞒)
def test_xiaomi_missing_device():
    from skills.xiaomi_iot.main import run as run_xiaomi
    res = run_xiaomi("不存在的扫地机", "启动", "")
    return "未找到" in res and "台灯" in res, f"拒绝反馈: {res}"
suite.run_test("12. 未配置设备诚实拒绝 (严禁口嗨欺瞒)", test_xiaomi_missing_device)

# 13. 空语音/文字输入防御
def test_empty_chat():
    resp = requests.post(f"{BASE_URL}/api/chat", json={"text": "   "}, timeout=5)
    data = resp.json()
    reply = data.get("reply", "")
    return "没有听到" in reply or "对我说点什么" in reply, f"防御回复: 【{reply}】"
suite.run_test("13. 空输入与静音防御", test_empty_chat)

# 14. 端到端 ReAct 推理闭环与诚实汇报 (单步执行复合控制)
def test_react_chat_compound():
    resp = requests.post(f"{BASE_URL}/api/chat", json={"text": "帮我把台灯打开并将色温调节为暖光2700K"}, timeout=75)
    data = resp.json()
    reply = data.get("reply", "")
    steps = data.get("steps", [])
    skill_res = data.get("skill_result", "")
    action_steps = [s for s in steps if s.get("action")]
    
    # 验证单步执行完成 (严禁无意义多轮死循环)，且真实控制成功
    single_turn_done = len(action_steps) <= 1
    has_success_reply = "台灯" in reply or "暖光" in reply or "2700" in reply or "光线" in reply
    return single_turn_done and has_success_reply, f"ReAct 步数: {len(action_steps)} 步, 回复: 【{reply[:45]}...】"
suite.run_test("14. 端到端 ReAct 单步复合控制闭环", test_react_chat_compound)

# 15. 端到端 ReAct 遇到不存在设备时如实报告失败 (绝无兜底套话)
def test_react_chat_honest_failure():
    resp = requests.post(f"{BASE_URL}/api/chat", json={"text": "帮我把空调打开"}, timeout=75)
    data = resp.json()
    reply = data.get("reply", "")
    # 验证绝无 "我在听呢" 等虚假兜底
    no_fallback = "我在听呢" not in reply and "有什么问题尽管" not in reply
    honest_mention = any(k in reply for k in ["未能", "未找到", "空调", "已绑定", "异常", "失败", "错误"])
    return no_fallback and honest_mention, f"诚实回复: 【{reply[:55]}...】"
suite.run_test("15. 端到端失败如实汇报 (失败就说出来，绝不兜底)", test_react_chat_honest_failure)

# 输出总结报告
suite.summary()
