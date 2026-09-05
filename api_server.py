#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
搎端API服务器
提供简历解析、岗位匹配、智能面试等功能
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from resume_parser import ResumeParser
from job_matcher import (
    JobMatcher,
    extract_skills_from_text,
    format_skill_tags,
    load_skill_vocabulary,
)
from interview_agent import InterviewAgent

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 初始化Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
CORS(app)  # 允许跨域请求

# 配置
ROOT_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = ROOT_DIR / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# 初始化服务
resume_parser = ResumeParser()
job_matcher = JobMatcher()
interview_agent = InterviewAgent()
_skill_vocabulary: List[str] = load_skill_vocabulary(ROOT_DIR / "all_labels.csv")
logging.info(f"已加载 {len(_skill_vocabulary)} 个技能词用于岗位技能抽取")

# 内存存储（生产环境应使用数据库）；简历额外落盘，避免重启后匹配全部变成 0%
resumes_store: Dict[str, Dict[str, Any]] = {}
jobs_store: List[Dict[str, Any]] = []
interview_sessions: Dict[str, Dict[str, Any]] = {}
RESUME_STORE_FILE = UPLOAD_FOLDER / "resumes_store.json"

# P4: resumes_store 读写锁 + 原子落盘
_resumes_lock = threading.RLock()

# P1: 简历上传异步任务
_RESUME_UPLOAD_WORKERS = int(os.environ.get("RESUME_UPLOAD_WORKERS", "2"))
_resume_upload_executor = ThreadPoolExecutor(
    max_workers=max(1, _RESUME_UPLOAD_WORKERS),
    thread_name_prefix="resume-upload",
)
resume_tasks: Dict[str, Dict[str, Any]] = {}

# P1/P2: 进程内 jobs 缓存 + 读写锁（按数据文件 mtime 失效）
_jobs_lock = threading.RLock()
_jobs_cache: List[Dict[str, Any]] = []
_jobs_cache_mtime: Optional[float] = None
_jobs_cache_source: str = "none"
_jobs_cache_path: Optional[Path] = None

# Regex for valid UUID-style file IDs (path traversal protection)
_VALID_FILE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

# 列表/匹配响应字段裁剪
_JOB_LIST_FIELDS = (
    "id",
    "title",
    "company",
    "location",
    "required_skills",
    "apply_url",
    "source_url",
    "salary_range",
    "posted_date",
    "job_level1",
    "job_level2",
    "category",
    "min_degree",
)
_DESC_SUMMARY_LEN = 200
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 100
_DEFAULT_TOP_K = 20
_MAX_TOP_K = 100


