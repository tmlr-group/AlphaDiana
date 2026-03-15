import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card,
  Table,
  Tag,
  Button,
  Progress,
  Space,
  Popconfirm,
  Drawer,
  Typography,
  message,
} from 'antd';
import {
  PlusOutlined,
  ReloadOutlined,
  ArrowLeftOutlined,
  StopOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { JobStatus } from '../types';
import { listJobs, getJob, cancelJob, getJobLogs } from '../api';

const { Text } = Typography;

const STATUS_COLORS: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [logDrawer, setLogDrawer] = useState<{ jobId: string; runId: string } | null>(null);
  const [logs, setLogs] = useState<string>('');
  const [logsLoading, setLogsLoading] = useState(false);
  const navigate = useNavigate();
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const logTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const fetchJobs = () => {
    listJobs()
      .then(setJobs)
      .catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchJobs();
    timerRef.current = setInterval(() => {
      listJobs().then((data) => {
        setJobs(data);
        data
          .filter((j) => j.status === 'running')
          .forEach((j) => {
            getJob(j.job_id).then((updated) => {
              setJobs((prev) =>
                prev.map((p) => (p.job_id === updated.job_id ? updated : p))
              );
            });
          });
      });
    }, 5000);
    return () => clearInterval(timerRef.current);
  }, []);

  // Auto-refresh logs when drawer is open
  const openLogs = (jobId: string, runId: string) => {
    setLogDrawer({ jobId, runId });
    setLogsLoading(true);
    getJobLogs(jobId)
      .then((data) => setLogs(data.logs))
      .catch(() => setLogs('Failed to load logs'))
      .finally(() => setLogsLoading(false));

    // Auto-refresh logs every 3s
    logTimerRef.current = setInterval(() => {
      getJobLogs(jobId)
        .then((data) => setLogs(data.logs))
        .catch(() => {});
    }, 3000);
  };

  const closeLogs = () => {
    setLogDrawer(null);
    setLogs('');
    clearInterval(logTimerRef.current);
  };

  const handleCancel = async (jobId: string) => {
    try {
      await cancelJob(jobId);
      message.success('Job cancelled');
      fetchJobs();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : 'Failed to cancel');
    }
  };

  const columns: ColumnsType<JobStatus> = [
    {
      title: 'Run ID',
      dataIndex: 'run_id',
      key: 'run_id',
      render: (id: string, job: JobStatus) =>
        job.status === 'completed' ? (
          <a onClick={() => navigate(`/runs/${encodeURIComponent(id)}`)}>{id}</a>
        ) : (
          <Text>{id}</Text>
        ),
    },
    {
      title: 'Agent',
      dataIndex: 'agent',
      key: 'agent',
    },
    {
      title: 'Model',
      dataIndex: 'model',
      key: 'model',
      render: (model: string) =>
        model ? <Text ellipsis={{ tooltip: model }} style={{ maxWidth: 180 }}>{model}</Text> : <Text type="secondary">-</Text>,
    },
    {
      title: 'Benchmark',
      dataIndex: 'benchmark',
      key: 'benchmark',
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] || 'default'}>{status.toUpperCase()}</Tag>
      ),
    },
    {
      title: 'Progress',
      key: 'progress',
      width: 200,
      render: (_: unknown, job: JobStatus) => {
        if (job.total_tasks === 0 && job.status === 'running') {
          return <Progress percent={0} status="active" size="small" />;
        }
        if (job.total_tasks > 0) {
          const pct = Math.round((job.progress / job.total_tasks) * 100);
          return (
            <Progress
              percent={pct}
              size="small"
              status={job.status === 'failed' ? 'exception' : job.status === 'running' ? 'active' : undefined}
              format={() => `${job.progress}/${job.total_tasks}`}
            />
          );
        }
        return <Text type="secondary">{job.progress} tasks</Text>;
      },
    },
    {
      title: 'Accuracy',
      dataIndex: 'accuracy',
      key: 'accuracy',
      render: (acc: number | null) =>
        acc !== null ? `${(acc * 100).toFixed(1)}%` : '-',
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (t: string) => (t ? new Date(t).toLocaleString() : '-'),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 280,
      render: (_: unknown, job: JobStatus) => (
        <Space>
          <Button
            size="small"
            icon={<FileTextOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              openLogs(job.job_id, job.run_id);
            }}
          >
            Logs
          </Button>
          {(job.status === 'running' || job.status === 'pending') && (
            <Popconfirm
              title="Cancel this job?"
              onConfirm={() => handleCancel(job.job_id)}
            >
              <Button size="small" danger icon={<StopOutlined />}>
                Cancel
              </Button>
            </Popconfirm>
          )}
          {job.status === 'completed' && (
            <Button
              size="small"
              type="link"
              onClick={() => navigate(`/runs/${encodeURIComponent(job.run_id)}`)}
            >
              View Results
            </Button>
          )}
          {job.status === 'failed' && job.error && (
            <Text type="danger" style={{ fontSize: 12 }}>
              {job.error.length > 60 ? job.error.slice(0, 60) + '...' : job.error}
            </Text>
          )}
        </Space>
      ),
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
        Back
      </Button>

      <Card
        title="Evaluation Jobs"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchJobs}>
              Refresh
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/jobs/new')}
            >
              New Evaluation
            </Button>
          </Space>
        }
      >
        <Table<JobStatus>
          columns={columns}
          dataSource={jobs}
          rowKey="job_id"
          loading={loading}
          pagination={false}
          locale={{ emptyText: 'No jobs yet. Click "New Evaluation" to get started.' }}
        />
      </Card>

      <Drawer
        title={
          logDrawer ? (
            <Space>
              <FileTextOutlined />
              <span>Logs: {logDrawer.runId}</span>
            </Space>
          ) : ''
        }
        open={!!logDrawer}
        onClose={closeLogs}
        placement="bottom"
        height={420}
        styles={{ body: { padding: 0 } }}
      >
        {logsLoading ? (
          <div style={{ padding: 24 }}>
            <Text type="secondary">Loading logs...</Text>
          </div>
        ) : logs ? (
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
            {logs.split('\n').map((line, i) => {
              let color = '#333';
              if (line.includes('[ERROR]')) color = '#cf1322';
              else if (line.includes('[WARNING]')) color = '#d48806';
              else if (line.includes('[INFO]')) color = '#595959';
              return (
                <div key={i} style={{ color, padding: '1px 0' }}>
                  <span style={{ color: '#bfbfbf', marginRight: 12, userSelect: 'none' }}>
                    {String(i + 1).padStart(3)}
                  </span>
                  {line}
                </div>
              );
            })}
          </pre>
        ) : (
          <div style={{ padding: 24 }}>
            <Text type="secondary">No logs available yet.</Text>
          </div>
        )}
      </Drawer>
    </div>
  );
}
