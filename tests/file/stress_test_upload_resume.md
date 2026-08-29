# FindJobs-Agent 压力测试计划（简历上传最小可行方案）

> 目标：用 **1 周内可完成** 的压测，产出能写进简历的对比数字（受理延迟 / 端到端完成时间 / P95 / 错误率 / 旁路短接口是否被拖垮），而不是把全站每个接口都测一遍。
>
> 原则：**只压「服务侧可控、可复现、不烧 LLM 钱」的路径**；LLM 上游 **必须用 Mock 隔离**（上传链路依赖 LLM，与 jobs/match 压测不同）。

---

## 1）任务清单（按顺序做）

> 标注说明：**[必选]** = 最小闭环必须完成；**[可选]** = 加分项，不做也能出主对比表。  
> 下列先列全部必选，再列可选。

### 必选

- [ ] **[必选] T1** 搭环境：安装 Locust、准备一份固定小体积测试 PDF、确认 `python api_server.py` 可启动、建立 `tests/results/`；记录「优化前」为同步阻塞上传（请求线程内跑完 `parse_resume`）
- [ ] **[必选] T2** 写并启动 Mock LLM 上游（固定 ~800ms 延迟 + 预设可被解析器接受的 JSON），用环境变量把 `LLM_API_URL` 指过去（**禁止**用真实 DeepSeek/OpenAI 做本方案压测）
- [ ] **[必选] T3** 建立 Baseline：用 Locust 压测 **重点 A：`POST /api/resume/upload`**；记录「HTTP 返回耗时」「解析完成总耗时」（优化前二者相同）、错误率
- [ ] **[必选] T4** 做最小优化（详见 `优化计划_v1_upload_resume.md`）：**必选落地**上传异步化（立即返回 task_id）+ 任务状态查询、`resumes_store` 锁 + 原子落盘
- [ ] **[必选] T5** 同场景复测：相同 Locust 参数再跑一遍，填「优化前 → 优化后」对比表（**必须分开填「受理 P95」与「端到端完成 P95」**）
- [ ] **[必选] T7** 整理简历素材：1 张对比表 + 2～3 句「约束 → 方案 → 数字」结论；把 Locust 命令、参数、CSV/截图放进 `tests/results/`

### 可选

- [ ] **[可选] T3-旁路** 加压上传时同时打 **重点 B：`GET /api/jobs?page=1&page_size=50` 或 `GET /api/health`**，观察长任务是否拖垮短接口（强烈建议，数字更好讲）
- [ ] **[可选] T4-加分** 在必选优化之外：两次 LLM 可并行则并行、同 PDF 内容哈希秒传（对应优化计划 P2/P3）
- [ ] **[可选] T6** 同文件二次上传：哈希命中时跳过 LLM，对比「首次解析完成耗时」vs「秒传受理/完成耗时」（依赖优化计划 P3）

---

## 2）为什么只压上传相关接口？（范围说明）

| 接口 | 是否压测 | 原因 |
|------|----------|------|
| `POST /api/resume/upload` | **必须** | 当前在请求线程内同步 PDF + 约 2 次 LLM，是典型「长任务堵短请求」；最易写出架构向数字 |
| `GET /api/resume/task/<id>`（优化后） | **必须（优化后）** | 异步化后 Locust/前端靠轮询拿完成状态；用于统计端到端完成时间 |
| `GET /api/health` 或分页 `GET /api/jobs` | **建议作旁路** | 加压上传时看短接口 P95/错误率，证明「长任务不堵请求线程」 |
| `GET /api/jobs` / `POST /api/jobs/match` 主叙事 | **不做本方案主压** | 已在 `stress_test_jobs_and_match.md` 完成 |
| 真实 DeepSeek/OpenAI | **禁止** | 贵、限流、抖动大；测的是厂商不是你的调度 |
| `POST /api/interview/*` / 爬虫 | **不做** | 非本方案范围 |

简历只需要讲清楚：**上传长任务的受理与完成延迟**，以及（建议）**并发上传时短接口仍可用**；可选写 **同文件哈希秒传**。

---

## 3）手把手操作指南（零基础）

### 3.0 压测是什么？你要得到什么数字？

压测 = 用工具模拟多人同时上传简历，观察服务是否变慢、报错、是否拖死其它接口。

上传场景必须区分两种时间（优化前后填表都要用）：

