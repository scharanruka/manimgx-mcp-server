"""Benchmark: 20 tiny preview clips in one batch worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from models import Storyboard
from server import project_create, project_render_batch, scene_write, storyboard_save

pytestmark = [pytest.mark.slow, pytest.mark.gpu]

CODE = """import manimgx as m


class Scene01(m.Scene):
    def construct(self) -> None:
        text = m.Typst("Bench")
        self.play(m.Write(text), run_time=0.2)
        self.wait(0.1)
"""


def test_twenty_scene_batch_benchmark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANIM_MCP_HOME", str(tmp_path / "projects"))
    project = project_create("Batch Benchmark")
    clips = [
        {
            "scene_id": f"scene_{index:02d}",
            "title": f"Clip {index}",
            "target_seconds": 3,
            "visual": "A word",
            "key_objects": ["text"],
            "animation_notes": "Write it.",
            "typst_expressions": ["Bench"],
        }
        for index in range(1, 21)
    ]
    storyboard_save(
        project.project_id,
        Storyboard.model_validate(
            {
                "project_id": project.project_id,
                "clips": clips,
                "total_seconds": 60,
            }
        ),
    )
    for index in range(1, 21):
        scene_write(project.project_id, f"scene_{index:02d}", CODE)

    report = project_render_batch(project.project_id)

    print(
        f"\nbatch: {report.scene_count} clips, {report.total_seconds:.2f}s video, "
        f"{report.elapsed_seconds:.2f}s wall, budget {report.budget_seconds:.0f}s"
    )
    assert report.ok
    assert report.within_budget
    assert report.elapsed_seconds < 60
