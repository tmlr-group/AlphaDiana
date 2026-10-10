import { Tooltip, Typography, Popover, Switch } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, WarningFilled } from '@ant-design/icons';
import { useState } from 'react';
import type { TaskResult } from '../types';

const { Text } = Typography;

function groupByTask(results: TaskResult[]) {
  const map = new Map<string, TaskResult[]>();
  for (const r of results) {
    const base = r.task_id;
    if (!map.has(base)) map.set(base, []);
    map.get(base)!.push(r);
  }
  for (const samples of map.values()) {
    samples.sort((a, b) => (a.sample_index ?? 0) - (b.sample_index ?? 0));
  }
  return map;
}

function rateColor(rate: number): string {
  if (rate >= 0.8) return '#52c41a';
  if (rate >= 0.5) return '#faad14';
  if (rate >= 0.3) return '#fa8c16';
  return '#ff4d4f';
}

function rateBg(rate: number): string {
  if (rate >= 0.8) return '#f6ffed';
  if (rate >= 0.5) return '#fffbe6';
  if (rate >= 0.3) return '#fff7e6';
  return '#fff2f0';
}

function rateBorder(rate: number): string {
  if (rate >= 0.8) return '#b7eb8f';
  if (rate >= 0.5) return '#ffe58f';
  if (rate >= 0.3) return '#ffd591';
  return '#ffccc7';
}

/** Mini sample dots shown in popover */
function SampleDots({ samples }: { samples: TaskResult[] }) {
  return (
    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', maxWidth: 280 }}>
      {samples.map((s, i) => {
        const bg = s.correct === true ? '#52c41a' : s.correct === false ? '#ff4d4f' : '#faad14';
        return (
          <Tooltip
            key={i}
            title={
              <div style={{ fontSize: 11 }}>
                <div>s{s.sample_index}: {s.correct === true ? 'PASS' : s.correct === false ? 'FAIL' : 'ERR'}</div>
                <div>Predicted: {String(s.predicted ?? '-')}</div>
                {s.wall_time_sec > 0 && <div>Time: {s.wall_time_sec.toFixed(1)}s</div>}
              </div>
            }
          >
            <div style={{ width: 12, height: 12, borderRadius: 2, background: bg, cursor: 'default' }} />
          </Tooltip>
        );
      })}
    </div>
  );
}

/** Single-sample view: one chip per result */
function SingleSampleView({ results }: { results: TaskResult[] }) {
  return (
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
  );
}

const CELL = 20;
const GAP = 2;

