from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark import read_model_config, weight_paths  # noqa: E402
from cache_switch import (  # noqa: E402
    CachePoint,
    CacheSwitchSuite,
    MeasurementTrace,
    PromptDescriptor,
    RequestTrace,
    SlotAction,
    aggregate_point,
    read_suite,
)
from render_chart import (  # noqa: E402
    Point,
    ScoreArtifact,
    cache_points,
    join_points,
    load_cache_runs,
    load_runs,
    load_scores,
    profile_points,
    render_cache_svg,
    render_profile_svg,
    render_svg,
)


class BenchmarkContractTest(unittest.TestCase):
    def test_cache_switch_suite_defines_two_four_point_curves(self) -> None:
        suite = read_suite(ROOT / "configs" / "cache-switch.yaml")
        self.assertEqual((1, 2, 4, 8), suite.ram_conversation_levels)
        self.assertEqual((8192, 16384, 32768, 49152), suite.disk_prompt_token_levels)

    def test_cache_switch_rejects_repeated_curve_levels(self) -> None:
        with self.assertRaises(ValueError):
            CacheSwitchSuite(
                ram_prompt_tokens=1024,
                ram_conversation_levels=(1, 1),
                disk_prompt_token_levels=(1024,),
            )

    def test_slot_response_accepts_server_payload_before_wall_clock_is_added(
        self,
    ) -> None:
        response = SlotAction.model_validate({"id_slot": 0, "n_written": 1024})
        self.assertEqual(0, response.wall_ms)

    def test_cache_switch_aggregate_includes_disk_restore_in_ttft(self) -> None:
        prompt = PromptDescriptor(
            conversation=0,
            prefix_target_tokens=1024,
            measured_tokens=1000,
            sha256="a" * 64,
        )
        request = RequestTrace(
            prompt=prompt,
            started_at="2026-08-11T00:00:00+00:00",
            ttft_ms=20,
            total_ms=30,
            cache_tokens=1000,
            prompt_tokens_evaluated=0,
            prompt_ms=0,
            prompt_tps=None,
            decode_ms=10,
            decode_tps=100,
            events=(),
        )
        measurement = MeasurementTrace(
            phase="disk_restore",
            conversation=0,
            request=request,
            restore=SlotAction(id_slot=0, wall_ms=40),
        )
        aggregate = aggregate_point(
            CachePoint(
                mode="disk",
                conversations=1,
                prompt_tokens=1024,
                estimated_working_set_bytes=100,
                cache_budget_bytes=0,
                estimated_working_set_ratio=None,
            ),
            (measurement,),
            120,
        )
        self.assertEqual(60, aggregate.end_to_end_ttft_mean_ms)
        self.assertEqual(1, aggregate.cached_fraction_mean)

    def test_all_model_configs_validate(self) -> None:
        configs = tuple(
            read_model_config(path)[0]
            for path in sorted((ROOT / "configs" / "models").glob("*.yaml"))
        )
        self.assertEqual(4, len(configs))
        self.assertEqual(
            {"fast", "balanced", "deep"},
            {profile.name for profile in configs[0].profiles},
        )
        self.assertTrue(
            all(config.sampling_source_url.startswith("https://") for config in configs)
        )

    def test_sharded_weights_require_complete_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "model-00001-of-00002.gguf"
            second = root / "model-00002-of-00002.gguf"
            first.touch()
            second.touch()
            self.assertEqual((first, second), weight_paths(first))
            second.unlink()
            with self.assertRaises(RuntimeError):
                weight_paths(first)

    def test_score_rejects_out_of_range_criterion(self) -> None:
        with self.assertRaises(ValueError):
            ScoreArtifact.model_validate(
                {
                    "run_id": "run",
                    "suite_sha256": "hash",
                    "reviewer": "blind-human",
                    "scored_at": "2026-08-10T00:00:00Z",
                    "tasks": [
                        {
                            "task": "incident",
                            "score": 3,
                            "maximum": 4,
                            "criteria": [0, 3],
                        }
                    ],
                }
            )

    def test_chart_renders_raw_profile_points(self) -> None:
        svg = render_svg(
            (
                Point(
                    run_id="run",
                    model="Model",
                    profile="balanced",
                    minutes=10,
                    score_percent=75,
                ),
            )
        )
        self.assertIn("balanced", svg)
        self.assertIn("raw profile points (n=1); no smoothing", svg)

    def test_profile_charts_use_committed_runs(self) -> None:
        points = profile_points(load_runs(ROOT / "results" / "runs"))
        self.assertEqual(12, len(points))
        self.assertIn(
            "Runtime vs reasoning budget", render_profile_svg(points, "runtime")
        )
        self.assertIn(
            "Decode throughput vs reasoning budget",
            render_profile_svg(points, "decode"),
        )

    def test_cache_charts_use_all_committed_raw_points(self) -> None:
        runs = load_cache_runs(ROOT / "results" / "cache-switch")
        self.assertEqual(32, len(runs))
        self.assertTrue(all(run.trace.gist_revision_url for run in runs))
        points = cache_points(runs)
        self.assertEqual(32, len(points))
        self.assertIn("RAM cache switching TTFT", render_cache_svg(points, "ram"))
        self.assertIn("Explicit disk restore + TTFT", render_cache_svg(points, "disk"))

    def test_committed_style_artifacts_join_to_pinned_traces(self) -> None:
        runs = load_runs(ROOT / "results" / "runs")
        scores = load_scores(ROOT / "results" / "scores")
        self.assertEqual(12, len(runs))
        points = join_points(runs, scores)
        self.assertEqual(12, len(points))
        self.assertTrue(
            all(
                run.trace.gist_revision_url
                and run.trace.gist_revision_url.count("/") >= 5
                for run in runs
            )
        )


if __name__ == "__main__":
    unittest.main()
