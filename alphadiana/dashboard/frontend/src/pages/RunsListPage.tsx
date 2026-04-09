import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card,
  Table,
  Tag,
  Progress,
  Statistic,
  Row,
  Col,
  Button,
  Input,
  Space,
  Popconfirm,
  Tooltip,
  message,
} from 'antd';
import {
  ExperimentOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { RunSummary } from '../types';
import { listRuns, deleteRun } from '../api';

export default function RunsListPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const navigate = useNavigate();

  const fetchRuns = () => {
    setLoading(true);
    listRuns()
      .then(setRuns)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchRuns();
  }, []);

  const handleDeleteRun = async (runId: string) => {
    try {
      await deleteRun(runId);
      message.success(`Run "${runId}" deleted`);
      fetchRuns();
      setSelectedKeys((prev) => prev.filter((k) => k !== runId));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : 'Failed to delete run');
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedKeys.length === 0) return;
    setDeleting(true);
    let ok = 0;
    let fail = 0;
    for (const runId of selectedKeys) {
      try {
        await deleteRun(runId);
        ok++;
      } catch {
        fail++;
      }
    }
    setDeleting(false);
    if (ok > 0) message.success(`Deleted ${ok} run(s)`);
    if (fail > 0) message.error(`Failed to delete ${fail} run(s)`);
    setSelectedKeys([]);
    fetchRuns();
  };

  const filtered = runs.filter(
    (r) =>
      r.run_id.toLowerCase().includes(search.toLowerCase()) ||
      r.agent.toLowerCase().includes(search.toLowerCase()) ||
      r.benchmark.toLowerCase().includes(search.toLowerCase())
  );

  const bestAccuracy = runs.length
    ? Math.max(...runs.map((r) => r.accuracy_total))
    : 0;
  const totalTasks = runs.reduce((s, r) => s + r.total_tasks, 0);
  const totalTokens = runs.reduce(
    (s, r) =>
      s +
      (r.total_tokens.prompt_tokens || 0) +
      (r.total_tokens.completion_tokens || 0),
    0
  );

  const columns: ColumnsType<RunSummary> = [
    {
      title: 'Run ID',
      dataIndex: 'run_id',
      key: 'run_id',
      render: (id: string) => (
        <a onClick={() => navigate(`/runs/${encodeURIComponent(id)}`)}>{id}</a>
      ),
      sorter: (a, b) => a.run_id.localeCompare(b.run_id),
    },
    {
      title: 'Agent',
      dataIndex: 'agent',
      key: 'agent',
      render: (agent: string, r: RunSummary) => (
        <span>
          {agent} <Tag color="blue">{r.agent_version}</Tag>
        </span>
      ),
      filters: [...new Set(runs.map((r) => r.agent))].map((a) => ({
        text: a,
        value: a,
      })),
      onFilter: (v, r) => r.agent === v,
    },
    {
      title: 'Model',
      dataIndex: 'model',
      key: 'model',
      render: (model: string) =>
        model ? (
          <Tooltip title={model}>
            <span style={{ fontSize: 12, maxWidth: 160, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {model}
            </span>
          </Tooltip>
        ) : (
          <span style={{ color: '#bfbfbf' }}>—</span>
        ),
    },
    {
      title: 'Benchmark',
      dataIndex: 'benchmark',
      key: 'benchmark',
      filters: [...new Set(runs.map((r) => r.benchmark))].map((b) => ({
        text: b,
        value: b,
      })),
      onFilter: (v, r) => r.benchmark === v,
    },
    {
      title: 'Accuracy',
      dataIndex: 'accuracy',
      key: 'accuracy',
      sorter: (a, b) => a.accuracy - b.accuracy,
      render: (_acc: number, r: RunSummary) => {
        const correct = Math.round(r.accuracy_total * r.total_tasks);
        return (
          <div>
            <Progress
              percent={Math.round(r.accuracy_total * 100)}
              size="small"
              status={r.accuracy_total >= 0.8 ? 'success' : r.accuracy_total >= 0.5 ? 'normal' : 'exception'}
            />
            <div style={{ fontSize: 11, color: '#595959', marginTop: 1 }}>
              {correct}/{r.total_tasks}
            </div>
            {r.num_samples > 1 && (
              <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 1 }}>
                Pass@{r.num_samples}: {Math.round(r.pass_at_k * 100)}%
                {' · '}
                Avg@{r.num_samples}: {Math.round(r.avg_at_k * 100)}%
              </div>
            )}
          </div>
        );
      },
      width: 200,
    },
    {
      title: 'Tasks',
      dataIndex: 'total_tasks',
      key: 'total_tasks',
      sorter: (a, b) => a.total_tasks - b.total_tasks,
      render: (total: number, r: RunSummary) => (
        <span>
          <Tag color="green">{r.completed}</Tag>/{total}
          {r.failed > 0 && <Tag color="red">{r.failed} err</Tag>}
        </span>
      ),
    },
    {
      title: 'Tokens',
      key: 'tokens',
      sorter: (a, b) =>
        a.total_tokens.prompt_tokens +
        a.total_tokens.completion_tokens -
        (b.total_tokens.prompt_tokens + b.total_tokens.completion_tokens),
      render: (_: unknown, r: RunSummary) => {
        const total =
          (r.total_tokens.prompt_tokens || 0) +
          (r.total_tokens.completion_tokens || 0);
        if (total === 0) return '-';
        return total > 1000 ? `${(total / 1000).toFixed(1)}K` : total;
      },
    },
    {
      title: 'Avg Time',
      dataIndex: 'mean_wall_time_sec',
      key: 'time',
      sorter: (a, b) => a.mean_wall_time_sec - b.mean_wall_time_sec,
      render: (t: number) => `${t.toFixed(1)}s`,
    },
    {
      title: 'Time',
      dataIndex: 'timestamp',
      key: 'timestamp',
      sorter: (a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''),
      defaultSortOrder: 'descend',
      render: (t: string) => (t ? new Date(t).toLocaleString() : '-'),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 80,
      render: (_: unknown, r: RunSummary) => (
        <Popconfirm
          title="Delete this run?"
          description="This will permanently delete all data (results, config, logs)."
          onConfirm={(e) => {
            e?.stopPropagation();
            handleDeleteRun(r.run_id);
          }}
          onCancel={(e) => e?.stopPropagation()}
        >
          <Button
            size="small"
            icon={<DeleteOutlined />}
            danger
            type="text"
            onClick={(e) => e.stopPropagation()}
          />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <Row gutter={[20, 20]} style={{ marginBottom: 28 }}>
        <Col span={6}>
          <Card
            style={{ borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <span style={{ fontSize: 18, color: '#595959' }}><ExperimentOutlined /></span>
              <span style={{ fontSize: 13, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.02em' }}>Total Runs</span>
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, color: '#141414', lineHeight: 1 }}>{runs.length}</div>
          </Card>
        </Col>
        <Col span={6}>
          <Card
            style={{ borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <Tooltip title={runs.length < 2 ? 'Add more runs to compare' : `Best across ${runs.length} runs`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                <span style={{ fontSize: 18, color: bestAccuracy >= 0.8 ? '#3f8600' : '#595959' }}><CheckCircleOutlined /></span>
                <span style={{ fontSize: 13, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.02em' }}>Best Accuracy</span>
              </div>
              <div style={{ fontSize: 32, fontWeight: 700, color: bestAccuracy >= 0.8 ? '#3f8600' : '#141414', lineHeight: 1 }}>
                {Math.round(bestAccuracy * 100)}<span style={{ fontSize: 18, fontWeight: 600, marginLeft: 2 }}>%</span>
              </div>
            </Tooltip>
          </Card>
        </Col>
        <Col span={6}>
          <Card
            style={{ borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <span style={{ fontSize: 18, color: '#595959' }}><ThunderboltOutlined /></span>
              <span style={{ fontSize: 13, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.02em' }}>Total Tasks</span>
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, color: '#141414', lineHeight: 1 }}>{totalTasks}</div>
          </Card>
        </Col>
        <Col span={6}>
          <Card
            style={{ borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <span style={{ fontSize: 18, color: '#595959' }}><DatabaseOutlined /></span>
              <span style={{ fontSize: 13, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.02em' }}>Total Tokens</span>
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, color: '#141414', lineHeight: 1 }}>
              {totalTokens > 1000 ? `${(totalTokens / 1000).toFixed(0)}K` : totalTokens}
            </div>
          </Card>
        </Col>
      </Row>

      <Card
        title={<span style={{ fontSize: 15, fontWeight: 600 }}>Evaluation Runs</span>}
        style={{ borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
        extra={
          <Space>
            <Input.Search
              placeholder="Search runs..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 250 }}
              allowClear
            />
            <Button icon={<ReloadOutlined />} onClick={fetchRuns} loading={loading} />
            <Popconfirm
              title={`Delete ${selectedKeys.length} selected run(s)?`}
              description="This will permanently delete all data for the selected runs."
              onConfirm={handleDeleteSelected}
              disabled={selectedKeys.length === 0}
            >
              <Button
                danger
                icon={<DeleteOutlined />}
                disabled={selectedKeys.length === 0}
                loading={deleting}
              >
                Delete ({selectedKeys.length})
              </Button>
            </Popconfirm>
            <Button
              type="primary"
              disabled={selectedKeys.length < 2}
              onClick={() =>
                navigate(`/compare?runs=${selectedKeys.join(',')}`)
              }
            >
              Compare ({selectedKeys.length})
            </Button>
          </Space>
        }
      >
        <Table<RunSummary>
          columns={columns}
          dataSource={filtered}
          rowKey="run_id"
          loading={loading}
          pagination={{ pageSize: 20 }}
          rowSelection={{
            selectedRowKeys: selectedKeys,
            onChange: (keys) => setSelectedKeys(keys as string[]),
          }}
          onRow={(record) => ({
            onClick: () => navigate(`/runs/${encodeURIComponent(record.run_id)}`),
            style: { cursor: 'pointer' },
          })}
          size="middle"
        />
      </Card>
    </div>
  );
}