| 指标 | 含义 | 优化前 | 优化后（异步） |
|------|------|--------|----------------|
| **受理延迟** | 上传 HTTP 返回的耗时 | ≈ 整段解析完成时间 | 应降到毫秒～百毫秒（立即返回 task_id） |
| **端到端完成时间** | 从发上传到解析 status=completed | 与受理延迟相同 | 受理返回后轮询 task，直到完成的总时间 |
| **QPS / RPS** | 每秒成功处理的上传（或受理）次数 | 往往极低 | 受理 RPS 应明显上升 |
| **P95 延迟** | 95% 请求在多少毫秒内完成 | 填受理/端到端两列 | 同上 |
| **错误率** | 5xx / 超时 / 连接失败占比 | 记基线 | 目标降至 0 或显著下降 |
| **旁路短接口 P95** | 加压上传时 health/jobs 的 P95 | 往往被拖高 | 应接近无上传压力时的水平 |

> 面试官常问：「你压的是真实模型还是 Mock？受理和完成是否分开？」——所以 **T2 Mock 必做**，**T3/T5 参数必须一致**，表格必须拆开两列时间。

---

### 3.1 T1：准备环境（约 30 分钟）

#### Step 1：进入项目根目录

```powershell
cd E:\LLM\project\FindJobs-Agent
```

#### Step 2：安装 Locust

```powershell
pip install locust
locust --version
```

#### Step 3：准备固定测试 PDF

任选其一：

- 从前端曾上传成功的文件复制一份到例如 `tests/fixtures/sample_resume.pdf`
- 或使用 `uploads/` 下已有的小体积 PDF（建议 &lt; 1MB，避免测到磁盘/上传带宽而不是解析调度）

记下路径，例如：

```powershell
$env:STRESS_RESUME_PDF="E:\LLM\project\FindJobs-Agent\tests\fixtures\sample_resume.pdf"
```

#### Step 4：建结果目录

```powershell
mkdir tests\results -Force
```

#### Step 5：先不要急着开 API

上传压测 **必须先开 Mock（T2）**，再开 API，否则容易打到真实 Key。

---

### 3.2 T2：Mock LLM（本方案必做，约 40 分钟）

#### 为什么要 Mock？

- 压真实 DeepSeek/OpenAI：**烧钱、被限流、延迟抖动大** → 测的是厂商，不是你的服务。
- Mock：固定 sleep + 固定 JSON → **测的是你的线程调度、超时、队列、并发安全、异步化效果**。

#### 最小 Mock 服务（建议路径 `tests/mock/mock_llm_server.py`）

```python
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
```

启动 Mock（终端 A）：

```powershell
python tests\mock\mock_llm_server.py
```

启动 API 并指向 Mock（终端 B）：

```powershell
cd E:\LLM\project\FindJobs-Agent
$env:LLM_API_URL="http://127.0.0.1:18080/v1/chat/completions"
python api_server.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
```

> 在线路径走 `llm_client.LLMClient`，会读 `LLM_API_URL`。确认上传时 Mock 终端有请求日志，才算指对了。

---

### 3.3 T3：写 Locust 脚本并跑 Baseline（约 1～2 小时）

#### Step 1：创建脚本 `tests/locustfile_upload_resume.py`

优化前（同步阻塞）示例：每次上传同一份 PDF，统计 upload 的 HTTP 耗时（此时 ≈ 端到端）。

```python
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
```

> 优化后（异步）：upload 期望快速 202；另加轮询 `GET /api/resume/task/<task_id>` 直到 `completed`/`failed`，用自定义事件或单独脚本统计「端到端完成时间」。具体路径以 `优化计划_v1_upload_resume.md` P1 落地为准。

手动冒烟（PowerShell）：

```powershell
curl.exe -X POST http://127.0.0.1:5000/api/resume/upload -F "file=@$env:STRESS_RESUME_PDF"
```

#### Step 2：启动 Locust Web UI

```powershell
$env:STRESS_RESUME_PDF="你的pdf完整路径"
$env:LLM_API_URL="http://127.0.0.1:18080/v1/chat/completions"
locust -f tests\locustfile_upload_resume.py --host http://127.0.0.1:5000
```

浏览器打开：http://localhost:8089

#### Step 3：第一次建议参数（Baseline，务必记下来）

上传比 jobs 重，**不要一上来 50 用户**。

| 参数 | 建议值 | 说明 |
|------|--------|------|
| Number of users | **1** 先跑通，再 **5** | 虚拟并发用户 |
| Spawn rate | **1**（5 用户时可用 1～2） | 每秒增加用户数 |
| Run time | **3m**（或手动停） | 至少跑满 2～3 分钟看稳态 |

点 Start。记录：

- `POST /api/resume/upload` 的 Median / **95%** / RPS / Fails
- 旁路 `GET /api/jobs?...` 的 P95（若被拖到很高，写成优化动机）

#### Step 4：无头模式（方便存档）

