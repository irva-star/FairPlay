"""Per-session metrics derived from evidence alone (no oracle)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evidence.store import EvidenceStore
from stats import metrics as M


def build(tmp_path: Path, events: list[tuple], *, persona: str = "free",
          session_id: str = "s1") -> EvidenceStore:
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on")
    store.start_session(session_id, run_id="r1", persona=persona, seed=1)
    for etype, payload in events:
        store.append_event(session_id, type=etype, payload=payload,
                           level_no=payload.get("level"))
    store.end_session(session_id, status="ok")
    return store


def level_end(level: int, result: str, *, best: int = 60, chosen: int | None = None) -> tuple:
    chosen = best if chosen is None else chosen
    return ("level_end", {"level": level, "result": result, "door": 0,
                          "chosen_chance": chosen, "best_chance": best,
                          "chose_best": chosen == best})


def test_metrics_for_a_typical_session(tmp_path: Path) -> None:
    events = [
        ("session_start", {"persona": "free"}),
        level_end(1, "WIN"),
        level_end(2, "LOSS"),
        ("offer_shown", {"level": 3, "price": 120}),
        ("offer_dismissed", {"level": 3}),
        ("level_start", {"level": 3, "follows_loss": True, "best_chance": 45}),
        level_end(3, "LOSS", best=45),
        ("offer_shown", {"level": 4, "price": 100}),
        ("offer_dismissed", {"level": 4}),
        ("level_start", {"level": 4, "follows_loss": True, "best_chance": 55}),
        level_end(4, "WIN", best=55),
        *[level_end(n, "WIN") for n in range(5, 11)],
        ("session_end", {"compliant": True}),
    ]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")

    assert m.wins == 8
    assert m.losses == 2
    assert m.offers == 2
    assert m.picks == 10
    assert m.m1 == pytest.approx(1.0)          # 2 offers / 2 losses
    assert m.m2 == pytest.approx(50.0)         # mean of 45 and 55
    assert m.m3 == pytest.approx(0.8)          # 8 of 10
    assert m.m4 == pytest.approx(110.0)        # mean of 120 and 100
    assert m.b1 == pytest.approx(1.0)
    store.close()


def test_a_session_with_no_losses_has_no_m1_or_m2(tmp_path: Path) -> None:
    """Averaging a zero in here would quietly pull the estimate toward the null."""
    events = [("session_start", {}), *[level_end(n, "WIN") for n in range(1, 11)],
              ("session_end", {"compliant": True})]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")
    assert m.losses == 0
    assert m.m1 is None
    assert m.m2 is None
    assert m.m3 == pytest.approx(1.0)
    store.close()


def test_a_session_with_no_offer_has_no_m4(tmp_path: Path) -> None:
    events = [("session_start", {}), level_end(1, "LOSS"),
              ("level_start", {"level": 2, "follows_loss": True, "best_chance": 60}),
              *[level_end(n, "WIN") for n in range(2, 11)],
              ("session_end", {"compliant": True})]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")
    assert m.offers == 0
    assert m.m1 == pytest.approx(0.0), "no offers over one loss is a real zero"
    assert m.m4 is None
    store.close()


def test_b1_counts_only_picks_that_took_the_best_door(tmp_path: Path) -> None:
    events = [("session_start", {}),
              level_end(1, "WIN", best=70, chosen=70),
              level_end(2, "LOSS", best=70, chosen=40),
              level_end(3, "WIN", best=70, chosen=70),
              level_end(4, "WIN", best=70, chosen=55),
              ("session_end", {"compliant": True})]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")
    assert m.picks == 4
    assert m.best_picks == 2
    assert m.b1 == pytest.approx(0.5)
    store.close()


def test_a_repeated_level_start_does_not_double_count_m2(tmp_path: Path) -> None:
    """A retry re-reads the same level; M2 must not count it twice."""
    events = [("session_start", {}), level_end(1, "LOSS"),
              ("level_start", {"level": 2, "follows_loss": True, "best_chance": 40}),
              ("level_start", {"level": 2, "follows_loss": True, "best_chance": 40}),
              level_end(2, "WIN", best=40),
              ("session_end", {"compliant": True})]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")
    assert m.m2 == pytest.approx(40.0)
    store.close()


def test_non_compliance_is_carried_through(tmp_path: Path) -> None:
    events = [("session_start", {}), level_end(1, "WIN"),
              ("session_end", {"compliant": False, "purchases": 2})]
    store = build(tmp_path, events)
    assert M.session_metrics(store.conn, "s1").compliant is False
    store.close()


def test_grouping_and_missing_counts(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on")
    for i, (persona, result) in enumerate([("spender", "LOSS"), ("free", "WIN"),
                                           ("free_twin", "WIN")]):
        sid = f"s{i}"
        store.start_session(sid, run_id="r1", persona=persona, seed=i)
        store.append_event(sid, type="session_start", payload={})
        store.append_event(sid, type="level_end",
                           payload={"level": 1, "result": result, "door": 0,
                                    "chosen_chance": 60, "best_chance": 60,
                                    "chose_best": True})
        store.append_event(sid, type="session_end", payload={"compliant": True})
        store.end_session(sid, status="ok")

    rows = M.run_metrics(store.conn, "r1")
    assert len(rows) == 3
    grouped = M.by_persona(rows, "M3")
    assert set(grouped) == {"spender", "free", "free_twin"}

    a, b = M.contrast_values(rows, "M3", "spender", "free")
    assert a == [0.0] and b == [1.0], "M3 is over levels resolved, one each here"

    missing = M.missing_counts(rows)
    assert missing["M1"] == 2, "the two winners have no offers-per-loss"
    assert missing["M4"] == 3
    assert missing["M3"] == 0
    store.close()


def test_deviation_sessions_can_be_excluded_for_the_sensitivity_analysis(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on")
    for sid, status in [("ok1", "ok"), ("bad1", "protocol_deviation")]:
        store.start_session(sid, run_id="r1", persona="free", seed=1)
        store.append_event(sid, type="level_end",
                           payload={"level": 1, "result": "WIN", "door": 0,
                                    "chosen_chance": 60, "best_chance": 60,
                                    "chose_best": True})
        store.end_session(sid, status=status)

    rows = M.run_metrics(store.conn, "r1")
    assert len(M.by_persona(rows, "M3")["free"]) == 2
    assert len(M.by_persona(rows, "M3", exclude_deviations=True)["free"]) == 1
    store.close()


def test_the_guard_pseudo_session_stays_out_of_the_analysis(tmp_path: Path) -> None:
    """Blocked requests are hash-chained under a pseudo-session; it is not gameplay."""
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on")
    for sid, persona in [("a", "free"), ("r1-guard", "guard")]:
        store.start_session(sid, run_id="r1", persona=persona, seed=1)
        store.append_event(sid, type="level_end",
                           payload={"level": 1, "result": "WIN", "door": 0,
                                    "chosen_chance": 60, "best_chance": 60,
                                    "chose_best": True})
        store.end_session(sid, status="ok")

    rows = M.run_metrics(store.conn, "r1")
    assert [r.persona for r in rows] == ["free"]
    store.close()


def test_m3_is_over_levels_resolved_not_a_fixed_ten(tmp_path: Path) -> None:
    """A session cut short must not have its missing levels read as losses."""
    events = [("session_start", {}), *[level_end(n, "WIN") for n in range(1, 10)],
              ("session_end", {"compliant": True})]
    store = build(tmp_path, events)
    m = M.session_metrics(store.conn, "s1")
    assert m.levels_resolved == 9
    assert m.m3 == pytest.approx(1.0)
    store.close()


def test_only_the_last_attempt_of_a_retried_session_is_analysed(tmp_path: Path) -> None:
    """Audit C3: attempt 1's events must not merge into attempt 2's."""
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on", switch="on")
    win = {"level": 1, "result": "WIN", "door": 0, "chosen_chance": 60,
           "best_chance": 60, "chose_best": True}
    for attempt, status, levels in [(1, "failed:TimeoutError", 3), (2, "ok", 10)]:
        sid = f"r1-free-7-a{attempt}"
        store.start_session(sid, run_id="r1", persona="free", seed=7, attempts=attempt)
        for _ in range(levels):
            store.append_event(sid, type="level_end", payload=win)
        store.end_session(sid, status=status)
    store.start_session("r1-free-8-a1", run_id="r1", persona="free", seed=8, attempts=1)
    store.append_event("r1-free-8-a1", type="level_end", payload=win)
    store.end_session("r1-free-8-a1", status="ok")

    rows = M.run_metrics(store.conn, "r1")
    assert sorted(r.session_id for r in rows) == ["r1-free-7-a2", "r1-free-8-a1"]
    assert {r.session_id: r.wins for r in rows}["r1-free-7-a2"] == 10
    assert all(r.m3 <= 1.0 for r in rows)
    # The failed attempt stays in the evidence store as attrition.
    statuses = [r[0] for r in store.conn.execute(
        "SELECT status FROM sessions WHERE seed=7 ORDER BY attempts")]
    assert statuses == ["failed:TimeoutError", "ok"]
    store.close()


def test_metrics_module_never_mentions_the_oracle() -> None:
    source = Path("stats/metrics.py").read_text(encoding="utf-8")
    assert "ground_truth" not in source
