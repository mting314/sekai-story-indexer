"""The `sekai ingest` pipeline: runner, step bodies, and ingest-state bookkeeping.

Everything here is offline. The runner is exercised with synthetic steps; the step
*bodies* run for real with their network dependencies injected, so a typo in the
wiring fails here rather than at 4am in the scheduled job.
"""

import inspect
import json

import pytest

from sekai_story_indexer import ingest as ing
from sekai_story_indexer.ingest import (
    IngestConfig,
    Step,
    build_pipeline,
    merge_ingest_state,
    run_steps,
    update_ingest_state,
)


def _ok(name, out="done"):
    return Step(name, lambda: out)


def _boom(name, *, required=True, tier=1):
    def run():
        raise RuntimeError(f"{name} exploded")

    return Step(name, run, required=required, tier=tier)


# --- runner ---------------------------------------------------------------


def test_steps_run_in_order_and_report_details():
    calls = []
    steps = [Step(n, (lambda n=n: (calls.append(n), f"{n} ok")[1])) for n in ("a", "b", "c")]
    report = run_steps(steps, log=lambda _m: None)
    assert calls == ["a", "b", "c"]
    assert report.ok
    assert [(r.name, r.status, r.detail) for r in report.steps] == [
        ("a", "ok", "a ok"), ("b", "ok", "b ok"), ("c", "ok", "c ok")
    ]


def test_optional_failure_is_recorded_but_the_run_continues():
    """The whole point of Tier 2: no key / spend cap must not stop Tier 1's siblings."""
    report = run_steps(
        [_ok("fetch"), _boom("summarize", required=False, tier=2), _ok("build-index")],
        log=lambda _m: None,
    )
    statuses = {r.name: r.status for r in report.steps}
    assert statuses == {"fetch": "ok", "summarize": "failed", "build-index": "ok"}
    assert report.ok, "an optional failure must not fail the run"
    assert "RuntimeError" in next(r for r in report.steps if r.name == "summarize").detail


def test_required_failure_aborts_the_rest_but_still_reports_them():
    report = run_steps([_ok("fetch"), _boom("classify"), _ok("build-index")], log=lambda _m: None)
    statuses = {r.name: r.status for r in report.steps}
    assert statuses == {"fetch": "ok", "classify": "failed", "build-index": "skipped"}
    assert not report.ok
    # Skipped steps are visible in the report, not silently dropped.
    assert "required step failed" in next(r for r in report.steps if r.name == "build-index").detail


def test_summary_lists_every_step():
    report = run_steps([_ok("fetch"), _boom("link", required=False)], log=lambda _m: None)
    text = report.summary()
    assert "1 ok, 1 failed, 0 skipped" in text
    assert "fetch" in text and "link" in text


def test_step_timings_use_the_injected_clock():
    ticks = iter([0.0, 5.0, 5.0, 8.0])
    report = run_steps([_ok("a"), _ok("b")], log=lambda _m: None, clock=lambda: next(ticks))
    assert [r.seconds for r in report.steps] == [5.0, 3.0]


# --- pipeline shape -------------------------------------------------------


def test_tier1_only_by_default():
    names = [s.name for s in build_pipeline(IngestConfig())]
    assert names == [
        "fetch", "fetch-card-stories", "fetch-area-conversations", "link-content",
        "build-lyric-map", "classify", "build-index", "record-state",
    ]
    assert all(s.tier == 1 for s in build_pipeline(IngestConfig()))


def test_with_llm_appends_tier2_after_tier1():
    steps = build_pipeline(IngestConfig(with_llm=True))
    tier2 = [s.name for s in steps if s.tier == 2]
    assert tier2 == ["summarize", "conclusions", "resonance"]
    # ...and only after the keyless work, so a failing LLM pass can't cost Tier 1.
    assert steps.index(next(s for s in steps if s.name == "summarize")) > steps.index(
        next(s for s in steps if s.name == "build-index")
    )
    assert all(not s.required for s in steps if s.tier == 2)


def test_network_side_quests_are_optional_and_local_rebuilds_are_required():
    by_name = {s.name: s for s in build_pipeline(IngestConfig())}
    assert [n for n, s in by_name.items() if s.required] == [
        "fetch", "classify", "build-index", "record-state"
    ]
    assert not by_name["link-content"].required
    assert not by_name["build-lyric-map"].required


