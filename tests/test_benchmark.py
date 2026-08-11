from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark import read_model_config, weight_paths  # noqa: E402
from render_chart import (  # noqa: E402
    Point,
    ScoreArtifact,
    join_points,
    load_runs,
    load_scores,
    profile_points,
    render_profile_svg,
    render_svg,
)


class BenchmarkContractTest(unittest.TestCase):
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
