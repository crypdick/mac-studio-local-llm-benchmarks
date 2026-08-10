#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic>=2.12,<3",
#   "pyyaml>=6.0,<7",
# ]
# ///
"""Run production-shaped model profiles and emit chart-ready metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from agentic_prompt_ab import (
    SYSTEM_PROMPT,
    BlindAssignment,
    EvalConfig,
    ManagedServer,
    ModelSpec,
    RunResult,
    SamplingConfig,
    TaskSpec,
    default_plan,
    execute_task,
    parse_csv,
    server_command,
    write_model,
)


ROOT = Path(__file__).resolve().parents[1]
SHARD_RE = re.compile(r"^(?P<prefix>.+)-\d{5}-of-(?P<count>\d{5})\.gguf$")


class WeightsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    path_env: str
    quant: str
    source_url: str
    source_revision: str


class ServerSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    context_size: int
    parallel: int = 1
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"
    flash_attention: bool = True
    batch_size: int = 2048
    ubatch_size: int = 2048
    gpu_layers: int = 999
    cache_ram_mib: int = 8192
    ctx_checkpoints: int = 32
    slot_prompt_similarity: float = 0.10
    request_timeout_s: int = 7200
    health_timeout_s: int = 180
    extra_args: tuple[str, ...] = ()


class ProfileConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    max_tokens: int
    reasoning_budget: int


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    key: str
    label: str
    alias: str
    weights: WeightsConfig
    server: ServerSettings
    sampling: SamplingConfig
    sampling_source_url: str
    profiles: tuple[ProfileConfig, ...]

    @model_validator(mode="after")
    def validate_profiles(self) -> Self:
        names = [profile.name for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("profile names must be unique")
        for profile in self.profiles:
            if profile.reasoning_budget >= profile.max_tokens:
                raise ValueError(
                    f"{profile.name}: reasoning budget must be below max tokens"
                )
            if profile.max_tokens >= self.server.context_size:
                raise ValueError(
                    f"{profile.name}: max tokens must be below context size"
                )
        return self


class WeightFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    bytes: int
    sha256: str | None


class ModelIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: str
    source_revision: str
    quant: str
    files: tuple[WeightFile, ...]
    aggregate_sha256: str | None


class HostInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    hostname: str
    platform: str
    machine: str
    macos_version: str | None
    hardware_model: str | None
    memory_bytes: int | None


class SoftwareInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    runner_commit: str | None
    runner_dirty: bool
    llama_server: str
    llama_server_version: str


class TracePointer(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str
    bytes: int
    gist_id: str | None = None
    gist_url: str | None = None
    gist_revision: str | None = None
    gist_revision_url: str | None = None


class TaskMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    ok: bool
    finish_reason: str | None
    wall_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None
    prompt_ms: float | None
    prompt_tps: float | None
    decode_ms: float | None
    decode_tps: float | None
    error: str | None


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    load_s: float | None
    task_wall_s: float
    total_wall_s: float | None
    successful_tasks: int
    natural_finishes: int
    prompt_tokens: int
    completion_tokens: int
    prompt_tps: float | None
    decode_tps: float | None
    server_rss_peak_gib: float | None


class RunMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    benchmark: str = "agentic-real-world-v1"
    provenance: str = "native"
    limitations: tuple[str, ...] = ()
    status: str
    error: str | None
    suite_sha256: str
    started_at: str
    finished_at: str
    model_key: str
    model_label: str
    profile: ProfileConfig
    config_source_sha256: str | None
    resolved_config_sha256: str
    resolved_config: ModelConfig
    model_identity: ModelIdentity
    host: HostInfo
    software: SoftwareInfo
    tasks: tuple[TaskMetric, ...]
    aggregate: AggregateMetrics
    trace: TracePointer

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        if self.resolved_config_sha256 != model_config_sha256(self.resolved_config):
            raise ValueError("resolved config hash mismatch")
        if len(self.suite_sha256) != 64 or len(self.trace.sha256) != 64:
            raise ValueError("suite and trace hashes must be SHA-256")
        if self.trace.gist_revision and self.trace.gist_revision not in (
            self.trace.gist_revision_url or ""
        ):
            raise ValueError("Gist revision URL is not pinned to recorded revision")
        return self


class TaskTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: TaskSpec
    result: RunResult


class TraceBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    system_prompt: str
    resolved_config: ModelConfig
    profile: ProfileConfig
    suite_sha256: str
    server_command: tuple[str, ...]
    tasks: tuple[TaskTrace, ...]
    error: str | None


class ScoreTaskTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    score: int | None = None
    maximum: int
    criteria: tuple[int | None, ...]


class ScoreTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    suite_sha256: str
    tasks: tuple[ScoreTaskTemplate, ...]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def model_config_sha256(config: ModelConfig) -> str:
    canonical = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return sha256_bytes(canonical)


def read_model_config(path: Path) -> tuple[ModelConfig, str]:
    raw = path.read_bytes()
    parsed = yaml.safe_load(raw)
    return ModelConfig.model_validate(parsed), sha256_bytes(raw)


def resolve_model_path(config: ModelConfig) -> Path:
    value = os.environ.get(config.weights.path_env)
    if not value:
        raise RuntimeError(f"Set {config.weights.path_env} to the first GGUF shard")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def weight_paths(first: Path) -> tuple[Path, ...]:
    match = SHARD_RE.match(first.name)
    if match is None:
        return (first,)
    paths = tuple(
        sorted(first.parent.glob(f"{match['prefix']}-*-of-{match['count']}.gguf"))
    )
    expected = int(match["count"])
    if len(paths) != expected:
        raise RuntimeError(
            f"Found {len(paths)} of {expected} model shards beside {first}"
        )
    return paths


def identify_model(
    config: ModelConfig, first: Path, hash_weights: bool
) -> ModelIdentity:
    files = []
    for path in weight_paths(first):
        digest = None
        if hash_weights:
            print(f"hashing {path.name}", flush=True)
            digest = sha256_file(path)
        files.append(
            WeightFile(name=path.name, bytes=path.stat().st_size, sha256=digest)
        )
    aggregate = None
    if hash_weights:
        canonical = "\n".join(
            f"{item.name}:{item.bytes}:{item.sha256}" for item in files
        )
        aggregate = sha256_bytes(canonical.encode())
    return ModelIdentity(
        source_url=config.weights.source_url,
        source_revision=config.weights.source_revision,
        quant=config.weights.quant,
        files=tuple(files),
        aggregate_sha256=aggregate,
    )


def command_output(command: tuple[str, ...]) -> str | None:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def int_output(command: tuple[str, ...]) -> int | None:
    value = command_output(command)
    try:
        return int(value) if value else None
    except ValueError:
        return None


def collect_host_info() -> HostInfo:
    return HostInfo(
        hostname=platform.node(),
        platform=platform.platform(),
        machine=platform.machine(),
        macos_version=command_output(("sw_vers", "-productVersion")),
        hardware_model=command_output(("sysctl", "-n", "hw.model")),
        memory_bytes=int_output(("sysctl", "-n", "hw.memsize")),
    )


def collect_software_info() -> SoftwareInfo:
    commit = command_output(("git", "-C", str(ROOT), "rev-parse", "HEAD"))
    status = command_output(
        (
            "git",
            "-C",
            str(ROOT),
            "status",
            "--porcelain",
            "--",
            "scripts",
            "configs",
        )
    )
    llama_server = os.environ.get(
        "LLAMA_SERVER", "/Applications/Ollama.app/Contents/Resources/llama-server"
    )
    version = command_output((llama_server, "--version")) or "unknown"
    return SoftwareInfo(
        runner_commit=commit,
        runner_dirty=bool(status),
        llama_server=Path(llama_server).name,
        llama_server_version=version,
    )


def suite_sha256(tasks: tuple[TaskSpec, ...]) -> str:
    body = json.dumps(
        {
            "system_prompt": SYSTEM_PROMPT,
            "tasks": [task.model_dump(mode="json") for task in tasks],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256_bytes(body)


def rss_gib(pid: int) -> float | None:
    value = command_output(("ps", "-o", "rss=", "-p", str(pid)))
    try:
        return int(value) / 1024**2 if value else None
    except ValueError:
        return None


def weighted_tps(
    results: tuple[RunResult, ...], token_field: str, ms_field: str
) -> float | None:
    tokens = 0
    milliseconds = 0.0
    for result in results:
        if result.timings is None:
            continue
        token_count = getattr(result.timings, token_field)
        elapsed_ms = getattr(result.timings, ms_field)
        if token_count is not None and elapsed_ms:
            tokens += token_count
            milliseconds += elapsed_ms
    return tokens * 1000 / milliseconds if milliseconds else None


def task_metric(result: RunResult) -> TaskMetric:
    usage = result.usage
    timings = result.timings
    return TaskMetric(
        task=result.task_key,
        ok=result.ok,
        finish_reason=result.finish_reason,
        wall_s=result.wall_s,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        cached_tokens=(
            usage.prompt_tokens_details.cached_tokens
            if usage and usage.prompt_tokens_details
            else None
        ),
        prompt_ms=timings.prompt_ms if timings else None,
        prompt_tps=timings.prompt_per_second if timings else None,
        decode_ms=timings.predicted_ms if timings else None,
        decode_tps=timings.predicted_per_second if timings else None,
        error=result.error,
    )


def aggregate_metrics(
    results: tuple[RunResult, ...],
    load_s: float,
    total_wall_s: float,
    rss_peak: float | None,
) -> AggregateMetrics:
    prompt_tokens = sum(
        result.usage.prompt_tokens or 0 for result in results if result.usage
    )
    completion_tokens = sum(
        result.usage.completion_tokens or 0 for result in results if result.usage
    )
    return AggregateMetrics(
        load_s=load_s,
        task_wall_s=sum(result.wall_s for result in results),
        total_wall_s=total_wall_s,
        successful_tasks=sum(result.ok for result in results),
        natural_finishes=sum(result.finish_reason == "stop" for result in results),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tps=weighted_tps(results, "prompt_n", "prompt_ms"),
        decode_tps=weighted_tps(results, "predicted_n", "predicted_ms"),
        server_rss_peak_gib=rss_peak,
    )


def write_json(path: Path, model: BaseModel) -> None:
    write_model(path, model)


def publish_trace(trace_path: Path, run_id: str) -> TracePointer:
    if shutil.which("gh") is None:
        raise RuntimeError("gh is required to publish traces")
    created = subprocess.run(
        (
            "gh",
            "gist",
            "create",
            "--public",
            "--desc",
            f"Mac Studio local LLM benchmark trace: {run_id}",
            str(trace_path),
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    gist_url = next(
        (line for line in created.splitlines() if line.startswith("https://")), ""
    )
    if not gist_url:
        raise RuntimeError(f"Could not parse Gist URL from gh output: {created}")
    gist_id = gist_url.rstrip("/").rsplit("/", 1)[-1]
    metadata = json.loads(
        subprocess.run(
            ("gh", "api", f"gists/{gist_id}"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    revision = metadata["history"][0]["version"]
    canonical_url = metadata["html_url"]
    return TracePointer(
        sha256=sha256_file(trace_path),
        bytes=trace_path.stat().st_size,
        gist_id=gist_id,
        gist_url=canonical_url,
        gist_revision=revision,
        gist_revision_url=f"{canonical_url}/{revision}",
    )


def local_trace_pointer(trace_path: Path) -> TracePointer:
    return TracePointer(sha256=sha256_file(trace_path), bytes=trace_path.stat().st_size)


def selected_profiles(
    config: ModelConfig, names: tuple[str, ...]
) -> tuple[ProfileConfig, ...]:
    if names == ("all",):
        return config.profiles
    selected = tuple(profile for profile in config.profiles if profile.name in names)
    missing = set(names) - {profile.name for profile in selected}
    if missing:
        raise ValueError(f"Unknown profiles for {config.key}: {sorted(missing)}")
    return selected


def sanitized_server_command(
    model: ModelSpec, config: EvalConfig, work_dir: Path
) -> tuple[str, ...]:
    command = server_command(model, config, work_dir / "slots")
    replacements = {
        str(model.model_path): f"${model.model_path.name}",
        command[0]: "$LLAMA_SERVER",
    }
    return tuple(replacements.get(value, value) for value in command)


def run_profile(
    model_config: ModelConfig,
    config_hash: str,
    model_identity: ModelIdentity,
    model_path: Path,
    profile: ProfileConfig,
    tasks: tuple[TaskSpec, ...],
    args: argparse.Namespace,
) -> Path:
    now = datetime.now(UTC)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{model_config.key}-{profile.name}"
    work_dir = args.work_dir / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    model = ModelSpec(
        key=model_config.key,
        label=model_config.label,
        model_path=model_path,
        alias=model_config.alias,
        extra_server_args=model_config.server.extra_args,
        sampling=model_config.sampling,
    )
    evaluation = EvalConfig(
        output_dir=work_dir,
        port=args.port,
        ctx_size=model_config.server.context_size,
        max_tokens=profile.max_tokens,
        reasoning_budget=profile.reasoning_budget,
        request_timeout_s=model_config.server.request_timeout_s,
        health_timeout_s=model_config.server.health_timeout_s,
        parallel=model_config.server.parallel,
        cache_type_k=model_config.server.cache_type_k,
        cache_type_v=model_config.server.cache_type_v,
        flash_attention=model_config.server.flash_attention,
        batch_size=model_config.server.batch_size,
        ubatch_size=model_config.server.ubatch_size,
        gpu_layers=model_config.server.gpu_layers,
        cache_ram_mib=model_config.server.cache_ram_mib,
        ctx_checkpoints=model_config.server.ctx_checkpoints,
        slot_prompt_similarity=model_config.server.slot_prompt_similarity,
    )
    assignment = BlindAssignment(
        candidate="candidate-a",
        model_key=model.key,
        model_label=model.label,
    )
    started = time.perf_counter()
    load_started = time.perf_counter()
    load_s = 0.0
    run_error = None
    results = []
    rss_samples = []
    try:
        with ManagedServer(model, evaluation, work_dir) as server:
            load_s = time.perf_counter() - load_started
            assert server.process is not None
            if sample := rss_gib(server.process.pid):
                rss_samples.append(sample)
            for task in tasks:
                print(f"{model.key}/{profile.name}: {task.key}", flush=True)
                result = execute_task(assignment, model, task, evaluation)
                results.append(result)
                write_json(work_dir / "task-results" / f"{task.key}.json", result)
                if sample := rss_gib(server.process.pid):
                    rss_samples.append(sample)
    except Exception as exc:  # noqa: BLE001 - failure is benchmark data
        load_s = time.perf_counter() - load_started
        run_error = f"{type(exc).__name__}: {exc}"
    finished = datetime.now(UTC)
    result_tuple = tuple(results)
    trace = TraceBundle(
        run_id=run_id,
        system_prompt=SYSTEM_PROMPT,
        resolved_config=model_config,
        profile=profile,
        suite_sha256=suite_sha256(tasks),
        server_command=sanitized_server_command(model, evaluation, work_dir),
        tasks=tuple(
            TaskTrace(task=task, result=result)
            for task, result in zip(tasks, result_tuple)
        ),
        error=run_error,
    )
    trace_path = work_dir / f"{run_id}-trace.json"
    write_json(trace_path, trace)
    write_json(
        work_dir / "score-template.json",
        ScoreTemplate(
            run_id=run_id,
            suite_sha256=suite_sha256(tasks),
            tasks=tuple(
                ScoreTaskTemplate(
                    task=task.key,
                    maximum=2 * len(task.rubric),
                    criteria=(None,) * len(task.rubric),
                )
                for task in tasks
            ),
        ),
    )
    pointer = local_trace_pointer(trace_path)
    if args.publish_gists:
        try:
            pointer = publish_trace(trace_path, run_id)
        except Exception as exc:  # noqa: BLE001 - retain recoverable local artifact
            publish_error = f"{type(exc).__name__}: {exc}"
            run_error = f"{run_error}; {publish_error}" if run_error else publish_error
    metrics = RunMetrics(
        run_id=run_id,
        status=(
            "failed"
            if run_error
            else "partial"
            if any(not result.ok for result in result_tuple)
            else "completed"
        ),
        error=run_error,
        suite_sha256=suite_sha256(tasks),
        started_at=now.isoformat(),
        finished_at=finished.isoformat(),
        model_key=model.key,
        model_label=model.label,
        profile=profile,
        config_source_sha256=config_hash,
        resolved_config_sha256=model_config_sha256(model_config),
        resolved_config=model_config,
        model_identity=model_identity,
        host=collect_host_info(),
        software=collect_software_info(),
        tasks=tuple(task_metric(result) for result in result_tuple),
        aggregate=aggregate_metrics(
            result_tuple,
            load_s,
            time.perf_counter() - started,
            max(rss_samples) if rss_samples else None,
        ),
        trace=pointer,
    )
    if args.publish_gists and pointer.gist_revision_url is None:
        draft = work_dir / "metrics.json"
        write_json(draft, metrics)
        raise RuntimeError(f"Trace publication failed; recoverable metrics: {draft}")
    output = args.results_dir / f"{run_id}.json"
    write_json(output, metrics)
    print(f"metrics: {output}", flush=True)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", type=Path)
    parser.add_argument("--profiles", default="all")
    parser.add_argument("--tasks", default="incident,concurrency,migration,quantum")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".benchmark-work")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results" / "runs")
    parser.add_argument(
        "--publish-gists", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--hash-weights", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_tasks = default_plan().tasks
    task_names = parse_csv(args.tasks)
    tasks = tuple(task for task in all_tasks if task.key in task_names)
    missing_tasks = set(task_names) - {task.key for task in tasks}
    if missing_tasks:
        raise ValueError(f"Unknown tasks: {sorted(missing_tasks)}")
    profile_names = parse_csv(args.profiles)
    loaded = tuple((path, *read_model_config(path)) for path in args.configs)
    matrix = [
        {"config": str(path), "model": config.key, "profile": profile.name}
        for path, config, _ in loaded
        for profile in selected_profiles(config, profile_names)
    ]
    if args.dry_run:
        print(json.dumps({"tasks": list(task_names), "runs": matrix}, indent=2))
        return
    software = collect_software_info()
    if software.runner_dirty and not args.allow_dirty:
        raise RuntimeError(
            "Commit scripts and configs before a benchmark run, or pass --allow-dirty"
        )
    if not args.publish_gists:
        print(
            "warning: metrics will contain only a local trace hash, not a Gist URL",
            file=sys.stderr,
        )
    for _, config, config_hash in loaded:
        model_path = resolve_model_path(config)
        identity = identify_model(config, model_path, args.hash_weights)
        for profile in selected_profiles(config, profile_names):
            run_profile(config, config_hash, identity, model_path, profile, tasks, args)


if __name__ == "__main__":
    main()
