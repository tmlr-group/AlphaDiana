import React from 'react';
import { Card, Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { RunSummary } from '../types';

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(0)}K`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

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
    <div style={{ flex: '0 0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
        <span style={{ fontSize: 11, color: '#8c8c8c' }}>{icon}</span>
        <span style={{ fontSize: 10, color: '#8c8c8c', fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{label}</span>
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? '#141414', lineHeight: 1.1, whiteSpace: 'nowrap' }}>
        {value}
        {suffix && <span style={{ fontSize: 12, fontWeight: 500, marginLeft: 1, color: color ?? '#595959' }}>{suffix}</span>}
      </div>
    </div>
  );
}

export default function SummaryCard({ summary }: { summary: RunSummary }) {
  const accuracyColor = summary.accuracy_total >= 0.8 ? '#3f8600' : '#cf1322';

  return (
    <Card
      style={{
        marginBottom: 16,
        borderRadius: 12,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03)',
        border: '1px solid #f0f0f0',
      }}
      styles={{ body: { padding: '14px 20px' } }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px', fontWeight: 600, borderRadius: 6 }}>
          {summary.agent}
        </Tag>
        <Tag color="cyan" style={{ borderRadius: 6 }}>{summary.agent_version}</Tag>
        <Tag color="purple" style={{ borderRadius: 6 }}>{summary.benchmark}</Tag>
        {summary.model && <Tag color="geekblue" style={{ borderRadius: 6 }}>{summary.model}</Tag>}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px 24px', flexWrap: 'wrap' }}>
        <StatItem
          label="Accuracy"
          value={Math.round(summary.accuracy_total * 100)}
          suffix="%"
          icon={<CheckCircleOutlined />}
          color={accuracyColor}
        />
        <StatItem
          label="Completed"
          value={`${summary.completed}/${summary.total_tasks}`}
          icon={<CheckCircleOutlined />}
        />
        <StatItem
          label="Failed"
          value={summary.failed}
          icon={<CloseCircleOutlined />}
          color={summary.failed > 0 ? '#cf1322' : undefined}
        />
        <StatItem
          label="Avg Time"
          value={summary.mean_wall_time_sec.toFixed(1)}
          suffix="s"
          icon={<ClockCircleOutlined />}
        />
        <StatItem
          label="Prompt Tok"
          value={fmtNum(summary.total_tokens.prompt_tokens || 0)}
          icon={<ThunderboltOutlined />}
        />
        <StatItem
          label="Compl Tok"
          value={fmtNum(summary.total_tokens.completion_tokens || 0)}
          icon={<ThunderboltOutlined />}
        />
        {summary.num_samples > 1 && (
          <>
            <StatItem
              label="Samples"
              value={summary.num_samples}
              icon={<ThunderboltOutlined />}
            />
            <StatItem
              label={`Pass@${summary.num_samples}`}
              value={Math.round(summary.pass_at_k * 100)}
              suffix="%"
              icon={<CheckCircleOutlined />}
              color={summary.pass_at_k >= 0.8 ? '#3f8600' : '#cf1322'}
            />
            <StatItem
              label={`Avg@${summary.num_samples}`}
              value={Math.round(summary.avg_at_k * 100)}
              suffix="%"
              icon={<CheckCircleOutlined />}
            />
          </>
        )}
      </div>
    </Card>
  );
}
