import { Column } from '@ant-design/charts';
import type { TaskResult } from '../types';

export default function TimeChart({ results }: { results: TaskResult[] }) {
  const data = results.map((r) => ({
    task: r.task_id,
    time: r.wall_time_sec,
  }));

  return (
    <Column
      data={data}
      xField="task"
      yField="time"
      style={{ fill: '#722ed1', maxWidth: 40, radiusTopLeft: 4, radiusTopRight: 4 }}
      axis={{
        x: { title: 'Task', labelAutoRotate: true },
        y: { title: 'Time (s)' },
      }}
      height={280}
    />
  );
}
