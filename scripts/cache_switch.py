#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic>=2.12,<3",
#   "pyyaml>=6.0,<7",
# ]
# ///
"""Measure production RAM prompt-cache switching and explicit disk-slot restore."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Literal, Self, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from agentic_prompt_ab import EvalConfig, ManagedServer, ModelSpec
from benchmark import (
    ROOT,
    HostInfo,
    ModelConfig,
    ModelIdentity,
    SoftwareInfo,
    TracePointer,
    collect_host_info,
    collect_software_info,
    identify_model,
    local_trace_pointer,
    model_config_sha256,
    publish_trace,
    read_model_config,
    resolve_model_path,
    rss_gib,
    sanitized_server_command,
    sha256_bytes,
    write_json,
)


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CacheSwitchSuite(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    ram_prompt_tokens: int
    ram_conversation_levels: tuple[int, ...]
    disk_prompt_token_levels: tuple[int, ...]
    completion_tokens: int = 1

    @model_validator(mode="after")
    def validate_curve_levels(self) -> Self:
        for name, values in (
            ("ram_conversation_levels", self.ram_conversation_levels),
            ("disk_prompt_token_levels", self.disk_prompt_token_levels),
        ):
            if not values or tuple(sorted(set(values))) != values or values[0] <= 0:
                raise ValueError(f"{name} must be positive, unique, and increasing")
        if self.ram_prompt_tokens <= 0 or self.completion_tokens <= 0:
            raise ValueError("token counts must be positive")
        return self


class RunOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    configs: tuple[Path, ...]
    suite: Path
    ram_prompt_tokens: int | None = None
    ram_levels: tuple[int, ...] | None = None
    disk_token_levels: tuple[int, ...] | None = None
    port: int = 11435
    work_dir: Path
    results_dir: Path
    publish_gists: bool = True
    hash_weights: bool = True
    allow_dirty: bool = False
    dry_run: bool = False


class TokenizeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str


class TokenizeResponse(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    tokens: tuple[int, ...]


class CompletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str
    n_predict: int
    stream: bool = True
    cache_prompt: bool = True
    id_slot: int = 0
    ignore_eos: bool = True
    return_progress: bool = True
    temperature: float
    top_p: float
    top_k: int | None = None
    min_p: float | None = None
    seed: int


class CacheTimings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    cache_n: int | None = None
    prompt_n: int | None = None
    prompt_ms: float | None = None
    prompt_per_second: float | None = None
    predicted_n: int | None = None
    predicted_ms: float | None = None
    predicted_per_second: float | None = None


class CompletionEvent(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    content: str = ""
    tokens: tuple[int, ...] = ()
    stop: bool = False
    timings: CacheTimings | None = None


class PromptDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation: int
    prefix_target_tokens: int
    measured_tokens: int
    sha256: str


class RequestSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: PromptDescriptor
    started_at: str
    ttft_ms: float
    total_ms: float
    cache_tokens: int | None
    prompt_tokens_evaluated: int | None
    prompt_ms: float | None
    prompt_tps: float | None
    decode_ms: float | None
    decode_tps: float | None


class RequestTrace(RequestSummary):
    events: tuple[CompletionEvent, ...]


class SlotRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str


class SlotTimings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    save_ms: float | None = None
    restore_ms: float | None = None


class SlotAction(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    id_slot: int
    filename: str | None = None
    n_saved: int | None = None
    n_written: int | None = None
    n_restored: int | None = None
    n_read: int | None = None
    wall_ms: float = 0.0
    timings: SlotTimings | None = None


class MeasurementSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase: Literal["fill", "ram_revisit", "disk_restore"]
    conversation: int
    request: RequestSummary
    save: SlotAction | None = None
    restore: SlotAction | None = None


class MeasurementTrace(MeasurementSummary):
    request: RequestTrace


class CachePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["ram", "disk"]
    conversations: int
    prompt_tokens: int
    estimated_working_set_bytes: int
    cache_budget_bytes: int
    estimated_working_set_ratio: float | None


class CacheAggregate(BaseModel):
    model_config = ConfigDict(frozen=True)

    ttft_mean_ms: float
    ttft_max_ms: float
    total_mean_ms: float
    cached_fraction_mean: float | None
    restore_mean_ms: float | None
    end_to_end_ttft_mean_ms: float
    server_rss_peak_gib: float | None


class CachePointTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    suite: CacheSwitchSuite
    suite_sha256: str
    model_key: str
    model_label: str
    point: CachePoint
    server_command: tuple[str, ...]
    measurements: tuple[MeasurementTrace, ...]


class CachePointArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    benchmark: str = "cache-switch-v1"
    status: Literal["completed", "partial"] = "completed"
    error: str | None = None
    limitations: tuple[str, ...]
    started_at: str
    finished_at: str
    suite: CacheSwitchSuite
    suite_sha256: str
    model_key: str
    model_label: str
    config_source_sha256: str
    resolved_config_sha256: str
    resolved_config: ModelConfig
    model_identity: ModelIdentity
    host: HostInfo
    software: SoftwareInfo
    point: CachePoint
    measurements: tuple[MeasurementSummary, ...]
    aggregate: CacheAggregate
    trace: TracePointer

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        if self.resolved_config_sha256 != model_config_sha256(self.resolved_config):
            raise ValueError("resolved config hash mismatch")
        if self.suite_sha256 != suite_sha256(self.suite):
            raise ValueError("suite hash mismatch")
        if self.trace.gist_revision and self.trace.gist_revision not in (
            self.trace.gist_revision_url or ""
        ):
            raise ValueError("Gist revision URL is not pinned")
        return self


def suite_sha256(suite: CacheSwitchSuite) -> str:
    canonical = json.dumps(
        suite.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return sha256_bytes(canonical)


def read_suite(path: Path) -> CacheSwitchSuite:
    return CacheSwitchSuite.model_validate(yaml.safe_load(path.read_bytes()))


def post_model(
    url: str,
    request: BaseModel,
    response_type: type[ResponseModel],
    timeout: int,
) -> tuple[float, ResponseModel]:
    body = request.model_dump_json(exclude_none=True).encode()
    http_request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {payload}") from exc
    return (time.perf_counter() - started), response_type.model_validate_json(payload)


def tokenize(base_url: str, content: str) -> int:
    _, response = post_model(
        f"{base_url}/tokenize", TokenizeRequest(content=content), TokenizeResponse, 120
    )
    return len(response.tokens)


def build_prompt(
    base_url: str, conversation: int, target_tokens: int
) -> tuple[str, PromptDescriptor]:
    header = (
        f"Conversation {conversation:03d}; cache-switch benchmark.\n"
        "Treat this deterministic transcript as prior context.\n"
    )
    block = (
        f"[{conversation:03d}] alpha beta gamma delta epsilon zeta eta theta iota "
        "kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega.\n"
    )
    header_tokens = tokenize(base_url, header)
    block_tokens = max(1, tokenize(base_url, block))
    repeats = max(1, (target_tokens - header_tokens) // block_tokens)
    prompt = header + block * repeats
    measured = tokenize(base_url, prompt)
    while measured > target_tokens and repeats > 1:
        repeats -= 1
        prompt = header + block * repeats
        measured = tokenize(base_url, prompt)
    while measured + block_tokens <= target_tokens:
        repeats += 1
        prompt = header + block * repeats
        measured = tokenize(base_url, prompt)
    descriptor = PromptDescriptor(
        conversation=conversation,
        prefix_target_tokens=target_tokens,
        measured_tokens=measured,
        sha256=sha256_bytes(prompt.encode()),
    )
    return prompt, descriptor


def append_followup(
    base_url: str,
    prompt: str,
    descriptor: PromptDescriptor,
    marker: str,
) -> tuple[str, PromptDescriptor]:
    extended = prompt + f"\nFollow-up turn {marker}: respond with one token.\n"
    return extended, PromptDescriptor(
        conversation=descriptor.conversation,
        prefix_target_tokens=descriptor.prefix_target_tokens,
        measured_tokens=tokenize(base_url, extended),
        sha256=sha256_bytes(extended.encode()),
    )


def stream_completion(
    base_url: str,
    prompt: str,
    descriptor: PromptDescriptor,
    model_config: ModelConfig,
    completion_tokens: int,
) -> RequestTrace:
    sampling = model_config.sampling
    request = CompletionRequest(
        prompt=prompt,
        n_predict=completion_tokens,
        temperature=sampling.temperature,
        top_p=sampling.top_p,
        top_k=sampling.top_k,
        min_p=sampling.min_p,
        seed=sampling.seed,
    )
    http_request = urllib.request.Request(
        f"{base_url}/completion",
        data=request.model_dump_json(exclude_none=True).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    events = []
    ttft_ms = None
    try:
        with urllib.request.urlopen(
            http_request, timeout=model_config.server.request_timeout_s
        ) as response:
            for raw_line in response:
                line = raw_line.strip()
                if not line.startswith(b"data: "):
                    continue
                payload = line[6:]
                if payload == b"[DONE]":
                    break
                event = CompletionEvent.model_validate_json(payload)
                events.append(event)
                if ttft_ms is None and event.content:
                    ttft_ms = (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from /completion: {payload}") from exc
    total_ms = (time.perf_counter() - started) * 1000
    if ttft_ms is None:
        raise RuntimeError("stream completed without a generated token")
    timings = next(
        (event.timings for event in reversed(events) if event.timings is not None), None
    )
    return RequestTrace(
        prompt=descriptor,
        started_at=started_at,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        cache_tokens=timings.cache_n if timings else None,
        prompt_tokens_evaluated=timings.prompt_n if timings else None,
        prompt_ms=timings.prompt_ms if timings else None,
        prompt_tps=timings.prompt_per_second if timings else None,
        decode_ms=timings.predicted_ms if timings else None,
        decode_tps=timings.predicted_per_second if timings else None,
        events=tuple(events),
    )


def slot_action(
    base_url: str,
    action: Literal["save", "restore"],
    filename: str,
) -> SlotAction:
    wall_s, response = post_model(
        f"{base_url}/slots/0?action={action}",
        SlotRequest(filename=filename),
        SlotAction,
        7200,
    )
    return response.model_copy(update={"wall_ms": wall_s * 1000})


def request_summary(request: RequestTrace) -> RequestSummary:
    return RequestSummary.model_validate(request.model_dump(exclude={"events"}))


def measurement_summary(measurement: MeasurementTrace) -> MeasurementSummary:
    return MeasurementSummary(
        phase=measurement.phase,
        conversation=measurement.conversation,
        request=request_summary(measurement.request),
        save=measurement.save,
        restore=measurement.restore,
    )


def aggregate_point(
    point: CachePoint,
    measurements: tuple[MeasurementTrace, ...],
    rss_peak: float | None,
) -> CacheAggregate:
    measured_phase = "ram_revisit" if point.mode == "ram" else "disk_restore"
    samples = tuple(item for item in measurements if item.phase == measured_phase)
    if not samples:
        raise ValueError(f"no {measured_phase} measurements")
    cached_fractions = [
        item.request.cache_tokens / item.request.prompt.measured_tokens
        for item in samples
        if item.request.cache_tokens is not None
    ]
    restore_ms = [item.restore.wall_ms for item in samples if item.restore is not None]
    return CacheAggregate(
        ttft_mean_ms=fmean(item.request.ttft_ms for item in samples),
        ttft_max_ms=max(item.request.ttft_ms for item in samples),
        total_mean_ms=fmean(item.request.total_ms for item in samples),
        cached_fraction_mean=fmean(cached_fractions) if cached_fractions else None,
        restore_mean_ms=fmean(restore_ms) if restore_ms else None,
        end_to_end_ttft_mean_ms=fmean(
            item.request.ttft_ms + (item.restore.wall_ms if item.restore else 0)
            for item in samples
        ),
        server_rss_peak_gib=rss_peak,
    )


def server_evaluation(
    model_config: ModelConfig, work_dir: Path, port: int, cache_ram_mib: int
) -> EvalConfig:
    server = model_config.server
    return EvalConfig(
        output_dir=work_dir,
        port=port,
        ctx_size=server.context_size,
        max_tokens=2,
        reasoning_budget=0,
        request_timeout_s=server.request_timeout_s,
        health_timeout_s=server.health_timeout_s,
        parallel=1,
        cache_type_k=server.cache_type_k,
        cache_type_v=server.cache_type_v,
        flash_attention=server.flash_attention,
        batch_size=server.batch_size,
        ubatch_size=server.ubatch_size,
        gpu_layers=server.gpu_layers,
        cache_ram_mib=cache_ram_mib,
        ctx_checkpoints=server.ctx_checkpoints,
        slot_prompt_similarity=server.slot_prompt_similarity,
    )


def point_run_id(model_key: str, point: CachePoint) -> str:
    suffix = (
        f"ram-{point.conversations}c"
        if point.mode == "ram"
        else f"disk-{point.prompt_tokens}t"
    )
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{model_key}-{suffix}"


def emit_point(
    *,
    model_config: ModelConfig,
    config_hash: str,
    identity: ModelIdentity,
    suite: CacheSwitchSuite,
    point: CachePoint,
    measurements: tuple[MeasurementTrace, ...],
    evaluation: EvalConfig,
    model: ModelSpec,
    server_dir: Path,
    results_dir: Path,
    work_dir: Path,
    publish_gists: bool,
    rss_peak: float | None,
    started_at: str,
) -> Path:
    run_id = point_run_id(model.key, point)
    trace = CachePointTrace(
        run_id=run_id,
        suite=suite,
        suite_sha256=suite_sha256(suite),
        model_key=model.key,
        model_label=model.label,
        point=point,
        server_command=sanitized_server_command(model, evaluation, server_dir),
        measurements=measurements,
    )
    trace_path = work_dir / "traces" / f"{run_id}-trace.json"
    write_json(trace_path, trace)
    pointer = local_trace_pointer(trace_path)
    publish_error = None
    if publish_gists:
        try:
            pointer = publish_trace(trace_path, run_id)
        except Exception as exc:  # noqa: BLE001 - preserve remaining curve points
            publish_error = f"{type(exc).__name__}: {exc}"
    limitations = (
        "One raw suite point (n=1); no confidence interval.",
        "Client-observed SSE TTFT includes local HTTP overhead.",
        (
            "Serialized slot bytes approximate RAM prompt-cache working-set bytes."
            if point.mode == "ram"
            else "Explicit slot restore is not automatic prompt-cache disk spill."
        ),
    )
    artifact = CachePointArtifact(
        run_id=run_id,
        status="partial" if publish_error else "completed",
        error=publish_error,
        limitations=limitations,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
        suite=suite,
        suite_sha256=suite_sha256(suite),
        model_key=model.key,
        model_label=model.label,
        config_source_sha256=config_hash,
        resolved_config_sha256=model_config_sha256(model_config),
        resolved_config=model_config,
        model_identity=identity,
        host=collect_host_info(),
        software=collect_software_info(),
        point=point,
        measurements=tuple(measurement_summary(item) for item in measurements),
        aggregate=aggregate_point(point, measurements, rss_peak),
        trace=pointer,
    )
    output = results_dir / f"{run_id}.json"
    write_json(output, artifact)
    print(f"metrics: {output}", flush=True)
    if publish_error:
        print(f"warning: {run_id}: {publish_error}", file=sys.stderr, flush=True)
    return output


def run_ram_curve(
    *,
    model_config: ModelConfig,
    config_hash: str,
    identity: ModelIdentity,
    suite: CacheSwitchSuite,
    model: ModelSpec,
    run_root: Path,
    args: RunOptions,
) -> None:
    server_dir = run_root / "ram-server"
    evaluation = server_evaluation(
        model_config, server_dir, args.port, model_config.server.cache_ram_mib
    )
    base_url = f"http://127.0.0.1:{args.port}"
    prompts: dict[int, tuple[str, PromptDescriptor]] = {}
    state_bytes: dict[int, int] = {}
    with ManagedServer(model, evaluation, server_dir) as server:
        assert server.process is not None
        for conversations in suite.ram_conversation_levels:
            started_at = datetime.now(UTC).isoformat()
            measurements = []
            for conversation in range(conversations):
                if conversation in prompts:
                    continue
                prompt, descriptor = build_prompt(
                    base_url, conversation, suite.ram_prompt_tokens
                )
                prompts[conversation] = (prompt, descriptor)
                request = stream_completion(
                    base_url,
                    prompt,
                    descriptor,
                    model_config,
                    suite.completion_tokens,
                )
                saved = slot_action(base_url, "save", f"ram-{conversation}.bin")
                state_bytes[conversation] = saved.n_written or 0
                measurements.append(
                    MeasurementTrace(
                        phase="fill",
                        conversation=conversation,
                        request=request,
                        save=saved,
                    )
                )
            for conversation in range(conversations):
                prompt, descriptor = append_followup(
                    base_url,
                    *prompts[conversation],
                    marker=f"ram-{conversations}",
                )
                request = stream_completion(
                    base_url,
                    prompt,
                    descriptor,
                    model_config,
                    suite.completion_tokens,
                )
                measurements.append(
                    MeasurementTrace(
                        phase="ram_revisit",
                        conversation=conversation,
                        request=request,
                    )
                )
            working_set_bytes = sum(
                state_bytes[index] for index in range(conversations)
            )
            cache_budget_bytes = model_config.server.cache_ram_mib * 1024**2
            point = CachePoint(
                mode="ram",
                conversations=conversations,
                prompt_tokens=suite.ram_prompt_tokens,
                estimated_working_set_bytes=working_set_bytes,
                cache_budget_bytes=cache_budget_bytes,
                estimated_working_set_ratio=(
                    working_set_bytes / cache_budget_bytes
                    if cache_budget_bytes
                    else None
                ),
            )
            emit_point(
                model_config=model_config,
                config_hash=config_hash,
                identity=identity,
                suite=suite,
                point=point,
                measurements=tuple(measurements),
                evaluation=evaluation,
                model=model,
                server_dir=server_dir,
                results_dir=args.results_dir,
                work_dir=run_root,
                publish_gists=args.publish_gists,
                rss_peak=rss_gib(server.process.pid),
                started_at=started_at,
            )


def run_disk_curve(
    *,
    model_config: ModelConfig,
    config_hash: str,
    identity: ModelIdentity,
    suite: CacheSwitchSuite,
    model: ModelSpec,
    run_root: Path,
    args: RunOptions,
) -> None:
    server_dir = run_root / "disk-server"
    evaluation = server_evaluation(model_config, server_dir, args.port, 0)
    base_url = f"http://127.0.0.1:{args.port}"
    with ManagedServer(model, evaluation, server_dir) as server:
        assert server.process is not None
        for index, prompt_tokens in enumerate(suite.disk_prompt_token_levels):
            started_at = datetime.now(UTC).isoformat()
            prompt, descriptor = build_prompt(base_url, 1000 + index, prompt_tokens)
            fill_request = stream_completion(
                base_url,
                prompt,
                descriptor,
                model_config,
                suite.completion_tokens,
            )
            filename = f"disk-{prompt_tokens}.bin"
            saved = slot_action(base_url, "save", filename)
            restored = slot_action(base_url, "restore", filename)
            prompt, descriptor = append_followup(
                base_url, prompt, descriptor, marker=f"disk-{prompt_tokens}"
            )
            restored_request = stream_completion(
                base_url,
                prompt,
                descriptor,
                model_config,
                suite.completion_tokens,
            )
            measurements = (
                MeasurementTrace(
                    phase="fill",
                    conversation=index,
                    request=fill_request,
                    save=saved,
                ),
                MeasurementTrace(
                    phase="disk_restore",
                    conversation=index,
                    request=restored_request,
                    restore=restored,
                ),
            )
            state_bytes = saved.n_written or restored.n_read or 0
            point = CachePoint(
                mode="disk",
                conversations=1,
                prompt_tokens=prompt_tokens,
                estimated_working_set_bytes=state_bytes,
                cache_budget_bytes=0,
                estimated_working_set_ratio=None,
            )
            emit_point(
                model_config=model_config,
                config_hash=config_hash,
                identity=identity,
                suite=suite,
                point=point,
                measurements=measurements,
                evaluation=evaluation,
                model=model,
                server_dir=server_dir,
                results_dir=args.results_dir,
                work_dir=run_root,
                publish_gists=args.publish_gists,
                rss_peak=rss_gib(server.process.pid),
                started_at=started_at,
            )


def run_model(
    model_config: ModelConfig,
    config_hash: str,
    suite: CacheSwitchSuite,
    args: RunOptions,
) -> None:
    if max(suite.disk_prompt_token_levels) + suite.completion_tokens >= (
        model_config.server.context_size
    ):
        raise ValueError(f"{model_config.key}: disk curve exceeds context size")
    if suite.ram_prompt_tokens + suite.completion_tokens >= (
        model_config.server.context_size
    ):
        raise ValueError(f"{model_config.key}: RAM curve exceeds context size")
    model_path = resolve_model_path(model_config)
    identity = identify_model(model_config, model_path, args.hash_weights)
    model = ModelSpec(
        key=model_config.key,
        label=model_config.label,
        model_path=model_path,
        alias=model_config.alias,
        extra_server_args=model_config.server.extra_args,
        sampling=model_config.sampling,
    )
    run_root = (
        args.work_dir
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{model_config.key}"
    )
    run_root.mkdir(parents=True, exist_ok=False)
    run_ram_curve(
        model_config=model_config,
        config_hash=config_hash,
        identity=identity,
        suite=suite,
        model=model,
        run_root=run_root,
        args=args,
    )
    run_disk_curve(
        model_config=model_config,
        config_hash=config_hash,
        identity=identity,
        suite=suite,
        model=model,
        run_root=run_root,
        args=args,
    )


def parse_ints(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in value.split(",") if item.strip())


def parse_args() -> RunOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", type=Path)
    parser.add_argument(
        "--suite", type=Path, default=ROOT / "configs/cache-switch.yaml"
    )
    parser.add_argument("--ram-prompt-tokens", type=int)
    parser.add_argument("--ram-levels", type=parse_ints)
    parser.add_argument("--disk-token-levels", type=parse_ints)
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument(
        "--work-dir", type=Path, default=ROOT / ".benchmark-work/cache-switch"
    )
    parser.add_argument(
        "--results-dir", type=Path, default=ROOT / "results/cache-switch"
    )
    parser.add_argument(
        "--publish-gists", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--hash-weights", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return RunOptions.model_validate(vars(parser.parse_args()))


def resolved_suite(args: RunOptions) -> CacheSwitchSuite:
    suite = read_suite(args.suite)
    return CacheSwitchSuite.model_validate(
        {
            **suite.model_dump(mode="python"),
            "ram_prompt_tokens": args.ram_prompt_tokens or suite.ram_prompt_tokens,
            "ram_conversation_levels": args.ram_levels or suite.ram_conversation_levels,
            "disk_prompt_token_levels": args.disk_token_levels
            or suite.disk_prompt_token_levels,
        }
    )


def main() -> None:
    args = parse_args()
    suite = resolved_suite(args)
    loaded = tuple((path, *read_model_config(path)) for path in args.configs)
    if args.dry_run:
        points = [
            {"mode": "ram", "conversations": level}
            for level in suite.ram_conversation_levels
        ] + [
            {"mode": "disk", "prompt_tokens": level}
            for level in suite.disk_prompt_token_levels
        ]
        print(
            json.dumps(
                {
                    "suite": suite.model_dump(mode="json"),
                    "runs": [
                        {"config": str(path), "model": config.key, "points": points}
                        for path, config, _ in loaded
                    ],
                },
                indent=2,
            )
        )
        return
    software = collect_software_info()
    if software.runner_dirty and not args.allow_dirty:
        raise RuntimeError(
            "Commit scripts and configs before a benchmark run, or pass --allow-dirty"
        )
    if not args.publish_gists:
        print(
            "warning: metrics contain local trace hashes, not Gist URLs",
            file=sys.stderr,
        )
    for _, config, config_hash in loaded:
        print(f"cache-switch: {config.key}", flush=True)
        run_model(config, config_hash, suite, args)


if __name__ == "__main__":
    main()
