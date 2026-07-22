# Remote Data-Gathering Handoff — How to Dispatch the Other-Machine Agent

**Companion to:** [`REMOTE_GATHER_PROTOCOL.md`](./REMOTE_GATHER_PROTOCOL.md)
(the technical spec the remote agent follows).
This file is the operator's guide: how to get the protocol onto the other
machine, what to tell the agent, and how to get the results back.

Last updated: 2026-07-22

---

## Overview

Two documents work together:

| File | Read by | Purpose |
|---|---|---|
| `REMOTE_GATHER_HANDOFF.md` (this file) | **You** (operator) | How to dispatch and collect |
| `REMOTE_GATHER_PROTOCOL.md` | **Remote agent** | What data to gather and in what schema |

The remote agent's job is narrow: **locate raw runs and emit standardized
CSVs.** It does not interpret results or edit the paper.

---

## Step 1 — Get the protocol + scripts onto the other machine

### Option A — machines share the git repo (preferred)

```bash
# on this (paper) machine
git add analyze_tools/REMOTE_GATHER_PROTOCOL.md analyze_tools/REMOTE_GATHER_HANDOFF.md
git commit -m "Add remote data-gathering protocol + handoff for S4"
git push

# on the remote machine
git pull
# the agent then reads analyze_tools/REMOTE_GATHER_PROTOCOL.md
```

### Option B — no shared remote

Copy the protocol and the two reusable scripts:

```bash
scp analyze_tools/REMOTE_GATHER_PROTOCOL.md \
    analyze_tools/compute_failure_taxonomy.py \
    analyze_tools/extract_entropy_token_scatter.py \
    <user>@<remote>:~/alphadiana_gather/
```

Or simply paste the full contents of `REMOTE_GATHER_PROTOCOL.md` into the
remote agent's chat.

---

## Step 2 — Kickoff prompt for the remote agent

Paste this verbatim (adjust the search roots if you know others):

> You are gathering raw experiment data for the AlphaDiana paper's Section 4.
> Read `REMOTE_GATHER_PROTOCOL.md` in full and follow it. Do **not** interpret
> results or edit the paper — your only job is to locate raw runs and emit
> standardized CSVs.
>
> Execute in order:
>
> 1. **Phase 0 (inventory).** Search `/path/to/weikai`,
>    `/path/to/xxx/alphadiana_offload`, `/path/to/jinbo`,
>    `/home/xxx/projects/422_full/results`,
>    `/hd1/models/siatmri_alphadiana_results`, and any HF sync dir. Produce
>    `run_manifest.csv` with the schema in §2. **Explicitly report every
>    Kimi-K2.6 and every MMMU-Pro run directory** — these are the top priority
>    and are 0% present on the paper machine. If none exist, say so in the notes.
> 2. **Stop and show me `run_manifest.csv` before proceeding.** I want to
>    confirm coverage before extraction.
> 3. **Phase 1 (per-trajectory tables).** For each run emit
>    `traj_<model>_<harness>_<bench>.csv` per §3 (one row per task/sample; keep
>    `null` rows; note your entropy convention).
> 4. **Phase 2.** Extend the registries in `compute_failure_taxonomy.py` and
>    `extract_entropy_token_scatter.py` with the Phase-0 paths, run them, emit
>    the CSVs in the **existing schemas**.
> 5. **Phase 4 (ship back).** Put only CSVs into `s4_remote_bundle/` plus a
>    `BUNDLE_NOTES.md`. Do not include raw trajectories or logprobs.
>
> Priorities: (1) Kimi all benchmarks, (2) MMMU-Pro all models, (3) off-machine
> IMO agents, (4) Gemma/Kimi failure+entropy extends. Do **not** attempt new
> multi-sample IMO runs (out of scope).

---

## Step 3 — Control point: confirm before extraction

The prompt tells the agent to **stop after Phase 0** and show `run_manifest.csv`.
This is deliberate: the biggest risk is that the **Kimi-K2.6** and **MMMU-Pro**
runs may not exist on any machine. Confirming coverage in the cheap inventory
step avoids wasting extraction effort, and surfaces early whether the paper
table's Kimi rows / MMMU-Pro column come from a source we still need to locate.

Check the manifest for:
- At least one run dir per Kimi-K2.6 × {GPQA, HLE, AIME, IMO, MMMU-Pro}.
- MMMU-Pro dirs for Qwen and Gemma.
- The off-machine IMO agents (Gemma OC/OCo/ZC; Qwen OCo).

If any are missing, decide whether to (re)run them before continuing.

---

## Step 4 — Get the results back

```bash
# on the remote machine
tar czf s4_remote_bundle.tgz s4_remote_bundle/
scp s4_remote_bundle.tgz \
    <user>@<paper-machine>:/path/to/xxx/AlphaDiana-dev/analyze_tools/
```

Then tell the paper-machine agent it has landed. It will unpack the CSVs, drop
them into the `analyze_tools/` pipeline, and regenerate the §4 main table,
figures, and the T2 (failure taxonomy) / T3 (token efficiency) analyses.

Expected bundle contents:

```
s4_remote_bundle/
  run_manifest.csv
  traj_<model>_<harness>_<bench>.csv      # one per run
  failure_taxonomy.csv                    # regenerated, extended coverage
  entropy_token_scatter.csv               # regenerated, extended coverage
  steps_<...>.csv                         # only if Phase 3 (action-level) was run
  BUNDLE_NOTES.md                         # what was found / missing / conventions
```

---

## Fill-in checklist for the operator

Before dispatching, replace these placeholders:

- [ ] `<user>@<remote>` — remote SSH target.
- [ ] `<user>@<paper-machine>` — this machine's SSH target for the return trip.
- [ ] Shared git repo? If yes, use Option A; if no, Option B.
- [ ] Any search roots beyond the five listed (extra data disks / users).
- [ ] Known location of Kimi-K2.6 and MMMU-Pro runs, if you have it.
