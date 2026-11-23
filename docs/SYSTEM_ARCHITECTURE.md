# 🤖 PIG 桌面具身 AI 伴侣 - 全景系统架构与电气连线拓扑

> **版本**：v2.0.0 (Embodied Release)  
> **适用平台**：树莓派 3B / 3B+ / 4B (Raspberry Pi OS 64-bit Lite) + PC WebGL 3D 数字孪生仿真平台  
> **核心宗旨**：软硬件完全解耦、流式低延迟交互、物理隔离供电、技能高内聚热插拔。

---

## 一、 软硬件全景系统架构图 (Full-Stack Architecture)

```mermaid
flowchart TD
    %% ======================= 顶层：用户交互界面 =======================
    subgraph UI_Layer ["0. 交互界面与数字孪生层 (Interaction & Digital Twin)"]
        Physical["🖥️ 实体机器人桌面交互<br/>(2.4寸表情屏 + 2-DOF 转头 + 语音交互 + 摸头杀)"]
        WebSim["🌐 Web 3D 仿真工作台 (localhost:8090)<br/>(Three.js 机械骨骼 + 240x320 动态眼球 + Web Audio 0ms 缝隙流式播放)"]
    end

    %% ======================= 第一层：认知大脑与智能中枢 =======================
    subgraph Brain_Layer ["1. 具身认知大脑层 (Cognitive Brain & Reasoning)"]
        LLM["🧠 认知大模型引擎 (Xiaomi MiMo-v2.6-Flash / 本地模型)"]
        Reasoner["⚡ ReAct 极速推理中枢 (AgentReasoner)<br/>• 动作指令：单轮投机执行直出 (FastPath ~1.2s)<br/>• 查询指令：首轮垫话 + 观测真实汇报<br/>• 严格防幻觉铁律 (真实 Observation 约束)"]
        PromptBuilder["📝 自适应提示词引擎 (PromptBuilder)<br/>• 指令型：15~35 字极简利落<br/>• 内容型 (笑话/故事)：80~200 字完整展开"]
        Memory["🧠 海马体长期记忆 (MemoryManager)<br/>• 用户档案/画像 • 机器人名字 (小猪)<br/>• 生物钟昼夜节律 profile • 向量化记忆提取"]
    end

    %% ======================= 第二层：技能插件生态 =======================
    subgraph Skill_Layer ["2. 动态技能生态层 (Skill Plugins Engine)"]
        SkillMgr["🧩 统一技能调度中枢 (SkillManager)<br/>• 动态别名纠偏路由 • 闹钟/倒计时调度器 • 热加载技能商店"]
        IoT["🏠 xiaomi_iot<br/>(米家生态：台灯/水壶/摄像机/饮水机)"]
        ActionSkill["💃 evolve_action / play_action<br/>(自创动作学习 & 表演)"]
        ReminderSkill["⏰ reminder<br/>(自然语言闹钟/定时器)"]
        OtherSkills["🌦️ weather / news / pomodoro / care<br/>(天气/新闻/番茄钟/主动关怀)"]
    end

    %% ======================= 第三层：流式全双工语音管线 =======================
    subgraph Audio_Layer ["3. 流式全双工语音管线 (Streaming Audio Pipeline)"]
        VAD["🎙️ 拾音与端点检测 (WakeWord 'Hey Jarvis' + WebRTC VAD)"]
        SSE["📡 SSE 实时事件流 (/api/chat/stream)<br/>• thought 实时思维流 • delta 文本流 • behaviors 动作序列"]
        TTS["🗣️ Edge-TTS 高精流式切片引擎<br/>• 标点智能断句 (≥16字大断句，≥22字逗号)<br/>• Web Audio API (AudioContext 无缝物理拼接)"]
    end

    %% ======================= 第四层：硬件抽象与外设驱动 =======================
    subgraph HAL_Layer ["4. 统一硬件抽象层 (HAL - Hardware Abstraction)"]
        HAL_Sim["🖥️ HAL-Simulation (虚拟孪生驱动)<br/>(WebGL Head / Canvas Eyes / Virtual Audio)"]
        HAL_Real["🔌 HAL-Hardware (物理外设驱动)<br/>(RPi.GPIO / SPI fb / OpenCV V4L2 / ALSA)"]
        MotionFSM["🤖 具身微动作状态机 (BehaviorFSM)<br/>• S曲线平滑加减速 • 昼夜生物钟 (发呆/打哈欠/伴睡)"]
        EyeRenderer["👀 240x320 动态眼球渲染 (SvgAnimator)<br/>(HAPPY / LOVE / WINK / SLEEP / DIZZY / NORMAL)"]
    end

    %% ======================= 底层：物理硬件元器件 =======================
    subgraph HW_Layer ["5. 物理硬件电气元器件 (Physical Components)"]
        RPi["🍓 树莓派 3B / 3B+ / 4B 主控板"]
        LCD["📺 2.4寸 ST7789 SPI 彩色显示屏 (8-Pin 焊针)"]
        Cam["📷 免驱 USB 摄像头裸板 (720P 60° 无畸变)"]
        Mic["🎤 免驱 USB 迷你直插麦克风"]
        Speaker["🔊 Y57 USB 扩音喇叭 (数字声卡+功放+喇叭一线通)"]
        Touch["👆 TTP223 电容触摸传感器 (摸头杀，隔壳感应)"]
        Servos["🦾 二自由度云台 (SG90 9g 舵机 × 2: Pan/Tilt)"]
    end

    %% ======================= 连线关系 =======================
    Physical <--> UI_Layer
    WebSim <--> UI_Layer

    UI_Layer <--> Audio_Layer
    UI_Layer <--> Brain_Layer

    Brain_Layer --> Reasoner
    Reasoner <--> LLM
    Reasoner --> PromptBuilder
    Reasoner <--> Memory
    Reasoner <--> SkillMgr

    SkillMgr --> IoT
    SkillMgr --> ActionSkill
    SkillMgr --> ReminderSkill
    SkillMgr --> OtherSkills

    Audio_Layer --> VAD
    Audio_Layer --> SSE
    Audio_Layer --> TTS

    Reasoner --> HAL_Layer
    SkillMgr --> HAL_Layer

    HAL_Layer --> HAL_Sim
    HAL_Layer --> HAL_Real
    HAL_Layer --> MotionFSM
    HAL_Layer --> EyeRenderer

    HAL_Sim --> WebSim
    HAL_Real --> HW_Layer

    RPi --- LCD
    RPi --- Cam
    RPi --- Mic
    RPi --- Speaker
    RPi --- Touch
    RPi --- Servos
```

