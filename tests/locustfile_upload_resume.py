# tests/locustfile_upload_resume.py
import os
from locust import HttpUser, task, between

PDF_PATH = os.environ.get(
    "STRESS_RESUME_PDF",
    r"E:\LLM\project\FindJobs-Agent\tests\fixtures\sample_resume.pdf",
)

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
                timeout=300,
            ) as resp:
                if resp.status_code not in (200, 202):
                    resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
                else:
                    resp.success()

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