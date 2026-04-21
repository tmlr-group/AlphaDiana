from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.config.validator import ConfigValidator
from alphadiana.utils.rock_ports import check_rock_services, resolve_rock_ports_from_env
from alphadiana.utils.rock_runtime import PREBUILT_SANDBOX_IMAGE

DEFAULT_MANIFEST_PATH = Path("configs/full_runs/rollout_local_vllm_campaign_20260419.yaml")

_BLOCKED_OPENCLAW_BENCHMARKS = {"terminal_bench2"}
_NEMOTRON_HIGH_RISK_BENCHMARKS = {"hle", "mmmu_pro"}
_MULTIMODAL_BENCHMARKS = {"hle", "mmmu_pro"}
_OPENCLAW_ROCK_IMAGE = "tmlrgroup/alphadiana:v1"
_ZEROCLAW_ROCK_IMAGE = "zeroclaw-reasoning:0.6.9"
_TB2_CONTROLLER_IMAGES = {
    "openclaw": "alphadiana/tb2-openclaw-controller:latest",
    "opencode": "alphadiana/tb2-opencode-controller:latest",
    "zeroclaw": "alphadiana/tb2-zeroclaw-controller:latest",
}
_SWEBENCH_RUNTIME_IMAGE_ENVS = {
    "openclaw": ("SWEBENCH_OPENCLAW_RUNTIME_IMAGE", PREBUILT_SANDBOX_IMAGE),
    "opencode": ("SWEBENCH_OPENCODE_RUNTIME_IMAGE", "tmlrgroup/alphadiana:opencode"),
    "zeroclaw": ("SWEBENCH_ZEROCLAW_RUNTIME_IMAGE", _ZEROCLAW_ROCK_IMAGE),
}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    model_name: str
    api_base_env: str
    api_key_env: str
    official_model_name: str
    supports_multimodal: bool

    @property
    def api_base_env_ref(self) -> str:
        return f"${self.api_base_env}"

    @property
    def api_key_env_ref(self) -> str:
        return f"${self.api_key_env}"


@dataclass(frozen=True)
class PathTemplate:
    id: str
    benchmark: str
    harness: str
    backend: str
    max_concurrent: int
    base_wave: str
    base_risk: str
    config_gaps: tuple[str, ...]
    config_path: str | None = None
    overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConcreteRun:
    campaign_id: str
    run_id: str
    model: ModelSpec
    path: PathTemplate
    wave: str
    risk: str
    risk_notes: tuple[str, ...]
    overrides: dict[str, Any]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    severity: str = "error"


