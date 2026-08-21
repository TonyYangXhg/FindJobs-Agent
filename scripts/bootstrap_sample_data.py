#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate local sample data so resume scoring and job matching can run."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXTRA = {
    "算法": [
        "Python", "机器学习", "深度学习", "PyTorch", "TensorFlow",
        "算法", "数据结构", "NLP", "计算机视觉", "推荐系统", "SQL",
    ],
    "后端": [
        "Java", "Python", "Go", "Spring Boot", "MySQL", "Redis",
        "Kafka", "Docker", "Kubernetes", "微服务", "Linux",
    ],
    "前端": [
        "JavaScript", "TypeScript", "React", "Vue", "HTML", "CSS",
        "Webpack", "Node.js", "前端工程化",
    ],
    "数据": [
        "SQL", "Python", "数据分析", "Hive", "Spark",
        "数据仓库", "ETL", "Tableau", "Excel",
    ],
    "产品": [
        "产品设计", "需求分析", "原型设计", "用户研究",
        "Axure", "Figma", "数据分析",
    ],
    "测试": [
        "测试", "自动化测试", "Selenium", "接口测试",
        "性能测试", "Python", "Java",
    ],
    "运维": ["Linux", "Docker", "Kubernetes", "CI/CD", "Shell", "监控", "Nginx"],
    "硬件": ["C++", "嵌入式", "Linux", "单片机", "驱动开发"],
}


def main() -> None:
    tax = json.loads((ROOT / "tech_taxonomy.json").read_text(encoding="utf-8"))

    rows = []
    all_job_skills = []
    for cat in tax.get("level1_categories", []):
        l1 = cat["name"]
        extras = EXTRA.get(l1, ["沟通协作", "项目管理"])
        for role in cat.get("level2_roles", []):
            name = role["name"]
            kws = role.get("keywords", [])
            tags = list(dict.fromkeys([*kws, *extras]))
            rows.append({"level_3rd": name, "tags": "|_|".join(tags)})
            all_job_skills.append((l1, name, tags))

    labels_path = ROOT / "all_labels.csv"
    with labels_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["level_3rd", "tags"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {labels_path.name}: {len(rows)} rows")

    companies = ["字节跳动", "腾讯", "阿里巴巴", "美团", "网易", "华为", "百度", "京东"]
    locations = ["北京", "上海", "深圳", "杭州", "广州"]
    jobs = []
    for i, (l1, title, tags) in enumerate(all_job_skills[:40]):
        skill_tags = " | ".join(
            f"{t} , {min(5, 3 + (j % 3))} , AI" for j, t in enumerate(tags[:8])
        )
        tag_preview = "、".join(tags[:5])
        jobs.append(
            {
                "job_id": f"sample_{i + 1:03d}",
                "job_title": title,
                "company_name": companies[i % len(companies)],
                "job_description": (
                    f"负责{title}相关工作，参与核心业务系统建设与优化。"
                    "要求具备扎实的专业基础和良好的团队协作能力。"
                ),
                "job_requirements": (
                    f"熟悉{tag_preview}；本科及以上学历；有相关项目经验优先。"
                ),
                "location": locations[i % len(locations)],
                "skill_tags": skill_tags,
                "job_level1": l1,
                "job_level2": title,
                "min_degree": "本科",
                "degree_priority": "硕士优先",
                "major_requirement": "计算机相关专业",
                "apply_url": "https://jobs.bytedance.com/",
                "source_url": "https://jobs.bytedance.com/",
                "category": l1,
            }
        )

    jobs_path = ROOT / "all_companies_jobs.json"
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {jobs_path.name}: {len(jobs)} jobs")


if __name__ == "__main__":
    main()
