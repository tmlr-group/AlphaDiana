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
  message,
} from 'antd';
import {
  ExperimentOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { RunSummary } from '../types';
import { listRuns } from '../api';

export default function RunsListPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = runs.filter(
    (r) =>
      r.run_id.toLowerCase().includes(search.toLowerCase()) ||
      r.agent.toLowerCase().includes(search.toLowerCase()) ||
      r.benchmark.toLowerCase().includes(search.toLowerCase())
  );

  const bestAccuracy = runs.length
    ? Math.max(...runs.map((r) => r.accuracy))
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
      render: (acc: number, r: RunSummary) => (
        <div>
          <Progress
            percent={Math.round(acc * 100)}
            size="small"
            status={acc >= 0.8 ? 'success' : acc >= 0.5 ? 'normal' : 'exception'}
          />
          {r.num_samples > 1 && (
            <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 2 }}>
              Pass@{r.num_samples}: {Math.round(r.pass_at_k * 100)}%
              {' · '}
              Avg@{r.num_samples}: {Math.round(r.avg_at_k * 100)}%
            </div>
          )}
        </div>
      ),
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
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Runs"
              value={runs.length}
              prefix={<ExperimentOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Best Accuracy"
              value={Math.round(bestAccuracy * 100)}
              suffix="%"
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Tasks"
              value={totalTasks}
              prefix={<ThunderboltOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Tokens"
              value={totalTokens > 1000 ? `${(totalTokens / 1000).toFixed(0)}K` : totalTokens}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="Evaluation Runs"
        extra={
          <Space>
            <Input.Search
              placeholder="Search runs..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 250 }}
              allowClear
            />
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