def test_card_and_area_fetches_can_be_switched_off():
    names = [s.name for s in build_pipeline(IngestConfig(cards=False, areas=False))]
    assert "fetch-card-stories" not in names and "fetch-area-conversations" not in names


def test_unit_stories_are_opt_in():
    assert "fetch-unit-stories" not in [s.name for s in build_pipeline(IngestConfig())]
    assert "fetch-unit-stories" in [
        s.name for s in build_pipeline(IngestConfig(unit_stories=True))
    ]


# --- step bodies ----------------------------------------------------------
#
# build_pipeline() only asserts shape, so without these a typo inside a step body
# would ship green. Each step runs for real with its network dependency injected.


@pytest.fixture
def cfg(tmp_path):
    return IngestConfig(
        story_root=tmp_path / "story",
        events_index=tmp_path / "events_index.json",
        content_parents=tmp_path / "content_parents.json",
        lyric_map=tmp_path / "lyric_page_map.json",
        derived_index=tmp_path / "derived_index.json.gz",
        state_path=tmp_path / "ingest_state.json",
    )


def test_fetch_step_forwards_limit_and_skip_existing(cfg, monkeypatch):
    seen = {}

    def fake(story_root, **kw):
        seen.update(story_root=story_root, **kw)
        return ["plan-a", "plan-b"]

    monkeypatch.setattr("sekai_story_indexer.source.fetcher.fetch_and_write", fake)
    cfg.limit_events = 3
    assert "2 events" in ing._fetch_events(cfg)
    assert seen["story_root"] == cfg.story_root
    assert seen["limit"] == 3 and seen["skip_existing"] is True
    assert seen["log"]("noise") is None, "per-episode chatter is silenced"


def test_fetch_step_passes_no_limit_for_zero(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "sekai_story_indexer.source.fetcher.fetch_and_write",
        lambda story_root, **kw: (seen.update(kw), [])[1],
    )
    ing._fetch_events(cfg)
    assert seen["limit"] is None, "0 means 'all', not 'fetch nothing'"


@pytest.mark.parametrize(
    "body,target,detail",
    [
        (ing._fetch_unit_stories, "fetch_unit_stories", "7 unit-story episodes"),
        (ing._fetch_cards, "fetch_card_stories", "7 card-story episodes"),
        (ing._fetch_areas, "fetch_area_conversations", "7 area-conversation talks"),
    ],
)
def test_corpus_fetch_steps_report_their_counts(cfg, monkeypatch, body, target, detail):
    monkeypatch.setattr(f"sekai_story_indexer.source.fetcher.{target}", lambda *a, **k: 7)
    assert detail in body(cfg)


def test_link_content_writes_the_parents_file(cfg, monkeypatch):
    doc = {"cards": {"1": {}, "2": {}}, "areas": {"9": {}}}
    for name in ("cards", "event_cards", "action_sets", "release_conditions",
                 "event_stories", "events"):
        monkeypatch.setattr(f"sekai_story_indexer.source.client.{name}", lambda: [])
    for name in ("build_card_parent_map", "build_area_event_map"):
        monkeypatch.setattr(f"sekai_story_indexer.source.transform.{name}", lambda *a, **k: {})
    monkeypatch.setattr(
        "sekai_story_indexer.source.transform.build_content_parents", lambda *a, **k: doc
    )
    assert ing._link_content(cfg) == "2 cards + 1 area talks linked"
    assert json.loads(cfg.content_parents.read_text()) == doc


def test_build_lyric_map_writes_meta_and_sorted_map(cfg, monkeypatch):
    monkeypatch.setattr(
        "sekai_story_indexer.source.client.musics", lambda: [{"id": 2}, {"id": 1}, {"id": "x"}]
    )
    monkeypatch.setattr("sekai_story_indexer.source.client.sekaipedia_song_pages", lambda: [])
    monkeypatch.setattr(
        "sekai_story_indexer.source.transform.build_lyric_page_map",
        lambda rows, known: {"mapping": {2: 22, 1: 11}, "missing": [3], "extra": []},
    )
    assert ing._build_lyric_map(cfg) == "2/2 songs mapped"
    doc = json.loads(cfg.lyric_map.read_text())
    assert list(doc["map"]) == ["1", "2"], "stable ordering keeps the commit diff clean"
    assert doc["_meta"]["missing"] == [3]


