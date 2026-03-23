# Pipeline Test Report — 2026-03-23

Tested AlphaDiana end-to-end following the README, on a shared Linux machine (A800 GPUs). Model: `qwen/qwen3-235b-a22b-2507` via OpenRouter.

## Test Summary

| Mode | Status | Notes |
|------|--------|-------|
| Direct LLM (AIME 2024) | Partial pass | 4/30 completed, 2 correct. API works after proxy fix. |
| OpenClaw + ROCK (custom) | Infra pass, eval fail | Sandbox starts in ~12s after port fix. Eval hit BrokenPipeError in gateway. |

---

## Issues Found

### Critical

#### 1. `scripts/` vs `dev/` path mismatch (FIXED)

**Symptom:** `quickstart.sh` fails at ROCK clone step; `rock_env.sh` fails with "ROCK repository not found".

**Root cause:** `setup_alphadiana_rock.sh` uses `PROJECT_ROOT="${SCRIPT_DIR%/dev}"` to find the project root. This only works if the script lives in a `dev/` directory, but scripts are in `scripts/`. Same issue in `rock_env.sh` (line: `_rock_env_project_root="${_rock_env_script_dir%/dev}"`).

**Impact:** The entire quickstart flow is broken. Users cannot set up the environment by following the README.

**Fix applied:** `alphadiana/utils/rock_ports.py` now searches both `scripts/.rock_ports.env` and `dev/.rock_ports.env`.

**README issue:** README says `source scripts/rock_env.sh` but `quickstart.sh` output says `source dev/rock_env.sh`. The README path is where the file lives, but the file's internal path computation is wrong.

#### 2. ROCK sandbox connects to wrong admin port (FIXED)

**Symptom:** All sandbox start attempts fail with "Get timed out: some object(s) not ready" — spent 17+ minutes trying profiles from 4g down to 768m.

**Root cause:** `rock_ports.py` only looked for `dev/.rock_ports.env` (line 19: `ROCK_PORTS_ENV_FILE = PROJECT_ROOT / "dev" / ".rock_ports.env"`). When the file didn't exist there, it fell back to default ports (admin=9000, proxy=9001) which don't match the actual running services.

**Impact:** OpenClaw evaluation is completely non-functional without manually exporting env vars.

**Fix applied:** Added `_find_rock_ports_env_file()` that searches both directories. Also added `alphadiana env` command and pre-flight check in `alphadiana run`.

#### 3. SOCKS proxy causes misleading "openai not installed" error

**Symptom:** Direct LLM agent fails with "The 'openai' package is required for DirectLLMAgent". But `openai` IS installed.

**Root cause:** The real error is buried: `ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`. The proxy env vars (`http_proxy`, `https_proxy`, `all_proxy`) cause httpx to try a SOCKS transport. The `except ImportError` in `setup()` catches this and silently sets `self._client = None`, then `solve()` raises the misleading error.

**Fix suggestion:** Catch the SOCKS proxy error separately and surface it clearly, or auto-unset proxy vars like `rock_env.sh` does.

### Medium

#### 4. Results file appends instead of overwriting

**Symptom:** Running `alphadiana run` twice with the same `run_id` appends results to the existing `.jsonl` file. The second run's results are mixed with first run's errors.

**Impact:** Accuracy calculations become incorrect when old error records are mixed with new results.

#### 5. `quickstart.sh` runs `ray stop --force` on shared machines

**Symptom:** On a shared machine, `ray stop --force` tries to kill other users' Ray processes (permission denied errors).

**Impact:** Noisy errors, potential disruption to other users.

#### 6. ROCK dynamic config not auto-generated for Redis port

**Symptom:** `rock-local-proxy.yml` hardcodes `redis.port: 6379`. If the user's Redis is on a different port (common on shared machines), the admin connects to the wrong Redis.

**Impact:** Admin may connect to another user's Redis, causing cross-contamination of sandbox state.

### Low

#### 7. Proxy warning message references wrong path

**Line:** `cli.py:30` — `"Run 'source dev/rock_env.sh' to unset them."`

Should say `source scripts/rock_env.sh` to match the README and actual file location.

#### 8. HuggingFace dataset cache warning

The AIME 2024 dataset produces a warning about using cached version. Not harmful but noisy.

---

## README Clarity Issues

1. **`scripts/` vs `dev/` inconsistency** — README says `source scripts/rock_env.sh` but `quickstart.sh` prints `source dev/rock_env.sh`. The scripts reference `dev/` paths internally but live in `scripts/`.

2. **Missing `openai` in prerequisites** — The Direct LLM example doesn't mention that `openai` must be importable. `quickstart.sh` handles this, but manual setup doesn't make it obvious.

3. **No mention of SOCKS proxy issue** — Many shared machines have proxy env vars set. The README should mention running `unset http_proxy https_proxy all_proxy` or sourcing `rock_env.sh` before any `alphadiana` commands.

4. **No `alphadiana env` / health check** — Users had no way to verify ROCK services were reachable before starting a long evaluation run. (FIXED — added `alphadiana env` command.)

5. **Config `api_key` field is confusing** — The `direct_llm.yaml` example has `api_key: "EMPTY"` with no hint about env var substitution (`${OPENROUTER_API_KEY}`). The getting_started doc partially covers this but the example config doesn't.

6. **Custom benchmark example in README uses `numeric` scorer** — But the OpenClaw config uses `math_verify`. No guidance on when to use which.

---

## Changes Made

### `alphadiana/utils/rock_ports.py`
- Search both `scripts/.rock_ports.env` and `dev/.rock_ports.env`
- Added `check_rock_services()` function for health checking admin/proxy/redis/docker

### `alphadiana/cli.py`
- Added `alphadiana env` command — shows port config and health checks all services
- Added pre-flight ROCK service check in `alphadiana run` for openclaw configs — fails fast with clear error instead of 17-minute timeout loop
