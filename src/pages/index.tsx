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
    description: 'Treat the evaluated unit as model plus harness, with Direct inference as the control.',
  },
  {
    icon: TargetIcon,
    title: 'Controlled Comparison',
    description: 'Hold the task, scorer, sandbox, and budget fixed while swapping the harness layer.',
  },
  {
    icon: AgentIcon,
    title: 'Open Harness Interfaces',
    description: 'Run direct inference and reasoning agent harnesses through one shared result schema.',
  },
  {
    icon: StandardizedIcon,
    title: 'Verifiable Reasoning Tasks',
    description: 'Use deterministic or programmatic scorers for math, science, multimodal, and coding tasks.',
  },
  {
    icon: TraceIcon,
    title: 'Trajectory Attribution',
    description: 'Log actions, observations, resource use, termination states, and scoring metadata.',
  },
  {
    icon: SandboxIcon,
    title: 'Sandboxed Execution',
    description: 'Run tool-using agents in isolated environments with explicit resource and reset policies.',
  },
  {
    icon: ConcurrencyIcon,
    title: 'Macro And Micro Studies',
    description: 'Compare whole harnesses, then ablate tools, skills, and memory to explain the score shifts.',
  },
  {
    icon: DashboardIcon,
    title: 'Reproducible Reports',
    description: 'Regenerate summaries from saved records and inspect the evaluation loop in the dashboard.',
  },
];

const macroFindings = [
  {
    tag: 'Model',
    title: 'Harnessing is not model-agnostic',
    body: 'The draft manuscript reports different harness effects across Qwen3.5-27B, Gemma-4-31B-IT, and Kimi-K2.6. Scores should be read as properties of model-harness-task triples.',
  },
  {
    tag: 'Task',
    title: 'Benefits depend on task structure',
    body: 'Harnesses help most when a task rewards stable computation or answer selection. They become unstable when tool use distracts from internal knowledge.',
  },
  {
    tag: 'Harness',
    title: 'No harness is uniformly best',
    body: 'ZeroClaw and OpenCode provide more stable gains in compatible settings, while OpenClaw has the widest envelope of improvements and failures.',
  },
];

const macroFigures = [
  {
    src: '/img/analysis/action-composition-qwen.png',
    title: 'Qwen3.5 action composition',
    caption: 'Correct and wrong trajectories allocate effort differently across reasoning, tool use, verification, and finalization.',
  },
  {
    src: '/img/analysis/action-composition-gemma.png',
    title: 'Gemma action composition',
    caption: 'The same action taxonomy makes outcome-conditioned process shifts comparable across model-harness cells.',
  },
  {
    src: '/img/analysis/post-tool-entropy-qwen.png',
    title: 'Qwen3.5 post-tool entropy',
    caption: 'Tool feedback is useful only when it reduces uncertainty instead of extending an unstable trajectory.',
  },
  {
    src: '/img/analysis/post-tool-entropy-gemma.png',
    title: 'Gemma post-tool entropy',
    caption: 'Entropy after tool calls helps separate productive feedback loops from long-tail failures.',
  },
];

const microFindings = [
  {
    tag: 'Tool',
    title: 'Tool access is harness-conditioned',
    body: 'The draft ablations report different tool-condition effects across ZeroClaw and OpenCode. Tool filtering also changes prompt conditions, so this is not a pure one-variable causal estimate.',
  },
  {
    tag: 'Skill',
    title: 'Skill loading depends on selection behavior',
    body: 'Qwen3.5 preferentially reads the math skill set over the general skill set, and the harness changes how sharply that preference appears.',
  },
  {
    tag: 'Memory',
    title: 'Memory behavior is harness-specific',
    body: 'Memory comparisons must name the persistence boundary and complete prompt/runtime condition. The current checkout ships only one Cross-Task memory config.',
  },
];

const microFigures = [
  {
    src: '/img/micro/skill-use-qwen-zeroclaw.png',
    title: 'Qwen3.5 + ZeroClaw skill use',
    caption: 'Math skills are consulted more often than general skills on reasoning tasks.',
  },
  {
    src: '/img/micro/skill-use-qwen-opencode.png',
    title: 'Qwen3.5 + OpenCode skill use',
    caption: 'OpenCode compresses the gap between math and general skill read rates.',
  },
  {
    src: '/img/micro/memory-cross-task-opencode.png',
    title: 'OpenCode cross-task memory',
    caption: 'Replayed prior solutions raise accuracy as the run progresses.',
  },
  {
    src: '/img/micro/memory-cross-task-openclaw.png',
    title: 'OpenClaw cross-task memory',
    caption: 'Retrieved notes distract the run and widen the gap from the no-memory baseline.',
  },
  {
    src: '/img/micro/memory-cross-task-zeroclaw.png',
    title: 'ZeroClaw cross-task memory',
    caption: 'The same memory intervention remains below the matched full-harness baseline.',
  },
];

const installCommand = `git clone https://github.com/tmlr-group/AlphaDiana
cd AlphaDiana

# Required by the security preflight in the current release
export OPENCLAW_GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Checkout-local conda env, dependencies, and services
bash scripts/quickstart.sh`;

const runCommand = `# Activate the environment once per terminal
source scripts/activate.sh

# Point DirectLLM at an OpenAI-compatible provider
export OPENAI_MODEL="your-model"
export OPENAI_API_BASE="https://your-provider.example/v1"
export OPENAI_API_KEY="your-provider-key"

# Validate and run a one-task GPQA DirectLLM smoke
alphadiana validate configs/examples/direct_llm_gpqa_diamond.yaml \\
  -o benchmark.config.max_tasks=1
alphadiana run configs/examples/direct_llm_gpqa_diamond.yaml \\
  -o run_id=quickstart_gpqa_directllm_t1_k1 \\
  -o benchmark.config.max_tasks=1 \\
  -o num_samples=1 \\
  -o max_concurrent=1

# Generate a report from the smoke run
alphadiana report results`;

