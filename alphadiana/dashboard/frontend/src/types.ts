export interface RunSummary {
  run_id: string;
  agent: string;
  agent_version: string;
  benchmark: string;
  total_tasks: number;
  completed: number;
  failed: number;
  accuracy: number;
  accuracy_total: number;
  mean_score: number;
  mean_wall_time_sec: number;
  total_tokens: { prompt_tokens: number; completion_tokens: number };
  per_category: Record<string, number>;
  error_distribution: Record<string, number>;
  model: string;
  num_samples: number;
  pass_at_k: number;
  avg_at_k: number;
  per_category_pass_at_k: Record<string, number>;
  per_category_avg_at_k: Record<string, number>;
  timestamp: string;
}

export interface TaskResult {
  task_id: string;
  sample_index: number;
  problem: string;
  ground_truth: unknown;
  predicted: unknown;
  correct: boolean | null;
  score: number | null;
  rationale: string;
  wall_time_sec: number;
  token_usage: { prompt_tokens?: number; completion_tokens?: number };
  trajectory: TrajectoryStep[];
  timestamp: string;
  error: Record<string, unknown> | null;
  task_metadata: Record<string, unknown>;
  finish_reason: string;
}

export interface TrajectoryStep {
  role: string;
  content: string;
  thinking?: string;
  tool_calls?: ToolCall[];
}

export interface ToolCall {
  id: string;
  tool: string;
  input: Record<string, unknown>;
}

export interface RunDetail {
  summary: RunSummary;
  config: Record<string, unknown> | null;
  results: TaskResult[];
}

export interface CompareRunEntry {
  run_id: string;
  summary: RunSummary;
  results_by_task: Record<string, TaskResult>;
}

export interface CreateJobRequest {
  run_id?: string;
  agent_name: string;
  agent_version?: string;
  agent_config: Record<string, unknown>;
  benchmark_name: string;
  benchmark_config: Record<string, unknown>;
  scorer_name?: string;
  scorer_config: Record<string, unknown>;
  sandbox_name?: string | null;
  sandbox_config: Record<string, unknown>;
  max_concurrent: number;
  redo_all: boolean;
  num_samples: number;
  metadata: Record<string, unknown>;
}

export interface JobStatus {
  job_id: string;
  run_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  agent: string;
  benchmark: string;
  model: string;
  progress: number;
  total_tasks: number;
  accuracy: number | null;
  error: string | null;
  created_at: string;
  original_request: Record<string, unknown> | null;
}

export interface SandboxInfo {
  sandbox_id: string;
  status: string;
  api_base: string;
  model: string;
  created_at: string;
  gateway_ready: boolean;
}

export interface DeployJob {
  deploy_id: string;
  status: 'pending' | 'deploying' | 'completed' | 'failed';
  sandbox_id: string;
  api_base: string;
  error: string;
  created_at: string;
}

export interface DeployAndRunRequest {
  model_api_base?: string;
  model_api_key?: string;
  model_name?: string;
  auto_clear_seconds?: number;
  run_id?: string;
  agent_version?: string;
  agent_config?: Record<string, unknown>;
  benchmark_name: string;
  benchmark_config: Record<string, unknown>;
  scorer_name?: string;
  scorer_config?: Record<string, unknown>;
  max_concurrent?: number;
  num_sandboxes?: number;
  redo_all?: boolean;
  num_samples?: number;
  metadata?: Record<string, unknown>;
}
