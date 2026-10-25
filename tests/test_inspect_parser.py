from __future__ import annotations

import pytest

from inspection import format_times, parse_inspect_output

VALID_OUTPUT = """scene_01.py: Scene01, 4.60 s, 6 plays
storyboard: renders/storyboards/scene_01.png
  #0   0.00– 0.80s  scene_01.py:20  Write(Typst)
layout: no problems in 6 samples
"""

PROBLEM_OUTPUT = """scene_01.py: Scene01, 2.50 s, 2 plays
storyboard: renders/storyboards/scene_01.png
layout: 2 problems, 1 note (likely meant)
  [1] t=2–2.5s (scene_01.py:9)  title (Typst): 20.43 past the right edge
      [2–3] the same for label, caption
  note: t=1–1.5s (scene_01.py:5)  arrow: runs off the left edge
"""


def test_parses_clean_output() -> None:
    issues, duration = parse_inspect_output(VALID_OUTPUT, 0)
    assert issues == []
    assert duration == 4.6


def test_parses_problems_notes_and_folded_lines() -> None:
    issues, duration = parse_inspect_output(PROBLEM_OUTPUT, 1)
    assert duration == 2.5
    assert len(issues) == 3
    assert issues[0].number == 1
    assert issues[0].line == 9
    assert not issues[0].note
    assert issues[1].number == 2
    assert "same problem" in issues[1].message
    assert issues[2].note
    assert issues[2].line == 5


def test_unparsed_failure_falls_back_to_stdout() -> None:
    issues, _ = parse_inspect_output("something went wrong\n", 1)
    assert len(issues) == 1
    assert issues[0].message == "something went wrong"


def test_format_times() -> None:
    assert format_times(None) is None
    assert format_times([]) is None
    assert format_times([1.5, "end"]) == "1.5,end"
    assert format_times([2, 0.25]) == "2,0.25"


def test_format_times_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        format_times([-1])
    with pytest.raises(ValueError):
        format_times(["middle"])  # type: ignore[list-item]
