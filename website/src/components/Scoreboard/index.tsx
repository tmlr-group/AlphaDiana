import React, { useState } from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

/**
 * Scoreboard for AlphaDiana's main result.
 *
 * AlphaDiana's thesis is harness-aware evaluation: an agent is a model-harness
 * system, and the same harness can help one model while hurting another. This
 * board stages selected rows from the paper's main result: the Direct
 * (no-harness) baseline vs. open harnesses, with green/red deltas marking
 * where harnessing improves or degrades the base model.
 *
 * Source: AlphaDiana paper, Table 1, "End-to-end performance of
 * model-harness systems on verifiable reasoning tasks." All values are % (higher
 * is better). AIME 26 reports Pass@4 / Avg@4; other benchmarks report Avg@1.
 */

interface Row {
  harness: string;
  vals: number[];
  deltas: number[] | null; // null for the Direct baseline
}

interface Model {
  id: string;
  name: string;
  verdict: 'hurts' | 'helps' | 'mixed';
  takeaway: string;
  rows: Row[];
}

const metrics = [
  { name: 'IMO-AnswerBench', metric: 'Avg@1' },
  { name: 'HLE-Verifiable', metric: 'Avg@1' },
  { name: 'GPQA-Diamond', metric: 'Avg@1' },
  { name: 'AIME 26', metric: 'Pass@4' },
  { name: 'AIME 26', metric: 'Avg@4' },
  { name: 'MMMU-Pro', metric: 'Avg@1' },
];

const models: Model[] = [
  {
    id: 'qwen',
    name: 'Qwen3.5-27B',
    verdict: 'hurts',
    takeaway: 'Draft table: Direct inference leads every harness row in this selected slice.',
    rows: [
      { harness: 'Direct', vals: [58.3, 23.0, 81.3, 96.7, 89.2, 73.4], deltas: null },
      { harness: 'OpenClaw', vals: [20.3, 13.4, 66.2, 83.3, 64.2, 68.3], deltas: [-38.0, -9.6, -15.1, -13.4, -25.0, -5.1] },
      { harness: 'ZeroClaw', vals: [17.5, 15.0, 77.8, 86.7, 66.7, 67.2], deltas: [-40.8, -8.0, -3.5, -10.0, -22.5, -6.2] },
      { harness: 'OpenCode', vals: [15.8, 13.9, 73.2, 86.7, 69.2, 69.4], deltas: [-42.5, -9.1, -8.1, -10.0, -20.0, -4.0] },
    ],
  },
  {
    id: 'gemma',
    name: 'Gemma-4-31B-IT',
    verdict: 'helps',
    takeaway: 'Draft table: several harness rows improve on IMO, GPQA, and AIME in this selected slice.',
    rows: [
      { harness: 'Direct', vals: [59.0, 27.9, 83.3, 96.7, 92.5, 65.8], deltas: null },
      { harness: 'OpenClaw', vals: [59.5, 24.2, 85.4, 100.0, 97.5, 56.8], deltas: [0.5, -3.7, 2.1, 3.3, 5.0, -9.0] },
      { harness: 'ZeroClaw', vals: [61.5, 29.1, 86.4, 100.0, 96.7, 66.4], deltas: [2.5, 1.2, 3.1, 3.3, 4.2, 0.6] },
      { harness: 'OpenCode', vals: [62.5, 24.0, 87.9, 100.0, 96.7, 67.4], deltas: [3.5, -3.9, 4.6, 3.3, 4.2, 1.6] },
    ],
  },
  {
    id: 'kimi',
    name: 'Kimi-K2.6',
    verdict: 'mixed',
    takeaway: 'Draft table: the selected cells contain a mixture of gains and losses against Direct.',
    rows: [
      { harness: 'Direct', vals: [42.0, 35.9, 77.8, 96.7, 85.8, 75.1], deltas: null },
      { harness: 'OpenClaw', vals: [27.3, 40.7, 31.8, 93.3, 72.5, 48.6], deltas: [-14.7, 4.8, -46.0, -3.4, -13.3, -26.5] },
      { harness: 'ZeroClaw', vals: [38.7, 33.7, 87.4, 100.0, 93.3, 64.7], deltas: [-3.3, -2.2, 9.6, 3.3, 7.5, -10.4] },
      { harness: 'OpenCode', vals: [48.5, 33.9, 80.8, 100.0, 86.7, 71.3], deltas: [6.5, -2.0, 3.0, 3.3, 0.9, -3.8] },
    ],
  },
];

