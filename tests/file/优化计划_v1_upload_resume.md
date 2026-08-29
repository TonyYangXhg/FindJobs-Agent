# FindJobs-Agent 优化计划 v1（简历上传精选必要项）

> 依据：当前实现与压测手册 `tests/file/stress_test_upload_resume.md`  
> - `POST /api/resume/upload`：在请求线程内同步调用 `resume_parser.parse_resume()`（PDF 抽取 + 约 **2 次 LLM** 串行）  
> - `llm_client`：`timeout=120`、`max_retry=3`，最坏可长时间占死 worker  
> - 落盘：`uploads/resumes_store.json` 全量重写，无锁、非原子  
> 原则：只做能直接改善「受理延迟 / 端到端完成时间 / 旁路短接口稳定性」、且能写进简历的最小集合；**压测一律 Mock LLM**；不做 Redis/Celery/换模型。

---

## 任务清单

> 标注说明：**[必选]** = v1 最小闭环必须完成；**[可选]** = 加分项，有依赖或时间不够可后置。  
> 下列先列全部必选，再列可选。

### 必选

- [ ] **[必选] P1** 将简历上传改为异步任务（立即返回，后台解析）+ 任务状态查询接口  
  **现状问题**：[`api_server.py`](../../api_server.py) 的 `upload_resume` 在 HTTP 请求线程内同步执行 `parse_resume`；一次上传占用 worker 长达数秒～数十秒（Mock 下约 2×800ms，真实上游更差），并发时拖垮同进程其它接口（如 `/api/jobs`）。  
  **做什么**：  
  1. `POST /api/resume/upload`：校验并落盘 PDF 后，创建 `task_id`，状态置为 `pending/running`，提交到进程内 `ThreadPoolExecutor`，**立即**返回 **202**（或明确约定的 JSON）含 `task_id`（可同时返回临时 `resume_id`）。  
  2. 新增 `GET /api/resume/task/<task_id>`（路径可微调，但需文档与 Locust 一致）：返回 `status`（`pending|running|completed|failed`）、失败原因、完成后的 `resume_id` / skills 摘要。  
  3. 完成后写入 `resumes_store`（走 P4 的锁与原子落盘）。  
  4. 前端/压测改为：上传 → 轮询 task → 再 match。  
  **验收**：Mock 下 1 用户：上传 HTTP 受理 P95 **&lt;200ms**；task 最终 `completed`；端到端完成时间单独统计。5 用户加压时，旁路 `GET /api/jobs?page=1&page_size=50` 的 P95/错误率明显优于优化前同步阻塞基线。  
  **简历价值**：「长任务不堵请求线程」——受理延迟与完成时间分离，是最硬的架构数字。

- [ ] **[必选] P4** 为 `resumes_store` 加读写锁，并改为原子落盘  
  **现状问题**：`_save_resumes_to_disk` 全量 `write_text`，无锁；并发上传时可能丢更新或写坏 JSON。异步化后多线程写 store，风险更大。  
  **做什么**：增加 `threading.RLock`（或与现有锁策略统一）；所有读/写 `resumes_store`、更新 hash 索引、落盘走同一把锁；落盘使用临时文件 + `os.replace`（Windows 可用等价原子替换）。临界区短小，**锁外**跑 PDF 解析与 LLM。  
  **验收**：5 用户并发上传（Mock）结束后，成功任务数与 store 中新增简历数一致（无静默丢失）；`resumes_store.json` 可被正常 `json.loads`；错误率 0 或仅含可解释的业务失败。  
  **简历价值**：讲清「并发下共享状态正确性」，承接 jobs 侧 RLock 经验。

- [ ] **[必选] P5** 更新上传 Locust 脚本与复测流程，产出「优化前 / 优化后」对比表并归档  
  **现状问题**：仅有 jobs/match 的 Locust；上传无统一脚本与「受理 vs 端到端」分列归档，简历数字无法复核。  
  **做什么**：  
  1. 维护 `tests/locustfile_upload_resume.py`：multipart 上传固定 PDF；旁路打分页 jobs 或 health；异步化后增加 task 轮询统计端到端。  
  2. 固定参数：先 **1 用户 / 3min**，再 **5 用户 / spawn-rate 1 / 3min**；全程 `LLM_API_URL` 指向 Mock。  
  3. CSV 输出到 `tests/results/upload_baseline_*` 与 `upload_after_*`。  
  4. 填对比表：受理 P95、端到端 P95、受理 RPS、旁路 P95、错误率；秒传二次耗时见下方可选 P3。  
  **验收**：`tests/results/` 下有基线与优化后两套结果；数字可直接粘贴进简历，并注明 Mock 条件。  
  **简历价值**：没有这一步，必选项都无法变成可信叙事。

### 可选