---

## 二、 树莓派 40-Pin 物理接线与隔离供电拓扑

```mermaid
graph TD
    subgraph Power_System ["⚡ 电源拓扑 (安全单线/隔离方案)"]
        WallPower["🔌 5V 3A / 3.5A 优质电源插头"]
        PiPowerIn["🍓 树莓派 Type-C / Micro-USB 供电口"]
        ServoPowerNode["⚡ 舵机独立电源节点 (Pin 2 / 或外接分线)"]
        Capacitor["🔋 1000μF 16V 滤波大电解电容<br/>(吸收舵机急转瞬间 1A 冲击电流，防欠压重启)"]
        SystemGND["⏚ 统一系统公共地 (Common GND)"]

        WallPower --> PiPowerIn
        PiPowerIn --> ServoPowerNode
        ServoPowerNode --- Capacitor
        Capacitor --- SystemGND
    end

    subgraph RaspberryPi_GPIO ["🍓 树莓派 40-Pin 物理引脚分配 (Pinout)"]
        direction TB
        Pin1["Pin 1: 3.3V PWR"]
        Pin2["Pin 2: 5.0V PWR"]
        Pin6["Pin 6: GND (地)"]
        Pin9["Pin 9: GND (地)"]
        Pin11["Pin 11: GPIO 17"]
        Pin12["Pin 12: GPIO 18 (PWM0)"]
        Pin15["Pin 15: GPIO 22"]
        Pin19["Pin 19: GPIO 10 (MOSI)"]
        Pin22["Pin 22: GPIO 25"]
        Pin23["Pin 23: GPIO 11 (SCLK)"]
        Pin24["Pin 24: GPIO 8 (CE0)"]
        Pin33["Pin 33: GPIO 13 (PWM1)"]
        USB_A["USB 端口 1"]
        USB_B["USB 端口 2"]
        USB_C["USB 端口 3"]
    end

    subgraph Devices ["🔌 连接的外设元器件"]
        Screen_VCC["ST7789 屏幕 VCC"]
        Screen_GND["ST7789 屏幕 GND"]
        Screen_SDA["ST7789 屏幕 SDA/DIN"]
        Screen_SCL["ST7789 屏幕 SCL/CLK"]
        Screen_CS["ST7789 屏幕 CS"]
        Screen_DC["ST7789 屏幕 DC"]
        Screen_RES["ST7789 屏幕 RES"]
        Screen_BLK["ST7789 屏幕 BLK (悬空或接3.3V)"]

        Pan_PWM["水平舵机 Pan (黄色信号线)"]
        Pan_VCC["水平舵机 Pan (红色 5V)"]
        Pan_GND["水平舵机 Pan (棕/黑 GND)"]

        Tilt_PWM["垂直舵机 Tilt (黄色信号线)"]
        Tilt_VCC["垂直舵机 Tilt (红色 5V)"]
        Tilt_GND["垂直舵机 Tilt (棕/黑 GND)"]

        Touch_VCC["TTP223 摸头传感器 VCC"]
        Touch_GND["TTP223 摸头传感器 GND"]
        Touch_SIG["TTP223 摸头传感器 SIG"]

        USB_Cam["📷 USB 720P 摄像头模组"]
        USB_Mic["🎤 USB 迷你直插麦克风"]
        USB_Spk["🔊 Y57 USB 一线通扩音喇叭"]
    end

    %% 屏幕接线
    Pin1 --> Screen_VCC
    Pin6 --> Screen_GND
    Pin19 --> Screen_SDA
    Pin23 --> Screen_SCL
    Pin24 --> Screen_CS
    Pin22 --> Screen_DC
    Pin15 --> Screen_RES

    %% 触摸传感器接线
    Pin1 --> Touch_VCC
    Pin9 --> Touch_GND
    Pin11 --> Touch_SIG

    %% 舵机信号线
    Pin12 --> Pan_PWM
    Pin33 --> Tilt_PWM

    %% 舵机供电线 (经过滤波电容保护)
    ServoPowerNode --> Pan_VCC
    ServoPowerNode --> Tilt_VCC
    SystemGND --> Pan_GND
    SystemGND --> Tilt_GND
    Pin6 --- SystemGND

    %% USB 外设
    USB_A --> USB_Cam
    USB_B --> USB_Mic
    USB_C --> USB_Spk
```

