# tests/mock/mock_llm_server.py
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

# 简历结构化 + 技能打分都会打到这里；固定延迟保证可复现
MOCK_LATENCY_S = 0.8

@app.post("/v1/chat/completions")
def chat():
    time.sleep(MOCK_LATENCY_S)
    # content 需能被 resume_parser 侧解析逻辑容忍；可按实测再调
    content = (
        '{"name":"Test","education":[],"experience":[],"skills":[]}\n'
        "Python:3\nJava:2"
    )
    return jsonify({
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=18080)