/** Matrix view: rows = tasks, columns = samples */
function MatrixView({ taskStats, nSamples }: {
  taskStats: { base: string; samples: TaskResult[]; correct: number; total: number; rate: number }[];
  nSamples: number;
}) {
  return (
    <div style={{ overflowX: 'auto' }}>
      {/* Column header */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 1, paddingLeft: 74 }}>
        {Array.from({ length: nSamples }, (_, i) => (
          <div
            key={i}
            style={{
              width: CELL,
              marginRight: GAP,
              textAlign: 'center',
              fontSize: 9,
              color: '#8c8c8c',
            }}
          >
            {i}
          </div>
        ))}
        <div style={{ width: 52, textAlign: 'center', fontSize: 9, color: '#8c8c8c', marginLeft: 6 }}>
          rate
        </div>
      </div>

      {/* Rows */}
      {taskStats.map(({ base, samples, correct: c, total, rate }) => (
        <div
          key={base}
          style={{
            display: 'flex',
            alignItems: 'center',
            marginBottom: GAP,
          }}
        >
          <div
            style={{
              width: 68,
              marginRight: 4,
              fontSize: 11,
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
          {samples.map((s, i) => {
            const bg = s.correct === true ? '#52c41a' : s.correct === false ? '#ff4d4f' : '#faad14';
            return (
              <Tooltip
                key={i}
                title={
                  <div style={{ fontSize: 12 }}>
                    <div><strong>{s.task_id}</strong> · s{s.sample_index}</div>
                    <div>Answer: {String(s.predicted ?? '-')}</div>
                    <div>Expected: {String(s.ground_truth ?? '-')}</div>
                    {s.wall_time_sec > 0 && <div>Time: {s.wall_time_sec.toFixed(1)}s</div>}
                  </div>
                }
              >
                <div
                  style={{
                    width: CELL,
                    height: CELL,
                    borderRadius: 3,
                    background: bg,
                    marginRight: GAP,
                    cursor: 'default',
                    flexShrink: 0,
                  }}
                />
              </Tooltip>
            );
          })}
          <div
            style={{
              marginLeft: 6,
              fontSize: 11,
              fontWeight: 600,
              color: rateColor(rate),
              width: 52,
              textAlign: 'center',
              flexShrink: 0,
            }}
          >
            {c}/{total}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Card view: one card per task colored by pass rate */
function CardView({ taskStats }: {
  taskStats: { base: string; samples: TaskResult[]; correct: number; total: number; rate: number }[];
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {taskStats.map(({ base, samples, correct: c, total, rate }) => (
        <Popover
          key={base}
          title={
            <span>
              <strong>{base}</strong>
              <span style={{ marginLeft: 8, color: rateColor(rate), fontWeight: 600 }}>
                {c}/{total} ({(rate * 100).toFixed(0)}%)
              </span>
            </span>
          }
          content={
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 6 }}>
                Expected: {String(samples[0]?.ground_truth ?? '-')}
              </div>
              <SampleDots samples={samples} />
            </div>
          }
          placement="bottom"
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              padding: '8px 14px',
              borderRadius: 8,
              background: rateBg(rate),
              border: `1px solid ${rateBorder(rate)}`,
              cursor: 'default',
              minWidth: 80,
              transition: 'transform 0.15s, box-shadow 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#595959' }}>{base}</span>
            <span style={{ fontSize: 18, fontWeight: 700, color: rateColor(rate), lineHeight: 1.2 }}>
              {(rate * 100).toFixed(0)}%
            </span>
            <span style={{ fontSize: 11, color: '#8c8c8c' }}>{c}/{total}</span>
          </div>
        </Popover>
      ))}
    </div>
  );
}

export default function ScoreChart({ results }: { results: TaskResult[] }) {
  const [showMatrix, setShowMatrix] = useState(false);

  const grouped = groupByTask(results);
  const nSamples = Math.max(...Array.from(grouped.values(), (v) => v.length));
  const isMultiSample = nSamples > 1;

  const totalCorrect = results.filter((r) => r.correct === true).length;
  const totalWrong = results.filter((r) => r.correct === false).length;
  const totalError = results.filter((r) => r.correct === null).length;
  const totalTasks = grouped.size;

  const taskStats = Array.from(grouped.entries()).map(([base, samples]) => {
    const c = samples.filter((s) => s.correct === true).length;
    return { base, samples, correct: c, total: samples.length, rate: c / samples.length };
  });

  return (
    <div>
      {/* Summary line */}
      <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <Text strong style={{ color: '#52c41a', fontSize: 14 }}>
          <CheckCircleFilled /> {totalCorrect}
        </Text>
        <Text strong style={{ color: '#ff4d4f', fontSize: 14 }}>
          <CloseCircleFilled /> {totalWrong}
        </Text>
        {totalError > 0 && (
          <Text strong style={{ color: '#faad14', fontSize: 14 }}>
            <WarningFilled /> {totalError}
          </Text>
        )}
        <Text type="secondary" style={{ fontSize: 13 }}>
          {totalTasks} tasks
          {isMultiSample && ` \u00d7 ${nSamples} samples`}
          {' \u00b7 '}
          {results.length > 0 ? (totalCorrect / results.length * 100).toFixed(1) : 0}%
        </Text>
        {isMultiSample && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>Samples matrix</Text>
            <Switch size="small" checked={showMatrix} onChange={setShowMatrix} />
          </div>
        )}
      </div>

      {/* Content */}
      {isMultiSample ? (
        showMatrix
          ? <MatrixView taskStats={taskStats} nSamples={nSamples} />
          : <CardView taskStats={taskStats} />
      ) : (
        <SingleSampleView results={results} />
      )}
    </div>
  );
}