const verdictLabel: Record<Model['verdict'], string> = {
  hurts: 'Draft: lower',
  helps: 'Draft: higher',
  mixed: 'Mixed',
};

function Cell({ value, delta }: { value: number; delta: number | null | undefined }) {
  if (delta === null || delta === undefined) {
    return <td className={styles.baseCell}>{value.toFixed(1)}</td>;
  }
  const sign = delta > 0 ? 'pos' : delta < 0 ? 'neg' : 'zero';
  return (
    <td className={clsx(styles.deltaCell, styles[sign])}>
      <span className={styles.cellVal}>{value.toFixed(1)}</span>
      <span className={styles.cellDelta}>
        {delta > 0 ? '+' : ''}
        {delta.toFixed(1)}
      </span>
    </td>
  );
}

export default function Scoreboard() {
  const [active, setActive] = useState(1); // default Gemma (clear "helps" story)
  const model = models[active];

  return (
    <section id="results" className={clsx('section-gray', styles.section)}>
      <div className={styles.inner}>
        <p className={styles.kicker}>Tournament Results</p>
        <h2 className={styles.heading}>Same harness, opposite outcomes</h2>
        <p className={styles.subhead}>
          The manuscript table compares the <strong>Direct</strong> no-harness baseline with open harnesses under matched
          models, tasks, scorers, and shared budgets while recording harness-specific runtime conditions. In the draft
          table, harnessing is <strong>not</strong> a uniform upgrade: it lifts Gemma,
          sinks Qwen3.5, and produces mixed behavior on Kimi-K2.6. <span className={styles.legendPos}>Green</span>{' '}
          means the harness beat Direct;{' '}
          <span className={styles.legendNeg}>red</span> means it lost ground.
        </p>

        <div className={styles.tabs} role="group" aria-label="Select model results">
          {models.map((m, i) => (
            <button
              key={m.id}
              aria-pressed={i === active}
              className={clsx(styles.tab, i === active && styles.activeTab)}
              onClick={() => setActive(i)}
              type="button">
              {m.name}
            </button>
          ))}
        </div>

        <div className={styles.boardWrap}>
          <div className={styles.boardTop}>
            <span className={styles.modelName}>{model.name}</span>
            <span className={clsx(styles.verdict, styles[`v_${model.verdict}`])}>{verdictLabel[model.verdict]}</span>
          </div>

          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.cornerTh}>Harness</th>
                  {metrics.map((m, i) => (
                    <th key={i}>
                      <span className={styles.mName}>{m.name}</span>
                      <span className={styles.mMetric}>{m.metric}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {model.rows.map((row) => (
                  <tr key={row.harness} className={clsx(row.deltas === null && styles.baseRow)}>
                    <th scope="row" className={styles.rowHead}>
                      {row.harness}
                      {row.deltas === null && <span className={styles.baseTag}>baseline</span>}
                    </th>
                    {row.vals.map((v, i) => (
                      <Cell key={i} value={v} delta={row.deltas ? row.deltas[i] : null} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className={styles.takeaway}>{model.takeaway}</p>
        </div>

        <p className={styles.footnote}>
          Values are percentages (higher is better); deltas are absolute differences vs. Direct. These are selected
          rows from the draft manuscript table, not current repository support evidence. Raw task-level artifacts are
          not included in this checkout; cite exact values only with the corresponding archived runs.
        </p>
      </div>
    </section>
  );
}
