# tests/locustfile.py
import os
from locust import HttpUser, task, between

RESUME_ID = os.environ.get("STRESS_RESUME_ID", "95ab93fb-d103-43f5-aa29-9a8d5958aa4c")
# 与 API 默认一致；可通过环境变量覆盖
MATCH_TOP_K = int(os.environ.get("STRESS_MATCH_TOP_K", "20"))


class ApiUser(HttpUser):
    # 每个虚拟用户两次请求之间的思考时间（秒）
    wait_time = between(0.1, 0.5)

    @task(3)  # 权重 3：更频繁打 jobs 分页列表（优化后默认路径）
    def list_jobs(self):
        with self.client.get(
            "/api/jobs?page=1&page_size=50",
            name="GET /api/jobs?page=1&page_size=50",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()

    @task(2)  # 权重 2：打匹配（带 top_k，禁止默认全量回包）
    def match_jobs(self):
        with self.client.post(
            "/api/jobs/match",
            json={"resume_id": RESUME_ID, "top_k": MATCH_TOP_K},
            name="POST /api/jobs/match",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
            else:
                resp.success()
