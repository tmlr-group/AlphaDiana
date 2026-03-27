import { Column } from '@ant-design/charts';
import { useState, useEffect, useRef } from 'react';
import type { TaskResult } from '../types';

function aggregateByTask(results: TaskResult[]) {
  const map = new Map<string, TaskResult[]>();
  for (const r of results) {
    if (!map.has(r.task_id)) map.set(r.task_id, []);
    map.get(r.task_id)!.push(r);
  }
  return Array.from(map.entries()).map(([task, samples]) => {
    const avgTime = samples.reduce((s, r) => s + r.wall_time_sec, 0) / samples.length;
    return { task, time: parseFloat(avgTime.toFixed(1)) };
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

export default function TimeChart({ results }: { results: TaskResult[] }) {
  const { ref, width } = useContainerWidth();
  const agg = aggregateByTask(results);
  const isMultiSample = results.length > agg.length;

  return (
    <div ref={ref} style={{ overflow: 'hidden', minWidth: 0 }}>
      <Column
        key={width}
        data={agg}
        xField="task"
        yField="time"
        autoFit
        style={{ fill: '#722ed1', maxWidth: 40, radiusTopLeft: 4, radiusTopRight: 4 }}
        axis={{
          x: { title: isMultiSample ? 'Task (avg)' : 'Task', labelAutoRotate: true },
          y: { title: 'Time (s)' },
        }}
        height={280}
      />
    </div>
  );
}
