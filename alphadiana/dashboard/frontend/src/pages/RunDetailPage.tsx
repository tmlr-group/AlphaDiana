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

type FilterMode = 'all' | 'correct' | 'wrong';

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

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (!detail) return <Text>Run not found</Text>;

  const filteredResults = detail.results.filter((r) => {
    if (filter === 'correct') return r.correct === true;
    if (filter === 'wrong') return r.correct === false || r.correct === null;
    return true;
  });

  const columns: ColumnsType<TaskResult> = [
    {
      title: 'Task ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 120,
      sorter: (a, b) => a.task_id.localeCompare(b.task_id),
    },
    {
      title: 'Problem',
      dataIndex: 'problem',
      key: 'problem',
      ellipsis: true,
      render: (text: string) => (
        <Text style={{ fontSize: 12 }}>{text.slice(0, 120)}...</Text>
      ),
    },
    {
      title: 'Expected',
      dataIndex: 'ground_truth',
      key: 'ground_truth',
      width: 100,
      render: (v: unknown) => <Tag>{String(v)}</Tag>,
    },
    {
      title: 'Predicted',
      dataIndex: 'predicted',
      key: 'predicted',
      width: 100,
      render: (v: unknown) => <Tag>{String(v)}</Tag>,
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
        const total =
          (r.token_usage?.prompt_tokens ?? 0) +
          (r.token_usage?.completion_tokens ?? 0);
        return total > 1000 ? `${(total / 1000).toFixed(1)}K` : total;
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
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Card title="Score by Task" size="small">
                  <ScoreChart results={detail.results} />
                </Card>
                <Card title="Token Usage by Task" size="small">
                  <TokenChart results={detail.results} />
                </Card>
                <Card title="Time by Task" size="small" style={{ gridColumn: '1 / -1' }}>
                  <TimeChart results={detail.results} />
                </Card>
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
                      Pass ({detail.results.filter((r) => r.correct).length})
                    </Radio.Button>
                    <Radio.Button value="wrong">
                      Fail (
                      {
                        detail.results.filter(
                          (r) => r.correct === false || r.correct === null
                        ).length
                      }
                      )
                    </Radio.Button>
                  </Radio.Group>
                }
              >
                <Table<TaskResult>
                  columns={columns}
                  dataSource={filteredResults}
                  rowKey="task_id"
                  pagination={false}
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
        title={drawerTask ? `Task: ${drawerTask.task_id}` : ''}
        open={!!drawerTask}
        onClose={() => setDrawerTask(null)}
        width={720}
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
                <Text strong>Predicted:</Text> {String(drawerTask.predicted)}
              </p>
              <p>
                <Text strong>Correct:</Text>{' '}
                {drawerTask.correct === true ? (
                  <Tag color="success">PASS</Tag>
                ) : (
                  <Tag color="error">FAIL</Tag>
                )}
              </p>
              <p>
                <Text strong>Rationale:</Text> {drawerTask.rationale}
              </p>
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