def test_classify_rewrites_the_index_in_place(cfg):
    cfg.events_index.write_text(
        json.dumps([{"event_id": 1, "name": "a"}, {"event_id": 2, "name": "b"}]), encoding="utf-8"
    )
    detail = ing._classify(cfg)
    rows = json.loads(cfg.events_index.read_text())
    assert all("plot_weight" in r for r in rows)
    assert all(r["name"] for r in rows), "classify must not drop existing fields"
    assert "2 events" in detail


def test_build_index_step_forwards_its_paths(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "sekai_story_indexer.query.derived_index.build_index_file",
        lambda story_root, **kw: (seen.update(story_root=story_root, **kw), kw["out_path"])[1],
    )
    assert str(cfg.derived_index) in ing._build_index(cfg)
    assert seen["story_root"] == cfg.story_root
    assert seen["events_index_path"] == cfg.events_index


def test_record_state_step_reports_new_events(cfg):
    cfg.events_index.write_text(json.dumps([{"event_id": 1}]), encoding="utf-8")
    assert ing._record_state(cfg) == "no new events", "the first run is a baseline"
    cfg.events_index.write_text(json.dumps([{"event_id": 1}, {"event_id": 2}]), encoding="utf-8")
    assert ing._record_state(cfg) == "1 new event(s): [2]"


# --- Tier 2 wiring --------------------------------------------------------
#
# The Tier-2 steps call Typer command functions directly, whose defaults are
# OptionInfo objects. If a command gains a parameter and ingest.py isn't updated,
# an OptionInfo silently arrives where a value belongs. Pin the signatures.

TIER2 = [("_summarize", "summarize"), ("_conclusions", "conclusions"), ("_resonance", "resonance")]


@pytest.mark.parametrize("body_name,cli_name", TIER2)
def test_tier2_steps_pass_every_cli_parameter(cfg, monkeypatch, body_name, cli_name):
    import typer

    from sekai_story_indexer import localcli

    expected = set(inspect.signature(getattr(localcli, cli_name)).parameters)
    captured: dict = {}
    monkeypatch.setattr(localcli, cli_name, lambda **kw: captured.update(kw))

    getattr(ing, body_name)(cfg)

    missing = expected - set(captured)
    assert not missing, (
        f"localcli.{cli_name} gained {sorted(missing)}; ingest.{body_name} must pass "
        f"it explicitly or Typer's OptionInfo default leaks in as a value"
    )
    leaked = [k for k, v in captured.items() if isinstance(v, typer.models.OptionInfo)]
    assert not leaked, f"OptionInfo passed as a value for {leaked}"


@pytest.mark.parametrize("body_name,cli_name", TIER2)
def test_tier2_steps_are_resumable_and_batch_capped(cfg, monkeypatch, body_name, cli_name):
    """--batch-limit is the spend-cap knob; skip_existing is what makes a capped
    run resumable instead of a partial rewrite."""
    from sekai_story_indexer import localcli

    captured: dict = {}
    monkeypatch.setattr(localcli, cli_name, lambda **kw: captured.update(kw))
    cfg.batch_limit = 3
    getattr(ing, body_name)(cfg)
    assert captured["limit"] == 3
    assert captured["skip_existing"] is True


# --- ingest state ---------------------------------------------------------


def test_merge_records_first_seen_and_reports_new_ids():
    rows = [{"event_id": 1}, {"event_id": 2}]
    state, new = merge_ingest_state(None, rows, now=100.0)
    assert new == [1, 2]
    assert state["first_seen"] == {"1": 100.0, "2": 100.0}

    state2, new2 = merge_ingest_state(state, [*rows, {"event_id": 3}], now=200.0)
    assert new2 == [3], "already-known events are not re-flagged"
    assert state2["first_seen"]["1"] == 100.0, "first-seen is never overwritten"
    assert state2["first_seen"]["3"] == 200.0
    assert state2["last_run"] == 200.0


def test_merge_ignores_rows_without_an_integer_event_id():
    state, new = merge_ingest_state(None, [{"event_id": None}, {"name": "x"}], now=1.0)
    assert new == [] and state["first_seen"] == {}


