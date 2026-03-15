import { Column } from '@ant-design/charts';
import { Empty } from 'antd';
import type { TaskResult } from '../types';

export default function TokenChart({ results }: { results: TaskResult[] }) {
  // Check if any task has non-zero token data
  const hasTokenData = results.some(
    (r) =>
      (r.token_usage?.prompt_tokens ?? 0) > 0 ||
      (r.token_usage?.completion_tokens ?? 0) > 0
  );

  if (!hasTokenData) {
    return (
      <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Empty
          description="Token usage data not available. The agent or gateway did not report token counts."
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      </div>
    );
  }

  const data = results.flatMap((r) => [
    {
      task: r.task_id,
      type: 'Prompt',
      tokens: r.token_usage?.prompt_tokens ?? 0,
    },
    {
      task: r.task_id,
      type: 'Completion',
      tokens: r.token_usage?.completion_tokens ?? 0,
    },
  ]);

  return (
    <Column
      data={data}
      xField="task"
      yField="tokens"
      colorField="type"
      stack
      scale={{
        color: {
          domain: ['Prompt', 'Completion'],
          range: ['#1677ff', '#69b1ff'],
        },
      }}
      axis={{
        x: { title: 'Task', labelAutoRotate: true },
        y: { title: 'Tokens' },
      }}
      height={280}
    />
  );
}
