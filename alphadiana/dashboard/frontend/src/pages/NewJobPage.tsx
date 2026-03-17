import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card,
  Form,
  Input,
  Select,
  InputNumber,
  Button,
  Switch,
  Divider,
  Row,
  Col,
  Typography,
  message,
  Alert,
  Radio,
  Spin,
  Tag,
  Drawer,
  Space,
  Tooltip,
} from 'antd';
import {
  ArrowLeftOutlined,
  PlayCircleOutlined,
  CloudServerOutlined,
  PlusOutlined,
  ReloadOutlined,
  FileTextOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import type { CreateJobRequest, SandboxInfo, DeployJob, DeployAndRunRequest } from '../types';
import { createJob, getRun, getRunConfig, listSandboxes, probeSandbox, deploySandbox, getDeployStatus, getDeployLogs, deployAndRun, listEnvKeys } from '../api';

const { Title, Text } = Typography;

// --- Agent definitions ---

const AGENTS = [
  {
    value: 'direct_llm',
    label: 'Direct LLM',
    description: 'Single-turn LLM API call, no tool use',
    fields: [
      { name: 'model', label: 'Model', required: true, placeholder: 'e.g. moonshotai/kimi-k2.5' },
      { name: 'api_base', label: 'API Base URL', required: true, placeholder: 'e.g. https://openrouter.ai/api/v1/' },
      { name: 'api_key', label: 'API Key', required: true, placeholder: 'API key', password: true },
      { name: 'temperature', label: 'Temperature', type: 'number' as const, default: 0.6 },
      { name: 'max_tokens', label: 'Max Tokens', type: 'number' as const, default: 32768 },
    ],
  },
  {
    value: 'openclaw',
    label: 'OpenClaw',
    description: 'Multi-turn agent with tool calling in ROCK sandbox',
    fields: [
      // OpenClaw agent fields are handled by a custom sandbox selector UI
      { name: 'temperature', label: 'Temperature', type: 'number' as const, default: 0.7 },
      { name: 'max_tokens', label: 'Max Tokens', type: 'number' as const, default: 32768 },
      { name: 'max_attempts', label: 'Max Retry Attempts', type: 'number' as const, default: 3 },
    ],
  },
];

// --- Benchmark presets: each preset = benchmark + dataset config bundled ---

interface BenchmarkPreset {
  value: string;
  label: string;
  description: string;
  benchmarkName: string;
  defaultScorer: string;
  benchmarkConfig: Record<string, string>;
}

const BENCHMARK_PRESETS: BenchmarkPreset[] = [
  // --- AIME ---
  {
    value: 'aime2026',
    label: 'AIME 2026',
    description: 'AIME 2026 Full (30 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'MathArena/aime_2026',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'aime2025',
    label: 'AIME 2025',
    description: 'AIME 2025 Full (30 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'MathArena/aime_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'aime2024',
    label: 'AIME 2024',
    description: 'AIME 2024 Full (30 problems, I + II)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'HuggingFaceH4/aime_2024',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  // --- Other competitions ---
  {
    value: 'hmmt_feb_2026',
    label: 'HMMT Feb 2026',
    description: 'Harvard-MIT Math Tournament February 2026 (33 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/hmmt_feb_2026',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'hmmt_nov_2025',
    label: 'HMMT Nov 2025',
    description: 'Harvard-MIT Math Tournament November 2025 (30 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/hmmt_nov_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'hmmt_feb_2025',
    label: 'HMMT Feb 2025',
    description: 'Harvard-MIT Math Tournament February 2025 (30 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/hmmt_feb_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'smt2025',
    label: 'SMT 2025',
    description: 'Stanford Math Tournament 2025 (53 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/smt_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'cmimc2025',
    label: 'CMIMC 2025',
    description: 'Carnegie Mellon Informatics & Math Competition 2025 (40 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/cmimc_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'brumo2025',
    label: 'BRUMO 2025',
    description: 'Bulgarian Math Olympiad 2025 (30 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'math_verify',
    benchmarkConfig: {
      dataset: 'MathArena/brumo_2025',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'custom',
    label: 'Custom HF Dataset',
    description: 'Load a HuggingFace dataset with problem + answer fields (numeric scorer)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {},
  },
];

const SCORERS = [
  { value: 'numeric', label: 'Numeric', description: 'Compare numeric values with tolerance' },
  { value: 'math_verify', label: 'Math Verify', description: 'Symbolic equivalence via SymPy (handles LaTeX, fractions, expressions)' },
  { value: 'exact_match', label: 'Exact Match', description: 'Math-aware string comparison' },
  { value: 'llm_judge', label: 'LLM Judge', description: 'Use LLM to evaluate correctness' },
];

// Known API base URL -> env var name mappings
const API_BASE_ENV_MAP: { pattern: string; envVar: string }[] = [
  { pattern: 'openrouter.ai', envVar: 'OPENROUTER_API_KEY' },
  { pattern: 'api.openai.com', envVar: 'OPENAI_API_KEY' },
  { pattern: 'siliconflow.cn', envVar: 'SILICONFLOW_API_KEY' },
  { pattern: 'volces.com', envVar: 'ARK_API_KEY' },
  { pattern: 'volcengine.com', envVar: 'ARK_API_KEY' },
  { pattern: 'api.deepseek.com', envVar: 'DEEPSEEK_API_KEY' },
  { pattern: 'api.anthropic.com', envVar: 'ANTHROPIC_API_KEY' },
  { pattern: 'api.moonshot.cn', envVar: 'MOONSHOT_API_KEY' },
  { pattern: 'dashscope.aliyuncs.com', envVar: 'DASHSCOPE_API_KEY' },
  { pattern: 'api.together.xyz', envVar: 'TOGETHER_API_KEY' },
];

function suggestEnvVar(apiBase: string): string {
  if (!apiBase) return '';
  const lower = apiBase.toLowerCase();
  for (const { pattern, envVar } of API_BASE_ENV_MAP) {
    if (lower.includes(pattern)) return envVar;
  }
  return '';
}

const API_KEY_TOOLTIP = (
  <span>
    Two modes supported:<br />
    <b>1. Direct key</b>: paste your API key directly<br />
    <b>2. Env variable</b>: type <code>$VAR_NAME</code> (e.g. <code>$OPENROUTER_API_KEY</code>) to reference a variable from the server&apos;s <code>.env</code> file or system environment
  </span>
);

export default function NewJobPage() {
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string>('direct_llm');
  const [selectedPreset, setSelectedPreset] = useState<string>('aime2025');
  const [redoAll, setRedoAll] = useState(false);

  // OpenClaw sandbox state
  const [sandboxMode, setSandboxMode] = useState<'existing' | 'new'>('existing');
  const [sandboxes, setSandboxes] = useState<SandboxInfo[]>([]);
  const [sandboxesLoading, setSandboxesLoading] = useState(false);
  const [selectedSandbox, setSelectedSandbox] = useState<string>('');
  const [deployJob, setDeployJob] = useState<DeployJob | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [deployLogs, setDeployLogs] = useState<string>('');
  const [logDrawerOpen, setLogDrawerOpen] = useState(false);
  const deployTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const logTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  // Per-sandbox probe results: sandbox_id -> { gateway_ready, model, probing }
  const [probeResults, setProbeResults] = useState<Record<string, { gateway_ready?: boolean; model?: string; probing: boolean }>>({});

  // Available env var names from .env
  const [envKeys, setEnvKeys] = useState<string[]>([]);

  // Existing run detection
  const [existingRun, setExistingRun] = useState<{ run_id: string; completed: number; total_tasks: number } | null>(null);
  const runIdCheckRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Auto-generated run ID preview (shown when user hasn't entered a custom one)
  const [previewRunId, setPreviewRunId] = useState<string>('');
  // Bump this to trigger preview re-computation
  const [previewTrigger, setPreviewTrigger] = useState(0);

  const agentDef = AGENTS.find((a) => a.value === selectedAgent);
  const presetDef = BENCHMARK_PRESETS.find((b) => b.value === selectedPreset);
  const isCustom = selectedPreset === 'custom';

  // Probe a single sandbox for gateway health and model info
  const handleProbe = (sid: string) => {
    setProbeResults((prev) => ({ ...prev, [sid]: { ...prev[sid], probing: true } }));
    probeSandbox(sid)
      .then((result) => {
        setProbeResults((prev) => ({
          ...prev,
          [sid]: { gateway_ready: result.gateway_ready, model: result.model, probing: false },
        }));
        // Also update the sandbox list in-place
        setSandboxes((prev) =>
          prev.map((s) =>
            s.sandbox_id === sid
              ? { ...s, gateway_ready: result.gateway_ready, model: result.model || s.model }
              : s
          )
        );
      })
      .catch(() => {
        setProbeResults((prev) => ({ ...prev, [sid]: { ...prev[sid], probing: false } }));
      });
  };

  // Load sandboxes when openclaw is selected (fast: no gateway probe)
  const fetchSandboxes = () => {
    setSandboxesLoading(true);
    setProbeResults({});
    listSandboxes()
      .then((data) => {
        setSandboxes(data);
        if (data.length > 0 && !selectedSandbox) {
          const running = data.find((s) => s.status === 'running');
          setSelectedSandbox((running || data[0]).sandbox_id);
          schedulePreviewUpdate();
        }
      })
      .catch(() => setSandboxes([]))
      .finally(() => setSandboxesLoading(false));
  };

  // Check if a run_id already exists (debounced) and auto-fill form from saved config
  const checkExistingRun = (runId: string) => {
    clearTimeout(runIdCheckRef.current);
    if (!runId.trim()) {
      setExistingRun(null);
      return;
    }
    runIdCheckRef.current = setTimeout(() => {
      getRun(runId.trim())
        .then((detail) => {
          setExistingRun({
            run_id: detail.summary.run_id,
            completed: detail.summary.completed,
            total_tasks: detail.summary.total_tasks,
          });
          // Auto-fill form from saved config
          getRunConfig(runId.trim())
            .then((config) => {
              const agentCfg = (config.agent as Record<string, unknown>) || {};
              const benchCfg = (config.benchmark as Record<string, unknown>) || {};
              const agentConfig = (agentCfg.config as Record<string, unknown>) || {};
              const benchConfig = (benchCfg.config as Record<string, unknown>) || {};
              const agentName = (agentCfg.name as string) || '';
              const benchmarkName = (benchCfg.name as string) || '';

              // Find matching preset
              const matchedPreset = BENCHMARK_PRESETS.find(
                (p) => p.benchmarkName === benchmarkName &&
                  JSON.stringify(p.benchmarkConfig) === JSON.stringify(benchConfig)
              );

              // Update agent selection
              if (agentName) {
                setSelectedAgent(agentName);
              }
              if (matchedPreset) {
                setSelectedPreset(matchedPreset.value);
              }

              // Fill form fields from saved agent config
              const formValues: Record<string, unknown> = {
                run_id: runId.trim(),
                max_concurrent: config.max_concurrent,
                num_samples: config.num_samples,
                benchmark_preset: matchedPreset?.value || selectedPreset,
              };
              // Fill all agent config fields dynamically
              for (const [key, val] of Object.entries(agentConfig)) {
                if (key === 'api_key' && typeof val === 'string' && val.includes('...')) {
                  // Skip masked API keys — user needs to re-enter or use $VAR_NAME
                  continue;
                }
                formValues[`agent_${key}`] = val;
              }
              // Set metadata fields
              const metadata = (config.metadata as Record<string, unknown>) || {};
              if (metadata.notes) formValues.notes = metadata.notes;

              form.setFieldsValue(formValues);
              message.info('Form auto-filled from saved config');
            })
            .catch(() => { /* config not available, that's ok */ });
        })
        .catch(() => setExistingRun(null));
    }, 500);
  };

  // Recursively sort object keys to match Python's json.dumps(sort_keys=True)
  const sortKeys = (obj: unknown): unknown => {
    if (obj === null || obj === undefined || typeof obj !== 'object') return obj;
    if (Array.isArray(obj)) return obj.map(sortKeys);
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[k] = sortKeys((obj as Record<string, unknown>)[k]);
    }
    return sorted;
  };

  // Simple SHA-256 that works in both secure and insecure contexts
  const sha256Hex = async (input: string): Promise<string> => {
    // Prefer crypto.subtle (requires secure context: HTTPS or localhost)
    if (globalThis.crypto?.subtle) {
      const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
      return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
    }
    // Fallback: simple non-crypto hash (FNV-1a inspired, good enough for run ID dedup)
    let h1 = 0x811c9dc5 >>> 0;
    let h2 = 0x01000193 >>> 0;
    for (let i = 0; i < input.length; i++) {
      const c = input.charCodeAt(i);
      h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
      h2 = Math.imul(h2 ^ c, 0x811c9dc5) >>> 0;
    }
    return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')).slice(0, 12);
  };

  // Compute deterministic run ID (mirrors backend _deterministic_run_id)
  const computePreviewRunId = async () => {
    const values = form.getFieldsValue();
    const customRunId = (values.run_id as string || '').trim();
    if (customRunId) {
      // User specified a custom ID — don't show preview, just check existence
      setPreviewRunId('');
      checkExistingRun(customRunId);
      return;
    }
    try {
      const { agentConfig, benchmarkConfig } = buildEvalConfig(values);
      const agentName = selectedAgent;
      const benchmarkName = presetDef?.benchmarkName || 'aime';
      const keyParts = sortKeys(buildRunIdentity(values, agentConfig, benchmarkConfig));
      const canonical = JSON.stringify(keyParts);
      const hashHex = await sha256Hex(canonical);
      const runId = `${agentName}-${benchmarkName}-${hashHex.slice(0, 12)}`;
      setPreviewRunId(runId);
      checkExistingRun(runId);
    } catch {
      setPreviewRunId('');
      setExistingRun(null);
    }
  };

  // Schedule a preview recomputation (debounced via state + useEffect)
  const schedulePreviewUpdate = () => {
    setPreviewTrigger((n) => n + 1);
  };

  // Recompute preview when trigger, agent, or benchmark changes
  useEffect(() => {
    const timer = setTimeout(() => {
      computePreviewRunId();
    }, 150);
    return () => clearTimeout(timer);
  }, [previewTrigger, selectedAgent, selectedPreset, sandboxMode, selectedSandbox]);

  // Load available env keys on mount
  useEffect(() => {
    listEnvKeys()
      .then((data) => setEnvKeys(data.keys))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedAgent === 'openclaw') {
      fetchSandboxes();
    }
    return () => {
      clearInterval(deployTimerRef.current);
      clearInterval(logTimerRef.current);
    };
  }, [selectedAgent]);

  const handleDeploySandbox = async () => {
    const apiBase = form.getFieldValue('sandbox_api_base') || '';
    const apiKey = form.getFieldValue('sandbox_api_key') || '';
    const modelName = form.getFieldValue('sandbox_model_name') || '';
    setDeploying(true);
    try {
      const job = await deploySandbox({
        model_api_base: apiBase,
        model_api_key: apiKey,
        model_name: modelName,
      });
      setDeployJob(job);
      setLogDrawerOpen(true);
      message.info('Sandbox deployment started');

      // Poll deploy status
      deployTimerRef.current = setInterval(async () => {
        try {
          const updated = await getDeployStatus(job.deploy_id);
          setDeployJob(updated);
          if (updated.status === 'completed') {
            clearInterval(deployTimerRef.current);
            clearInterval(logTimerRef.current);
            setDeploying(false);
            setSelectedSandbox(updated.sandbox_id);
            setSandboxMode('existing');
            message.success('Sandbox deployed successfully!');
            fetchSandboxes();
          } else if (updated.status === 'failed') {
            clearInterval(deployTimerRef.current);
            clearInterval(logTimerRef.current);
            setDeploying(false);
            message.error(`Deploy failed: ${updated.error}`);
          }
        } catch {
          // ignore polling errors
        }
      }, 3000);

      // Poll deploy logs
      logTimerRef.current = setInterval(() => {
        getDeployLogs(job.deploy_id)
          .then((data) => setDeployLogs(data.logs))
          .catch(() => {});
      }, 2000);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : 'Failed to start deploy');
      setDeploying(false);
    }
  };

  const getSelectedSandboxApiBase = (): string => {
    const sb = sandboxes.find((s) => s.sandbox_id === selectedSandbox);
    return sb?.api_base || '';
  };

  const buildEvalConfig = (values: Record<string, unknown>) => {
    const agentConfig: Record<string, unknown> = {};
    for (const f of agentDef?.fields || []) {
      const val = values[`agent_${f.name}`];
      if (val !== undefined && val !== '' && val !== null) {
        agentConfig[f.name] = val;
      }
    }

    let benchmarkConfig: Record<string, unknown>;
    if (isCustom) {
      benchmarkConfig = {};
      for (const key of ['dataset', 'data_config', 'split', 'problem_field', 'answer_field']) {
        const val = values[`bench_${key}`];
        if (val !== undefined && val !== '' && val !== null) {
          benchmarkConfig[key] = val;
        }
      }
    } else {
      benchmarkConfig = { ...(presetDef?.benchmarkConfig || {}) };
    }

    if (values.smoke_test) {
      benchmarkConfig.max_tasks = 1;
    }

    const scorerConfig: Record<string, unknown> = {};
    if (values.scorer_tolerance !== undefined && values.scorer_tolerance !== null) {
      scorerConfig.tolerance = values.scorer_tolerance;
    }

    return { agentConfig, benchmarkConfig, scorerConfig };
  };

  const buildRunIdentity = (
    values: Record<string, unknown>,
    agentConfig: Record<string, unknown>,
    benchmarkConfig: Record<string, unknown>
  ) => {
    const benchmarkName = presetDef?.benchmarkName || 'aime';
    const credentialFields = new Set(['api_key', 'gateway_token', 'secret', 'secret_key', 'token']);
    const filteredAgentConfig: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(agentConfig)) {
      if (!credentialFields.has(k.toLowerCase())) {
        filteredAgentConfig[k] = v;
      }
    }

    const identity: Record<string, unknown> = {
      agent: selectedAgent,
      benchmark: benchmarkName,
      agent_config: filteredAgentConfig,
      benchmark_config: benchmarkConfig,
    };

    if (selectedAgent === 'openclaw') {
      if (sandboxMode === 'existing') {
        if (selectedSandbox) {
          identity.sandbox_id = selectedSandbox;
        }
      } else {
        identity.deploy_and_run = true;
        const baseModel = (values.sandbox_model_name as string) || '';
        const modelApiBase = (values.sandbox_api_base as string) || '';
        if (baseModel) {
          identity.base_model = baseModel;
        }
        if (modelApiBase) {
          identity.model_api_base = modelApiBase;
        }
      }
    }

    return identity;
  };

  const handleDeployAndRun = async () => {
    const values = form.getFieldsValue();
    setSubmitting(true);
    try {
      const { agentConfig, benchmarkConfig, scorerConfig } = buildEvalConfig(values);

      // Gateway token for openclaw
      agentConfig.gateway_token = (values.agent_gateway_token as string) || 'OPENCLAW';

      const req: DeployAndRunRequest = {
        model_api_base: (values.sandbox_api_base as string) || '',
        model_api_key: (values.sandbox_api_key as string) || '',
        model_name: (values.sandbox_model_name as string) || '',
        run_id: (values.run_id as string) || '',
        agent_version: (values.agent_version as string) || '',
        agent_config: agentConfig,
        benchmark_name: presetDef?.benchmarkName || 'aime',
        benchmark_config: benchmarkConfig,
        scorer_name: (values.scorer_name as string) || presetDef?.defaultScorer || '',
        scorer_config: scorerConfig,
        max_concurrent: (values.max_concurrent as number) || 1,
        redo_all: (values.redo_all as boolean) || false,
        num_samples: (values.num_samples as number) || 1,
        metadata: {
          notes: values.notes || '',
          preset: selectedPreset,
          benchmark_label: presetDef?.label || presetDef?.benchmarkName || '',
          smoke_test: !!values.smoke_test,
          deploy_and_run: true,
          base_model: (values.sandbox_model_name as string) || '',
          model_api_base: (values.sandbox_api_base as string) || '',
        },
      };

      const result = await deployAndRun(req);
      message.success(`Job created: ${result.run_id}. Sandbox deploying, eval will start automatically.`);
      navigate('/jobs');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : 'Failed to start deploy & run');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      const { agentConfig, benchmarkConfig, scorerConfig } = buildEvalConfig(values);

      // For OpenClaw, set api_base from selected sandbox
      if (selectedAgent === 'openclaw') {
        if (sandboxMode === 'new') {
          message.error('OpenClaw is in "Deploy New Sandbox" mode. Use "Deploy & Run Evaluation" or "Deploy Only" first.');
          setSubmitting(false);
          return;
        }
        const apiBase = getSelectedSandboxApiBase();
        if (!apiBase) {
          message.error('Please select a sandbox or deploy a new one');
          setSubmitting(false);
          return;
        }
        agentConfig.api_base = apiBase;
        agentConfig.model = agentConfig.model || 'openclaw';
        agentConfig.gateway_token = (values.agent_gateway_token as string) || 'OPENCLAW';
      }

      const req: CreateJobRequest = {
        run_id: (values.run_id as string) || '',
        agent_name: selectedAgent,
        agent_version: (values.agent_version as string) || '',
        agent_config: agentConfig,
        benchmark_name: presetDef?.benchmarkName || 'aime',
        benchmark_config: benchmarkConfig,
        scorer_name: (values.scorer_name as string) || presetDef?.defaultScorer || '',
        scorer_config: scorerConfig,
        sandbox_name: null,
        sandbox_config: {},
        max_concurrent: (values.max_concurrent as number) || 1,
        redo_all: (values.redo_all as boolean) || false,
        num_samples: (values.num_samples as number) || 1,
        metadata: {
          notes: values.notes || '',
          preset: selectedPreset,
          benchmark_label: presetDef?.label || presetDef?.benchmarkName || '',
          smoke_test: !!values.smoke_test,
          sandbox_mode: selectedAgent === 'openclaw' ? sandboxMode : undefined,
          sandbox_id: selectedAgent === 'openclaw' ? selectedSandbox : undefined,
        },
      };

      const job = await createJob(req);
      message.success(`Job created: ${job.run_id}`);
      navigate('/jobs');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : 'Failed to create job');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Button
        icon={<ArrowLeftOutlined />}
        type="text"
        onClick={() => navigate('/jobs')}
        style={{ marginBottom: 16 }}
      >
        Back to Jobs
      </Button>

      <Title level={3}>New Evaluation</Title>

      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        onValuesChange={(changed) => {
          // Re-compute preview run ID when config-affecting fields change
          const keys = Object.keys(changed);
          if ('redo_all' in changed) setRedoAll(changed.redo_all as boolean);
          const affects = keys.some(k =>
            k.startsWith('agent_') || k === 'benchmark_preset' || k === 'smoke_test' ||
            k.startsWith('bench_') || k.startsWith('sandbox_') || k === 'agent_name'
          );
          if (affects) schedulePreviewUpdate();
        }}
        initialValues={{
          agent_name: 'direct_llm',
          benchmark_preset: 'aime2025',
          scorer_name: 'numeric',
          max_concurrent: 3,
          num_samples: 1,
          redo_all: false,
          smoke_test: false,
          agent_temperature: 0.6,
          agent_max_tokens: 32768,
        }}
      >
        {/* Run ID & Notes */}
        <Card size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="run_id" label="Run ID (optional)" tooltip="Leave empty to auto-generate a deterministic ID based on Agent, Benchmark, Agent Config, and Benchmark Config. Same parameters → same Run ID → automatic checkpoint resume. Custom IDs work fine but won't auto-resume across submissions.">
                <Input
                  placeholder={previewRunId ? `Will use: ${previewRunId}` : 'Auto-generated from config (deterministic)'}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    if (v) {
                      setPreviewRunId('');
                      checkExistingRun(v);
                    } else {
                      schedulePreviewUpdate();
                    }
                  }}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="notes" label="Notes">
                <Input placeholder="Description of this evaluation" />
              </Form.Item>
            </Col>
          </Row>
          {existingRun && form.getFieldValue('run_id') && (
            <Alert
              message={`Run "${existingRun.run_id}" already exists (${existingRun.completed}/${existingRun.total_tasks} completed)`}
              description={
                redoAll
                  ? `⚠️ Redo All is ON: existing ${existingRun.completed} results will be permanently deleted and all tasks will be re-evaluated from scratch.`
                  : `Will resume from checkpoint: ${existingRun.total_tasks - existingRun.completed} remaining tasks will be evaluated, ${existingRun.completed} completed tasks will be skipped.`
              }
              type={redoAll ? 'warning' : 'info'}
              showIcon
              style={{ marginTop: -8 }}
            />
          )}
        </Card>

        {/* Benchmark Selection */}
        <Card title="Benchmark" size="small" style={{ marginBottom: 16 }}>
          <Form.Item name="benchmark_preset" label="Benchmark">
            <Select
              options={BENCHMARK_PRESETS.map((b) => ({
                value: b.value,
                label: (
                  <span>
                    <strong>{b.label}</strong>
                    <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                      {b.description}
                    </Text>
                  </span>
                ),
              }))}
              onChange={(v) => {
                setSelectedPreset(v);
                const def = BENCHMARK_PRESETS.find((b) => b.value === v);
                if (def?.defaultScorer) {
                  form.setFieldValue('scorer_name', def.defaultScorer);
                }
              }}
            />
          </Form.Item>

          {/* Show preset details */}
          {presetDef && !isCustom && Object.keys(presetDef.benchmarkConfig).length > 0 && (
            <div style={{ background: '#f6f6f6', padding: '8px 12px', borderRadius: 4, fontSize: 12 }}>
              {Object.entries(presetDef.benchmarkConfig).map(([k, v]) => (
                <div key={k}>
                  <Text type="secondary">{k}:</Text> <Text code>{v}</Text>
                </div>
              ))}
            </div>
          )}

          {/* Custom HF dataset fields */}
          {isCustom && (
            <>
              <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', padding: '8px 12px', borderRadius: 4, fontSize: 12, marginBottom: 12 }}>
                <Text strong style={{ fontSize: 12 }}>Supported dataset format:</Text>
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  <li>HuggingFace <code>datasets</code> compatible (loaded via <code>load_dataset</code>)</li>
                  <li>Must contain a <b>problem</b> (text) column and an <b>answer</b> (numeric) column</li>
                  <li>Answer is compared numerically with tolerance — best suited for math competition datasets (AIME, AMC, custom problem sets, etc.)</li>
                  <li>Multi-config datasets supported via the Data Config field (e.g. <code>AIME2025-I</code>)</li>
                </ul>
              </div>
              <Divider style={{ margin: '12px 0' }} />
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item
                    name="bench_dataset"
                    label="HuggingFace Dataset"
                    rules={[{ required: true, message: 'Dataset is required' }]}
                  >
                    <Input placeholder="e.g. HuggingFaceH4/aime_2024" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="bench_data_config" label="Data Config">
                    <Input placeholder="optional, e.g. AIME2025-I" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="bench_split" label="Split">
                    <Input placeholder="default: train" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="bench_problem_field" label="Problem Field">
                    <Input placeholder="default: problem" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="bench_answer_field" label="Answer Field">
                    <Input placeholder="default: answer" />
                  </Form.Item>
                </Col>
              </Row>
            </>
          )}
        </Card>

        {/* Agent Configuration */}
        <Card title="Agent" size="small" style={{ marginBottom: 16 }}>
          <Form.Item name="agent_name" label="Agent Type">
            <Select
              options={AGENTS.map((a) => ({
                value: a.value,
                label: (
                  <span>
                    <strong>{a.label}</strong>
                    <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                      {a.description}
                    </Text>
                  </span>
                ),
              }))}
              onChange={(v) => setSelectedAgent(v)}
            />
          </Form.Item>
          <Form.Item
            name="agent_version"
            label={
              <Space size={4}>
                <span>Agent Version</span>
                <Tooltip title="Optional label for tracking which model version or agent variant was used. Displayed in results for reference only — does not affect execution.">
                  <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                </Tooltip>
              </Space>
            }
          >
            <Input placeholder="e.g. Kimi-K2.5" />
          </Form.Item>

          {/* OpenClaw Sandbox Section */}
          {selectedAgent === 'openclaw' && (
            <>
              <Divider style={{ margin: '12px 0' }}>
                <CloudServerOutlined /> ROCK Sandbox
              </Divider>

              <Radio.Group
                value={sandboxMode}
                onChange={(e) => {
                  setSandboxMode(e.target.value);
                  schedulePreviewUpdate();
                }}
                style={{ marginBottom: 16 }}
              >
                <Radio.Button value="existing">Use Existing Sandbox</Radio.Button>
                <Radio.Button value="new">Deploy New Sandbox</Radio.Button>
              </Radio.Group>

              {sandboxMode === 'existing' && (
                <div>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                    <Select
                      style={{ flex: 1 }}
                      placeholder={sandboxesLoading ? 'Loading sandboxes...' : 'Select a sandbox'}
                      loading={sandboxesLoading}
                      value={selectedSandbox || undefined}
                      onChange={(value) => {
                        setSelectedSandbox(value);
                        schedulePreviewUpdate();
                      }}
                      notFoundContent={
                        sandboxesLoading ? <Spin size="small" /> : <Text type="secondary">No sandboxes found</Text>
                      }
                      options={sandboxes.map((s) => {
                        const probe = probeResults[s.sandbox_id];
                        const model = probe?.model || s.model;
                        const gwReady = probe ? probe.gateway_ready : undefined;
                        const gwTag = gwReady === true ? (
                          <Tag color="green" style={{ marginRight: 4 }}>Ready</Tag>
                        ) : gwReady === false ? (
                          <Tag color="red" style={{ marginRight: 4 }}>No Gateway</Tag>
                        ) : s.status === 'running' ? (
                          <Tag color="default" style={{ marginRight: 4 }}>{s.status}</Tag>
                        ) : (
                          <Tag color="default" style={{ marginRight: 4 }}>{s.status}</Tag>
                        );
                        return {
                          value: s.sandbox_id,
                          label: (
                            <span>
                              {gwTag}
                              {model ? (
                                <>
                                  <strong>{model}</strong>
                                  <Text type="secondary" style={{ marginLeft: 6, fontSize: 11 }}>({s.sandbox_id.slice(0, 8)})</Text>
                                </>
                              ) : (
                                <span>{s.sandbox_id.slice(0, 12)}</span>
                              )}
                            </span>
                          ),
                        };
                      })}
                    />
                    <Button icon={<ReloadOutlined />} onClick={fetchSandboxes} loading={sandboxesLoading} />
                  </div>
                  {selectedSandbox && (() => {
                    const sb = sandboxes.find((s) => s.sandbox_id === selectedSandbox);
                    const probe = probeResults[selectedSandbox];
                    const model = probe?.model || sb?.model;
                    const gwReady = probe ? probe.gateway_ready : undefined;
                    const isProbing = probe?.probing ?? false;
                    return (
                      <>
                        <div style={{ background: '#f6f6f6', padding: '8px 12px', borderRadius: 4, fontSize: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div>
                            {model && (
                              <div style={{ marginBottom: 4 }}>
                                <Text type="secondary">Model:</Text>{' '}
                                <Text strong>{model}</Text>
                              </div>
                            )}
                            <div>
                              <Text type="secondary">API Base:</Text>{' '}
                              <Text code>{getSelectedSandboxApiBase()}</Text>
                            </div>
                            {gwReady !== undefined && (
                              <div style={{ marginTop: 4 }}>
                                <Text type="secondary">Gateway:</Text>{' '}
                                {gwReady ? (
                                  <Tag color="green">Ready</Tag>
                                ) : (
                                  <Tag color="red">Not Available</Tag>
                                )}
                              </div>
                            )}
                          </div>
                          <Button
                            size="small"
                            loading={isProbing}
                            onClick={() => handleProbe(selectedSandbox)}
                          >
                            {isProbing ? 'Checking...' : gwReady === undefined ? 'Check Status' : 'Re-check'}
                          </Button>
                        </div>
                        {gwReady === false && (
                          <Alert
                            message="This sandbox does not have a working OpenClaw gateway. The evaluation will fail. Deploy a new sandbox instead."
                            type="error"
                            showIcon
                            style={{ marginTop: 8 }}
                          />
                        )}
                      </>
                    );
                  })()}
                  {sandboxes.length === 0 && !sandboxesLoading && (
                    <Alert
                      message="No sandboxes available. Deploy a new one or check ROCK service."
                      type="warning"
                      showIcon
                      style={{ marginTop: 8 }}
                    />
                  )}
                </div>
              )}

              {sandboxMode === 'new' && (
                <div>
                  <Alert
                    message="Deploy a new ROCK sandbox with OpenClaw. Configure the base model that OpenClaw will use."
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                  />
                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item name="sandbox_api_base" label="Model API Base URL">
                        <Input
                          placeholder="e.g. https://openrouter.ai/api/v1/"
                          onChange={(e) => {
                            const suggested = suggestEnvVar(e.target.value);
                            if (suggested) {
                              const currentKey = form.getFieldValue('sandbox_api_key') || '';
                              if (!currentKey || currentKey.startsWith('$')) {
                                form.setFieldValue('sandbox_api_key', `$${suggested}`);
                              }
                            }
                          }}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="sandbox_model_name" label="Model Name">
                        <Input placeholder="e.g. moonshotai/kimi-k2.5" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item
                        name="sandbox_api_key"
                        label={
                          <Space size={4}>
                            <span>Model API Key</span>
                            <Tooltip title={API_KEY_TOOLTIP} overlayStyle={{ maxWidth: 360 }}>
                              <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                            </Tooltip>
                          </Space>
                        }
                      >
                        <Input
                          placeholder="sk-... or $ENV_VAR_NAME"
                          allowClear
                        />
                      </Form.Item>
                      <div style={{ marginTop: -16, marginBottom: 12 }}>
                        <Text type="secondary" style={{ fontSize: 11, marginRight: 6 }}>Quick fill:</Text>
                        {(() => {
                          const apiBase = form.getFieldValue('sandbox_api_base') || '';
                          const suggested = suggestEnvVar(apiBase);
                          const defaults = ['OPENAI_API_KEY', 'OPENROUTER_API_KEY'];
                          const tags = suggested && !defaults.includes(suggested)
                            ? [suggested, ...defaults]
                            : defaults;
                          return tags.map((k) => (
                            <Tag
                              key={k}
                              style={{ cursor: 'pointer', fontSize: 11 }}
                              color={k === suggested ? 'blue' : undefined}
                              onClick={() => form.setFieldValue('sandbox_api_key', `$${k}`)}
                            >
                              ${k}{k === suggested && envKeys.includes(k) ? ' (.env)' : ''}
                            </Tag>
                          ));
                        })()}
                      </div>
                    </Col>
                  </Row>
                  <Space wrap>
                    <Button
                      icon={<PlusOutlined />}
                      htmlType="button"
                      onClick={handleDeploySandbox}
                      loading={deploying}
                    >
                      {deploying ? 'Deploying...' : 'Deploy Only'}
                    </Button>
                    <Button
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      htmlType="button"
                      onClick={handleDeployAndRun}
                      loading={submitting}
                    >
                      Deploy & Run Evaluation
                    </Button>
                    {deployJob && (
                      <Button
                        icon={<FileTextOutlined />}
                        onClick={() => {
                          setLogDrawerOpen(true);
                          if (deployJob) {
                            getDeployLogs(deployJob.deploy_id)
                              .then((data) => setDeployLogs(data.logs))
                              .catch(() => {});
                          }
                        }}
                      >
                        Deploy Logs
                      </Button>
                    )}
                    {deployJob && (
                      <Tag color={
                        deployJob.status === 'completed' ? 'green' :
                        deployJob.status === 'failed' ? 'red' :
                        deployJob.status === 'deploying' ? 'blue' : 'default'
                      }>
                        {deployJob.status.toUpperCase()}
                        {deployJob.sandbox_id && ` - ${deployJob.sandbox_id.slice(0, 12)}`}
                      </Tag>
                    )}
                  </Space>
                  <div style={{ marginTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      "Deploy & Run" will deploy a sandbox and automatically start the evaluation once it's ready. Monitor progress on the Jobs page.
                    </Text>
                  </div>
                </div>
              )}

              <Divider style={{ margin: '12px 0' }}>Agent Parameters</Divider>
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item name="agent_gateway_token" label="Gateway Token">
                    <Input placeholder="default: OPENCLAW" />
                  </Form.Item>
                </Col>
              </Row>
            </>
          )}

          {/* Common agent fields */}
          <Row gutter={16}>
            {agentDef?.fields.map((f) => {
              // Special handling for api_key fields
              if ('password' in f && f.password) {
                return (
                  <Col span={12} key={f.name}>
                    <Form.Item
                      name={`agent_${f.name}`}
                      label={
                        <Space size={4}>
                          <span>{f.label}</span>
                          <Tooltip title={API_KEY_TOOLTIP} overlayStyle={{ maxWidth: 360 }}>
                            <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                          </Tooltip>
                        </Space>
                      }
                      rules={f.required ? [{ required: true, message: `${f.label} is required` }] : []}
                    >
                      <Input
                        placeholder="sk-... or $ENV_VAR_NAME"
                        allowClear
                      />
                    </Form.Item>
                    <div style={{ marginTop: -16, marginBottom: 12 }}>
                      <Text type="secondary" style={{ fontSize: 11, marginRight: 6 }}>Quick fill:</Text>
                      {(() => {
                        const apiBase = form.getFieldValue('agent_api_base') || '';
                        const suggested = suggestEnvVar(apiBase);
                        // Curated defaults + auto-matched suggestion
                        const defaults = ['OPENAI_API_KEY', 'OPENROUTER_API_KEY'];
                        const tags = suggested && !defaults.includes(suggested)
                          ? [suggested, ...defaults]
                          : defaults;
                        return tags.map((k) => (
                          <Tag
                            key={k}
                            style={{ cursor: 'pointer', fontSize: 11 }}
                            color={k === suggested ? 'blue' : undefined}
                            onClick={() => form.setFieldValue(`agent_${f.name}`, `$${k}`)}
                          >
                            ${k}{k === suggested && envKeys.includes(k) ? ' (.env)' : ''}
                          </Tag>
                        ));
                      })()}
                    </div>
                  </Col>
                );
              }

              return (
                <Col span={f.type === 'number' ? 8 : 12} key={f.name}>
                  <Form.Item
                    name={`agent_${f.name}`}
                    label={f.label}
                    rules={f.required ? [{ required: true, message: `${f.label} is required` }] : []}
                  >
                    {f.type === 'number' ? (
                      <InputNumber
                        style={{ width: '100%' }}
                        placeholder={String(f.default ?? '')}
                      />
                    ) : (
                      <Input
                        placeholder={f.placeholder}
                        onChange={f.name === 'api_base' ? (e) => {
                          // Auto-suggest env var when base URL changes
                          const suggested = suggestEnvVar(e.target.value);
                          if (suggested) {
                            const currentKey = form.getFieldValue('agent_api_key') || '';
                            if (!currentKey || currentKey.startsWith('$')) {
                              form.setFieldValue('agent_api_key', `$${suggested}`);
                            }
                          }
                        } : undefined}
                      />
                    )}
                  </Form.Item>
                </Col>
              );
            })}
          </Row>
          {!form.getFieldValue('run_id') && previewRunId && (
            <Alert
              type={existingRun ? 'info' : undefined}
              showIcon
              style={{ marginTop: -4 }}
              message={
                existingRun
                  ? `Run ID "${previewRunId}" already exists (${existingRun.completed}/${existingRun.total_tasks} completed). ${
                      redoAll
                        ? `⚠️ Redo All is ON: existing ${existingRun.completed} results will be permanently deleted.`
                        : `Will resume from checkpoint, ${existingRun.total_tasks - existingRun.completed} remaining tasks to evaluate.`
                    }`
                  : `Run ID: ${previewRunId} (new run)`
              }
            />
          )}
        </Card>

        {/* Scorer & Execution */}
        <Card title="Scorer & Execution" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="scorer_name" label="Scorer">
                <Select
                  options={SCORERS.map((s) => ({
                    value: s.value,
                    label: (
                      <span>
                        <strong>{s.label}</strong>
                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                          {s.description}
                        </Text>
                      </span>
                    ),
                  }))}
                />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item
                name="scorer_tolerance"
                label={
                  <Space size={4}>
                    <span>Tolerance</span>
                    <Tooltip title="Numeric scorer accepted absolute error. Example: 1e-6 means |pred - gt| <= 1e-6 is counted as correct.">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                  </Space>
                }
              >
                <InputNumber style={{ width: '100%' }} placeholder="1e-6" step={0.000001} />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item
                name="max_concurrent"
                label={
                  <Space size={4}>
                    <span>Concurrency</span>
                    <Tooltip title="How many benchmark tasks run in parallel. For direct_llm this can improve speed. For OpenClaw, high values may cause timeout/resource contention.">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                  </Space>
                }
              >
                <InputNumber min={1} max={20} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item
                name="num_samples"
                label={
                  <Space size={4}>
                    <span>Samples (k)</span>
                    <Tooltip title="Number of independent samples per task for pass@k evaluation. When k > 1, each task is solved k times independently, and pass@k / avg@k metrics are reported.">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                  </Space>
                }
              >
                <InputNumber min={1} max={64} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item
                name="redo_all"
                label={
                  <Space size={4}>
                    <span>Redo All</span>
                    <Tooltip title="When OFF (default): resumes from checkpoint — skips already completed tasks and only evaluates remaining ones. When ON: deletes existing results and re-evaluates all tasks from scratch. This is irreversible.">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                  </Space>
                }
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item
                name="smoke_test"
                label={
                  <Space size={4}>
                    <span>Smoke Test</span>
                    <Tooltip title="Run a quick pipeline check with only 1 task (sets benchmark_config.max_tasks=1).">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                  </Space>
                }
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Card>

        <Alert
          message="The evaluation will run in the background. Monitor progress on the Jobs page."
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />

        <Button
          type="primary"
          htmlType="submit"
          icon={<PlayCircleOutlined />}
          loading={submitting}
          disabled={selectedAgent === 'openclaw' && sandboxMode === 'new'}
          size="large"
          block
        >
          {selectedAgent === 'openclaw' && sandboxMode === 'new'
            ? 'Use Deploy & Run Evaluation Above'
            : 'Start Evaluation'}
        </Button>
      </Form>

      <Drawer
        title={deployJob ? `Deploy Logs: ${deployJob.deploy_id.slice(0, 12)}` : 'Deploy Logs'}
        open={logDrawerOpen}
        onClose={() => setLogDrawerOpen(false)}
        placement="bottom"
        height={420}
        styles={{ body: { padding: 0 } }}
      >
        {deployLogs ? (
          <pre
            style={{
              fontSize: 12,
              lineHeight: 1.8,
              fontFamily: "'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace",
              background: '#fafafa',
              color: '#333',
              padding: '16px 24px',
              margin: 0,
              overflow: 'auto',
              height: '100%',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              borderTop: '1px solid #f0f0f0',
            }}
          >
            {deployLogs}
          </pre>
        ) : (
          <div style={{ padding: 24 }}>
            <Text type="secondary">No deploy logs yet.</Text>
          </div>
        )}
      </Drawer>
    </div>
  );
}