def load_campaign(path: str | Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = Path(path)
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Campaign manifest must be a mapping: {manifest_path}")
    return data


def load_models(path: str | Path = DEFAULT_MANIFEST_PATH) -> list[ModelSpec]:
    data = load_campaign(path)
    raw_models = data.get("models", [])
    models: list[ModelSpec] = []
    for item in raw_models:
        if not isinstance(item, dict):
            raise ValueError("Each model spec must be a mapping")
        models.append(
            ModelSpec(
                id=str(item["id"]),
                model_name=str(item["model_name"]),
                api_base_env=str(item["api_base_env"]),
                api_key_env=str(item["api_key_env"]),
                official_model_name=str(item["official_model_name"]),
                supports_multimodal=bool(item.get("supports_multimodal", False)),
            )
        )
    return models


def load_path_templates(path: str | Path = DEFAULT_MANIFEST_PATH) -> list[PathTemplate]:
    data = load_campaign(path)
    raw_templates = data.get("path_templates", [])
    templates: list[PathTemplate] = []
    for item in raw_templates:
        if not isinstance(item, dict):
            raise ValueError("Each path template must be a mapping")
        templates.append(
            PathTemplate(
                id=str(item["id"]),
                benchmark=str(item["benchmark"]),
                harness=str(item["harness"]),
                backend=str(item["backend"]),
                config_path=str(item["config_path"]) if item.get("config_path") else None,
                max_concurrent=int(item["max_concurrent"]),
                base_wave=str(item["base_wave"]),
                base_risk=str(item["base_risk"]),
                config_gaps=tuple(str(x) for x in item.get("config_gaps", [])),
                overrides=dict(item.get("overrides", {})),
            )
        )
    return templates


def expand_campaign(path: str | Path = DEFAULT_MANIFEST_PATH) -> list[ConcreteRun]:
    campaign = load_campaign(path)
    campaign_id = str(campaign["campaign_id"])
    run_prefix = str(campaign.get("defaults", {}).get("run_id_prefix", campaign_id))
    models = load_models(path)
    templates = load_path_templates(path)

    concrete: list[ConcreteRun] = []
    for model in models:
        for template in templates:
            wave, risk, notes = _resolve_wave_and_risk(template, model)
            run_id = f"{run_prefix}_{template.benchmark}_{template.harness}_{model.id}"
            overrides = _render_overrides(template.overrides or {}, run_id, model)
            concrete.append(
                ConcreteRun(
                    campaign_id=campaign_id,
                    run_id=run_id,
                    model=model,
                    path=template,
                    wave=wave,
                    risk=risk,
                    risk_notes=notes,
                    overrides=overrides,
                )
            )
    return concrete


def filter_runs(
    runs: list[ConcreteRun],
    *,
    model: str | None = None,
    benchmark: str | None = None,
    harness: str | None = None,
    wave: str | None = None,
    risk: str | None = None,
) -> list[ConcreteRun]:
    selected = runs
    if model:
        selected = [run for run in selected if run.model.id == model]
    if benchmark:
        selected = [run for run in selected if run.path.benchmark == benchmark]
    if harness:
        selected = [run for run in selected if run.path.harness == harness]
    if wave:
        selected = [run for run in selected if run.wave == wave]
    if risk:
        selected = [run for run in selected if run.risk == risk]
    return selected


def summarize_runs(runs: list[ConcreteRun]) -> dict[str, Any]:
    by_wave: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for run in runs:
        by_wave[run.wave] = by_wave.get(run.wave, 0) + 1
        by_risk[run.risk] = by_risk.get(run.risk, 0) + 1
    return {
        "total_runs": len(runs),
        "waves": by_wave,
        "risks": by_risk,
    }


def render_validate_command(run: ConcreteRun) -> str | None:
    if run.path.backend != "alphadiana" or not run.path.config_path:
        return None

    parts = [
        "source scripts/activate.sh",
        "export PYTHONPATH=$PWD",
        _render_alphadiana_cli("validate", run),
    ]
    return "\n".join(parts)


def render_run_command(run: ConcreteRun) -> str:
    if run.path.backend == "alphadiana":
        log_path = shlex.quote(f"logs/{run.run_id}.log")
        parts = [
            "source scripts/activate.sh",
            "export PYTHONPATH=$PWD",
            "mkdir -p logs",
            _render_alphadiana_cli("run", run) + f" 2>&1 | tee {log_path}",
        ]
        return "\n".join(parts)
    if run.path.backend == "official_terminal_bench_2":
        return _render_official_tb2_command(run)
    if run.path.backend == "official_swebench_pro":
        return _render_official_swebench_command(run)
    raise ValueError(f"Unsupported backend: {run.path.backend}")


def validate_run_config(run: ConcreteRun) -> list[str]:
    if run.path.backend != "alphadiana" or not run.path.config_path:
        return []

    missing_required = [
        check.name for check in _required_checks_for_run(run)
        if not check.ok and check.severity == "error"
    ]
    if missing_required:
        return [f"missing prerequisites: {', '.join(missing_required)}"]

    try:
        validator = ConfigValidator()
        config = ExperimentConfig.from_yaml(
            run.path.config_path,
            overrides=_resolve_overrides_for_validation(run.overrides),
        )
        return validator.validate(config)
    except Exception as exc:  # pragma: no cover - defensive
        return [f"config validation raised {type(exc).__name__}: {exc}"]


def preflight_campaign(
    path: str | Path = DEFAULT_MANIFEST_PATH,
    *,
    probe_vllm: bool = False,
    check_docker: bool = False,
    check_rock: bool = False,
    validate_configs: bool = True,
) -> dict[str, list[CheckResult]]:
    campaign = load_campaign(path)
    models = load_models(path)
    runs = expand_campaign(path)
    manifest_path = Path(path)

    results: dict[str, list[CheckResult]] = {
        "manifest": [CheckResult("manifest", manifest_path.exists(), str(manifest_path), "error")],
        "models": [],
        "paths": [],
        "runs": [],
    }

    for model in models:
        results["models"].extend(_model_env_checks(model))
        if probe_vllm:
            results["models"].append(_probe_vllm_model(model))

    for template in load_path_templates(path):
        if template.config_path:
            cfg_path = Path(template.config_path)
            results["paths"].append(
                CheckResult(
                    template.id,
                    cfg_path.exists(),
                    template.config_path,
                    "error",
                )
            )

    if check_docker:
        results["paths"].append(_check_docker())
    if check_rock:
        results["paths"].extend(_check_rock())

    seen_image_checks: set[str] = set()
    for run in runs:
        results["runs"].extend(_required_checks_for_run(run))
        if check_docker:
            for image_check in _docker_image_checks_for_run(run):
                if image_check.name in seen_image_checks:
                    continue
                seen_image_checks.add(image_check.name)
                results["paths"].append(image_check)
        if validate_configs and run.path.backend == "alphadiana":
            errors = validate_run_config(run)
            results["runs"].append(
                CheckResult(
                    f"{run.run_id}:config",
                    not errors,
                    "; ".join(errors) if errors else "config valid",
                    "error",
                )
            )

    return results


def collect_preflight_failures(results: dict[str, list[CheckResult]]) -> list[CheckResult]:
    failures: list[CheckResult] = []
    for checks in results.values():
        for check in checks:
            if not check.ok and check.severity == "error":
                failures.append(check)
    return failures


def render_preflight_report(results: dict[str, list[CheckResult]]) -> str:
    lines: list[str] = []
    for section, checks in results.items():
        lines.append(f"[{section}]")
        for check in checks:
            status = "OK" if check.ok else ("WARN" if check.severity == "warning" else "FAIL")
            lines.append(f"{status} {check.name}: {check.detail}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _resolve_wave_and_risk(
    template: PathTemplate,
    model: ModelSpec,
) -> tuple[str, str, tuple[str, ...]]:
    wave = template.base_wave
    risk = template.base_risk
    notes: list[str] = []

    if template.harness == "openclaw" and template.benchmark in _BLOCKED_OPENCLAW_BENCHMARKS:
        wave = "wave_d_blocked"
        risk = "blocked_but_requested"
        notes.append(f"{template.benchmark} x openclaw is not rollout-ready on this branch")
        return wave, risk, tuple(notes)

    if model.id == "nemotron_30b_a3b" and template.benchmark in _NEMOTRON_HIGH_RISK_BENCHMARKS:
        wave = "wave_c_high_risk"
        risk = "high_risk"
        notes.append(f"{model.model_name} stays high-risk on {template.benchmark}")
        return wave, risk, tuple(notes)

    if template.backend.startswith("official_"):
        wave = "wave_b_official"
        risk = "official_checkout"
        notes.append("runs from the local official checkout rather than AlphaDiana")

    return wave, risk, tuple(notes)


def _render_overrides(
    overrides: dict[str, Any],
    run_id: str,
    model: ModelSpec,
) -> dict[str, Any]:
    context = {
        "run_id": run_id,
        "model_id": model.id,
        "model_name": model.model_name,
        "api_base_env": model.api_base_env,
        "api_key_env": model.api_key_env,
        "api_base_env_ref": model.api_base_env_ref,
        "api_key_env_ref": model.api_key_env_ref,
        "official_model_name": model.official_model_name,
    }
    rendered: dict[str, Any] = {}
    for key, value in overrides.items():
        rendered[key] = _format_template_value(value, context)
    return rendered


def _format_template_value(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(context)
    return value


def _render_alphadiana_cli(command: str, run: ConcreteRun) -> str:
    if not run.path.config_path:
        raise ValueError("AlphaDiana run is missing config_path")

    cli_parts = [
        "python -m alphadiana.cli",
        command,
        shlex.quote(run.path.config_path),
    ]
    for key, value in run.overrides.items():
        cli_parts.append(f"-o {_render_override_arg(key, value)}")
    return " \\\n  ".join(cli_parts)


def _override_value_to_cli(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_override_arg(key: str, value: Any) -> str:
    raw_value = _override_value_to_cli(value)
    shell_value = raw_value if _is_shell_ref(raw_value) else _escape_for_double_quotes(raw_value)
    return f'"{key}={shell_value}"'


def _is_shell_ref(value: str) -> bool:
    return value.strip().startswith("$")


def _escape_for_double_quotes(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


def _render_official_tb2_command(run: ConcreteRun) -> str:
    workdir_ref = _tb2_workdir_ref()
    api_key_expr = _env_with_default(run.model.api_key_env, "EMPTY")
    llm_call_kwargs = json.dumps({"top_p": 0.95, "max_tokens": 32768}, separators=(",", ":"))
    model_q = shlex.quote(run.model.official_model_name)
    run_id_q = shlex.quote(run.run_id)
    llm_kwargs_q = shlex.quote(f"llm_call_kwargs={llm_call_kwargs}")
    return (
        'repo_root="$PWD"\n'
        'mkdir -p "$repo_root/logs"\n'
        '{\n'
        f"cd {workdir_ref}\n"
        "unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy\n"
        f"OPENAI_API_KEY={api_key_expr} \\\n"
        "uv run harbor run --dataset terminal-bench@2.0 \\\n"
        "  --agent terminus-2 \\\n"
        f"  --model {model_q} \\\n"
        f"  --job-name {run_id_q} \\\n"
        "  --jobs-dir jobs \\\n"
        f"  --n-concurrent {run.path.max_concurrent} \\\n"
        "  --debug \\\n"
        f"  --ak api_base={run.model.api_base_env_ref} \\\n"
        "  --ak temperature=0.6 \\\n"
        "  --ak reasoning_effort=high \\\n"
        f"  --ak {llm_kwargs_q}\n"
        f'}} 2>&1 | tee "$repo_root/logs/{run.run_id}.log"'
    )


def _render_official_swebench_command(run: ConcreteRun) -> str:
    root_ref = _swebench_workdir_ref()
    api_key_expr = _env_with_default(run.model.api_key_env, "EMPTY")
    api_base_ref = run.model.api_base_env_ref
    run_id_q = shlex.quote(run.run_id)
    model_q = shlex.quote(run.model.official_model_name)
    return (
        'repo_root="$PWD"\n'
        'mkdir -p "$repo_root/logs"\n'
        '{\n'
        f"cd {root_ref}\n"
        "unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy\n"
        "if [ ! -f SWE-agent/data/instances.yaml ]; then\n"
        "  ./.venv/bin/python helper_code/generate_sweagent_instances.py --dockerhub_username jefzda\n"
        "fi\n"
        "cd SWE-agent\n"
        f"OPENAI_API_KEY={api_key_expr} OPENAI_BASE_URL={api_base_ref} \\\n"
        "../.venv/bin/sweagent run-batch \\\n"
        "  --config config/tool_use.yaml \\\n"
        f"  --output_dir ../sweagent_results/{run_id_q} \\\n"
        f"  --num_workers {run.path.max_concurrent} \\\n"
        "  --random_delay_multiplier 0 \\\n"
        "  --instances.type file \\\n"
        "  --instances.path data/instances.yaml \\\n"
        "  --instances.shuffle=False \\\n"
        "  --instances.deployment.type docker \\\n"
        "  --instances.deployment.startup_timeout 1800 \\\n"
        f"  --agent.model.name {model_q} \\\n"
        f"  --agent.model.api_base {api_base_ref} \\\n"
        f"  --agent.model.api_key {api_key_expr} \\\n"
        "  --agent.model.temperature 0.6 \\\n"
        "  --agent.model.top_p 0.95 \\\n"
        "  --agent.model.max_output_tokens 32768 \\\n"
        "  --agent.model.per_instance_cost_limit 0 \\\n"
        "  --agent.model.total_cost_limit 0 \\\n"
        "  --agent.model.per_instance_call_limit 20 \\\n"
        "  --progress_bar False\n"
        "cd ..\n"
        "./.venv/bin/python helper_code/gather_patches.py \\\n"
        f"  --directory sweagent_results/{run_id_q} \\\n"
        f"  --prefix {run_id_q} \\\n"
        f"  --output sweagent_results/{run_id_q}_patches.json\n"
        "./.venv/bin/python swe_bench_pro_eval.py \\\n"
        "  --raw_sample_path=swe_bench_pro_full.csv \\\n"
        f"  --patch_path=sweagent_results/{run_id_q}_patches.json \\\n"
        f"  --output_dir=swebench_eval/{run_id_q} \\\n"
        "  --scripts_dir=run_scripts \\\n"
        "  --num_workers=100 \\\n"
        "  --dockerhub_username=jefzda\n"
        f'}} 2>&1 | tee "$repo_root/logs/{run.run_id}.log"'
    )


def _model_env_checks(model: ModelSpec) -> list[CheckResult]:
    api_base = os.environ.get(model.api_base_env, "").strip()
    api_key = os.environ.get(model.api_key_env, "").strip()
    checks = [
        CheckResult(model.api_base_env, bool(api_base), api_base or "<unset>", "error"),
    ]
    checks.append(
        CheckResult(
            model.api_key_env,
            bool(api_key),
            "<set>" if api_key else "<unset, defaulting to EMPTY in generated commands>",
            "warning",
        )
    )
    return checks


def _probe_vllm_model(model: ModelSpec) -> CheckResult:
    api_base = os.environ.get(model.api_base_env, "").strip().rstrip("/")
    if not api_base:
        return CheckResult(model.id, False, f"{model.api_base_env} is unset", "error")

    url = f"{api_base}/models"
    headers = {}
    api_key = os.environ.get(model.api_key_env, "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return CheckResult(model.id, False, f"{url} probe failed: {exc}", "error")
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(model.id, False, f"{url} probe failed: {exc}", "error")

    ids = [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    if model.model_name not in ids:
        detail = f"served ids={ids or '<none>'}; expected {model.model_name}"
        return CheckResult(model.id, False, detail, "error")
    return CheckResult(model.id, True, f"{url} serves {model.model_name}", "error")


def _required_checks_for_run(run: ConcreteRun) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if run.path.backend == "alphadiana":
        if run.path.config_path:
            cfg_path = Path(run.path.config_path)
            checks.append(CheckResult(run.path.id, cfg_path.exists(), str(cfg_path), "error"))
        checks.extend(_env_and_path_checks_for_alphadiana(run))
    elif run.path.backend == "official_terminal_bench_2":
        checks.append(_check_resolved_root("DIRECTLLM_TB2_ROOT", "DIRECTLLM_ROOT", "terminal-bench-2"))
    elif run.path.backend == "official_swebench_pro":
        checks.append(_check_resolved_root("DIRECTLLM_SWEBENCH_ROOT", "DIRECTLLM_ROOT", "SWE-bench_Pro-os"))
    if run.path.benchmark in _MULTIMODAL_BENCHMARKS and not run.model.supports_multimodal:
        checks.append(
            CheckResult(
                f"{run.run_id}:multimodal",
                False,
                f"{run.model.model_name} is marked supports_multimodal=false",
                "warning",
            )
        )
    return checks


def _env_and_path_checks_for_alphadiana(run: ConcreteRun) -> list[CheckResult]:
    checks = [
        CheckResult(run.model.api_base_env, bool(os.environ.get(run.model.api_base_env, "").strip()),
                    os.environ.get(run.model.api_base_env, "").strip() or "<unset>", "error"),
        CheckResult(run.model.api_key_env, bool(os.environ.get(run.model.api_key_env, "").strip()),
                    "<set>" if os.environ.get(run.model.api_key_env, "").strip() else "<unset, defaulting to EMPTY in generated commands>",
                    "warning"),
    ]
    if run.path.benchmark == "hle":
        checks.append(
            CheckResult("HF_TOKEN", bool(os.environ.get("HF_TOKEN", "").strip()),
                        "<set>" if os.environ.get("HF_TOKEN", "").strip() else "<unset>", "error")
        )
    if run.path.benchmark == "terminal_bench2":
        checks.append(_check_path_env("TERMINAL_BENCH2_DIR"))
    if run.path.benchmark == "swebench_pro":
        checks.append(_check_path_env("SWE_BENCH_PRO_EVAL_SCRIPT"))
        checks.append(_check_path_env("SWE_BENCH_PRO_SCRIPTS_DIR", expect_dir=True))
    return checks


def _check_path_env(name: str, *, expect_dir: bool = False) -> CheckResult:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return CheckResult(name, False, "<unset>", "error")
    path = Path(raw).expanduser()
    ok = path.is_dir() if expect_dir else path.exists()
    return CheckResult(name, ok, raw, "error")


def _check_resolved_root(primary_env: str, root_env: str, subdir: str) -> CheckResult:
    resolved = resolve_workdir(primary_env, root_env, subdir)
    ok = bool(resolved and Path(resolved).exists())
    return CheckResult(primary_env, ok, resolved or "<unset>", "error")


def _check_docker() -> CheckResult:
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult("docker", False, "docker not found in PATH", "error")

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "docker version failed"
        return CheckResult("docker", False, detail, "error")
    return CheckResult("docker", True, proc.stdout.strip(), "error")


def _check_docker_image(image: str) -> CheckResult:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult(f"docker-image:{image}", False, "docker not found in PATH", "error")

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "docker image inspect failed"
        return CheckResult(f"docker-image:{image}", False, detail, "error")
    return CheckResult(f"docker-image:{image}", True, image, "error")


def _docker_image_checks_for_run(run: ConcreteRun) -> list[CheckResult]:
    images = []

    if run.path.backend == "alphadiana":
        if run.path.harness == "openclaw":
            images.append(_OPENCLAW_ROCK_IMAGE)
        elif run.path.harness == "zeroclaw":
            images.append(_ZEROCLAW_ROCK_IMAGE)

        if run.path.benchmark == "terminal_bench2":
            controller_image = _TB2_CONTROLLER_IMAGES.get(run.path.harness)
            if controller_image:
                images.append(controller_image)

        if run.path.benchmark == "swebench_pro":
            runtime_image = _resolve_swebench_runtime_source_image(run.path.harness)
            if runtime_image:
                images.append(runtime_image)

    ordered_unique = tuple(dict.fromkeys(images))
    return [_check_docker_image(image) for image in ordered_unique]


def _resolve_swebench_runtime_source_image(harness: str) -> str | None:
    image_spec = _SWEBENCH_RUNTIME_IMAGE_ENVS.get(harness)
    if image_spec is None:
        return None
    env_name, default = image_spec
    return os.environ.get(env_name, default).strip() or default


def _check_rock() -> list[CheckResult]:
    ports = resolve_rock_ports_from_env()
    checks = check_rock_services(ports, timeout=5.0)
    results: list[CheckResult] = []
    for service, outcome in checks.items():
        if service == "docker":
            continue
        ok = outcome is True
        detail = "reachable" if ok else str(outcome)
        results.append(CheckResult(f"rock:{service}", ok, detail, "error"))
    return results


def _resolve_overrides_for_validation(overrides: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in overrides.items():
        if isinstance(value, str) and value.startswith("$") and "{" not in value:
            resolved_value = os.environ.get(value[1:], "")
            if not resolved_value:
                resolved_value = "EMPTY"
        else:
            resolved_value = value
        _insert_nested_override(resolved, key.split("."), resolved_value)
    return resolved


def _insert_nested_override(target: dict[str, Any], parts: list[str], value: Any) -> None:
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def resolve_workdir(primary_env: str, root_env: str, subdir: str) -> str | None:
    primary = os.environ.get(primary_env, "").strip()
    if primary:
        return primary
    root = os.environ.get(root_env, "").strip()
    if root:
        return str(Path(root) / subdir)
    return None


def _tb2_workdir_ref() -> str:
    return "${DIRECTLLM_TB2_ROOT:-${DIRECTLLM_ROOT}/terminal-bench-2}"


def _swebench_workdir_ref() -> str:
    return "${DIRECTLLM_SWEBENCH_ROOT:-${DIRECTLLM_ROOT}/SWE-bench_Pro-os}"


def _env_with_default(name: str, default: str) -> str:
    return f"${{{name}:-{default}}}"