---

## 三、 核心模块数据流与调用时序 (Interaction Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor User as 主人 (胖虎)
    participant HW as 物理硬件 (麦克风/触控/眼球/舵机)
    participant Pipeline as 流式交互管线 (Audio & SSE)
    participant Brain as 具身大脑 (AgentReasoner & ReAct)
    participant Skill as 技能中枢 (SkillManager)
    participant IoT as 米家生态 (MiHome / MIoT)

    %% 场景 1：触控抚摸
    User->>HW: 手掌抚摸头顶 (摸头杀)
    HW->>Pipeline: TTP223 触发高电平中断 (Pin 11)
    Pipeline->>HW: 眼球瞬间切为 LOVE 表情 (弯月眨眼) + 发出呼噜轻鸣 (Purr SFX)
    Pipeline->>HW: 舵机轻微歪头 15 度撒娇

    %% 场景 2：语音指令查询与控制
    User->>HW: “我米家都有什么设备？”
    HW->>Pipeline: 麦克风录入音频流 -> VAD 端点切分
    Pipeline->>Brain: 用户文本：“我米家都有什么设备？”
    
    Note over Brain: Round 1 推理：识别为查询类技能<br/>生成先行口头垫话 + 调度 xiaomi_iot
    Brain-->>Pipeline: SSE push: type="thought" (具身大脑实时思考中...)
    Brain-->>Pipeline: SSE push: type="sentence" ("好嘞胖虎，我来看看你米家都有啥设备～")
    Pipeline->>HW: Y57 音箱 0.8s 极速朗读垫话（消除主人等待感）

    par 异步查询真实设备
        Brain->>Skill: dispatch(xiaomi_iot, device="all", action="查询设备列表")
        Skill->>IoT: 读取 xiaomi_config.json 真实已绑定 7 台设备
        IoT-->>Skill: 返回台灯、水壶、摄像机、饮水机、电视盒子等
        Skill-->>Brain: Observation (真实设备清单数据就绪)
    end

    Note over Brain: Round 2 推理：严格基于 Observation 总结真实数据<br/>严禁脑补虚假设备！
    Brain-->>Pipeline: SSE push: type="done" ("查好啦胖虎！你米家一共绑了7台设备...")
    Pipeline->>HW: Y57 音箱 0ms 缝隙平滑无缝播报真实设备结果！
    Pipeline->>HW: 舵机同步点头（NOD，两帧律动）
```

---

## 四、 关键技术规范与开发红线

1. **音频走 USB，绝不碰 I2S / PCM 引脚**：
   - 树莓派的 `GPIO 18` 已分配给水平舵机做硬件 PWM；
   - 严禁接入 I2S 音频模块（如 MAX98357A），否则音频位时钟与舵机发生严重硬件碰撞导致舵机狂抖破音。
2. **严防欠压重启（Brownout Protection）**：
   - 舵机供电端必须并联 $1000\mu\text{F} \sim 2200\mu\text{F}$ 滤波电解电容，或采用独立 5V 2A 供电；
   - 树莓派整机电源必须使用足额 $5\text{V } 3\text{A}$ 优质供电头。
3. **ReAct 单轮直出 vs 双轮垫话分离**：
   - **执行动作类**（定闹钟、做动作、改名字、开/关台灯）：第 1 轮直接在 `reply` 给出确认语，单轮极速闭环（~1.2s 完成），跳过第 2 轮；
   - **数据查询类**（查米家设备、查天气、查新闻）：第 1 轮必须给出即时垫话先行朗读，第 2 轮等待底层真实 `Observation` 返回后如实汇报，**严禁脑补未绑定的虚假设备**！
4. **场景自适应字数机制（Adaptive Verbosity）**：
   - 日常指令操作控制：严格控制在 15~35 字；
   - 讲笑话、讲故事、原理科普：放开字数枷锁（80~200 字），保证完整的情境铺垫与爆笑包袱展开。
