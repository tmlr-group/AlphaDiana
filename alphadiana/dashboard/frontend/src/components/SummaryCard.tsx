import { Card, Statistic, Row, Col, Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { RunSummary } from '../types';

export default function SummaryCard({ summary }: { summary: RunSummary }) {
  return (
    <Card style={{ marginBottom: 24 }}>
      <Row gutter={16} align="middle">
        <Col>
          <Tag color="blue" style={{ fontSize: 14, padding: '4px 12px' }}>
            {summary.agent}
          </Tag>
          <Tag color="cyan">{summary.agent_version}</Tag>
          <Tag color="purple">{summary.benchmark}</Tag>
          {summary.model && <Tag color="geekblue">{summary.model}</Tag>}
        </Col>
      </Row>
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={4}>
          <Statistic
            title="Accuracy"
            value={Math.round(summary.accuracy_total * 100)}
            suffix="%"
            prefix={<CheckCircleOutlined />}
            styles={{
              content: { color: summary.accuracy_total >= 0.8 ? '#3f8600' : '#cf1322' },
            }}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="Completed"
            value={summary.completed}
            suffix={`/ ${summary.total_tasks}`}
            prefix={<CheckCircleOutlined />}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="Failed"
            value={summary.failed}
            prefix={<CloseCircleOutlined />}
            styles={{ content: { color: summary.failed > 0 ? '#cf1322' : undefined } }}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="Avg Time"
            value={summary.mean_wall_time_sec.toFixed(1)}
            suffix="s"
            prefix={<ClockCircleOutlined />}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="Prompt Tokens"
            value={summary.total_tokens.prompt_tokens || 0}
            prefix={<ThunderboltOutlined />}
          />
        </Col>
        <Col span={4}>
          <Statistic
            title="Completion Tokens"
            value={summary.total_tokens.completion_tokens || 0}
            prefix={<ThunderboltOutlined />}
          />
        </Col>
      </Row>
      {summary.num_samples > 1 && (
        <Row gutter={16} style={{ marginTop: 16 }}>
          <Col span={4}>
            <Statistic
              title={`Samples (k)`}
              value={summary.num_samples}
              prefix={<ThunderboltOutlined />}
            />
          </Col>
          <Col span={4}>
            <Statistic
              title={`Pass@${summary.num_samples}`}
              value={Math.round(summary.pass_at_k * 100)}
              suffix="%"
              prefix={<CheckCircleOutlined />}
              styles={{
                content: { color: summary.pass_at_k >= 0.8 ? '#3f8600' : '#cf1322' },
              }}
            />
          </Col>
          <Col span={4}>
            <Statistic
              title={`Avg@${summary.num_samples}`}
              value={Math.round(summary.avg_at_k * 100)}
              suffix="%"
              prefix={<CheckCircleOutlined />}
            />
          </Col>
        </Row>
      )}
    </Card>
  );
}
