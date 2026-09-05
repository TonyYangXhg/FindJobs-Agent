#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最小自测：P1 异步上传 + P4 原子落盘（不依赖真实 LLM）。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 使用临时 resumes_store，避免污染正式数据
TEST_UPLOADS = ROOT / "uploads" / "_selftest_upload"
TEST_UPLOADS.mkdir(parents=True, exist_ok=True)

import api_server as api

api.UPLOAD_FOLDER = TEST_UPLOADS
api.app.config["UPLOAD_FOLDER"] = str(TEST_UPLOADS)
api.RESUME_STORE_FILE = TEST_UPLOADS / "resumes_store.json"
api.resumes_store.clear()
api.resume_tasks.clear()


def _fake_parse(path: str):
    time.sleep(0.05)  # 模拟短耗时，仍在锁外
    return {
        "extracted_info": {"name": "SelfTest"},
        "upload_date": "2026-01-01T00:00:00",
        "skills": [{"skill_name": "Python", "score": 3}],
    }


api.resume_parser.parse_resume = _fake_parse  # type: ignore

PDF = ROOT / "tests" / "fixtures" / "sample_resume.pdf"
if not PDF.exists():
    # 最小假 PDF 字节（仅占位；parse 已被 mock）
    PDF = TEST_UPLOADS / "dummy.pdf"
    PDF.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")


def main() -> None:
    client = api.app.test_client()

    t0 = time.perf_counter()
    with PDF.open("rb") as f:
        resp = client.post(
            "/api/resume/upload",
            data={"file": (f, "sample_resume.pdf")},
            content_type="multipart/form-data",
        )
    accept_ms = (time.perf_counter() - t0) * 1000
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body.get("task_id") and body.get("resume_id")
    assert body.get("status") == "pending"
    assert accept_ms < 500, f"受理过慢: {accept_ms:.1f}ms"
    print(f"[OK] P1 accept {accept_ms:.1f}ms status=202 task={body['task_id']}")

    task_id = body["task_id"]
    resume_id = body["resume_id"]
    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        tr = client.get(f"/api/resume/task/{task_id}")
        assert tr.status_code == 200
        final = tr.get_json()
        if final["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert final and final["status"] == "completed", final
    assert final.get("resume", {}).get("id") == resume_id
    assert final.get("skills")
    print(f"[OK] P1 task completed resume={resume_id}")

    gr = client.get(f"/api/resume/{resume_id}")
    assert gr.status_code == 200
    assert gr.get_json()["resume"]["id"] == resume_id
    print("[OK] GET /api/resume/<id>")

    # P4: 落盘文件可解析
    assert api.RESUME_STORE_FILE.exists()
    payload = json.loads(api.RESUME_STORE_FILE.read_text(encoding="utf-8"))
    assert resume_id in payload
    print(f"[OK] P4 atomic store keys={len(payload)}")

    # 旁路短接口仍可用
    jr = client.get("/api/jobs?page=1&page_size=50")
    assert jr.status_code == 200
    print("[OK] bypass GET /api/jobs")

    print("ALL SELFTESTS PASSED")


if __name__ == "__main__":
    main()
