# MoltBench mini-SGLang — Run Audit Report

- difficulty: easy   agent: none   max_attempts: 3
- correctness: PASS   OG: 1.1301   throughput: 4703.69 ±14.69 tok/s
- baseline (in-situ, not agent-writable): 4162.32 tok/s
- attempts_used: 0   cost (K): {'attempts_used': 0, 'max_attempts': 3, 'wall_time_sec': 46.0, 'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'token_source': 'none', 'cost_per_improvement': 353.7}

## Integrity checks (anti-hack — Design-5 §5/§6)

- verifier integrity (hidden /eval files unchanged during run): OK
- agent-changed files (should all be under /workspace/repo): []
- /eval writable by agent user (convention; integrity hash enforces tamper-detection): True
- official score computed by hidden verifier from a clean server restart: True
- baseline measured in-situ on pristine seed (not hardcoded, not agent-writable): True

## Artifact suitcase

- [x] events.jsonl
- [x] model_calls.jsonl
- [x] file_diffs.jsonl
- [x] initial_workspace.tar
- [x] final_workspace.tar
- [x] best_workspace.tar
- [x] task_config.json
- [x] manifest.json
