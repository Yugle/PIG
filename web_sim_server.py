import http.server
import socketserver
import os
import json
import requests

PORT = 8090
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

def load_config():
    cfg_file = os.path.join(DIRECTORY, "config.json")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/web_simulator.html"
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                req_json = json.loads(post_data.decode("utf-8"))
                user_text = req_json.get("text", "")
            except Exception:
                user_text = ""

            cfg = load_config()
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "https://token.sensenova.cn/v1").rstrip("/")
            model = cfg.get("llm_model", "sensenova-6.8-flash-lite")

            sys_prompt = """你是桌面的具身实体机器人搭子。根据主人的话，输出符合情境的肢体语言与回答。
严格输出 JSON 结构，不要包含任何 markdown 或其他文本：
{
  "reply": "你说话的内容 (30字以内)",
  "action": "NOD | SHAKE | NONE | LOOK_UP | SURPRISE",
  "emotion": "NORMAL | HAPPY | CONFUSED | SAD | ANGRY | SURPRISED",
  "pan": 90,
  "tilt": 90
}
说明：
- pan: 水平头部朝向 [30, 150]，90 为正中
- tilt: 俯仰头部朝向 [70, 110]，90 为平视，80 为轻微抬头看人
- action: NOD(点头认同), SHAKE(摇头否认), LOOK_UP(抬头注视), SURPRISE(吃惊), NONE(静止)
- emotion: 表情类型
"""

            reply_data = {
                "reply": "我在听呢，有什么问题尽管问我吧！",
                "action": "NONE",
                "emotion": "HAPPY",
                "pan": 90,
                "tilt": 90
            }

            if api_key and "YOUR_LLM_API_KEY" not in api_key:
                try:
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_text}
                        ]
                    }
                    resp = requests.post(url, headers=headers, json=payload, timeout=60)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"].strip()
                        if content.startswith("```"):
                            content = content.split("```")[1]
                            if content.startswith("json"):
                                content = content[4:]
                            content = content.strip()
                        parsed = json.loads(content)
                        reply_data.update(parsed)
                except Exception as e:
                    print(f"⚠️ [Web-LLM Error]: {e}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(reply_data, ensure_ascii=False).encode("utf-8"))
            return

        return super().do_POST()

def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        print("="*60)
        print("🚀 [Web 3D 仿真工作台已就绪]")
        print(f"👉 本地访问地址: http://localhost:{PORT}")
        print("✨ 包含: 3D WebGL 机械实体视角 + 2.4寸动态眼球屏 + 具身动作交互")
        print("="*60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 仿真服务器已关闭")

if __name__ == "__main__":
    run_server()
