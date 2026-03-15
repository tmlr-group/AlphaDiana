import { Tooltip, Typography, Switch } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, WarningFilled } from '@ant-design/icons';
import { useState } from 'react';
import type { TaskResult } from '../types';

const { Text } = Typography;

/** Group results by task_id (multi-sample runs share the same task_id with different sample_index). */
function groupByTask(results: TaskResult[]) {
  const map = new Map<string, TaskResult[]>();
  for (const r of results) {
    const base = r.task_id;
    if (!map.has(base)) map.set(base, []);
    map.get(base)!.push(r);
  }
  // Sort samples within each task by sample_index.
  for (const samples of map.values()) {
    samples.sort((a, b) => (a.sample_index ?? 0) - (b.sample_index ?? 0));
  }
  return map;
}

const CELL = 26;
const GAP = 3;

function CellDot({ r, size = CELL }: { r: TaskResult; size?: number }) {
  const isCorrect = r.correct === true;
  const isWrong = r.correct === false;
  const bg = isCorrect ? '#52c41a' : isWrong ? '#ff4d4f' : '#faad14';

  const tip = (
    <div style={{ fontSize: 12 }}>
      <div><strong>{r.task_id}</strong></div>
      <div>Answer: {String(r.predicted ?? '-')}</div>
      <div>Expected: {String(r.ground_truth ?? '-')}</div>
      {r.wall_time_sec > 0 && <div>Time: {r.wall_time_sec.toFixed(1)}s</div>}
      {(r.score ?? null) !== null && <div>Score: {r.score}</div>}
    </div>
  );

  return (
    <Tooltip title={tip}>
      <div
        style={{
          width: size,
          height: size,
          borderRadius: 4,
          background: bg,
          cursor: 'default',
          flexShrink: 0,
          transition: 'transform 0.1s',
        }}
        onMouseEnter={(e) => { (e.target as HTMLElement).style.transform = 'scale(1.2)'; }}
        onMouseLeave={(e) => { (e.target as HTMLElement).style.transform = 'scale(1)'; }}
      />
    </Tooltip>
  );
}

export default function ScoreChart({ results }: { results: TaskResult[] }) {
  const [showMatrix, setShowMatrix] = useState(false);

  const grouped = groupByTask(results);
  const nSamples = Math.max(...Array.from(grouped.values(), (v) => v.length));
  const isMultiSample = nSamples > 1;

  // Summary stats (always from real data)
  const totalCorrect = results.filter((r) => r.correct === true).length;
  const totalWrong = results.filter((r) => r.correct === false).length;
  const totalError = results.filter((r) => r.correct === null).length;
  const totalTasks = grouped.size;

  // Per-task pass rate
  const taskStats = Array.from(grouped.entries()).map(([base, samples]) => {
    const c = samples.filter((s) => s.correct === true).length;
    return { base, samples, correct: c, total: samples.length, rate: c / samples.length };
  });

  const showingMatrix = showMatrix || isMultiSample;

  return (
    <div>
      {/* Summary */}
      <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <Text strong style={{ color: '#52c41a' }}>
          <CheckCircleFilled /> {totalCorrect}
        </Text>
        <Text strong style={{ color: '#ff4d4f' }}>
          <CloseCircleFilled /> {totalWrong}
        </Text>
        {totalError > 0 && (
          <Text strong style={{ color: '#faad14' }}>
            <WarningFilled /> {totalError}
          </Text>
        )}
        <Text type="secondary">
          {totalTasks} tasks
          {isMultiSample && ` \u00d7 ${nSamples} samples`}
          {' \u00b7 '}
          {results.length > 0 ? (totalCorrect / results.length * 100).toFixed(1) : 0}%
        </Text>
        {/* Matrix toggle */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>Samples matrix</Text>
          <Switch size="small" checked={showingMatrix} onChange={setShowMatrix} disabled={isMultiSample} />
        </div>
      </div>

      {/* Matrix view */}
      {showingMatrix ? (
        <div style={{ overflowX: 'auto' }}>
          {/* Column header (only if multi-sample) */}
          {isMultiSample && (
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 2, paddingLeft: 90 }}>
              {Array.from({ length: nSamples }, (_, i) => (
                <div
                  key={i}
                  style={{
                    width: CELL,
                    marginRight: GAP,
                    textAlign: 'center',
                    fontSize: 10,
                    color: '#8c8c8c',
                  }}
                >
                  s{i}
                </div>
              ))}
              <div style={{ width: 60, textAlign: 'center', fontSize: 10, color: '#8c8c8c', marginLeft: 8 }}>
                pass rate
              </div>
            </div>
          )}

          {/* Rows: one per task */}
          {taskStats.map(({ base, samples, correct: c, total, rate }) => (
            <div
              key={base}
              style={{
                display: 'flex',
                alignItems: 'center',
                marginBottom: GAP,
              }}
            >
              {/* Row label */}
              <div
                style={{
                  width: 84,
                  marginRight: 6,
                  fontSize: 12,
                  fontFamily: 'monospace',
                  color: '#595959',
                  textAlign: 'right',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                }}
                title={base}
              >
                {base}
              </div>

              {/* Sample cells */}
              {samples.map((s, i) => (
                <div key={i} style={{ marginRight: GAP }}>
                  <CellDot r={s} />
                </div>
              ))}

              {/* Pass rate badge */}
              <div
                style={{
                  marginLeft: 8,
                  fontSize: 12,
                  fontWeight: 600,
                  color: rate >= 0.8 ? '#52c41a' : rate >= 0.5 ? '#faad14' : '#ff4d4f',
                  width: 60,
                  textAlign: 'center',
                }}
              >
                {c}/{total} ({(rate * 100).toFixed(0)}%)
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Default: labeled cells grid */
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {results.map((r) => {
            const isCorrect = r.correct === true;
            const isWrong = r.correct === false;
            const bg = isCorrect ? '#f6ffed' : isWrong ? '#fff2f0' : '#fffbe6';
            const border = isCorrect ? '#b7eb8f' : isWrong ? '#ffccc7' : '#ffe58f';
            const icon = isCorrect ? (
              <CheckCircleFilled style={{ color: '#52c41a' }} />
            ) : isWrong ? (
              <CloseCircleFilled style={{ color: '#ff4d4f' }} />
            ) : (
              <WarningFilled style={{ color: '#faad14' }} />
            );

            const tipContent = (
              <div>
                <div><strong>{r.task_id}</strong></div>
                <div>Answer: {String(r.predicted ?? '-')}</div>
                <div>Expected: {String(r.ground_truth ?? '-')}</div>
                {r.wall_time_sec > 0 && <div>Time: {r.wall_time_sec.toFixed(1)}s</div>}
              </div>
            );

            return (
              <Tooltip key={r.task_id} title={tipContent}>
                <div
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '4px 10px',
                    borderRadius: 6,
                    background: bg,
                    border: `1px solid ${border}`,
                    cursor: 'default',
                    fontSize: 12,
                    minWidth: 70,
                  }}
                >
                  {icon}
                  <span style={{ fontFamily: 'monospace' }}>{r.task_id}</span>
                </div>
              </Tooltip>
            );
          })}
        </div>
      )}
    </div>
  );
}
