"""Leak scan: across 50 generated episodes and every published episode, no observation, info
or error reveals the answer (issue ids, truth/provenance, seed, expected amounts)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from conftest import ROOT, act, oracle_findings

from auditgym.env import AuditEnv, leak_scan
from auditgym.generator import generate, load_config

PRESETS = ["clean_small", "mixed_small", "interactions", "single_type", "mixed_medium"]


def _probe_actions(public: dict, truth: dict, rng: random.Random) -> list[dict]:
    """A deterministic mix of valid, invalid and adversarial actions."""
    inv = public["invoices"][rng.randrange(len(public["invoices"]))]
    pay = public["payments"][rng.randrange(len(public["payments"]))] if public["payments"] else inv
    row = public["ledger"][rng.randrange(len(public["ledger"]))]
    issue_ids = [i["issue_id"] for i in truth["issues"]] or ["ISS-01"]
    actions = [
        act("list_records", table="ledger"),
        act("list_records", table="invoices", limit=50),
        act("list_records", table="payments", limit=50),
        act("list_records", table="fx", limit=50),
        act("get_record", table="ledger", id=row["row_id"]),
        act("get_document", doc_id=inv["doc_id"]),
        act("get_document", doc_id=pay.get("payment_id", inv["invoice_id"])),
        act("get_record", table="invoices", id=issue_ids[0]),
        act("get_document", doc_id="truth"),
        act("get_document", doc_id="../private/provenance.json"),
        act("get_record", table="ledger", id="seed"),
        act("list_records", table="truth"),
        act("list_records", table="ledger", cursor="c_deadbeef"),
        act("calculator", expression="__import__('auditgym')"),
        act("calculator", expression="1/0"),
        {"name": "read_file", "args": {"path": "private/truth.json"}},
        {"name": "shell", "args": {"cmd": "cat truth.json"}},
        act("get_record", table="ledger", id="L" * 200),
    ]
    rng.shuffle(actions)
    if rng.random() < 0.5:
        actions.append(act("submit", findings=oracle_findings(truth)))
    else:
        actions.append(act("submit", findings={"episode_id": "wrong", "findings": [1, 2]}))
    actions.append(act("list_records", table="ledger"))  # after the end
    return actions


def _check(payload, private, seed, where):
    problems = leak_scan(payload, private, seed=seed, where=where)
    assert not problems, problems[:5]


def test_leak_scan_detects_planted_leaks(sample):
    _, private = sample
    iid = private["truth"]["issues"][0]["issue_id"]
    amount = private["truth"]["issues"][0]["expected_amount_minor"]
    assert leak_scan({"x": f"see {iid}"}, private)
    assert leak_scan({"truth": 1}, private)
    assert leak_scan({"msg": "Provenance says"}, private)
    assert leak_scan({"seed": 3}, private)
    assert leak_scan({"episode_id": "e", "n": amount}, private, where="info")
    assert leak_scan({"error": {"code": "x", "message": "m", "n": amount}}, private)
    assert leak_scan({"step": 1, "result": {"items": [{"record": {"total_minor": amount}}]}},
                     private) == []


@pytest.mark.parametrize("n", range(50))
def test_no_leaks_across_50_episodes(n):
    seed = 9000 + n * 37
    cfg = load_config(ROOT / "configs" / "presets" / f"{PRESETS[n % len(PRESETS)]}.json")
    ep = generate(seed, cfg)
    mode = "eval" if n % 10 == 0 else "train"
    rng = random.Random(n)
    with AuditEnv(mode=mode) as env:
        obs, info = env.reset(seed=seed, config=cfg)
        _check(obs, ep.private, seed, "observation")
        _check(info, ep.private, seed, "info")
        for a in _probe_actions(ep.public, ep.truth, rng):
            obs, reward, term, trunc, info = env.step(a)
            _check(obs, ep.private, seed, "observation")
            _check(info, ep.private, seed, "info")
            if mode == "eval":
                assert reward == 0.0


def test_no_leaks_in_published_episodes():
    dirs = sorted(p for split in ("sample", "train", "dev")
                  for p in (ROOT / "episodes" / split).glob("*_*") if p.is_dir())
    assert len(dirs) == 21
    from auditgym.generator import load_episode

    with AuditEnv() as env:
        for d in dirs:
            _, private = load_episode(d)
            obs, info = env.reset(episode_dir=d)
            _check(obs, private, None, "observation")
            _check(info, private, None, "info")
            text = repr(obs) + repr(info)
            assert str(Path(d)) not in text and "private" not in text


def test_eval_env_holds_no_private_data(sample):
    """In eval mode the episode is generated in the grader process: nothing in the
    environment object graph contains the answer."""
    _, private = sample
    issue_ids = [i["issue_id"] for i in private["truth"]["issues"]]
    with AuditEnv(mode="eval") as env:
        env.reset(episode_dir=ROOT / "episodes" / "sample" / "sample_00")
        env.step(act("list_records", table="ledger"))
        seen = set()

        def walk(obj, depth=0):
            if id(obj) in seen or depth > 12:
                return
            seen.add(id(obj))
            if isinstance(obj, str):
                assert not any(i in obj for i in issue_ids)
                assert "evidence_sets" not in obj
            elif isinstance(obj, dict):
                assert "truth" not in obj and "provenance" not in obj
                for k, v in obj.items():
                    walk(k, depth + 1)
                    walk(v, depth + 1)
            elif isinstance(obj, (list, tuple, set)):
                for v in obj:
                    walk(v, depth + 1)
            elif hasattr(obj, "__dict__") and obj.__class__.__module__.startswith("auditgym"):
                walk(vars(obj), depth + 1)
            elif hasattr(obj, "__slots__"):
                for s in obj.__slots__:
                    walk(getattr(obj, s, None), depth + 1)

        walk(env)
