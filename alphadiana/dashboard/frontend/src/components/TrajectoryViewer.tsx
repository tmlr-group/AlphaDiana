import { Card, Tag, Typography, Collapse } from 'antd';
import type { TrajectoryStep } from '../types';

const { Text, Paragraph } = Typography;

const roleColors: Record<string, string> = {
  user: 'blue',
  assistant: 'green',
  system: 'orange',
  tool: 'purple',
};

export default function TrajectoryViewer({
  trajectory,
}: {
  trajectory: TrajectoryStep[];
}) {
  if (!trajectory || trajectory.length === 0) {
    return <Text type="secondary">No trajectory data</Text>;
  }

  return (
    <div style={{ maxHeight: 600, overflowY: 'auto' }}>
      {trajectory.map((step, i) => (
        <Card
          key={`${step.role}_${i}`}
          size="small"
          style={{ marginBottom: 8 }}
          title={
            <Tag color={roleColors[step.role] || 'default'}>{step.role}</Tag>
          }
        >
          {step.thinking && (
            <Collapse
              size="small"
              items={[
                {
                  key: 'thinking',
                  label: <Text type="secondary">Thinking</Text>,
                  children: (
                    <Paragraph
                      style={{
                        whiteSpace: 'pre-wrap',
                        fontSize: 12,
                        maxHeight: 300,
                        overflow: 'auto',
                        background: '#f6f6f6',
                        padding: 8,
                        borderRadius: 4,
                      }}
                    >
                      {step.thinking}
                    </Paragraph>
                  ),
                },
              ]}
              style={{ marginBottom: 8 }}
            />
          )}
          <Paragraph
            style={{
              whiteSpace: 'pre-wrap',
              fontSize: 13,
              marginBottom: 0,
              maxHeight: 400,
              overflow: 'auto',
            }}
          >
            {step.content || '(empty)'}
          </Paragraph>
          {step.tool_calls && step.tool_calls.length > 0 && (
            <Collapse
              size="small"
              items={step.tool_calls.map((tc, j) => ({
                key: j,
                label: (
                  <span>
                    <Tag color="volcano">{tc.tool}</Tag> {tc.id}
                  </span>
                ),
                children: (
                  <pre style={{ fontSize: 11, margin: 0 }}>
                    {JSON.stringify(tc.input, null, 2)}
                  </pre>
                ),
              }))}
              style={{ marginTop: 8 }}
            />
          )}
        </Card>
      ))}
    </div>
  );
}
