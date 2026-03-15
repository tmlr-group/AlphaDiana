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
import { createJob, listSandboxes, deploySandbox, getDeployStatus, getDeployLogs, deployAndRun } from '../api';

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
  {
    value: 'aime2025-I',
    label: 'AIME 2025-I',
    description: 'AIME 2025 Part I (15 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'opencompass/AIME2025',
      data_config: 'AIME2025-I',
      split: 'test',
      problem_field: 'question',
      answer_field: 'answer',
    },
  },
  {
    value: 'aime2025-II',
    label: 'AIME 2025-II',
    description: 'AIME 2025 Part II (15 problems)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'opencompass/AIME2025',
      data_config: 'AIME2025-II',
      split: 'test',
      problem_field: 'question',
      answer_field: 'answer',
    },
  },
  {
    value: 'aime2024',
    label: 'AIME 2024',
    description: 'AIME 2024 (HuggingFaceH4)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {
      dataset: 'HuggingFaceH4/aime_2024',
      split: 'train',
      problem_field: 'problem',
      answer_field: 'answer',
    },
  },
  {
    value: 'math',
    label: 'MATH',
    description: 'Hendrycks MATH dataset',
    benchmarkName: 'math',
    defaultScorer: 'exact_match',
    benchmarkConfig: { split: 'test' },
  },
  {
    value: 'frontier_math',
    label: 'FrontierMath',
    description: 'FrontierMath benchmark',
    benchmarkName: 'frontier_math',
    defaultScorer: 'numeric',
    benchmarkConfig: { split: 'test' },
  },
  {
    value: 'hle',
    label: 'HLE',
    description: "Humanity's Last Exam",
    benchmarkName: 'hle',
    defaultScorer: 'llm_judge',
    benchmarkConfig: { split: 'test' },
  },
  {
    value: 'custom-aime',
    label: 'AIME (Custom HF Dataset)',
    description: 'Use a custom HuggingFace AIME dataset (optional data_config for multi-config sets)',
    benchmarkName: 'aime',
    defaultScorer: 'numeric',
    benchmarkConfig: {},
  },
];

const SCORERS = [
  { value: 'numeric', label: 'Numeric', description: 'Compare numeric values with tolerance' },
  { value: 'exact_match', label: 'Exact Match', description: 'Math-aware string comparison' },
  { value: 'llm_judge', label: 'LLM Judge', description: 'Use LLM to evaluate correctness' },
];

