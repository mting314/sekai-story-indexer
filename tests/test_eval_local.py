"""Regression gate: the local backend must pass the full golden set.

If a change to retrieval, scoping, nickname resolution, or answer construction
regresses any golden case, this test fails with a per-case breakdown.
"""

from pathlib import Path

from sekai_story_indexer.eval.local_eval import run_golden_local

REPO = Path(__file__).resolve().parent.parent


def test_local_golden_set_no_regression():
    report = run_golden_local(REPO / "eval" / "golden_local.json", base_dir=REPO)
    assert report.ok, "\n" + report.summary()


def test_golden_set_has_meaningful_coverage():
    # guard against the gate silently emptying out
    report = run_golden_local(REPO / "eval" / "golden_local.json", base_dir=REPO)
    assert report.total >= 6
