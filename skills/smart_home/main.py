import json
import os

STATE_FILE = "/tmp/smart_home_state.json"

def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"客厅灯": "关闭", "卧室灯": "关闭", "空调": "关闭 (26°C)", "加湿器": "关闭"}

def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

def run(device="客厅灯", action="打开"):
    state = _load_state()
    dev_key = device.strip()
    act = action.strip()

    state[dev_key] = act
    _save_state(state)
    return f"指令已下发！已为你成功将【{dev_key}】执行【{act}】操作，当前设备状态已同步更新。"
