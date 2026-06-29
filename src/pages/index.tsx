import Hero from '@site/src/components/Hero';
import FeaturesGrid from '@site/src/components/FeaturesGrid';
import InstallBox from '@site/src/components/InstallBox';
import Scoreboard from '@site/src/components/Scoreboard';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import React, { ReactNode } from 'react';
import {
  StandardizedIcon,
  AgentIcon,
  SandboxIcon,
  BalanceIcon,
  TargetIcon,
  TraceIcon,
  DashboardIcon,
  ConcurrencyIcon,
} from '@site/src/components/icons/FeatureIcons';
import styles from './index.module.css';

const features = [
  {
    icon: BalanceIcon,
    title: 'Harness-Aware Evaluation',
    description: 'Score the full model-harness system, not the model alone — with Direct (no-harness) as a control.',
  },
  {
    icon: TargetIcon,
    title: 'Controlled Comparison',
    description: 'Pair the same task, scorer, sandbox, and budget with different agents — turning leaderboards into experiments.',
  },
  {
    icon: AgentIcon,
    title: 'Open Harnesses + Direct',
    description: 'OpenClaw, ZeroClaw, and OpenCode, plus a no-harness baseline for every model.',
  },
  {
    icon: StandardizedIcon,
    title: 'Verifiable Benchmarks',
    description: 'IMO-AnswerBench, HLE-Verifiable, AIME 26, GPQA-Diamond, and MMMU-Pro — clean correctness signals.',
  },
  {
    icon: TraceIcon,
    title: 'Trajectory Attribution',
    description: 'Full trace logging attributes success and failure to reasoning, tool use, verification, and budget.',
  },
  {
    icon: SandboxIcon,
    title: 'Sandboxed Execution',
    description: 'Isolated environments with resource limits, reset semantics, and provenance logging.',
  },
  {
    icon: ConcurrencyIcon,
    title: 'Macro + Micro Studies',
    description: 'Outcome comparisons of direct vs. agentic execution, plus ablations of tools, skills, and memory.',
  },
  {
    icon: DashboardIcon,
    title: 'Reproducible & Auditable',
    description: 'Reports regenerate from saved records; an interactive dashboard makes the whole loop inspectable.',
  },
];

// Architecture diagram (hidden for now — see the "One run, fully factorized"
// note below to re-enable):
// const archChart = `flowchart TD
//   C["Config — budgets, policies"] --> U["Runner"] ...`;

const scope = [
  {
    count: '3',
    label: 'Open models',
    items: ['Qwen3.5-27B', 'Gemma-4-31B-IT', 'Kimi-K2.6'],
  },
  {
    count: '3 + 1',
    label: 'Harnesses + Direct',
    items: ['OpenClaw', 'ZeroClaw', 'OpenCode', 'Direct (no harness)'],
  },
  {
    count: '5',
    label: 'Verifiable benchmarks',
    items: ['IMO-AnswerBench', 'HLE-Verifiable', 'AIME 26', 'GPQA-Diamond', 'MMMU-Pro'],
  },
];

const findings = [
  {
    tag: 'Model',
    title: 'Harnessing is not model-agnostic',
    body: 'The same agent layer consistently degrades Qwen3.5-27B, consistently benefits Gemma-4-31B-IT, and yields mixed outcomes for Kimi-K2.6.',
  },
  {
    tag: 'Task',
    title: 'Benefits are task-dependent',
    body: 'Harnesses help most when a task rewards stable computation or answer selection, but become unstable on knowledge-intensive tasks where they distract from the model’s own knowledge.',
  },
  {
    tag: 'Harness',
    title: 'No harness is uniformly best',
    body: 'ZeroClaw and OpenCode give more stable gains across compatible settings, while OpenClaw has the widest envelope — both the strongest improvements and the most severe drops.',
  },
];

const installCommand = `git clone https://github.com/tmlr-group/AlphaDiana
cd AlphaDiana

# One-click setup: conda env, dependencies, services
bash scripts/quickstart.sh

# Pull the reasoning image (OpenClaw pre-installed)
docker pull tmlrgroup/alphadiana:v1`;

const runCommand = `# Activate the environment (once per terminal)
source scripts/activate.sh

# Check services, then run an evaluation
alphadiana env
alphadiana run configs/test_openclaw_quick.yaml

# Generate a report
alphadiana report results/`;

