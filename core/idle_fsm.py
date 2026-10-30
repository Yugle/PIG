"""
桌面具身 AI 伴侣机器人 - 闲置自主生命体征状态机 (Idle Aliveness FSM)
让机器人即使在无人对话时，也具备生物呼吸节律与自主意识演变：
1. IDLE (正常待机): 叠加微正弦呼吸起伏 (±1.5° 舒缓游弋) + 自然随机眨眼与跳视
2. BORED (无聊发呆): 闲置超过 2 分钟，偶尔打哈欠、伸个懒腰扭扭头
3. DOZING (打瞌睡): 闲置超过 5 分钟，脑袋慢慢垂下 (Tilt 下倾)，半闭眼打瞌睡
4. WAKING (惊醒反弹): 瞌睡中被声音/消息唤醒时，瞬间受惊抬头瞪圆大眼
5. PROACTIVE (主动关怀): 周期性主动向主人发起一句温馨关怀
"""

import time
import math
import random

class IdleState:
    IDLE = "IDLE"
    BORED = "BORED"
    DOZING = "DOZING"
    WAKING = "WAKING"
    ACTIVE = "ACTIVE"

class IdleFSM:
    def __init__(self, bored_timeout_sec=120, dozing_timeout_sec=300):
        self.state = IdleState.IDLE
        self.last_active_time = time.time()
        self.bored_timeout_sec = bored_timeout_sec
        self.dozing_timeout_sec = dozing_timeout_sec
        self.start_time = time.time()

        # 主动关怀提醒库
        self.proactive_phrases = [
            "主人，屏幕看久了容易眼干，闭上眼睛休息半分钟吧~",
            "工作再忙也要记得多喝温水哦！",
            "伸个懒腰活动一下肩膀，精神会更好呢！",
            "今天辛苦啦，有我在桌面上默默陪着你！"
        ]
        self.last_proactive_time = time.time()

    def notify_active(self):
        """重置交互活跃时间戳（例如用户说话、发送消息、点击操作）"""
        if self.state == IdleState.DOZING:
            # 如果正在瞌睡，触发惊醒反弹状态
            self.state = IdleState.WAKING
        else:
            self.state = IdleState.ACTIVE
        self.last_active_time = time.time()

    def finish_active(self):
        """交互执行完毕，恢复待机"""
        self.state = IdleState.IDLE
        self.last_active_time = time.time()

    def update(self) -> dict:
        """
        每帧周期性更新状态机 (推荐 10Hz~30Hz 轮询调用)
        返回当前帧的呼吸位姿微调与表情推荐
        :return: {
            "state": str,
            "pan_offset": float,
            "tilt_offset": float,
            "emotion": str or None,
            "action": str or None,
            "proactive_text": str or None
        }
        """
        now = time.time()
        idle_duration = now - self.last_active_time
        t = now - self.start_time

        # 1. 状态转移判断
        if self.state == IdleState.WAKING:
            # 惊醒状态持续 0.8 秒后回退 ACTIVE
            if idle_duration > 0.8:
                self.state = IdleState.ACTIVE

        elif self.state != IdleState.ACTIVE:
            if idle_duration > self.dozing_timeout_sec:
                self.state = IdleState.DOZING
            elif idle_duration > self.bored_timeout_sec:
                self.state = IdleState.BORED
            else:
                self.state = IdleState.IDLE

        # 2. 根据状态生成生命律动参数
        pan_offset = 0.0
        tilt_offset = 0.0
        recommended_emotion = None
        recommended_action = None
        proactive_text = None

        if self.state == IdleState.IDLE:
            # 基础呼吸正弦微动：周期约 4.5 秒，振幅 ±1.2°
            pan_offset = math.sin(t * 1.4) * 1.2
            tilt_offset = math.cos(t * 1.4) * 0.9

        elif self.state == IdleState.BORED:
            # 无聊状态：呼吸更缓慢，偶尔伸个懒腰
            pan_offset = math.sin(t * 0.8) * 2.0
            tilt_offset = math.cos(t * 0.8) * 1.5
            # 每隔 40 秒偶尔眨眼伸懒腰
            if int(t) % 40 == 0:
                recommended_emotion = "CONFUSED"
                recommended_action = "LOOK_UP"

        elif self.state == IdleState.DOZING:
            # 打瞌睡：脑袋缓慢下沉 12°，深度微弱呼吸
            tilt_offset = 12.0 + math.sin(t * 0.6) * 1.5
            pan_offset = math.cos(t * 0.3) * 1.0
            recommended_emotion = "SLEEPY"

        elif self.state == IdleState.WAKING:
            # 惊醒反弹：猛抬头 -15°，吃惊表情
            tilt_offset = -15.0
            recommended_emotion = "SURPRISED"
            recommended_action = "SURPRISE"

        # 3. 主动关怀触发检查 (每 20 分钟空闲主动关心一次)
        if idle_duration > 600 and (now - self.last_proactive_time) > 1200:
            self.last_proactive_time = now
            proactive_text = random.choice(self.proactive_phrases)

        return {
            "state": self.state,
            "pan_offset": pan_offset,
            "tilt_offset": tilt_offset,
            "emotion": recommended_emotion,
            "action": recommended_action,
            "proactive_text": proactive_text
        }