export default function NewJobPage() {
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string>('direct_llm');
  const [selectedPreset, setSelectedPreset] = useState<string>('aime2025-I');

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

  const agentDef = AGENTS.find((a) => a.value === selectedAgent);
  const presetDef = BENCHMARK_PRESETS.find((b) => b.value === selectedPreset);
  const isCustom = selectedPreset === 'custom-aime';

  // Load sandboxes when openclaw is selected
  const fetchSandboxes = () => {
    setSandboxesLoading(true);
    listSandboxes()
      .then((data) => {
        setSandboxes(data);
        if (data.length > 0 && !selectedSandbox) {
          // Prefer a gateway-ready sandbox, otherwise first running one
          const ready = data.find((s) => s.gateway_ready);
          const running = data.find((s) => s.status === 'running');
          setSelectedSandbox((ready || running || data[0]).sandbox_id);
        }
      })
      .catch(() => setSandboxes([]))
      .finally(() => setSandboxesLoading(false));
  };

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
        metadata: {
          notes: values.notes || '',
          preset: selectedPreset,
          benchmark_label: presetDef?.label || presetDef?.benchmarkName || '',
          smoke_test: !!values.smoke_test,
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
        metadata: {
          notes: values.notes || '',
          preset: selectedPreset,
          benchmark_label: presetDef?.label || presetDef?.benchmarkName || '',
          smoke_test: !!values.smoke_test,
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
        initialValues={{
          agent_name: 'direct_llm',
          benchmark_preset: 'aime2025-I',
          scorer_name: 'numeric',
          max_concurrent: 3,
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
              <Form.Item name="run_id" label="Run ID (optional)">
                <Input placeholder="Auto-generated if empty" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="notes" label="Notes">
                <Input placeholder="Description of this evaluation" />
              </Form.Item>
            </Col>
          </Row>
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

          {/* Custom AIME fields */}
          {isCustom && (
            <>
              <Divider style={{ margin: '12px 0' }} />
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item
                    name="bench_dataset"
                    label="HuggingFace Dataset"
                    rules={[{ required: true, message: 'Dataset is required' }]}
                  >
                    <Input placeholder="e.g. opencompass/AIME2025" />
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
          <Form.Item name="agent_version" label="Agent Version">
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
                onChange={(e) => setSandboxMode(e.target.value)}
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
                      onChange={setSelectedSandbox}
                      notFoundContent={
                        sandboxesLoading ? <Spin size="small" /> : <Text type="secondary">No sandboxes found</Text>
                      }
                      options={sandboxes.map((s) => ({
                        value: s.sandbox_id,
                        label: (
                          <span>
                            {s.gateway_ready ? (
                              <Tag color="green" style={{ marginRight: 4 }}>Ready</Tag>
                            ) : s.status === 'running' ? (
                              <Tag color="orange" style={{ marginRight: 4 }}>No Gateway</Tag>
                            ) : (
                              <Tag color="default" style={{ marginRight: 4 }}>{s.status}</Tag>
                            )}
                            {s.model ? (
                              <>
                                <strong>{s.model}</strong>
                                <Text type="secondary" style={{ marginLeft: 6, fontSize: 11 }}>({s.sandbox_id.slice(0, 8)})</Text>
                              </>
                            ) : (
                              <span>{s.sandbox_id.slice(0, 12)}</span>
                            )}
                          </span>
                        ),
                      }))}
                    />
                    <Button icon={<ReloadOutlined />} onClick={fetchSandboxes} loading={sandboxesLoading} />
                  </div>
                  {selectedSandbox && (
                    <>
                      <div style={{ background: '#f6f6f6', padding: '8px 12px', borderRadius: 4, fontSize: 12 }}>
                        {(() => {
                          const sb = sandboxes.find((s) => s.sandbox_id === selectedSandbox);
                          return sb?.model ? (
                            <div style={{ marginBottom: 4 }}>
                              <Text type="secondary">Model:</Text>{' '}
                              <Text strong>{sb.model}</Text>
                            </div>
                          ) : null;
                        })()}
                        <Text type="secondary">API Base:</Text>{' '}
                        <Text code>{getSelectedSandboxApiBase()}</Text>
                      </div>
                      {(() => {
                        const sb = sandboxes.find((s) => s.sandbox_id === selectedSandbox);
                        if (sb && !sb.gateway_ready) {
                          return (
                            <Alert
                              message="This sandbox does not have a working OpenClaw gateway. The evaluation will fail with 'All connection attempts failed'. Deploy a new sandbox instead."
                              type="error"
                              showIcon
                              style={{ marginTop: 8 }}
                            />
                          );
                        }
                        return null;
                      })()}
                    </>
                  )}
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
                        <Input placeholder="e.g. https://openrouter.ai/api/v1/" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="sandbox_model_name" label="Model Name">
                        <Input placeholder="e.g. moonshotai/kimi-k2.5" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="sandbox_api_key" label="Model API Key">
                        <Input.Password placeholder="API key for the base model" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Space wrap>
                    <Button
                      icon={<PlusOutlined />}
                      onClick={handleDeploySandbox}
                      loading={deploying}
                    >
                      {deploying ? 'Deploying...' : 'Deploy Only'}
                    </Button>
                    <Button
                      type="primary"
                      icon={<PlayCircleOutlined />}
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
            {agentDef?.fields.map((f) => (
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
                      type={'password' in f && f.password ? 'password' : 'text'}
                    />
                  )}
                </Form.Item>
              </Col>
            ))}
          </Row>
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
              <Form.Item name="redo_all" label="Redo All" valuePropName="checked">
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
          size="large"
          block
        >
          Start Evaluation
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
