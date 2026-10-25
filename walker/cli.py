"""`python -m walker plan | approve | run` (SPEC section 6, rules S1, S5, S14).

A batch starts only after a named person approved *this* plan, including its
cost estimate. The plan hash covers the origin, the switch, the personas and
session counts, the model and the prompt hashes, so changing any of them
invalidates the approval rather than silently reusing it.

    python -m walker plan configs/run_on.yaml
    python -m walker approve <plan_hash> --name "Full Name"
    python -m walker run configs/run_on.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence.store import DEFAULT_DB, EvidenceStore
from walker import guard, prompts
from walker.budget import (
    BudgetExceeded, BudgetGuard, Pricing, estimate_session_cost, load_pricing,
)
from walker.agent import MAX_STEPS, SessionDeps, run_session
from walker.writer import EvidenceWriter

PERSONAS_PATH = Path("configs/personas.yaml")
ENV_PATH = Path(".env")


def load_env_file(path: str | Path = ENV_PATH) -> list[str]:
    """Read .env into the process environment. Returns the names it set.

    Done with the standard library rather than python-dotenv so the key's only
    hop is file -> this process, with no extra dependency in the chain that
    handles it (rule S13).

    Values already set in the real environment win, so nothing here can
    silently override a key you exported on purpose. The values themselves are
    never returned or logged -- only the names.
    """
    if not path or not Path(path).exists():
        return []

    loaded: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_yaml(path: str | Path) -> dict:
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5, check=False)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@dataclass
class Plan:
    label: str
    origin: str
    switch: str
    model: str
    personas: dict[str, int]
    seed_base: int
    master_seed: int
    concurrency: int
    max_steps: int
    system_prompt_hash: str
    tools_hash: str
    config_hash: str
    jobs: list[tuple[str, int]] = field(default_factory=list)

    @property
    def sessions(self) -> int:
        return sum(self.personas.values())

    @property
    def estimated_calls(self) -> int:
        from walker.budget import EST_STEPS_PER_SESSION

        return self.sessions * EST_STEPS_PER_SESSION

    def estimated_cost(self, pricing: Pricing) -> float:
        return self.sessions * estimate_session_cost(pricing.prices)

    def hashable(self) -> dict:
        return {
            "label": self.label, "origin": self.origin, "switch": self.switch,
            "model": self.model, "personas": self.personas, "seed_base": self.seed_base,
            "master_seed": self.master_seed, "max_steps": self.max_steps,
            "system_prompt_hash": self.system_prompt_hash, "tools_hash": self.tools_hash,
        }

    @property
    def plan_hash(self) -> str:
        return _sha256(_canonical(self.hashable()))


def _check_config(config: dict, config_path: str | Path) -> None:
    """Reject YAML 1.1 surprises: a bare `on` or `off` loads as a boolean.

    An unquoted `label: on` used to become the run label "True", and no analysis
    contrast ever matched it. Quote the values: `label: "on"`, `switch: "on"`.
    """
    label = config.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(f"{config_path}: label must be a quoted string, got {label!r}")
    switch = config.get("switch")
    if switch not in ("on", "off"):
        raise ValueError(f"{config_path}: switch must be \"on\" or \"off\" (quoted), got {switch!r}")


def build_plan(config_path: str | Path, *, concurrency: int | None = None) -> Plan:
    config = load_yaml(config_path)
    _check_config(config, config_path)
    personas_cfg = load_yaml(PERSONAS_PATH)["personas"]

    personas = {name: int(n) for name, n in (config.get("personas") or {}).items()}
    for name in personas:
        if name not in personas_cfg:
            raise ValueError(f"persona {name!r} is not defined in {PERSONAS_PATH}")

    plan = Plan(
        label=config["label"],
        origin=str(config["origin"]),
        switch=config["switch"],
        model=str(config.get("model", "claude-haiku-5-5")),
        personas=personas,
        seed_base=int(config.get("seed_base", 0)),
        master_seed=int(config.get("master_seed", 0)),
        concurrency=int(concurrency or config.get("concurrency", 4)),
        max_steps=int(config.get("max_steps", MAX_STEPS)),
        system_prompt_hash=prompts.system_prompt_hash(),
        tools_hash=prompts.tools_hash(),
        config_hash=_sha256(_canonical(config)),
    )

    # Jobs are (persona, seed) pairs, shuffled with the batch's master seed so
    # personas interleave rather than running in blocks.
    jobs: list[tuple[str, int]] = []
    offset = 0
    for name, count in sorted(personas.items()):
        for i in range(count):
            jobs.append((name, plan.seed_base + offset + i))
        offset += count
    random.Random(plan.master_seed).shuffle(jobs)
    plan.jobs = jobs
    plan.concurrency = min(plan.concurrency, 8)
    return plan


def print_plan(plan: Plan, pricing: Pricing, *, spent: float) -> None:
    remaining = max(0.0, pricing.caps.run - spent)
    estimate = plan.estimated_cost(pricing)
    print(f"Run plan: {plan.label}")
    print(f"  origin               {plan.origin}")
    print(f"  server switch        PLANTED_SWITCH={plan.switch}")
    print(f"  model                {plan.model}")
    print(f"  personas             " + ", ".join(f"{k} x{v}" for k, v in sorted(plan.personas.items())))
    print(f"  sessions             {plan.sessions}")
    print(f"  concurrency          {plan.concurrency}")
    print(f"  max steps/session    {plan.max_steps}")
    print(f"  system prompt sha256 {plan.system_prompt_hash}")
    print(f"  tools sha256         {plan.tools_hash}")
    print(f"  estimated calls      ~{plan.estimated_calls}")
    print(f"  estimated cost       ${estimate:.4f}")
    print(f"  spent so far         ${spent:.4f} of ${pricing.caps.run:.2f} cap")
    print(f"  remaining            ${remaining:.4f}")
    if estimate > remaining:
        print("  STATUS               REFUSED: estimate exceeds the remaining cap")
    print(f"  plan hash            {plan.plan_hash}")
    print()
    print(f"To approve:  python -m walker approve {plan.plan_hash} --name \"Your Name\"")


def cmd_plan(args) -> int:
    pricing = load_pricing(args.pricing)
    plan = build_plan(args.config, concurrency=args.concurrency)
    guard.assert_config_origin(plan.origin)
    with EvidenceStore(args.db) as store:
        print_plan(plan, pricing, spent=store.total_spend_usd())
    return 0


def cmd_approve(args) -> int:
    with EvidenceStore(args.db) as store:
        approval_id = store.record_approval(
            kind="run_plan", object_hash=args.plan_hash, approver=args.name, note=args.note or "",
        )
    print(f"approval {approval_id} recorded for plan {args.plan_hash} by {args.name}")
    return 0


def cmd_run(args) -> int:
    pricing = load_pricing(args.pricing)
    plan = build_plan(args.config, concurrency=args.concurrency)
    guard.assert_config_origin(plan.origin)

    # Fail before launching a browser if the key is absent, rather than part way
    # through a batch. Only its presence is checked; the value is never printed.
    if args.llm == "real" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("REFUSED: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        print("Put it in .env (gitignored) as ANTHROPIC_API_KEY=... , or export it.",
              file=sys.stderr)
        print("For a zero-cost smoke run instead: --llm fake", file=sys.stderr)
        return 2

    with EvidenceStore(args.db) as store:
        approval = store.find_approval(kind="run_plan", object_hash=plan.plan_hash)
        if approval is None:
            print(f"REFUSED: no approval for plan {plan.plan_hash}.", file=sys.stderr)
            print(f"Run: python -m walker approve {plan.plan_hash} --name \"Your Name\"",
                  file=sys.stderr)
            return 2

        spent = store.total_spend_usd()
        cap = pricing.caps.development if args.llm == "fake" else pricing.caps.run
        budget = BudgetGuard(
            pricing.prices, cap_usd=cap, per_session_cap_usd=pricing.caps.per_session,
            already_spent_usd=spent, on_spend=store.record_spend,
        )
        try:
            budget.check_estimate(plan.estimated_cost(pricing))
        except BudgetExceeded as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2

        print(f"approved by {approval['approver']} at {approval['approved_at']}")
        return asyncio.run(_run_batch(plan, pricing, store, budget, args))


async def _run_batch(plan: Plan, pricing: Pricing, store: EvidenceStore,
                     budget: BudgetGuard, args) -> int:
    from playwright.async_api import async_playwright

    from walker.llm import AnthropicWalker, FakeLLM
    from walker.ratelimit import RateLimiter

    personas_cfg = load_yaml(PERSONAS_PATH)["personas"]
    run_id = f"{plan.label}-{uuid.uuid4().hex[:8]}"
    approval = store.find_approval(kind="run_plan", object_hash=plan.plan_hash)
    store.create_run(run_id, label=plan.label, switch=plan.switch, config_hash=plan.config_hash,
                     plan_hash=plan.plan_hash, approval_id=approval["approval_id"],
                     git_commit=git_commit(), status="running")

    limiter = RateLimiter(default_requests_per_minute=pricing.default_requests_per_minute,
                          fraction=pricing.header_safety_fraction)
    llm = FakeLLM(seed=plan.master_seed) if args.llm == "fake" else AnthropicWalker(
        limiter, model=plan.model)

    blocked: list[str] = []
    semaphore = asyncio.Semaphore(plan.concurrency)
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=guard.chromium_args())
        async with EvidenceWriter(store) as writer:

            async def one(persona: str, seed: int) -> None:
                async with semaphore:
                    attempts = pricing.limits.session_retries + 1
                    for attempt in range(1, attempts + 1):
                        # Each attempt is its own session with its own hash chain,
                        # so a retry never appends to a failed attempt's events.
                        # The analysis keeps only the last attempt per seed.
                        session_id = f"{run_id}-{persona}-{seed}-a{attempt}"
                        store.start_session(session_id, run_id=run_id, persona=persona,
                                            seed=seed, attempts=attempt)
                        try:
                            budget.check_can_start_session()
                        except BudgetExceeded as exc:
                            store.end_session(session_id, status="budget_stop")
                            print(f"  {session_id}: {exc}")
                            return

                        context = await browser.new_context(**guard.CONTEXT_OPTIONS)
                        await guard.install_guard(context, blocked.append)
                        page = await context.new_page()
                        try:
                            await page.goto(f"{plan.origin}/?seed={seed}")
                            result = await asyncio.wait_for(
                                run_session(
                                    page, session_id=session_id, persona=persona,
                                    persona_sentence=personas_cfg[persona]["sentence"],
                                    seed=seed, run_id=run_id, max_steps=plan.max_steps,
                                    deps=SessionDeps(
                                        llm=llm, emit=writer.emitter(session_id),
                                        capture=writer.capturer(page, session_id),
                                        prices=pricing.prices, budget=budget,
                                    ),
                                ),
                                timeout=pricing.limits.session_timeout_seconds,
                            )
                            store.end_session(session_id, status=result.status)
                            results.append(result)
                            print(f"  {session_id}: {result.status} "
                                  f"steps={result.steps} ${result.cost_usd:.5f}"
                                  f"{'' if result.compliant else ' NON-COMPLIANT'}")
                            return
                        except Exception as exc:  # noqa: BLE001 - every failure is attrition
                            label = type(exc).__name__
                            store.end_session(session_id, status=f"failed:{label}")
                            if attempt >= attempts:
                                print(f"  {session_id}: failed after {attempt} attempts ({label})")
                            else:
                                print(f"  {session_id}: attempt {attempt} failed ({label}), retrying")
                        finally:
                            await context.close()

            await asyncio.gather(*(one(p, s) for p, s in plan.jobs))

        await browser.close()

    # Blocked requests get their own session row so they are hash-chained and
    # covered by the manifest like everything else, rather than sitting in the
    # events table outside any verified chain.
    if blocked:
        guard_session = f"{run_id}-guard"
        store.start_session(guard_session, run_id=run_id, persona="guard", seed=0)
        for url in blocked:
            store.append_event(guard_session, type="blocked_request", payload={"url": url})
        store.end_session(guard_session, status="ok")

    store.set_run_status(run_id, "complete")
    path = store.write_manifest(run_id)

    ok = sum(1 for r in results if r.status == "ok")
    noncompliant = sum(1 for r in results if not r.compliant)
    print(f"\nrun {run_id}: {ok}/{plan.sessions} sessions ok, "
          f"{noncompliant} non-compliant, {len(blocked)} blocked requests")
    print(f"spend this cap: ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}")
    print(f"manifest: {path}")
    print(f"verify:   python -m evidence verify {run_id}")
    if not limiter.saw_headers and args.llm != "fake":
        print("note: no rate-limit headers were seen; the 40 req/min default stayed in force")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m walker")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--pricing", default="configs/pricing.yaml")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="print the run plan and its hash")
    p.add_argument("config")
    p.add_argument("--concurrency", type=int, default=None)
    p.set_defaults(func=cmd_plan)

    a = sub.add_parser("approve", help="record a named human's approval of a plan")
    a.add_argument("plan_hash")
    a.add_argument("--name", required=True)
    a.add_argument("--note", default="")
    a.set_defaults(func=cmd_approve)

    r = sub.add_parser("run", help="run an approved batch")
    r.add_argument("config")
    r.add_argument("--concurrency", type=int, default=None)
    r.add_argument("--llm", choices=["real", "fake"], default="real")
    r.set_defaults(func=cmd_run)
    return ap


def main(argv: list[str] | None = None) -> int:
    # Only the walker ever needs the key, so it is loaded here and nowhere else.
    # The game server and the dashboard never call this.
    load_env_file()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