```powershell
$env:STRESS_RESUME_PDF="你的pdf完整路径"
locust -f tests\locustfile_upload_resume.py --host http://127.0.0.1:5000 `
  --users 1 --spawn-rate 1 --run-time 3m --headless `
  --csv tests\results\upload_baseline_1u

locust -f tests\locustfile_upload_resume.py --host http://127.0.0.1:5000 `
  --users 5 --spawn-rate 1 --run-time 3m --headless `
  --csv tests\results\upload_baseline_5u
```

#### Step 5：填写 Baseline 表（手抄或 Excel）

> 数据来源：2026-08-29 Locust（1u：`upload_resume_baseline/_stats.csv`；5u：Web UI，约 3min）。1u 样本：upload×2、bypass×3；5u：upload×47、bypass×12。
>
> 旁路短接口 = 压测时顺便打一下的、本来很快的接口，用来看「上传这种慢活会不会把别的接口也拖慢」。

| 场景 | Users | 指标类型 | 接口 | RPS | P95 (ms) | Avg Size | 错误率 |
|------|-------|----------|------|-----|----------|----------|--------|
| 优化前 | 1 | 受理(=端到端) | POST /api/resume/upload | **≈0.04** | **17000** | **339 B** | **0%** (0/2) |
| 优化前 | 1 | 旁路短接口 | GET /api/jobs?page=1&page_size=50 | **≈0.06** | **13000** | **~67KB** (68679) | **0%** (0/3) |
| 优化前 | 5 | 受理(=端到端) | POST /api/resume/upload | **≈0.1** | **18000** | **339 B** | **0%** (0/47) |
| 优化前 | 5 | 旁路短接口 | GET /api/jobs?page=1&page_size=50 | **≈0** | **4** | **~67KB** (68679) | **0%** (0/12) |

**1 用户补充：** upload Median≈**16632** ms、Avg≈**16607** ms；bypass Median≈**4** ms、Avg≈**4233** ms、Max≈**12691** ms。

**5 用户补充：** upload Median≈**17000** ms、Avg≈**16952** ms、Max≈**17855** ms；bypass Median/P95≈**4 ms**。合计约 **59** 次请求、失败 **0**；图表稳态延迟约 **18s**（被 upload 主导），瞬时 RPS 约 **0.5～1.5**。

**基线解读（为何要做异步化）：**

1. **上传始终是长任务**：1u/5u 受理 P95 都在 **17～18s**；用户加到 5，单次上传并不变快，吞吐仍约 **0.1 RPS**。  
2. **旁路要分场景看**：1u 小样本时 bypass P95 偶发到 **13s**；5u 样本更足时 P95 回到 **4ms**（体积仍约 67KB）。jobs 本身已优化，但 upload 占满请求线程时仍可能饿死短接口——异步化要用「受理变快 + 加压时旁路更稳」来证明。  
3. **优化目标**：受理 P95 **17s → &lt;200ms**；端到端单独统计；旁路保持毫秒级、避免再出现秒级尖刺。

> 若 upload 大量超时：先确认 Mock 在跑、PDF 路径正确；再把 users 降到 1。同步路径下 5 并发把 Flask 打满是预期现象，恰好说明需要异步化。

**期望（务实，供 T4 对照）：**

| 指标 | 优化前（本次实测） | 优化后期望 |
|------|--------------------|------------|
| 上传受理 P95 | **~17～18s**（1u/5u） | **&lt;200ms**（立即 202） |
| 端到端完成 P95（Mock） | **~17～18s**（=受理） | 并行后约 **~0.8s+业务**；仅异步串行约 **~1.6s+业务** |
| 旁路 jobs P95（有上传压力时） | 1u 偶发 **~13s**；5u 稳态 **~4ms** | 保持毫秒级，避免再出现秒级尖刺 |
| 同文件二次上传 | 再跑完整 LLM | 哈希命中 → **毫秒级** |

---

### 3.4 T4：做「简历够用」的最小优化（约 1～2 天）

按优先级只做这些（细节见 `优化计划_v1_upload_resume.md`）：

1. **上传异步化**  
   - `POST /api/resume/upload` 存文件后立即返回 `202` + `task_id`（或等价）  
   - 后台 `ThreadPoolExecutor` 执行 `parse_resume`  
   - `GET /api/resume/task/<task_id>` 查询 `pending/running/completed/failed`

2. **两次 LLM 可并行则并行**  
   - Mock 下验证端到端完成时间下降约 40%～50%  
   - 禁止在共享锁内调用 LLM

3. **同文件内容哈希秒传**  
   - 对 PDF bytes 做 hash；命中则跳过 LLM，返回已有 `resume_id`