export default function Home(): ReactNode {
  return (
    <Layout
      title="AlphaDiana"
      description="AlphaDiana: A System for Evaluating Reasoning Agents.">
      <main>
        <div className={styles.container}>
          <Hero
            title="AlphaDiana"
            description="A System for Evaluating Reasoning Agents"
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
                with prompt-in and answer-out evaluation. An agent is the <em>car</em> that embeds it in control
                flow, tools, state, policies, and sandbox constraints. AlphaDiana is the <em>tournament organizer</em>:
                it fixes the rules across agents and logs canonical traces for correctness, efficiency, reliability,
                and failure analysis.
              </figcaption>
            </figure>
          </section>

          <section className={styles.introSection}>
            <div className={styles.introContent}>
              <p>
                Reasoning agents such as OpenClaw are <strong>model-harness systems</strong>. The model supplies
                reasoning capacity, while the harness realizes it through tool use, multi-turn control, execution
                feedback, memory, and runtime policy. Existing evaluations often test models without a harness or
                fix a single harness, which conflates base-model reasoning with harness orchestration. AlphaDiana
                factorizes each run into a benchmark, model, harness, environment, scorer, and budget, so researchers
                can ask whether gains come from stronger reasoning or from a specific runtime scaffold.
              </p>
            </div>
          </section>

          <Scoreboard />

          <FeaturesGrid title="What AlphaDiana standardizes" features={features} background="white" />

          <section className={styles.findingsSection}>
            <div className={styles.findingsInner}>
              <p className={styles.kicker}>Macro Findings</p>
              <h2>What the result table reveals</h2>
              <div className={styles.findingsGrid}>
                {macroFindings.map((f) => (
                  <div key={f.tag} className={styles.findingCard}>
                    <span className={styles.findingTag}>{f.tag} perspective</span>
                    <h3>{f.title}</h3>
                    <p>{f.body}</p>
                  </div>
                ))}
              </div>
              <p className={styles.findingsNote}>
                Direct inference is not just another baseline. It is the control that separates useful orchestration
                from harness-induced failure modes.
              </p>
            </div>
          </section>

          <section className={styles.analysisSection}>
            <div className={styles.analysisInner}>
              <p className={styles.kicker}>Process Analysis</p>
              <h2>Scores are only the entry point</h2>
              <p className={styles.flowLead}>
                AlphaDiana preserves trajectories, actions, tool feedback, and token-level uncertainty. These plots
                from the latest draft show why a model-harness system succeeds or fails after the final score is known.
              </p>
              <div className={styles.figureGrid}>
                {macroFigures.map((figure) => (
                  <figure key={figure.src} className={styles.analysisFigure}>
                    <img src={figure.src} alt={figure.title} loading="lazy" />
                    <figcaption>
                      <strong>{figure.title}.</strong> {figure.caption}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </div>
          </section>

          <section className={styles.microSection}>
            <div className={styles.microInner}>
              <p className={styles.kicker}>Micro Findings</p>
              <h2>Capability ablations explain the shifts</h2>
              <p className={styles.flowLead}>
                The micro study compares matched harness conditions for tools, skills, and memory. A condition may
                include both prompt and runtime changes, so interpret each delta as a bundle rather than an isolated
                causal effect.
              </p>
              <div className={styles.findingsGrid}>
                {microFindings.map((f) => (
                  <div key={f.tag} className={styles.findingCard}>
                    <span className={styles.findingTag}>{f.tag} axis</span>
                    <h3>{f.title}</h3>
                    <p>{f.body}</p>
                  </div>
                ))}
              </div>
              <div className={styles.microFigureGrid}>
                {microFigures.map((figure) => (
                  <figure key={figure.src} className={styles.microFigure}>
                    <img src={figure.src} alt={figure.title} loading="lazy" />
                    <figcaption>
                      <strong>{figure.title}.</strong> {figure.caption}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </div>
          </section>

          <section id="dashboard" className={styles.dashboardSection}>
            <div className={styles.dashboardContent}>
              <p className={styles.kicker}>Dashboard</p>
              <h2>Launch, monitor, and compare</h2>
              <p className={styles.flowLead}>
                The screenshots show an AIME 2026 Direct LLM evaluation with four samples per problem. The result view
                reports 30/30 completed tasks, zero failed runs, 97% Pass@4, and 93% Avg@4 from saved records.
              </p>
              <div className={styles.dashboardGrid}>
                <figure className={styles.dashShot}>
                  <img src="/img/dashboard-create.png" alt="Creating an AIME 2026 Direct LLM evaluation" loading="lazy" />
                  <figcaption>Create an AIME 2026 Direct LLM evaluation from explicit configuration fields.</figcaption>
                </figure>
              <figure className={styles.dashShot}>
                <img src="/img/dashboard-results.png" alt="AIME 2026 Direct LLM evaluation results" loading="lazy" />
                <figcaption>Inspect the completed run with Pass@4, Avg@4, token usage, and runtime statistics.</figcaption>
              </figure>
            </div>
          </div>
        </section>

          <section className={styles.quickstartSection}>
            <div className={styles.quickstartContent}>
              <p className={styles.kicker}>Quick Start</p>
              <h2>Run a GPQA DirectLLM smoke evaluation</h2>
              <p className={styles.flowLead}>
                The command below uses <code>configs/examples/direct_llm_gpqa_diamond.yaml</code>, the
                sandbox-free baseline from the Quick Start guide, downscaled to one problem and one sample. Replace
                the provider placeholders before running it. See the{' '}
                <Link to="/docs/getting-started/installation">getting-started guide</Link> for setup details.
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
