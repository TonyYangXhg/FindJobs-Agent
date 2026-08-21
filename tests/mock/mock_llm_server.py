# test/mock_llm_server.py
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.post("/v1/chat/completions")
def chat():
    time.sleep(0.8)  # 模拟上游 RTT
    body = request.get_json(force=True) or {}
    # 返回 OpenAI 兼容结构；内容可按需改成合法 JSON 字符串
    content = '{"skills": [], "ok": true}'
    return jsonify({
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=18080)