4. **`resumes_store` 读写锁 + 原子落盘**  
   - `RLock` + 临时文件 + `os.replace`

---

### 3.5 T5：复测并做对比表（约 30 分钟）

**必须使用与 T3 完全相同的 users / spawn-rate / run-time，且仍指向 Mock。**

```powershell
$env:LLM_API_URL="http://127.0.0.1:18080/v1/chat/completions"
$env:STRESS_RESUME_PDF="你的pdf完整路径"
locust -f tests\locustfile_upload_resume.py --host http://127.0.0.1:5000 `
  --users 1 --spawn-rate 1 --run-time 3m --headless `
  --csv tests\results\upload_after_1u

locust -f tests\locustfile_upload_resume.py --host http://127.0.0.1:5000 `
  --users 5 --spawn-rate 1 --run-time 3m --headless `
  --csv tests\results\upload_after_5u
```

对比表模板：

| 指标 | 优化前（实测） | 优化后 | 提升 |
|------|----------------|--------|------|
| 上传受理 P95（1u） | **17000 ms** |  |  |
| 端到端完成 P95（1u，Mock） | **17000 ms**（=受理） |  |  |
| 上传受理 P95（5u） | **18000 ms** |  |  |
| 上传受理 RPS（5u） | **≈0.1** |  |  |
| 旁路 jobs P95（1u 加压时） | **13000 ms**（Median 仍 4） |  |  |
| 旁路 jobs P95（5u 加压时） | **4 ms** |  |  |
| 上传错误率（1u / 5u） | **0% / 0%** |  |  |

把 `upload_baseline_*` 与 `upload_after_*` CSV/截图一起留存。

---

### 3.6 T6（可选）：同 PDF 哈希秒传

1. 先上传一次，记录端到端完成时间  
2. 不改文件再上传一次（或 Locust 连续两次同一 PDF）  
3. 第二次应走哈希命中：无 Mock 调用（Mock 日志无新增）或耗时降到毫秒级  

验收：二次上传完成/受理耗时较首次下降至少一个数量级。

---

### 3.7 T7：简历怎么写（直接套数字）

把实测数字填进去，句式示例：

> 简历上传链路在 Mock LLM 下建立 Locust 基线；将同步阻塞解析改为异步任务（立即返回 task_id），上传受理 P95 从 **Ams** 降至 **Bms**；5 并发下旁路岗位列表 P95 从 **Cms** 降至 **Dms**，错误率 **E% → 0**。结构化抽取与技能打分并行后，端到端完成时间降低约 **X%**；同文件哈希秒传使重复上传由秒级降至毫秒级。

---

## 4）常见坑（第一次压测几乎都会遇到）

| 现象 | 可能原因 | 怎么办 |
|------|----------|--------|
| 上传极慢且 Mock 无日志 | `LLM_API_URL` 未设置，打到了真实上游 | 重启 API 前先设环境变量；看 Mock 终端是否有请求 |
| Locust multipart 报错 | `files=` 用法/文件句柄过早关闭 | 每个请求内 `with open(...)`；参考上文脚本 |
| 5 用户大量超时 | 同步路径下 worker 被 LLM 占满 | 属基线预期；记下数字后做异步化 |
| 旁路 jobs 也变慢 | 长任务堵在同一进程请求线程 | 正是 P1 要解决的问题 |
| 优化后只盯 upload HTTP 变快 | 忘了测「解析是否真完成」 | 必须轮询 task；对比表拆受理/端到端 |
| 解析结果空/失败 | Mock 返回 JSON 形状不被解析器接受 | 按 `resume_parser` 调整 Mock `content`，先手动 curl 跑通 |

---

## 5）本方案明确不做的事（避免范围膨胀）

- 不上 Locust 压真实付费 LLM
- 不把 jobs/match 主压测再做一遍（见另一份文档）
- 不上 Celery/Redis/K8s（第一版线程池足够写简历）
- 不上 K6/JMeter（多工具不增加说服力）
- 不把「换更快模型」当成工程优化主叙事

---

## 6）建议时间表

| 天 | 任务 |
|----|------|
| Day 1 | T1 + T2 + T3 Baseline（1u 再 5u，先有数字） |
| Day 2～3 | T4 异步化 + 锁/原子落盘（必要时再做 LLM 并行） |
| Day 4 | T5 复测 + 填对比表；T6 哈希秒传可选 |
| Day 5 | T7 简历措辞与结果归档 |

**记住顺序：先 Baseline（Mock 下），再优化，再同参数复测。** 没有「优化前」数字、或不声明 Mock，简历上站不住。
