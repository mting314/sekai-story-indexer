"""Regression eval for the query backend.

Runs a golden Q&A set through an engine and scores each case on:
  * evidence arc  — top citation's arc_id matches ``expect_arc``
  * scope         — resolved scope arc matches ``expect_scope_arc``
  * unit          — top citation unit matches ``expect_unit``
  * answer text   — answer contains any of ``answer_contains_any``
  * refusal       — ``expect_no_answer`` cases must return no citations

Deterministic against the local engine, so it gates regressions in retrieval and
scoping. The same golden format can target the full engine (which returns a
string answer and no structured citations — content checks still apply).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaseResult:
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        return self.passed == self.total

    def summary(self) -> str:
        lines = [f"{self.passed}/{self.total} cases passed"]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{mark}] {r.id}" + (f" — {'; '.join(r.failures)}" if r.failures else ""))
        return "\n".join(lines)


def _check_case(engine, case: dict) -> CaseResult:
    failures: list[str] = []
    result = engine.query(
        case["question"], unit=case.get("unit"), event_id=case.get("event_id")
    )
    answer = (result.get("answer") or "").lower()
    citations = result.get("citations") or []
    scope = result.get("scope") or {}

    if case.get("expect_no_answer"):
        if citations:
            failures.append(f"expected no answer but got {len(citations)} citations")
    else:
        if case.get("expect_arc"):
            top_arc = citations[0]["arc_id"] if citations else None
            if top_arc != case["expect_arc"]:
                failures.append(f"top arc {top_arc!r} != {case['expect_arc']!r}")
        if case.get("expect_unit"):
            top_unit = citations[0]["unit"] if citations else None
            if top_unit != case["expect_unit"]:
                failures.append(f"top unit {top_unit!r} != {case['expect_unit']!r}")

    if case.get("expect_scope_arc") and scope.get("arc_id") != case["expect_scope_arc"]:
        failures.append(f"scope arc {scope.get('arc_id')!r} != {case['expect_scope_arc']!r}")

    wanted = case.get("answer_contains_any")
    if wanted and not any(w.lower() in answer for w in wanted):
        failures.append(f"answer missing any of {wanted}")

    return CaseResult(id=case["id"], passed=not failures, failures=failures)


def run_eval(engine, cases: list[dict]) -> EvalReport:
    return EvalReport([_check_case(engine, c) for c in cases])


def load_golden(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_golden_local(golden_path: str | Path, *, base_dir: str | Path = ".") -> EvalReport:
    """Build the local engine from the paths named in the golden file and run it."""
    from ..query.local import build_local_engine

    golden = load_golden(golden_path)
    base = Path(base_dir)
    events_index = json.loads((base / golden["events_index"]).read_text(encoding="utf-8"))
    engine = build_local_engine(base / golden["corpus"], events_index)
    return run_eval(engine, golden["cases"])
