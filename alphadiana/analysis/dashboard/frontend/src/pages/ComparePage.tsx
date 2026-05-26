import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Card, Table, Tag, Statistic, Row, Col, Spin, Button, Typography, message } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Column } from '@ant-design/charts';
import type { CompareRunEntry } from '../types';
import { compareRuns } from '../api';

const { Text } = Typography;

export default function ComparePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [entries, setEntries] = useState<CompareRunEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const runIds = (searchParams.get('runs') || '').split(',').filter(Boolean);

  useEffect(() => {
    if (runIds.length < 2) {
      setLoading(false);
      return;
    }
    compareRuns(runIds)
      .then(setEntries)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, [searchParams.get('runs')]);

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (entries.length === 0) return <Text>No runs to compare</Text>;

  // Collect all task IDs across runs
  const allTaskIds = [
    ...new Set(entries.flatMap((e) => Object.keys(e.results_by_task))),
  ].sort();

  // Summary comparison chart data
  const summaryData = entries.map((e) => ({
    run: e.run_id.length > 25 ? e.run_id.slice(0, 25) + '...' : e.run_id,
    accuracy: Math.round(e.summary.accuracy_total * 100),
  }));

  // Build table columns: Task ID + one column per run
  const columns = [
    {
      title: 'Task ID',
      dataIndex: 'task_id',
      key: 'task_id',
      fixed: 'left' as const,
      width: 120,
    },
    ...entries.map((entry) => ({
      title: entry.run_id.length > 20 ? entry.run_id.slice(0, 20) + '...' : entry.run_id,
      key: entry.run_id,
      width: 150,
      render: (_: unknown, row: { task_id: string }) => {
        const result = entry.results_by_task[row.task_id];
        if (!result) return <Tag>N/A</Tag>;
        return (
          <span>
            {result.correct === true ? (
              <Tag color="success">PASS</Tag>
            ) : result.correct === false ? (
              <Tag color="error">FAIL</Tag>
            ) : (
              <Tag color="warning">ERR</Tag>
            )}
            <Text style={{ fontSize: 11, marginLeft: 4 }}>
              {result.score !== null ? result.score.toFixed(1) : '-'}
            </Text>
          </span>
        );
      },
    })),
  ];

  const tableData = allTaskIds.map((tid) => ({ task_id: tid, key: tid }));

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

      <Row gutter={16} style={{ marginBottom: 24 }}>
        {entries.map((e) => (
          <Col key={e.run_id} span={Math.min(8, Math.floor(24 / entries.length))}>
            <Card size="small" title={e.run_id}>
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic
                    title="Accuracy"
                    value={Math.round(e.summary.accuracy_total * 100)}
                    suffix="%"
                    styles={{
                      content: { color: e.summary.accuracy_total >= 0.8 ? '#3f8600' : '#cf1322' },
                    }}
                  />
                </Col>
                <Col span={8}>
                  <Statistic title="Tasks" value={e.summary.total_tasks} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="Avg Time"
                    value={e.summary.mean_wall_time_sec.toFixed(1)}
                    suffix="s"
                  />
                </Col>
              </Row>
              {e.summary.num_samples > 1 && (
                <Row gutter={16} style={{ marginTop: 8 }}>
                  <Col span={8}>
                    <Statistic
                      title={`Pass@${e.summary.num_samples}`}
                      value={Math.round(e.summary.pass_at_k * 100)}
                      suffix="%"
                    />
                  </Col>
                  <Col span={8}>
                    <Statistic
                      title={`Avg@${e.summary.num_samples}`}
                      value={Math.round(e.summary.avg_at_k * 100)}
                      suffix="%"
                    />
                  </Col>
                </Row>
              )}
            </Card>
          </Col>
        ))}
      </Row>

      <Card title="Accuracy Comparison" style={{ marginBottom: 24 }} size="small">
        <Column
          data={summaryData}
          xField="run"
          yField="accuracy"
          style={{ maxWidth: 80, radiusTopLeft: 8, radiusTopRight: 8 }}
          scale={{ y: { domainMax: 100 } }}
          axis={{
            x: { title: 'Run' },
            y: { title: 'Accuracy (%)' },
          }}
          height={250}
          label={{
            text: (d: { accuracy: number }) => `${d.accuracy}%`,
            textBaseline: 'bottom',
          }}
        />
      </Card>

      <Card title="Per-Task Comparison" size="small">
        <Table
          columns={columns}
          dataSource={tableData}
          pagination={false}
          scroll={{ x: 'max-content' }}
          size="small"
        />
      </Card>
    </div>
  );
}
