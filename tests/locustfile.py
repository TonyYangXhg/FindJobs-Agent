# test/locustfile.py
import os
from locust import HttpUser, task, between

RESUME_ID = os.environ.get("STRESS_RESUME_ID", "95ab93fb-d103-43f5-aa29-9a8d5958aa4c")

class ApiUser(HttpUser):
    # 每个虚拟用户两次请求之间的思考时间（秒）
    wait_time = between(0.1, 0.5)

    @task(3)  # 权重 3：更频繁打 jobs 列表
    def list_jobs(self):
        with self.client.get("/api/jobs", name="GET /api/jobs", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()

    @task(2)  # 权重 2：打匹配
    def match_jobs(self):
        with self.client.post(
            "/api/jobs/match",
            json={"resume_id": RESUME_ID},
            name="POST /api/jobs/match",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
            else:
                resp.success()