#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic>=2.12,<3",
#   "pyyaml>=6.0,<7",
# ]
# ///
"""Render README benchmark chart from committed run and score artifacts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from html import escape
from math import ceil, floor, log10
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from benchmark import ROOT, RunMetrics
from cache_switch import CachePointArtifact


COLORS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2")


class TaskScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    score: int
    maximum: int
    criteria: tuple[int, ...] = ()

    @field_validator("criteria")
    @classmethod
    def validate_criteria(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value not in (0, 1, 2) for value in values):
            raise ValueError("criteria must contain only 0, 1, or 2")
        return values

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.maximum <= 0 or not 0 <= self.score <= self.maximum:
            raise ValueError("score must be between zero and maximum")
        if self.criteria and (
            sum(self.criteria) != self.score or 2 * len(self.criteria) != self.maximum
        ):
            raise ValueError("criterion scores must match task total")
        return self


class ScoreArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    run_id: str
    suite_sha256: str
    reviewer: str
    scored_at: str
    tasks: tuple[TaskScore, ...]

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        names = [task.task for task in self.tasks]
        if not names or len(names) != len(set(names)):
            raise ValueError("score artifact tasks must be nonempty and unique")
        return self

    @property
    def score(self) -> int:
        return sum(task.score for task in self.tasks)

    @property
    def maximum(self) -> int:
        return sum(task.maximum for task in self.tasks)


class Point(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    model: str
    profile: str
    minutes: float
    score_percent: float


class ProfilePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    model: str
    profile: str
    reasoning_budget: int
    runtime_minutes: float
    decode_tps: float


class XYPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    profile: str
    x: float
    y: float


class CacheChartPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    mode: str
    x: float
    ttft_seconds: float


def load_runs(directory: Path) -> tuple[RunMetrics, ...]:
    return tuple(
        RunMetrics.model_validate_json(path.read_text())
        for path in sorted(directory.glob("*.json"))
    )


def load_scores(directory: Path) -> tuple[ScoreArtifact, ...]:
    return tuple(
        ScoreArtifact.model_validate_json(path.read_text())
        for path in sorted(directory.glob("*.json"))
    )


def load_cache_runs(directory: Path) -> tuple[CachePointArtifact, ...]:
    return tuple(
        CachePointArtifact.model_validate_json(path.read_text())
        for path in sorted(directory.glob("*.json"))
    )


def join_points(
    runs: tuple[RunMetrics, ...], scores: tuple[ScoreArtifact, ...]
) -> tuple[Point, ...]:
    by_run = {score.run_id: score for score in scores}
    points = []
    for run in runs:
        if run.status != "completed":
            continue
        score = by_run.get(run.run_id)
        if score is None:
            continue
        if score.suite_sha256 != run.suite_sha256:
            raise ValueError(f"suite hash mismatch for {run.run_id}")
        points.append(
            Point(
                run_id=run.run_id,
                model=run.model_label,
                profile=run.profile.name,
                minutes=run.aggregate.task_wall_s / 60,
                score_percent=score.score * 100 / score.maximum,
            )
        )
    return tuple(points)


def profile_points(runs: tuple[RunMetrics, ...]) -> tuple[ProfilePoint, ...]:
    return tuple(
        ProfilePoint(
            run_id=run.run_id,
            model=run.model_label,
            profile=run.profile.name,
            reasoning_budget=run.profile.reasoning_budget,
            runtime_minutes=run.aggregate.task_wall_s / 60,
            decode_tps=float(run.aggregate.decode_tps),
        )
        for run in runs
        if run.status == "completed" and run.aggregate.decode_tps is not None
    )


def _format_tick(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.3g}K"
    return f"{value:.4g}"


def _render_xy_svg(
    points: tuple[XYPoint, ...],
    *,
    title: str,
    subtitle: str,
    x_label: str,
    y_label: str,
    y_limit: float | None = None,
    x_ticks: tuple[float, ...] | None = None,
    label_points: bool = True,
    log_y: bool = False,
) -> str:
    width, height = 1120, 620
    left, right, top, bottom = 90, 320, 60, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    if not points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="300" viewBox="0 0 {width} 300">'
            '<rect width="100%" height="100%" fill="#fff"/>'
            f'<text x="500" y="130" text-anchor="middle" font-family="system-ui" font-size="24">{escape(title)}</text>'
            '<text x="500" y="175" text-anchor="middle" font-family="system-ui" font-size="16" fill="#64748b">No matching run artifacts</text>'
            "</svg>\n"
        )
    max_x = max(point.x for point in points)
    x_limit = max(1.0, max_x * 1.08)
    max_y = max(point.y for point in points)
    if log_y:
        if any(point.y <= 0 for point in points):
            raise ValueError("log-scale values must be positive")
        log_y_min = floor(log10(min(point.y for point in points)))
        log_y_max = ceil(log10(max_y))
        if log_y_min == log_y_max:
            log_y_max += 1
        y_ticks = tuple(10**power for power in range(log_y_min, log_y_max + 1))
    else:
        y_limit = y_limit or max(1.0, max_y * 1.08)
        y_ticks = tuple(y_limit * index / 5 for index in range(6))

    def x(value: float) -> float:
        return left + value / x_limit * plot_width

    def y(value: float) -> float:
        if log_y:
            return (
                top + (log_y_max - log10(value)) / (log_y_max - log_y_min) * plot_height
            )
        assert y_limit is not None
        return top + (y_limit - value) / y_limit * plot_height

    grouped: dict[str, list[XYPoint]] = defaultdict(list)
    for point in points:
        grouped[point.model].append(point)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        "<style>text{font-family:system-ui,-apple-system,sans-serif}.axis{fill:#475569;font-size:13px}.label{fill:#0f172a;font-size:12px}.title{fill:#0f172a;font-size:24px;font-weight:600}</style>",
        f'<text class="title" x="90" y="34">{escape(title)}</text>',
        f'<text class="axis" x="90" y="54">{escape(subtitle)}</text>',
    ]
    for value in y_ticks:
        ypos = y(value)
        parts.append(
            f'<line x1="{left}" y1="{ypos:.1f}" x2="{left + plot_width}" y2="{ypos:.1f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text class="axis" x="{left - 12}" y="{ypos + 4:.1f}" text-anchor="end">{_format_tick(round(value, 1))}</text>'
        )
    tick_values = x_ticks or tuple(x_limit * index / 5 for index in range(6))
    for value in tick_values:
        xpos = x(value)
        parts.append(
            f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" y2="{top + plot_height}" stroke="#f1f5f9"/>'
        )
        parts.append(
            f'<text class="axis" x="{xpos:.1f}" y="{top + plot_height + 24}" text-anchor="middle">{_format_tick(value)}</text>'
        )
    parts.extend(
        (
            f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#64748b"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#64748b"/>',
            f'<text class="axis" x="{left + plot_width / 2:.1f}" y="{height - 20}" text-anchor="middle">{escape(x_label)}</text>',
            f'<text class="axis" transform="translate(24 {top + plot_height / 2:.1f}) rotate(-90)" text-anchor="middle">{escape(y_label)}</text>',
        )
    )
    legend_x = left + plot_width + 25
    for model_index, (model, model_points) in enumerate(sorted(grouped.items())):
        color = COLORS[model_index % len(COLORS)]
        ordered = sorted(model_points, key=lambda point: point.x)
        if len(ordered) > 1:
            coordinates = " ".join(
                f"{x(point.x):.1f},{y(point.y):.1f}" for point in ordered
            )
            parts.append(
                f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2"/>'
            )
        for point in ordered:
            xpos, ypos = x(point.x), y(point.y)
            title = escape(
                f"{point.model} / {point.profile}: "
                f"{y_label} {point.y:.1f}; {x_label} {point.x:.1f}"
            )
            parts.append(
                f'<circle cx="{xpos:.1f}" cy="{ypos:.1f}" r="6" fill="{color}"><title>{title}</title></circle>'
            )
            if label_points:
                parts.append(
                    f'<text class="label" x="{xpos + 9:.1f}" y="{ypos - 8:.1f}">{escape(point.profile)}</text>'
                )
        legend_y = top + 20 + model_index * 42
        parts.append(f'<circle cx="{legend_x}" cy="{legend_y}" r="6" fill="{color}"/>')
        words = escape(model)
        parts.append(
            f'<text class="label" x="{legend_x + 14}" y="{legend_y + 4}">{words}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_svg(points: tuple[Point, ...]) -> str:
    return _render_xy_svg(
        tuple(
            XYPoint(
                model=point.model,
                profile=point.profile,
                x=point.minutes,
                y=point.score_percent,
            )
            for point in points
        ),
        title="Production agentic quality vs task runtime",
        subtitle="Vendor-recommended sampling; raw profile points (n=1); no smoothing",
        x_label="Task runtime (minutes)",
        y_label="Rubric score (%)",
        y_limit=100,
        label_points=False,
    )


def render_profile_svg(points: tuple[ProfilePoint, ...], metric: str) -> str:
    if metric == "runtime":
        title = "Runtime vs reasoning budget"
        y_label = "Four-task runtime (minutes)"
    elif metric == "decode":
        title = "Decode throughput vs reasoning budget"
        y_label = "Weighted decode (tok/s)"
    else:
        raise ValueError(f"unknown profile metric: {metric}")
    return _render_xy_svg(
        tuple(
            XYPoint(
                model=point.model,
                profile=point.profile,
                x=point.reasoning_budget,
                y=(point.runtime_minutes if metric == "runtime" else point.decode_tps),
            )
            for point in points
        ),
        title=title,
        subtitle="Production sampling; fast 4.1K, balanced 10K, deep 24K; n=1; no smoothing",
        x_label="Reasoning budget (tokens)",
        y_label=y_label,
        x_ticks=tuple(sorted({point.reasoning_budget for point in points})),
        label_points=False,
    )


def cache_points(runs: tuple[CachePointArtifact, ...]) -> tuple[CacheChartPoint, ...]:
    return tuple(
        CacheChartPoint(
            model=run.model_label,
            mode=run.point.mode,
            x=(
                float(run.point.conversations)
                if run.point.mode == "ram"
                else float(run.point.prompt_tokens)
            ),
            ttft_seconds=(
                run.aggregate.ttft_mean_ms
                if run.point.mode == "ram"
                else run.aggregate.end_to_end_ttft_mean_ms
            )
            / 1000,
        )
        for run in runs
        if run.status == "completed"
    )


def render_cache_svg(points: tuple[CacheChartPoint, ...], mode: str) -> str:
    selected = tuple(point for point in points if point.mode == mode)
    if mode == "ram":
        title = "RAM cache switching TTFT"
        subtitle = "16K-token conversations; raw points (n=1); no smoothing; log y-axis"
        x_label = "Active conversations"
        x_ticks = (1, 2, 4, 8)
    elif mode == "disk":
        title = "Explicit disk restore + TTFT"
        subtitle = (
            "Restore plus streamed TTFT; raw points (n=1); no smoothing; log y-axis"
        )
        x_label = "Prompt tokens"
        x_ticks = (8192, 16384, 32768, 49152)
    else:
        raise ValueError(f"unknown cache mode: {mode}")
    return _render_xy_svg(
        tuple(
            XYPoint(model=point.model, profile="", x=point.x, y=point.ttft_seconds)
            for point in selected
        ),
        title=title,
        subtitle=subtitle,
        x_label=x_label,
        y_label="TTFT (seconds, log scale)",
        x_ticks=x_ticks,
        label_points=False,
        log_y=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=ROOT / "results" / "runs")
    parser.add_argument("--scores", type=Path, default=ROOT / "results" / "scores")
    parser.add_argument(
        "--cache-runs", type=Path, default=ROOT / "results" / "cache-switch"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "charts" / "agentic-quality-vs-runtime.svg",
    )
    parser.add_argument(
        "--runtime-output",
        type=Path,
        default=ROOT / "charts" / "runtime-vs-reasoning-budget.svg",
    )
    parser.add_argument(
        "--decode-output",
        type=Path,
        default=ROOT / "charts" / "decode-vs-reasoning-budget.svg",
    )
    parser.add_argument(
        "--cache-ram-output",
        type=Path,
        default=ROOT / "charts" / "cache-ram-switch-ttft.svg",
    )
    parser.add_argument(
        "--cache-disk-output",
        type=Path,
        default=ROOT / "charts" / "cache-disk-restore-ttft.svg",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runs = load_runs(args.runs)
    points = join_points(runs, load_scores(args.scores))
    profiles = profile_points(runs)
    caches = cache_points(load_cache_runs(args.cache_runs))
    for output in (
        args.output,
        args.runtime_output,
        args.decode_output,
        args.cache_ram_output,
        args.cache_disk_output,
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_svg(points), encoding="utf-8")
    args.runtime_output.write_text(
        render_profile_svg(profiles, "runtime"), encoding="utf-8"
    )
    args.decode_output.write_text(
        render_profile_svg(profiles, "decode"), encoding="utf-8"
    )
    args.cache_ram_output.write_text(render_cache_svg(caches, "ram"), encoding="utf-8")
    args.cache_disk_output.write_text(
        render_cache_svg(caches, "disk"), encoding="utf-8"
    )
    print(
        f"wrote 5 charts from {len(points)} production scores, "
        f"{len(profiles)} profile runs, and {len(caches)} cache points"
    )


if __name__ == "__main__":
    main()
