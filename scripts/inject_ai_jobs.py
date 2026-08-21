#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inject LLM/Agent sample jobs into all_companies_jobs.json without touching all_labels.csv."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_PATH = ROOT / "all_companies_jobs.json"

AI_JOBS = [
    ("大语言模型工程师", ["LLM", "大语言模型", "Transformer", "微调", "LoRA", "PyTorch", "Python", "Prompt Engineering"]),
    ("AI Agent工程师", ["Agent", "AI Agent", "LLM", "LangChain", "LangGraph", "MCP", "Tool Calling", "Python"]),
    ("Agent开发工程师", ["Agent", "智能体", "Function Calling", "MCP", "LangChain", "LLM", "Python", "API"]),
    ("RAG工程师", ["RAG", "向量数据库", "Embedding", "FAISS", "Milvus", "LangChain", "LLM", "Python"]),
    ("Prompt工程师", ["Prompt Engineering", "LLM", "Agent", "Few-shot", "Chain-of-Thought", "LangChain", "Python", "评测"]),
    ("LLM应用工程师", ["LLM", "RAG", "Agent", "LangChain", "OpenAI API", "FastAPI", "Python", "向量数据库"]),
    ("AI应用开发工程师", ["AI应用", "LLM", "Agent", "RAG", "多模态", "Python", "FastAPI", "Prompt Engineering"]),
    ("多模态算法工程师", ["多模态", "VLM", "CLIP", "LLM", "Transformer", "PyTorch", "深度学习", "Python"]),
    ("模型推理优化工程师", ["推理优化", "vLLM", "量化", "LLM", "CUDA", "PyTorch", "Kubernetes", "Python"]),
    ("AI平台工程师", ["AI平台", "LLMOps", "模型部署", "LLM", "Kubernetes", "Docker", "Python", "GPU"]),
]


def main() -> None:
    jobs = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = [j for j in jobs if not str(j.get("job_id", "")).startswith("ai_")]

    companies = ["字节跳动", "腾讯", "阿里巴巴", "美团", "网易", "华为", "百度", "京东", "商汤", "月之暗面"]
    locations = ["北京", "上海", "深圳", "杭州", "广州"]

    new_jobs = []
    for i, (title, tags) in enumerate(AI_JOBS):
        skill_tags = " | ".join(
            f"{t} , {min(5, 3 + (j % 3))} , AI" for j, t in enumerate(tags)
        )
        preview = "、".join(tags[:5])
        new_jobs.append(
            {
                "job_id": f"ai_{i + 1:03d}",
                "job_title": title,
                "company_name": companies[i % len(companies)],
                "job_description": (
                    f"负责{title}相关研发与落地，参与大模型/Agent 核心能力建设与业务应用。"
                ),
                "job_requirements": (
                    f"熟悉{preview}；本科及以上学历；有相关项目经验优先。"
                ),
                "location": locations[i % len(locations)],
                "skill_tags": skill_tags,
                "job_level1": "算法",
                "job_level2": title,
                "min_degree": "本科",
                "degree_priority": "硕士优先",
                "major_requirement": "计算机/人工智能相关专业",
                "apply_url": "https://jobs.bytedance.com/",
                "source_url": "https://jobs.bytedance.com/",
                "category": "算法",
            }
        )

    boost = ["LLM", "Agent", "RAG", "Prompt Engineering"]
    for job in jobs:
        title = job.get("job_title", "")
        if any(k in title for k in ["自然语言", "机器学习", "深度学习", "算法研究"]):
            existing = job.get("skill_tags", "")
            for b in boost:
                if b not in existing:
                    existing = f"{b} , 5 , AI | " + existing
            job["skill_tags"] = existing

    jobs = new_jobs + jobs
    JOBS_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {JOBS_PATH.name}: {len(jobs)} jobs ({len(new_jobs)} AI jobs)")


if __name__ == "__main__":
    main()