export default function Home(): ReactNode {
  return (
    <Layout
      title="AlphaDiana"
      description="AlphaDiana: harness-aware evaluation of open agents on verifiable reasoning tasks.">
      <main>
        <div className={styles.container}>
          <Hero
            title="AlphaDiana"
            description="Harness-Aware Evaluation of Open Agents on Verifiable Reasoning Tasks"
            primaryButtonText="Get Started"
            primaryButtonLink="/docs/intro"
            secondaryButtonText="GitHub"
            secondaryButtonLink="https://github.com/tmlr-group/AlphaDiana"
          />

          <section className={styles.motivationBand}>
            <figure className={styles.motivationFigure}>
              <img src="/img/motivation.png" alt="From model-centric to harness-aware evaluation" loading="lazy" />
              <figcaption>
                <strong>Reasoning is more than the model.</strong> A foundation model is an <em>engine</em> scored
                prompt-in / answer-out; an agent is the <em>car</em> that embeds it in a runtime of control, tools,
                state, and sandbox. AlphaDiana is the <em>tournament organizer</em> — it fixes the rules across
                agents and logs canonical traces for analyzing correctness, efficiency, reliability, and failures.
              </figcaption>
            </figure>
          </section>

          <section className={styles.introSection}>
            <div className={styles.introContent}>
              <p>
                Open agents such as OpenClaw are <strong>model-harness systems</strong>: the model supplies
                reasoning capacity, while the harness realizes it through tool use, multi-turn control, execution
                feedback, and memory. Existing evaluations either test models without a harness or fix a single
                harness — conflating base-model reasoning with harness orchestration. AlphaDiana makes the
                evaluation boundary explicit, factorizing each run into a benchmark, model, harness, environment,
                scorer, and budget, so you can ask <strong>whether gains come from a stronger model or a specific
                harness</strong>.
              </p>
            </div>
          </section>

          <section className={styles.scopeSection}>
            <div className={styles.scopeInner}>
              {scope.map((s) => (
                <div key={s.label} className={styles.scopeCard}>
                  <div className={styles.scopeCount}>{s.count}</div>
                  <div className={styles.scopeLabel}>{s.label}</div>
                  <div className={styles.scopeChips}>
                    {s.items.map((it) => (
                      <span key={it} className={styles.chip}>
                        {it}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <FeaturesGrid title="What AlphaDiana standardizes" features={features} background="white" />

          <Scoreboard />

          <section className={styles.findingsSection}>
            <div className={styles.findingsInner}>
              <p className={styles.kicker}>Key Findings</p>
              <h2>What harness-aware evaluation reveals</h2>
              <div className={styles.findingsGrid}>
                {findings.map((f) => (
                  <div key={f.tag} className={styles.findingCard}>
                    <span className={styles.findingTag}>{f.tag} perspective</span>
                    <h3>{f.title}</h3>
                    <p>{f.body}</p>
                  </div>
                ))}
              </div>
              <p className={styles.findingsNote}>
                A micro-level study goes further: stripping tools or adding skill libraries has{' '}
                <strong>non-monotonic</strong> effects — they help compatible model-harness settings but can
                distract, over-constrain, or destabilize otherwise strong direct reasoning.
              </p>
            </div>
          </section>

          {/* "One run, fully factorized" architecture section hidden per request.
              Re-enable by restoring the <section className={styles.flowSection}> block
              with the <Mermaid value={archChart} /> diagram. */}

          <section id="dashboard" className={styles.dashboardSection}>
            <div className={styles.dashboardContent}>
              <p className={styles.kicker}>Dashboard</p>
              <h2>Launch, monitor, and compare — visually</h2>
              <p className={styles.flowLead}>
                Configure a run from explicit fields, then inspect aggregate outcomes and per-task behavior —
                accuracy, completion, sample efficiency, token usage, and runtime — all computed from saved records.
              </p>
              <div className={styles.dashboardGrid}>
                <figure className={styles.dashShot}>
                  <img src="/img/dashboard-create.png" alt="Creating a new evaluation in the dashboard" loading="lazy" />
                  <figcaption>Create a run from explicit configuration fields.</figcaption>
                </figure>
                <figure className={styles.dashShot}>
                  <img src="/img/dashboard-results.png" alt="Evaluation results in the dashboard" loading="lazy" />
                  <figcaption>Inspect results — here a 30/30 AIME 26 run at 97% Pass@4, 93% Avg@4.</figcaption>
                </figure>
              </div>
              <div className={styles.dashboardActions}>
                <Link className="button button--primary button--lg" to="/docs/architecture/scoring-and-results">
                  Results &amp; reporting
                </Link>
              </div>
            </div>
          </section>

          <section className={styles.quickstartSection}>
            <div className={styles.quickstartContent}>
              <p className={styles.kicker}>Quick Start</p>
              <h2>Run your first evaluation</h2>
              <p className={styles.flowLead}>
                Linux, Python ≥ 3.10, Conda, and Docker required. See the{' '}
                <Link to="/docs/getting-started/installation">getting-started guide</Link> for the full walkthrough.
              </p>
              <div className={styles.installGrid}>
                <div>
                  <h3>1. Install</h3>
                  <InstallBox command={installCommand} language="bash" />
                </div>
                <div>
                  <h3>2. Run</h3>
                  <InstallBox command={runCommand} language="bash" />
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </Layout>
  );
}