- [ ] **[可选] P2** 简历解析内两次 LLM 调用可并行则并行（Mock 下量化）  
  **现状问题**：[`resume_parser.py`](../../resume_parser.py) 中结构化抽取与技能打分大致 **串行** 两次 `LLMClient.chat`；Mock 固定 800ms 时端到端接近 **1.6s+**，真实上游更差。若打分主要依赖 PDF 原文 + 规则召回候选标签，则不必等结构化 JSON 完成。  
  **做什么**：梳理依赖后，用 `ThreadPoolExecutor`/`concurrent.futures` 并行提交「结构化」与「打分」（仅当无硬依赖时）；若存在硬依赖，则文档如实写明「仅异步化、未并行」并跳过本项数字。禁止在持有 `resumes_store` 锁时调用 LLM。  
  **验收**：同一 Mock（800ms）下，端到端完成 P95 相对串行下降约 **40%～50%**（例如 ~1.6s → ~0.8s 量级，允许业务开销）；功能结果（skills 非空或与基线一致的可接受率）不显著变差。  
  **简历价值**：体现「读懂调用图、消除伪串行」，与面试链路并行叙事同类，且可用 Mock 复现。

- [ ] **[可选] P3** 同文件内容哈希秒传（跳过重复 LLM）  
  **现状问题**：同一 PDF 重复上传仍完整走 2 次 LLM，浪费时间与配额，压测与演示都难看。  
  **做什么**：对上传 bytes（或落盘后文件）计算内容哈希（如 SHA-256）；维护 `hash → resume_id` 索引（内存 + 随 `resumes_store` 持久化）；命中则直接返回已有简历/completed 任务，**不调用** Mock/真实 LLM。注意：仅内容相同才命中；改名不改内容应仍命中。  
  **验收**：同一 PDF 连续上传两次：第二次 Mock 无新请求（或耗时毫秒级）；首次仍正常解析。Locust/手工表记录「首次端到端」vs「秒传受理/完成」。  
  **简历价值**：「重复上传由秒级降至毫秒级」，演示与数字都极好讲。

---

## 明确不在 v1 范围（避免分心）

| 不做 | 原因 |
|------|------|
| Redis / Celery / 独立队列服务 | 单机线程池已够第一版「异步化」数字 |
| 真实 Key 压测 LLM | 贵、不可复现；本方案强制 Mock |
| 换更快模型 / 只调 prompt | 像调参，不像服务端工程优化 |
| PyPDF2 微优化当主叙事 | 通常远小于 2 次 LLM 耗时 |
| jobs/match 再改一轮 | 见 `优化计划_v1_jobs_and_match.md` |
| 面试状态机并行 | 另案可选，不纳入上传 v1 |

---

## 建议执行顺序

```
【必选】P1 异步化 + 任务查询 → 【必选】P4 锁与原子落盘
    → 【可选】P2 LLM 并行（依赖允许时）→ 【可选】P3 哈希秒传
    → 【必选】P5 复测归档
```

说明：P1 与 P4 建议同一迭代落地（异步后无锁更容易丢数据）。P2/P3 为加分项，时间紧可只做必选仍能出「受理 P95 断崖 + 旁路不被拖死」主表。完成 P5 后出简历最终数字。

---

## 对比表模板（P5 填写）

> 优化前：2026-08-29 Locust（1u CSV + 5u UI）。1u：upload×2、bypass×3；5u：upload×47、bypass×12。优化后行待测。

| 场景 | Users | 指标类型 | 接口 / 说明 | RPS | P95 (ms) | 错误率 |
|------|-------|----------|-------------|-----|----------|--------|
| 优化前 | 1 | 受理(=端到端) | POST /api/resume/upload | **≈0.04** | **17000** | **0%** (0/2) |
| 优化前 | 1 | 旁路 | GET /api/jobs?page=1&page_size=50 | **≈0.06** | **13000** | **0%** (0/3) |
| 优化前 | 5 | 受理(=端到端) | POST /api/resume/upload | **≈0.1** | **18000** | **0%** (0/47) |
| 优化前 | 5 | 旁路 | GET /api/jobs?page=1&page_size=50 | **≈0** | **4** | **0%** (0/12) |
| 优化后 | 1 | 受理 | POST /api/resume/upload → 202 | __ | __ | __ |
| 优化后 | 1 | 端到端完成 | upload + 轮询 task | __ | __ | __ |
| 优化后 | 5 | 受理 | POST /api/resume/upload → 202 | __ | __ | __ |
| 优化后 | 5 | 端到端完成 | upload + 轮询 task | __ | __ | __ |
| 优化后 | 5 | 旁路 | GET /api/jobs?page=1&page_size=50 | __ | __ | __ |
| 优化后（可选 T6） | 1 | 秒传 | 同一 PDF 第二次上传 | __ | __ | __ |

> 条件备注：Mock 延迟 ≈ **800 ms**；PDF ≈ `tests/fixtures/sample_resume.pdf`（或 uploads 下 AgentHarness.pdf）；API = `python api_server.py` + `LLM_API_URL`→Mock。  
> 旁路补充：jobs Avg Size≈**67KB**（分页优化后正常）。1u 时 P95 被上传阻塞抬到 **13s**；5u 时 P95 回到 **4ms**，但 upload 仍约 **17～18s**——主矛盾是同步长任务，不是 jobs 体积。
)
