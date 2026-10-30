import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional

from drivers.eye_renderer import EyeRenderer
from drivers.tts_engine import TTSEngine

# 自动加载 HAL
is_raspberry_pi = False
if os.path.exists("/proc/device-tree/model"):
    try:
        with open("/proc/device-tree/model", "r") as f:
            if "Raspberry Pi" in f.read():
                is_raspberry_pi = True
    except Exception:
        pass

if is_raspberry_pi:
    from drivers.hal_physical import PhysicalRobotHAL as RobotHAL
    hal = RobotHAL()
else:
    from drivers.hal_simulation import SimulationRobotHAL as RobotHAL
    hal = RobotHAL()

eyes = EyeRenderer(240, 320)
tts = TTSEngine()

app = FastAPI(
    title="Desktop Embodied AI Robot Remote API",
    description="用于手机局域网或外部系统向伴侣机器人发送广播指令、表情及动作",
    version="1.0.0"
)

class BroadcastMessage(BaseModel):
    text: str = Field(..., description="代播的文本内容")
    emotion: Optional[str] = Field("HAPPY", description="表情: NORMAL | HAPPY | CONFUSED | SAD | ANGRY | SURPRISED")
    pan: Optional[int] = Field(90, description="水平角度 [20, 160]")
    tilt: Optional[int] = Field(85, description="俯仰角度 [60, 120]")

class ActionMessage(BaseModel):
    action: str = Field(..., description="动作: NOD | SHAKE | RESET")
    pan: Optional[int] = Field(90, description="水平角度")
    tilt: Optional[int] = Field(90, description="俯仰角度")

def handle_broadcast(msg: BroadcastMessage):
    try:
        hal.set_angles(msg.pan, msg.tilt)
        hal.display_face(eyes.render_eyes(msg.emotion))
        tts.speak(msg.text)
        hal.display_face(eyes.render_eyes("NORMAL"))
        hal.set_angles(90, 90)
    except Exception as e:
        print(f"⚠️ [代播执行失败]: {e}")

@app.get("/health")
def health_check():
    return {"status": "ok", "mode": "physical" if is_raspberry_pi else "simulation"}

@app.get("/status")
def get_status():
    return {
        "pan": hal.cur_pan,
        "tilt": hal.cur_tilt,
        "is_raspberry_pi": is_raspberry_pi
    }

@app.post("/speak")
async def trigger_speak(msg: BroadcastMessage, bg: BackgroundTasks):
    """
    触发语音播报与肢体联动
    """
    bg.add_task(handle_broadcast, msg)
    return {
        "status": "success",
        "message": f"已加入播报队列: '{msg.text}'",
        "params": {"emotion": msg.emotion, "pan": msg.pan, "tilt": msg.tilt}
    }

@app.post("/action")
async def trigger_action(msg: ActionMessage):
    """
    直接控制舵机动作
    """
    action = msg.action.upper()
    if action == "NOD":
        hal.set_angles(msg.pan, 75)
        hal.set_angles(msg.pan, 95)
        hal.set_angles(msg.pan, 90)
    elif action == "SHAKE":
        hal.set_angles(msg.pan - 20, msg.tilt)
        hal.set_angles(msg.pan + 20, msg.tilt)
        hal.set_angles(msg.pan, msg.tilt)
    elif action == "RESET":
        hal.set_angles(90, 90)
    else:
        hal.set_angles(msg.pan, msg.tilt)

    return {"status": "success", "action": action, "pan": hal.cur_pan, "tilt": hal.cur_tilt}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
