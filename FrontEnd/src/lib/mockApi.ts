import type { JobMatch, JobPosition, Resume, ResumeSkill } from '../types';

const API_BASE = (import.meta.env.VITE_API_URL || '/api').replace(/\/$/, '');

type UploadResumeResponse = {
  resume: Resume;
  skills: ResumeSkill[];
};

type JobsResponse = {
  jobs: JobPosition[];
};

type JobMatchResponse = {
  matches: JobMatch[];
};

type StartInterviewResponse = {
  session_id: string;
  message: string;
};

type InterviewMessageResponse = {
  message: string;
};

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const message =
      typeof body?.error === 'string'
        ? body.error
        : typeof body?.message === 'string'
          ? body.message
          : `Request failed with ${response.status}`;
    throw new Error(message);
  }

  return body as T;
}

export async function simulateResumeUpload(file: File): Promise<UploadResumeResponse> {
  const form = new FormData();
  form.append('file', file);

  const response = await fetch(`${API_BASE}/resume/upload`, {
    method: 'POST',
    body: form,
  });

  // 异步受理：202 + task_id，轮询直到 completed/failed
  if (response.status === 202) {
    const accepted = await response.json();
    const taskId = accepted?.task_id;
    if (!taskId) {
      throw new Error('Upload accepted but missing task_id');
    }

    const started = Date.now();
    const timeoutMs = 5 * 60 * 1000;
    while (Date.now() - started < timeoutMs) {
      const taskResp = await fetch(`${API_BASE}/resume/task/${taskId}`);
      const task = await readJson<{
        status: string;
        error?: string;
        resume?: Resume;
        skills?: ResumeSkill[];
      }>(taskResp);

      if (task.status === 'completed' && task.resume) {
        return {
          resume: task.resume,
          skills: task.skills || [],
        };
      }
      if (task.status === 'failed') {
        throw new Error(task.error || 'Resume parse failed');
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error('Resume parse timed out');
  }

  return readJson<UploadResumeResponse>(response);
}

export async function getJobs(): Promise<JobPosition[]> {
  // 前端仍做全量筛选/展示 description，走 ?all=1；压测默认路径为分页裁剪
  const response = await fetch(`${API_BASE}/jobs?all=1`);
  const data = await readJson<JobsResponse>(response);
  return data.jobs;
}

export async function simulateJobMatching(resumeId: string): Promise<JobMatch[]> {
  const response = await fetch(`${API_BASE}/jobs/match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // 上限 100，便于列表页按匹配分排序；API 默认 top_k=20 供压测/轻量调用
    body: JSON.stringify({ resume_id: resumeId, top_k: 100 }),
  });
  const data = await readJson<JobMatchResponse>(response);
  return data.matches;
}

export async function startInterview(
  resumeId: string | undefined,
  jobId: string | undefined,
): Promise<{ sessionId: string; message: string }> {
  const response = await fetch(`${API_BASE}/interview/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resume_id: resumeId, job_id: jobId }),
  });
  const data = await readJson<StartInterviewResponse>(response);
  return { sessionId: data.session_id, message: data.message };
}

export async function simulateInterviewChat(
  sessionId: string,
  message: string,
): Promise<string> {
  const response = await fetch(`${API_BASE}/interview/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  const data = await readJson<InterviewMessageResponse>(response);
  return data.message;
}