def _is_safe_file_id(file_id: str) -> bool:
    """Validate file_id to prevent path traversal attacks."""
    return bool(file_id) and _VALID_FILE_ID_RE.match(file_id) is not None


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _load_resumes_from_disk() -> None:
    if not RESUME_STORE_FILE.exists():
        return
    try:
        payload = json.loads(RESUME_STORE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            with _resumes_lock:
                resumes_store.update(payload)
            logging.info(f"已从磁盘恢复 {len(payload)} 份简历")
    except Exception as e:
        logging.warning(f"恢复简历缓存失败: {e}")


def _save_resumes_to_disk_unlocked() -> None:
    """原子落盘；调用方须已持有 _resumes_lock。"""
    try:
        tmp_path = RESUME_STORE_FILE.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(resumes_store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, RESUME_STORE_FILE)
    except Exception as e:
        logging.warning(f"保存简历缓存失败: {e}")


def _save_resumes_to_disk() -> None:
    with _resumes_lock:
        _save_resumes_to_disk_unlocked()


def _set_resume_task(task_id: str, **fields: Any) -> None:
    with _resumes_lock:
        task = resume_tasks.get(task_id)
        if task is None:
            return
        task.update(fields)
        task["updated_at"] = datetime.now().isoformat()


def _run_resume_parse_task(task_id: str, resume_id: str, file_path: str, filename: str) -> None:
    """后台解析简历：LLM/PDF 在锁外执行，写 store 时短临界区。"""
    _set_resume_task(task_id, status="running")
    logging.info(f"开始异步解析简历: task={task_id} path={file_path}")
    try:
        result = resume_parser.parse_resume(file_path)
        resume_data = {
            "id": resume_id,
            "user_id": "default_user",
            "file_name": filename,
            "file_url": f"/api/resume/file/{resume_id}",
            "extracted_info": result["extracted_info"],
            "upload_date": result["upload_date"],
            "status": "completed",
            "skills": result["skills"],
        }
        with _resumes_lock:
            resumes_store[resume_id] = resume_data
            _save_resumes_to_disk_unlocked()
            task = resume_tasks.get(task_id)
            if task is not None:
                task.update(
                    {
                        "status": "completed",
                        "error": None,
                        "resume": {
                            "id": resume_data["id"],
                            "user_id": resume_data["user_id"],
                            "file_name": resume_data["file_name"],
                            "file_url": resume_data["file_url"],
                            "extracted_info": resume_data["extracted_info"],
                            "upload_date": resume_data["upload_date"],
                            "status": resume_data["status"],
                        },
                        "skills": resume_data["skills"],
                        "updated_at": datetime.now().isoformat(),
                    }
                )
        logging.info(f"简历异步解析完成: task={task_id} resume={resume_id}")
    except Exception as e:
        logging.error(f"简历异步解析失败: task={task_id} err={e}", exc_info=True)
        _set_resume_task(task_id, status="failed", error=str(e))


def _enrich_job_skills(job: Dict[str, Any]) -> Dict[str, Any]:
    """Fill required_skills from JD text when LLM skill_tags are missing."""
    skills = [s for s in (job.get("required_skills") or []) if str(s).strip()]
    raw = str(job.get("skill_tags_raw") or "")
    if raw.lower() == "nan":
        raw = ""

    if not skills and raw:
        skills = parse_skill_tags(raw)

    if not skills:
        blob = " ".join(
            [
                str(job.get("title") or ""),
                str(job.get("description") or ""),
                str(job.get("requirements") or ""),
            ]
        )
        skills = extract_skills_from_text(blob, _skill_vocabulary)
        if skills:
            raw = format_skill_tags(skills)

    job["required_skills"] = skills
    job["skill_tags_raw"] = raw
    return job


_load_resumes_from_disk()


def parse_skill_tags(tag_string: str) -> List[str]:
    """解析技能标签字符串，返回技能名称列表"""
    return [name for name, _score in job_matcher.parse_job_skills(tag_string)]


def _resolve_jobs_data_file() -> Tuple[Optional[Path], str]:
    """按优先级解析岗位数据文件路径与来源标识。"""
    enriched_csv = ROOT_DIR / "jobs_enriched.csv"
    if enriched_csv.exists():
        return enriched_csv, "enriched_csv"
    json_file = ROOT_DIR / "all_companies_jobs.json"
    if json_file.exists():
        return json_file, "json"
    bytedance_csv = ROOT_DIR / "bytedance_jobs_enriched.csv"
    if bytedance_csv.exists():
        return bytedance_csv, "bytedance_csv"
    return None, "none"


def _parse_jobs_from_enriched_csv(path: Path) -> List[Dict[str, Any]]:
    df = pd.read_csv(path)
    jobs: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        skill_tags_raw = str(row.get("skill_tags", ""))
        job = {
            "id": str(row.get("job_id", uuid.uuid4())),
            "title": str(row.get("job_title", "")),
            "company": str(row.get("company_name", "")),
            "description": str(row.get("job_description", "")),
            "required_skills": parse_skill_tags(skill_tags_raw),
            "location": str(row.get("location", "")),
            "salary_range": "面议",
            "posted_date": "2024-01-01",
            "job_level1": str(row.get("job_level1", "")),
            "job_level2": str(row.get("job_level2", "")),
            "min_degree": str(row.get("min_degree", "")),
            "degree_priority": str(row.get("degree_priority", "")),
            "major_requirement": str(row.get("major_requirement_text", "")),
            "skill_tags_raw": skill_tags_raw,
            "apply_url": str(row.get("apply_url", "")),
            "source_url": str(row.get("source_url", "")),
            "category": str(row.get("category", "")),
            "requirements": str(row.get("job_requirements", "")),
        }
        jobs.append(_enrich_job_skills(job))
    return jobs


def _parse_jobs_from_json(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        raw_jobs = json.load(f)

    jobs: List[Dict[str, Any]] = []
    for raw in raw_jobs:
        skill_tags_raw = str(raw.get("skill_tags", ""))
        has_enriched = bool(skill_tags_raw and skill_tags_raw != "nan")
        job = {
            "id": str(raw.get("job_id", uuid.uuid4())),
            "title": str(raw.get("job_title", "")),
            "company": str(raw.get("company_name", "")),
            "description": str(raw.get("job_description", "")),
            "required_skills": parse_skill_tags(skill_tags_raw) if has_enriched else [],
            "location": str(raw.get("location", "")),
            "salary_range": "面议",
            "posted_date": "2024-01-01",
            "job_level1": str(raw.get("job_level1", raw.get("job_type", ""))),
            "job_level2": str(raw.get("job_level2", raw.get("special_program", ""))),
            "min_degree": str(raw.get("min_degree", "")),
            "degree_priority": str(raw.get("degree_priority", "")),
            "major_requirement": str(raw.get("major_requirement", "")),
            "skill_tags_raw": skill_tags_raw,
            "apply_url": str(raw.get("apply_url", "")),
            "source_url": str(raw.get("source_url", "")),
            "category": str(raw.get("category", "")),
            "requirements": str(raw.get("job_requirements", "")),
        }
        jobs.append(_enrich_job_skills(job))
    return jobs


def _parse_jobs_from_bytedance_csv(path: Path) -> List[Dict[str, Any]]:
    df = pd.read_csv(path)
    jobs: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        skill_tags_raw = str(row.get("skill_tags", ""))
        job = {
            "id": str(row.get("job_id", uuid.uuid4())),
            "title": str(row.get("job_title", "")),
            "company": str(row.get("company_name", "字节跳动")),
            "description": str(row.get("job_description", "")),
            "required_skills": parse_skill_tags(skill_tags_raw),
            "location": str(row.get("location", "")),
            "salary_range": "面议",
            "posted_date": "2024-01-01",
            "job_level1": str(row.get("job_level1", "")),
            "job_level2": str(row.get("job_level2", "")),
            "min_degree": str(row.get("min_degree", "")),
            "skill_tags_raw": skill_tags_raw,
            "apply_url": str(row.get("apply_url", "")),
            "source_url": str(row.get("source_url", "")),
        }
        jobs.append(_enrich_job_skills(job))
    return jobs


def _parse_jobs_file(path: Path, source: str) -> List[Dict[str, Any]]:
    if source == "enriched_csv":
        return _parse_jobs_from_enriched_csv(path)
    if source == "json":
        return _parse_jobs_from_json(path)
    if source == "bytedance_csv":
        return _parse_jobs_from_bytedance_csv(path)
    return []

# 更新缓存
def _sync_jobs_store(jobs: List[Dict[str, Any]]) -> None:
    """在持锁状态下同步 jobs_store 与缓存列表。"""
    jobs_store.clear()
    jobs_store.extend(jobs)


def _ensure_jobs_loaded() -> Tuple[List[Dict[str, Any]], str]:
    """
    按数据文件 mtime 加载 /复用岗位缓存，并同步 jobs_store。
    必须在短临界区内完成读缓存 / 写缓存 / 写 jobs_store。
    """
    global _jobs_cache, _jobs_cache_mtime, _jobs_cache_source, _jobs_cache_path

    with _jobs_lock:
        # 加载工作信息文件
        path, source = _resolve_jobs_data_file()
        if path is None:
            _jobs_cache = []
            _jobs_cache_mtime = None
            _jobs_cache_source = "none"
            _jobs_cache_path = None
            _sync_jobs_store([])
            return [], "none"
        # 当前这次请求里，刚从磁盘查到的文件修改时间
        mtime = path.stat().st_mtime
        if (
            _jobs_cache
            # 磁盘上这份岗位文件现在的修改时间 vs 上次装进缓存时记下的修改时间，如果不一致则更新缓存_jobs_cache_mtime
            and _jobs_cache_mtime == mtime
            and _jobs_cache_source == source
            and _jobs_cache_path == path
        ):
            return _jobs_cache, source

        logging.info(f"从数据文件加载岗位: {path} (source={source})")
        # 解析缓存
        jobs = _parse_jobs_file(path, source)
        _jobs_cache = jobs
        # _jobs_cache_mtime：上次把岗位装进 _jobs_cache 时，记下的那个修改时间
        _jobs_cache_mtime = mtime
        _jobs_cache_source = source
        _jobs_cache_path = path
        _sync_jobs_store(jobs)
        logging.info(f"加载了 {len(jobs)} 个岗位 (source={source})")
        return jobs, source


def _trim_job_for_list(job: Dict[str, Any]) -> Dict[str, Any]:
    """列表响应字段裁剪：不含完整 description/requirements。"""
    item = {field: job.get(field) for field in _JOB_LIST_FIELDS}
    desc = str(job.get("description") or "")
    item["description_summary"] = desc[:_DESC_SUMMARY_LEN]
    skills = item.get("required_skills") or []
    if isinstance(skills, list) and len(skills) > 30:
        item["required_skills"] = skills[:30]
    return item


def _trim_job_for_match(job: Dict[str, Any]) -> Dict[str, Any]:
    """匹配结果内嵌岗位字段裁剪。"""
    return {
        "id": job.get("id"),
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "required_skills": (job.get("required_skills") or [])[:30],
        "apply_url": job.get("apply_url", ""),
        "source_url": job.get("source_url", ""),
        "salary_range": job.get("salary_range", ""),
    }


def _trim_match_result(match: Dict[str, Any]) -> Dict[str, Any]:
    """匹配结果字段精简，去掉超长 JD。"""
    details = match.get("match_details") or {}
    job = match.get("job") or {}
    return {
        "job_id": match.get("job_id"),
        "match_score": match.get("match_score"),
        "matched_skills": match.get("matched_skills") or [],
        "missing_skills": match.get("missing_skills") or [],
        "match_details": {
            "match_count": details.get("match_count"),
            "total_job_skills": details.get("total_job_skills"),
            "match_rate": details.get("match_rate"),
            "avg_resume_score": details.get("avg_resume_score"),
        },
        "job": _trim_job_for_match(job) if isinstance(job, dict) else job,
    }


def _parse_bool_flag(raw: Optional[str]) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({'status': 'ok', 'message': 'API server is running'})


@app.route('/api/resume/upload', methods=['POST'])
def upload_resume():
    """上传简历：立即受理，后台异步解析（P1）。"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'Only PDF files are allowed'}), 400

        filename = secure_filename(file.filename)
        resume_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        file_path = UPLOAD_FOLDER / f"{resume_id}_{filename}"
        file.save(str(file_path))

        now = datetime.now().isoformat()
        with _resumes_lock:
            resume_tasks[task_id] = {
                "task_id": task_id,
                "resume_id": resume_id,
                "status": "pending",
                "error": None,
                "file_name": filename,
                "created_at": now,
                "updated_at": now,
                "resume": None,
                "skills": None,
            }

        _resume_upload_executor.submit(
            _run_resume_parse_task,
            task_id,
            resume_id,
            str(file_path),
            filename,
        )
        logging.info(f"简历上传已受理: task={task_id} resume={resume_id}")

        return jsonify({
            "task_id": task_id,
            "resume_id": resume_id,
            "status": "pending",
            "message": "Upload accepted; poll GET /api/resume/task/<task_id>",
        }), 202

    except Exception as e:
        logging.error(f"简历上传受理失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/resume/task/<task_id>', methods=['GET'])
def get_resume_task(task_id: str):
    """查询简历异步解析任务状态（P1）。"""
    if not _is_safe_file_id(task_id):
        return jsonify({'error': 'Invalid task ID'}), 400
    with _resumes_lock:
        task = resume_tasks.get(task_id)
        if task is None:
            return jsonify({'error': 'Task not found'}), 404
        payload = dict(task)
    return jsonify(payload), 200


@app.route('/api/resume/file/<file_id>', methods=['GET'])
def get_resume_file(file_id: str):
    """获取箠历文件"""
    try:
        if not _is_safe_file_id(file_id):
            return jsonify({'error': 'Invalid file ID'}), 400
        # 查找文件
        for file_path in UPLOAD_FOLDER.glob(f"{file_id}_*"):
            if file_path.is_file():
                return send_file(str(file_path), mimetype='application/pdf')
        return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        logging.error(f"获取文件失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/resume/<resume_id>', methods=['GET'])
def get_resume(resume_id: str):
    """获取箠历详情"""
    with _resumes_lock:
        if resume_id not in resumes_store:
            return jsonify({'error': 'Resume not found'}), 404
        resume_data = dict(resumes_store[resume_id])
        skills = list(resume_data.get('skills') or [])

    return jsonify({
        'resume': {
            'id': resume_data['id'],
            'user_id': resume_data['user_id'],
            'file_name': resume_data['file_name'],
            'file_url': resume_data['file_url'],
            'extracted_info': resume_data['extracted_info'],
            'upload_date': resume_data['upload_date'],
            'status': resume_data['status']
        },
        'skills': skills
    }), 200


@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    """获取岗位列表（默认分页 + 字段裁剪；?all=1 返回较全字段）

    数据加载优先级：
    1. jobs_enriched.csv - 流水线智能分析后的完整数据（含技能评分）
    2. all_companies_jobs.json - 爬取的JSON数据（可能含技能标签）
    3. bytedance_jobs_enriched.csv - 原始字节跳动数据
    """
    try:
        jobs, data_source = _ensure_jobs_loaded()
        if data_source == "none":
            return jsonify({
                "jobs": [],
                "total": 0,
                "page": 1,
                "page_size": _DEFAULT_PAGE_SIZE,
                "message": "No jobs file found",
                "data_source": data_source,
            }), 200

        return_all = _parse_bool_flag(request.args.get("all"))
        if return_all:
            with _jobs_lock:
                snapshot = list(jobs)
            return jsonify({
                "jobs": snapshot,
                "total": len(snapshot),
                "page": 1,
                "page_size": len(snapshot),
                "data_source": data_source,
            }), 200

        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.args.get("page_size", _DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError):
            page_size = _DEFAULT_PAGE_SIZE

        page = max(1, page)
        page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

        with _jobs_lock:
            total = len(jobs)
            start = (page - 1) * page_size
            end = start + page_size
            page_jobs = jobs[start:end]
            trimmed = [_trim_job_for_list(job) for job in page_jobs]

        return jsonify({
            "jobs": trimmed,
            "total": total,
            "page": page,
            "page_size": page_size,
            "data_source": data_source,
        }), 200

    except Exception as e:
        logging.error(f"加载岗位失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/jobs/match', methods=['POST'])
def match_jobs():
    """岗位匹配（全量计分，仅返回 top_k）"""
    try:
        data = request.json or {}
        resume_id = data.get('resume_id')

        with _resumes_lock:
            if not resume_id or resume_id not in resumes_store:
                return jsonify({'error': 'Resume not found'}), 404
            resume_skills = list(resumes_store[resume_id].get('skills') or [])

        try:
            top_k = int(data.get("top_k", _DEFAULT_TOP_K))
        except (TypeError, ValueError):
            top_k = _DEFAULT_TOP_K
        top_k = max(1, min(top_k, _MAX_TOP_K))

        # 确保 jobs_store / 缓存已加载（锁内短临界区）
        jobs, _data_source = _ensure_jobs_loaded()
        with _jobs_lock:
            jobs_snapshot = list(jobs)

        # 匹配岗位（已经按匹配度排序）；锁外计算，避免长时间持锁
        matches = job_matcher.match_jobs(resume_skills, jobs_snapshot)
        total_matched = len(matches)
        top_matches = [_trim_match_result(m) for m in matches[:top_k]]

        return jsonify({
            'matches': top_matches,
            'resume_id': resume_id,
            'top_k': top_k,
            'total_matched': total_matched,
            'candidate_total': len(jobs_snapshot),
        }), 200

    except Exception as e:
        logging.error(f"岗位匹配失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/interview/start', methods=['POST'])
def start_interview():
    """开始面试"""
    try:
        data = request.json
        resume_id = data.get('resume_id')
        job_id = data.get('job_id')

        with _resumes_lock:
            if resume_id and resume_id not in resumes_store:
                return jsonify({'error': 'Resume not found'}), 404
            resume_data = dict(resumes_store[resume_id]) if resume_id else None

        session_id = str(uuid.uuid4())

        with _jobs_lock:
            if not jobs_store:
                _ensure_jobs_loaded()
            job_data = next((j for j in jobs_store if j['id'] == job_id), None) if job_id else None

        # 初始化面试会话
        interview_sessions[session_id] = {
            'id': session_id,
            'resume_id': resume_id,
            'job_id': job_id,
            'started_at': datetime.now().isoformat(),
            'status': 'active',
            'messages': [],
            'stage': 'greeting',  # 当前阶段：greeting -> qa -> summary
            'phase': 'greeting',
            'qa_count': 0,
            'max_qa': 5
        }

        # 生成开场白（不出题）
        start_result = interview_agent.start_interview(resume_data, job_data)

        # 添加开场白消息（仅开场白与自我介绍提示）
        greeting_msg = {
            'id': str(uuid.uuid4()),
            'session_id': session_id,
            'role': 'assistant',
            'content': f"{start_result.get('greeting', '')}\n\n{start_result.get('self_intro', '')}",
            'created_at': datetime.now().isoformat(),
            'question': None,
            'stage': start_result.get('stage', 'greeting')
        }
        interview_sessions[session_id]['messages'].append(greeting_msg)
        interview_sessions[session_id]['stage'] = start_result.get('stage', 'greeting')
        interview_sessions[session_id]['phase'] = start_result.get('stage', 'greeting')

        return jsonify({
            'session_id': session_id,
            'message': f"{start_result.get('greeting', '')}\n\n{start_result.get('self_intro', '')}",
            'question': None,
            'stage': start_result.get('stage', 'greeting')
        }), 200

    except Exception as e:
        logging.error(f"开始面试失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/interview/<session_id>/message', methods=['POST'])
def send_interview_message(session_id: str):
    """发送面试消息"""
    try:
        if session_id not in interview_sessions:
            return jsonify({'error': 'Session not found'}), 404

        data = request.json
        user_message = data.get('message', '')

        if not user_message:
            return jsonify({'error': 'Message is required'}), 400

        session = interview_sessions[session_id]
        current_stage = session.get('stage', 'greeting')

        # 添加用户消息
        user_msg = {
            'id': str(uuid.uuid4()),
            'session_id': session_id,
            'role': 'user',
            'content': user_message,
            'created_at': datetime.now().isoformat()
        }
        session['messages'].append(user_msg)

        # 获取简历和岗位信息
        with _resumes_lock:
            rid = session.get('resume_id')
            resume_data = dict(resumes_store[rid]) if rid and rid in resumes_store else None
        with _jobs_lock:
            job_data = next((j for j in jobs_store if j['id'] == session['job_id']), None) if session['job_id'] else None

        # 生成AI回复
        response = interview_agent.respond(
            user_message,
            session['messages'],
            resume_data,
            job_data,
            session_state=session
        )

        # 更新阶段与计数器
        new_phase = response.get('phase', session.get('phase', current_stage))
        session['phase'] = new_phase
        session['stage'] = new_phase
        if 'qa_count' in response:
            session['qa_count'] = response['qa_count']

        # 构建回复内容
        reply_content = response.get('message', '')

        # 添加AI回复
        assistant_msg = {
            'id': str(uuid.uuid4()),
            'session_id': session_id,
            'role': 'assistant',
            'content': reply_content,
            'created_at': datetime.now().isoformat(),
            'question': response.get('question'),
            'evaluation': response.get('evaluation'),
            'stage': new_phase
        }
        session['messages'].append(assistant_msg)

        return jsonify({
            'message': reply_content,
            'session_id': session_id,
            'stage': new_phase,
            'question': response.get('question'),
            'evaluation': response.get('evaluation'),
            'final_feedback': response.get('final_feedback'),
            'average_score': response.get('average_score')
        }), 200

    except Exception as e:
        logging.error(f"发送消息失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/interview/<session_id>', methods=['GET'])
def get_interview_session(session_id: str):
    """获取面试会话"""
    if session_id not in interview_sessions:
        return jsonify({'error': 'Session not found'}), 404

    session = interview_sessions[session_id]
    return jsonify({
        'session': {
            'id': session['id'],
            'resume_id': session['resume_id'],
            'job_id': session['job_id'],
            'started_at': session['started_at'],
            'status': session['status']
        },
        'messages': session['messages']
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug)