def test_backdating_uses_the_release_date_and_never_the_future():
    rows = [
        {"event_id": 1, "started_at": 500_000},          # released (epoch ms) -> 500s
        {"event_id": 2, "started_at": 9_000_000_000},    # announced, not out yet
        {"event_id": 3},                                 # no date -> clock
    ]
    state, _ = merge_ingest_state(None, rows, now=1000.0, backdate=True)
    assert state["first_seen"] == {"1": 500.0, "2": 1000.0, "3": 1000.0}


def test_first_run_over_an_existing_corpus_is_a_baseline_not_209_new_events(tmp_path):
    """Otherwise adopting the pipeline would light up the entire back catalogue —
    both in the announced list AND (via first_seen) in the app's NEW badge."""
    idx = tmp_path / "events_index.json"
    rows = [{"event_id": i, "started_at": 100_000 + i} for i in range(5)]
    idx.write_text(json.dumps(rows), encoding="utf-8")
    state_path = tmp_path / "ingest_state.json"

    assert update_ingest_state(idx, state_path, now=1_000_000.0) == []
    doc = json.loads(state_path.read_text())
    assert doc["baseline"] is True
    assert doc["last_new_event_ids"] == []
    assert len(doc["first_seen"]) == 5  # still recorded, just not announced
    # ...and backdated to the release dates, so the badge doesn't fire either.
    assert doc["first_seen"]["0"] == 100.0

    # The *next* run does announce a genuinely new event, stamped at ingest time.
    rows.append({"event_id": 5, "started_at": 100_005})
    idx.write_text(json.dumps(rows), encoding="utf-8")
    assert update_ingest_state(idx, state_path, now=2_000_000.0) == [5]
    doc = json.loads(state_path.read_text())
    assert doc["last_new_event_ids"] == [5]
    assert doc["first_seen"]["5"] == 2_000_000.0


def test_corrupt_state_file_is_treated_as_a_fresh_baseline(tmp_path):
    idx = tmp_path / "events_index.json"
    idx.write_text(json.dumps([{"event_id": 7}]), encoding="utf-8")
    state_path = tmp_path / "ingest_state.json"
    state_path.write_text("{not json", encoding="utf-8")
    assert update_ingest_state(idx, state_path, now=5.0) == []
    assert json.loads(state_path.read_text())["first_seen"] == {"7": 5.0}


def test_missing_events_index_is_not_fatal_and_writes_nothing(tmp_path):
    state_path = tmp_path / "ingest_state.json"
    assert update_ingest_state(tmp_path / "nope.json", state_path, now=5.0) == []
    assert not state_path.exists(), "a run that learned nothing must not stamp state"


def test_an_empty_index_cannot_poison_the_next_real_run(tmp_path):
    """Regression: writing a baseline marker for zero events made the NEXT run look
    incremental, so it stamped the whole catalogue at `now` and badged it all NEW —
    exactly what the baseline rule exists to prevent."""
    idx = tmp_path / "events_index.json"
    state_path = tmp_path / "ingest_state.json"

    idx.write_text("[]", encoding="utf-8")
    assert update_ingest_state(idx, state_path, now=1_000_000.0) == []
    assert not state_path.exists()

    # The first run with real data is still treated as the baseline: backdated,
    # nothing announced.
    idx.write_text(
        json.dumps([{"event_id": i, "started_at": 100_000} for i in range(3)]), encoding="utf-8"
    )
    assert update_ingest_state(idx, state_path, now=2_000_000.0) == []
    doc = json.loads(state_path.read_text())
    assert doc["baseline"] is True
    assert set(doc["first_seen"].values()) == {100.0}, "backdated, not stamped at now"


# --- CLI wiring -----------------------------------------------------------


def test_cli_ingest_exits_nonzero_when_a_required_step_fails(monkeypatch, tmp_path):
    """`sekai ingest` must fail loudly for Tier 1 so a scheduler notices."""
    import typer

    from sekai_story_indexer import localcli

    monkeypatch.setattr(
        "sekai_story_indexer.ingest.build_pipeline", lambda cfg: [_boom("fetch")]
    )
    with pytest.raises(typer.Exit) as exc:
        localcli.ingest(
            story_root=tmp_path, events_index=tmp_path / "events_index.json",
            skip_existing=True, limit_events=0, cards=False, areas=False,
            unit_stories=False, with_llm=False, batch_limit=5, model="",
            reload_url="", state_path=tmp_path / "ingest_state.json",
        )
    assert exc.value.exit_code == 1
