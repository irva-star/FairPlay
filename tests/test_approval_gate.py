"""Rule S14: a batch starts only after a named person approved that exact plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from evidence.store import EvidenceStore

yaml = pytest.importorskip("yaml")

from walker.budget import BudgetExceeded, BudgetGuard, load_pricing  # noqa: E402
from walker.cli import build_plan, cmd_run  # noqa: E402


class Args:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def test_a_plan_hash_covers_what_matters(tmp_path: Path) -> None:
    plan = build_plan("configs/run_on.yaml")
    assert plan.plan_hash == build_plan("configs/run_on.yaml").plan_hash, "must be stable"

    hashable = plan.hashable()
    for field in ("origin", "switch", "model", "personas", "system_prompt_hash", "tools_hash"):
        assert field in hashable, f"{field} must be inside the approved hash"


def test_different_batches_have_different_plan_hashes() -> None:
    on = build_plan("configs/run_on.yaml")
    off = build_plan("configs/run_off.yaml")
    pilot = build_plan("configs/run_pilot.yaml")
    assert len({on.plan_hash, off.plan_hash, pilot.plan_hash}) == 3


def test_changing_the_prompt_invalidates_the_approval(monkeypatch) -> None:
    """An approval is for a plan, not a standing permission."""
    before = build_plan("configs/run_on.yaml").plan_hash
    monkeypatch.setattr("walker.prompts.SYSTEM_PROMPT_TEMPLATE",
                        "You are a different agent now. Persona: {persona_sentence}")
    after = build_plan("configs/run_on.yaml").plan_hash
    assert before != after


def test_the_on_batch_includes_the_aa_control() -> None:
    plan = build_plan("configs/run_on.yaml")
    assert set(plan.personas) == {"spender", "free", "free_twin"}
    assert plan.personas["free_twin"] == plan.personas["free"]


def test_the_off_batch_has_no_aa_twin() -> None:
    plan = build_plan("configs/run_off.yaml")
    assert "free_twin" not in plan.personas
    assert plan.switch == "off"


def test_seeds_are_unique_within_and_across_batches() -> None:
    plans = [build_plan(p) for p in ("configs/run_pilot.yaml", "configs/run_on.yaml",
                                     "configs/run_off.yaml")]
    for plan in plans:
        seeds = [seed for _, seed in plan.jobs]
        assert len(seeds) == len(set(seeds)), f"{plan.label} replays a seed"
        assert len(plan.jobs) == plan.sessions

    everything = [seed for plan in plans for _, seed in plan.jobs]
    assert len(everything) == len(set(everything)), "seeds collide across batches"


def test_jobs_are_interleaved_not_run_in_persona_blocks() -> None:
    """Personas must share the hour, so drift cannot masquerade as an effect."""
    plan = build_plan("configs/run_on.yaml")
    personas = [persona for persona, _ in plan.jobs]
    first_ten = set(personas[:10])
    assert len(first_ten) > 1, "the first ten jobs are all one persona"


def test_the_shuffle_is_reproducible() -> None:
    assert build_plan("configs/run_on.yaml").jobs == build_plan("configs/run_on.yaml").jobs


def test_concurrency_is_capped_at_eight() -> None:
    plan = build_plan("configs/run_on.yaml", concurrency=99)
    assert plan.concurrency == 8


def test_run_refuses_without_an_approval(tmp_path: Path, capsys) -> None:
    db = tmp_path / "evidence.db"
    EvidenceStore(db).close()
    code = cmd_run(Args(config="configs/run_pilot.yaml", db=str(db),
                        pricing="configs/pricing.yaml", concurrency=1, llm="fake"))
    assert code == 2
    assert "no approval" in capsys.readouterr().err.lower()


def test_run_proceeds_past_the_gate_once_approved(tmp_path: Path, monkeypatch) -> None:
    """Checks the gate itself, not the batch: the browser is never launched."""
    db = tmp_path / "evidence.db"
    plan = build_plan("configs/run_pilot.yaml")
    with EvidenceStore(db) as store:
        store.record_approval(kind="run_plan", object_hash=plan.plan_hash,
                              approver="Test Approver")

    called = {}

    async def fake_batch(plan, pricing, store, budget, args):
        called["ran"] = plan.plan_hash
        return 0

    monkeypatch.setattr("walker.cli._run_batch", fake_batch)
    code = cmd_run(Args(config="configs/run_pilot.yaml", db=str(db),
                        pricing="configs/pricing.yaml", concurrency=1, llm="fake"))
    assert code == 0
    assert called["ran"] == plan.plan_hash


def test_an_approval_for_a_different_plan_does_not_count(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    other = build_plan("configs/run_off.yaml")
    with EvidenceStore(db) as store:
        store.record_approval(kind="run_plan", object_hash=other.plan_hash, approver="Someone")

    code = cmd_run(Args(config="configs/run_pilot.yaml", db=str(db),
                        pricing="configs/pricing.yaml", concurrency=1, llm="fake"))
    assert code == 2


def test_run_refuses_when_the_estimate_exceeds_the_remaining_cap(tmp_path: Path, capsys) -> None:
    db = tmp_path / "evidence.db"
    plan = build_plan("configs/run_on.yaml")
    with EvidenceStore(db) as store:
        store.record_approval(kind="run_plan", object_hash=plan.plan_hash, approver="Someone")
        store.record_spend({"run_id": "earlier", "session_id": "x", "model": "m",
                            "input_tokens": 0, "output_tokens": 0, "usd": 9.999})

    code = cmd_run(Args(config="configs/run_on.yaml", db=str(db),
                        pricing="configs/pricing.yaml", concurrency=1, llm="real"))
    assert code == 2
    assert "refused" in capsys.readouterr().err.lower()


def test_the_estimate_for_the_main_batches_fits_the_cap() -> None:
    pricing = load_pricing("configs/pricing.yaml")
    total = sum(build_plan(p).estimated_cost(pricing)
                for p in ("configs/run_pilot.yaml", "configs/run_on.yaml",
                          "configs/run_off.yaml"))
    assert total < pricing.caps.run, f"planned batches estimate ${total:.4f}"
    guard = BudgetGuard(pricing.prices, cap_usd=pricing.caps.run,
                        per_session_cap_usd=pricing.caps.per_session)
    guard.check_estimate(total)


def test_a_public_origin_in_a_config_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text(yaml.safe_dump({
        "label": "bad", "origin": "https://example.com", "switch": "on",
        "model": "claude-haiku-5-5", "seed_base": 9000, "master_seed": 1,
        "personas": {"free": 1},
    }), encoding="utf-8")

    db = tmp_path / "evidence.db"
    EvidenceStore(db).close()
    with pytest.raises(ValueError, match="not allowlisted"):
        cmd_run(Args(config=str(config), db=str(db), pricing="configs/pricing.yaml",
                     concurrency=1, llm="fake"))


def test_approvals_are_recorded_with_a_name_and_a_time(tmp_path: Path) -> None:
    with EvidenceStore(tmp_path / "e.db") as store:
        approval_id = store.record_approval(kind="run_plan", object_hash="abc",
                                            approver="Sarah H", note="checked the cost")
        row = store.find_approval(kind="run_plan", object_hash="abc")
        assert row["approval_id"] == approval_id
        assert row["approver"] == "Sarah H"
        assert row["approved_at"]
        assert row["note"] == "checked the cost"
        assert store.find_approval(kind="finding", object_hash="abc") is None


def test_personas_differ_by_exactly_one_sentence() -> None:
    personas = yaml.safe_load(Path("configs/personas.yaml").read_text(encoding="utf-8"))["personas"]
    assert set(personas) == {"spender", "free", "free_twin", "renter", "member", "member_twin"}
    assert personas["member"]["sentence"] == personas["member_twin"]["sentence"], (
        "member_twin is StreamVault's A/A control and must be byte-identical to member"
    )
    assert personas["renter"]["sentence"] != personas["member"]["sentence"]
    assert personas["free"]["sentence"] == personas["free_twin"]["sentence"], (
        "free_twin is the A/A control and must be byte-identical to free"
    )
    assert personas["spender"]["sentence"] != personas["free"]["sentence"]
    assert personas["spender"]["expect_purchases"] == 1
    assert personas["free"]["expect_purchases"] == 0
