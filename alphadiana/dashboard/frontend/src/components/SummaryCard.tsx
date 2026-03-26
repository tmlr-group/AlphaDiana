import React from 'react';
import { Card, Row, Col, Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { RunSummary } from '../types';

function StatItem({
  label,
  value,
  suffix,
  icon,
  color,
}: {
  label: string;
  value: string | number;
  suffix?: string;
  icon: React.ReactNode;
  color?: string;
}) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 13, color: '#8c8c8c' }}>{icon}</span>
        <span style={{ fontSize: 12, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.02em' }}>{label}</span>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color ?? '#141414', lineHeight: 1 }}>
        {value}
        {suffix && <span style={{ fontSize: 15, fontWeight: 500, marginLeft: 2, color: color ?? '#595959' }}>{suffix}</span>}
      </div>
    </div>
  );
}

export default function SummaryCard({ summary }: { summary: RunSummary }) {
  const accuracyColor = summary.accuracy_total >= 0.8 ? '#3f8600' : '#cf1322';

  return (
    <Card style={{ marginBottom: 20, borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <Tag color="blue" style={{ fontSize: 13, padding: '3px 10px', fontWeight: 600 }}>
          {summary.agent}
        </Tag>
        <Tag color="cyan">{summary.agent_version}</Tag>
        <Tag color="purple">{summary.benchmark}</Tag>
        {summary.model && <Tag color="geekblue">{summary.model}</Tag>}
      </div>
      <Row gutter={[32, 16]}>
        <Col>
          <StatItem
            label="Accuracy"
            value={Math.round(summary.accuracy_total * 100)}
            suffix="%"
            icon={<CheckCircleOutlined />}
            color={accuracyColor}
          />
        </Col>
        <Col>
          <StatItem
            label="Completed"
            value={`${summary.completed} / ${summary.total_tasks}`}
            icon={<CheckCircleOutlined />}
          />
        </Col>
        <Col>
          <StatItem
            label="Failed"
            value={summary.failed}
            icon={<CloseCircleOutlined />}
            color={summary.failed > 0 ? '#cf1322' : undefined}
          />
        </Col>
        <Col>
          <StatItem
            label="Avg Time"
            value={summary.mean_wall_time_sec.toFixed(1)}
            suffix="s"
            icon={<ClockCircleOutlined />}
          />
        </Col>
        <Col>
          <StatItem
            label="Prompt Tokens"
            value={summary.total_tokens.prompt_tokens || 0}
            icon={<ThunderboltOutlined />}
          />
        </Col>
        <Col>
          <StatItem
            label="Completion Tokens"
            value={summary.total_tokens.completion_tokens || 0}
            icon={<ThunderboltOutlined />}
          />
        </Col>
        {summary.num_samples > 1 && (
          <>
            <Col>
              <StatItem
                label="Samples (k)"
                value={summary.num_samples}
                icon={<ThunderboltOutlined />}
              />
            </Col>
            <Col>
              <StatItem
                label={`Pass@${summary.num_samples}`}
                value={Math.round(summary.pass_at_k * 100)}
                suffix="%"
                icon={<CheckCircleOutlined />}
                color={summary.pass_at_k >= 0.8 ? '#3f8600' : '#cf1322'}
              />
            </Col>
            <Col>
              <StatItem
                label={`Avg@${summary.num_samples}`}
                value={Math.round(summary.avg_at_k * 100)}
                suffix="%"
                icon={<CheckCircleOutlined />}
              />
            </Col>
          </>
        )}
      </Row>
    </Card>
  );
}
