import type { RunSummary, RunDetail, TaskResult, CompareRunEntry, CreateJobRequest, JobStatus, SandboxInfo, DeployJob, DeployAndRunRequest } from './types';

const BASE = '/api';

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.text();
    let msg = `API error ${res.status}`;
    try { msg = JSON.parse(body).detail ?? msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export function listRuns(): Promise<RunSummary[]> {
  return fetchJson<RunSummary[]>(`${BASE}/runs`);
}

export function getRun(runId: string): Promise<RunDetail> {
  return fetchJson<RunDetail>(`${BASE}/runs/${encodeURIComponent(runId)}`);
}

export function getRunConfig(runId: string): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(`${BASE}/runs/${encodeURIComponent(runId)}/config`);
}

export function getTask(runId: string, taskId: string): Promise<TaskResult> {
  return fetchJson<TaskResult>(
    `${BASE}/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}`
  );
}

export function compareRuns(runIds: string[]): Promise<CompareRunEntry[]> {
  return fetchJson<CompareRunEntry[]>(
    `${BASE}/compare?runs=${runIds.map(encodeURIComponent).join(',')}`
  );
}

export function listJobs(): Promise<JobStatus[]> {
  return fetchJson<JobStatus[]>(`${BASE}/jobs`);
}

export function createJob(req: CreateJobRequest): Promise<JobStatus> {
  return fetchJson<JobStatus>(`${BASE}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export function getJob(jobId: string): Promise<JobStatus> {
  return fetchJson<JobStatus>(`${BASE}/jobs/${jobId}`);
}

export function cancelJob(jobId: string): Promise<void> {
  return fetchJson(`${BASE}/jobs/${jobId}/cancel`, { method: 'POST' });
}

export function getJobLogs(jobId: string): Promise<{ job_id: string; logs: string }> {
  return fetchJson(`${BASE}/jobs/${jobId}/logs`);
}

export function resumeJob(jobId: string): Promise<JobStatus> {
  return fetchJson<JobStatus>(`${BASE}/jobs/${jobId}/resume`, { method: 'POST' });
}

export function deleteJob(jobId: string): Promise<{ status: string; job_id: string }> {
  return fetchJson(`${BASE}/jobs/${jobId}`, { method: 'DELETE' });
}

export function deleteRun(runId: string): Promise<{ run_id: string; jobs_removed: number; files_removed: string[] }> {
  return fetchJson(`${BASE}/runs/${encodeURIComponent(runId)}?confirm=true`, { method: 'DELETE' });
}

// --- Sandbox APIs ---

export function listSandboxes(): Promise<SandboxInfo[]> {
  return fetchJson<SandboxInfo[]>(`${BASE}/sandboxes`);
}

export function probeSandbox(sandboxId: string): Promise<{ gateway_ready: boolean; model: string }> {
  return fetchJson(`${BASE}/sandboxes/${encodeURIComponent(sandboxId)}/probe`);
}

export function deploySandbox(params: {
  model_api_base?: string;
  model_api_key?: string;
  model_name?: string;
  auto_clear_seconds?: number;
}): Promise<DeployJob> {
  const qs = new URLSearchParams();
  if (params.model_api_base) qs.set('model_api_base', params.model_api_base);
  if (params.model_api_key) qs.set('model_api_key', params.model_api_key);
  if (params.model_name) qs.set('model_name', params.model_name);
  if (params.auto_clear_seconds) qs.set('auto_clear_seconds', String(params.auto_clear_seconds));
  return fetchJson<DeployJob>(`${BASE}/sandboxes/deploy?${qs.toString()}`, { method: 'POST' });
}

export function getDeployStatus(deployId: string): Promise<DeployJob> {
  return fetchJson<DeployJob>(`${BASE}/sandboxes/deploy/${deployId}`);
}

export function getDeployLogs(deployId: string): Promise<{ deploy_id: string; logs: string }> {
  return fetchJson(`${BASE}/sandboxes/deploy/${deployId}/logs`);
}

export function listEnvKeys(): Promise<{ keys: string[] }> {
  return fetchJson(`${BASE}/env-keys`);
}

export function deployAndRun(req: DeployAndRunRequest): Promise<{ job_id: string; run_id: string; deploy_id: string; status: string }> {
  return fetchJson(`${BASE}/jobs/deploy-and-run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}
