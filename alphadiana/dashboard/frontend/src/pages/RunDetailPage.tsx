import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card,
  Table,
  Tag,
  Tabs,
  Drawer,
  Typography,
  Spin,
  Radio,
  Button,
  message,
} from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { RunDetail, TaskResult } from '../types';
import { getRun } from '../api';
import SummaryCard from '../components/SummaryCard';
import ScoreChart from '../components/ScoreChart';
import TokenChart from '../components/TokenChart';
import TimeChart from '../components/TimeChart';
import TrajectoryViewer from '../components/TrajectoryViewer';

const { Paragraph, Text } = Typography;

type FilterMode = 'all' | 'correct' | 'wrong' | 'error';

export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterMode>('all');
  const [drawerTask, setDrawerTask] = useState<TaskResult | null>(null);

  useEffect(() => {
    if (!runId) return;
    getRun(runId)
      .then(setDetail)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) return <Spin size="large" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 'calc(100vh - 96px)' }} />;
  if (!detail) return (
    <div>
      <Button
        icon={<ArrowLeftOutlined />}
        type="text"
        onClick={() => navigate('/')}
        style={{ marginBottom: 16 }}
      >
        Back to Runs
      </Button>
      <div>
        <Text type="secondary">Run not found</Text>
      </div>
    </div>
  );

  const filteredResults = detail.results.filter((r) => {
    if (filter === 'correct') return r.correct === true;
    if (filter === 'wrong') return r.correct === false;
    if (filter === 'error') return r.correct === null;
    return true;
  });

  const countPass = detail.results.filter((r) => r.correct === true).length;
  const countFail = detail.results.filter((r) => r.correct === false).length;
  const countError = detail.results.filter((r) => r.correct === null).length;

  const isMultiSample = detail.summary.num_samples > 1;

  const columns: ColumnsType<TaskResult> = [
    {
      title: 'Task ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 120,
      sorter: (a, b) => a.task_id.localeCompare(b.task_id),
    },
    ...(isMultiSample
      ? [
          {
            title: 'Sample',
            dataIndex: 'sample_index',
            key: 'sample_index',
            width: 80,
            sorter: (a: TaskResult, b: TaskResult) =>
              a.sample_index - b.sample_index,
            render: (si: number) => `s${si}`,
          },
        ]
      : []),
    {
      title: 'Problem',
      dataIndex: 'problem',
      key: 'problem',
      ellipsis: true,
      render: (text: string) => (
        <Text style={{ fontSize: 12 }}>{text.length > 120 ? text.slice(0, 120) + '...' : text}</Text>
      ),
    },
    {
      title: 'Expected',
      dataIndex: 'ground_truth',
      key: 'ground_truth',
      width: 100,
      render: (v: unknown) => <Tag>{v != null ? String(v) : '-'}</Tag>,
    },
    {
      title: 'Predicted',
      dataIndex: 'predicted',
      key: 'predicted',
      width: 100,
      render: (v: unknown) => <Tag>{v != null ? String(v) : '-'}</Tag>,
    },
    {
      title: 'Result',
      dataIndex: 'correct',
      key: 'correct',
      width: 80,
      filters: [
        { text: 'Correct', value: true },
        { text: 'Wrong', value: false },
      ],
      onFilter: (v, r) => r.correct === v,
      render: (correct: boolean | null) =>
        correct === true ? (
          <Tag color="success">PASS</Tag>
        ) : correct === false ? (
          <Tag color="error">FAIL</Tag>
        ) : (
          <Tag color="warning">ERR</Tag>
        ),
    },
    {
      title: 'Score',
      dataIndex: 'score',
      key: 'score',
      width: 80,
      sorter: (a, b) => (a.score ?? 0) - (b.score ?? 0),
      render: (s: number | null) =>
        s !== null ? s.toFixed(2) : '-',
    },
    {
      title: 'Time (s)',
      dataIndex: 'wall_time_sec',
      key: 'time',
      width: 100,
      sorter: (a, b) => a.wall_time_sec - b.wall_time_sec,
      render: (t: number) => t.toFixed(1),
    },
    {
      title: 'Tokens',
      key: 'tokens',
      width: 100,
      render: (_: unknown, r: TaskResult) => {
        const prompt = r.token_usage?.prompt_tokens;
        const completion = r.token_usage?.completion_tokens;
        if (prompt == null && completion == null) return '-';
        const total = (prompt ?? 0) + (completion ?? 0);
        return total === 0 ? '-' : total > 1000 ? `${(total / 1000).toFixed(1)}K` : total;
      },
    },
  ];

  return (
    <div>
      <Button
        icon={<ArrowLeftOutlined />}
        type="text"
        onClick={() => navigate('/')}
        style={{ marginBottom: 16 }}
      >
        Back to Runs
      </Button>

      <SummaryCard summary={detail.summary} />

      <Tabs
        defaultActiveKey="charts"
        items={[
          {
            key: 'charts',
            label: 'Charts',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title="Score by Task" size="small">
                  <ScoreChart results={detail.results} />
                </Card>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: 16 }}>
                  <Card title="Token Usage by Task" size="small" style={{ overflow: 'hidden', minWidth: 0 }}>
                    <div style={{ overflow: 'hidden', minWidth: 0 }}>
                      <TokenChart results={detail.results} />
                    </div>
                  </Card>
                  <Card title="Time by Task" size="small" style={{ overflow: 'hidden', minWidth: 0 }}>
                    <div style={{ overflow: 'hidden', minWidth: 0 }}>
                      <TimeChart results={detail.results} />
                    </div>
                  </Card>
                </div>
              </div>
            ),
          },
          {
            key: 'results',
            label: 'Results Table',
            children: (
              <Card
                size="small"
                extra={
                  <Radio.Group
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    optionType="button"
                    buttonStyle="solid"
                    size="small"
                  >
                    <Radio.Button value="all">
                      All ({detail.results.length})
                    </Radio.Button>
                    <Radio.Button value="correct">
                      Pass ({countPass})
                    </Radio.Button>
                    <Radio.Button value="wrong">
                      Fail ({countFail})
                    </Radio.Button>
                    {countError > 0 && (
                      <Radio.Button value="error">
                        Error ({countError})
                      </Radio.Button>
                    )}
                  </Radio.Group>
                }
              >
                <Table<TaskResult>
                  columns={columns}
                  dataSource={filteredResults}
                  rowKey={(r) => `${r.task_id}_s${r.sample_index}`}
                  pagination={{
                    pageSize: 50,
                    showSizeChanger: true,
                    pageSizeOptions: ['20', '50', '100'],
                    hideOnSinglePage: true,
                    showTotal: (total, range) => `${range[0]}-${range[1]} / ${total} items`,
                  }}
                  size="small"
                  onRow={(record) => ({
                    onClick: () => setDrawerTask(record),
                    style: { cursor: 'pointer' },
                  })}
                />
              </Card>
            ),
          },
          {
            key: 'config',
            label: 'Config',
            children: (
              <Card size="small">
                <pre style={{ fontSize: 12, margin: 0 }}>
                  {detail.config
                    ? JSON.stringify(detail.config, null, 2)
                    : 'No config found'}
                </pre>
              </Card>
            ),
          },
        ]}
        style={{ marginTop: 16 }}
      />

      <Drawer
        title={
          drawerTask
            ? isMultiSample
              ? `Task: ${drawerTask.task_id}  ·  sample ${drawerTask.sample_index}`
              : `Task: ${drawerTask.task_id}`
            : ''
        }
        open={!!drawerTask}
        onClose={() => setDrawerTask(null)}
        styles={{ wrapper: { width: '720px' } }}
      >
        {drawerTask && (
          <div>
            <Card size="small" title="Problem" style={{ marginBottom: 16 }}>
              <Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>
                {drawerTask.problem}
              </Paragraph>
            </Card>

            <Card size="small" title="Result" style={{ marginBottom: 16 }}>
              <p>
                <Text strong>Expected:</Text> {String(drawerTask.ground_truth)}
              </p>
              <p>
                <Text strong>Predicted:</Text> {drawerTask.predicted != null ? String(drawerTask.predicted) : '-'}
              </p>
              <p>
                <Text strong>Result:</Text>{' '}
                {drawerTask.correct === true ? (
                  <Tag color="success">PASS</Tag>
                ) : drawerTask.correct === false ? (
                  <Tag color="error">FAIL</Tag>
                ) : (
                  <Tag color="warning">ERROR</Tag>
                )}
                {drawerTask.finish_reason === 'length' && (
                  <Tag color="orange">Truncated</Tag>
                )}
              </p>
              {drawerTask.error && (
                <p>
                  <Text strong>Error:</Text>{' '}
                  <Text type="danger">
                    {String(drawerTask.error.error_type ?? '')}
                    {drawerTask.error.message ? `: ${String(drawerTask.error.message)}` : ''}
                  </Text>
                </p>
              )}
              {drawerTask.rationale && (
                <p>
                  <Text strong>Rationale:</Text> {drawerTask.rationale}
                </p>
              )}
            </Card>

<Card size="small" title="Agent Trajectory">
              <TrajectoryViewer trajectory={drawerTask.trajectory} />
            </Card>
          </div>
        )}
      </Drawer>
    </div>
  );
}
