import { Column } from '@ant-design/charts';
import { Empty } from 'antd';
import { useState, useEffect, useRef } from 'react';
import type { TaskResult } from '../types';

function aggregateByTask(results: TaskResult[]) {
  const map = new Map<string, TaskResult[]>();
  for (const r of results) {
    if (!map.has(r.task_id)) map.set(r.task_id, []);
    map.get(r.task_id)!.push(r);
  }
  return Array.from(map.entries()).map(([task, samples]) => {
    const avgPrompt = samples.reduce((s, r) => s + (r.token_usage?.prompt_tokens ?? 0), 0) / samples.length;
    const avgCompletion = samples.reduce((s, r) => s + (r.token_usage?.completion_tokens ?? 0), 0) / samples.length;
    return { task, avgPrompt: Math.round(avgPrompt), avgCompletion: Math.round(avgCompletion) };
  });
}

function useContainerWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);
  return { ref, width };
}

export default function TokenChart({ results }: { results: TaskResult[] }) {
  const { ref, width } = useContainerWidth();

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

  const agg = aggregateByTask(results);
  const isMultiSample = results.length > agg.length;

  const data = agg.flatMap((a) => [
    { task: a.task, type: 'Prompt', tokens: a.avgPrompt },
    { task: a.task, type: 'Completion', tokens: a.avgCompletion },
  ]);

  return (
    <div ref={ref} style={{ overflow: 'hidden', minWidth: 0 }}>
      <Column
        key={width}
        data={data}
        xField="task"
        yField="tokens"
        colorField="type"
        stack
        autoFit
        scale={{
          color: {
            domain: ['Prompt', 'Completion'],
            range: ['#1677ff', '#69b1ff'],
          },
        }}
        axis={{
          x: { title: isMultiSample ? 'Task (avg)' : 'Task', labelAutoRotate: true },
          y: { title: 'Tokens' },
        }}
        height={280}
      />
    </div>
  );
}
