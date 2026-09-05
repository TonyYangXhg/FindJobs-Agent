# tests/locustfile_upload_resume.py
"""简历上传压测：异步受理 + 可选轮询端到端 + 旁路短接口。"""
import os
import time
from locust import HttpUser, task, between, events

PDF_PATH = os.environ.get(
    "STRESS_RESUME_PDF",
    r"E:\LLM\project\FindJobs-Agent\tests\fixtures\sample_resume.pdf",
)
# 是否在同一 task 内轮询到 completed（统计端到端）；默认开启
POLL_UNTIL_DONE = os.environ.get("STRESS_UPLOAD_POLL", "1") not in ("0", "false", "False")
POLL_TIMEOUT_S = float(os.environ.get("STRESS_UPLOAD_POLL_TIMEOUT_S", "180"))


class UploadUser(HttpUser):
    wait_time = between(0.5, 1.0)

    @task(3)
    def upload_resume(self):
        with open(PDF_PATH, "rb") as f:
            files = {"file": ("sample_resume.pdf", f, "application/pdf")}
            with self.client.post(
                "/api/resume/upload",
                files=files,
                name="POST /api/resume/upload",
                catch_response=True,
                timeout=60,
            ) as resp:
                if resp.status_code not in (200, 202):
                    resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
                    return
                resp.success()
                if resp.status_code != 202 or not POLL_UNTIL_DONE:
                    return
                try:
                    task_id = resp.json().get("task_id")
                except Exception:
                    return
                if not task_id:
                    return

        # 端到端：轮询 task 直到完成（单独 name，便于对比受理 vs 完成）
        deadline = time.time() + POLL_TIMEOUT_S
        while time.time() < deadline:
            with self.client.get(
                f"/api/resume/task/{task_id}",
                name="GET /api/resume/task/<id> (e2e poll)",
                catch_response=True,
                timeout=30,
            ) as poll:
                if poll.status_code != 200:
                    poll.failure(f"status={poll.status_code}")
                    return
                try:
                    body = poll.json()
                except Exception as e:
                    poll.failure(str(e))
                    return
                status = body.get("status")
                if status == "completed":
                    poll.success()
                    return
                if status == "failed":
                    poll.failure(f"parse failed: {body.get('error')}")
                    return
                poll.success()
            time.sleep(0.4)
        events.request.fire(
            request_type="GET",
            name="GET /api/resume/task/<id> (e2e poll)",
            response_time=POLL_TIMEOUT_S * 1000,
            response_length=0,
            exception=TimeoutError("poll timeout"),
            context={},
        )

    @task(1)  # 旁路：观察短接口是否被拖垮
    def health_or_jobs(self):
        with self.client.get(
            "/api/jobs?page=1&page_size=50",
            name="GET /api/jobs?page=1&page_size=50 (bypass)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()
