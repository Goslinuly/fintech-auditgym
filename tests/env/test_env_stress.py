"""Largest episode (2000 ledger rows, 200 documents): an agent can page through every table
within the step budget, quickly and with modest memory (official limit: 2 CPU, 4 GB)."""

from __future__ import annotations

import time
import tracemalloc

from conftest import act

from auditgym.env import AuditEnv

LARGE = {"n_ledger_rows": 2000, "n_documents": 200}


def _read_all(env, table):
    steps = 0
    cursor = None
    rows = 0
    while True:
        args = {"table": table, "limit": 50}
        if cursor:
            args["cursor"] = cursor
        obs, *_ = env.step({"name": "list_records", "args": args})
        steps += 1
        rows += len(obs["result"]["items"])
        cursor = obs["result"]["next_cursor"]
        if cursor is None:
            return rows, steps


def test_large_episode_fits_budget():
    tracemalloc.start()
    start = time.perf_counter()
    with AuditEnv(mode="train") as env:
        obs, info = env.reset(seed=424242, config=LARGE)
        counts = obs["tables"]
        assert counts["ledger"] >= 2000 - 50
        total_steps = 0
        for table in ("ledger", "invoices", "payments", "fx"):
            rows, steps = _read_all(env, table)
            assert rows == counts[table]
            total_steps += steps
        assert total_steps < 100
        obs, reward, term, trunc, _ = env.step(
            act("submit", findings={"episode_id": info["episode_id"], "findings": []}))
        assert term and not trunc
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 20, elapsed
    assert peak < 300 * 1024 * 1024, peak


def test_many_resets_are_fast_and_bounded():
    start = time.perf_counter()
    with AuditEnv(mode="train") as env:
        for seed in range(30):
            env.reset(seed=seed, config={"n_ledger_rows": 300, "n_documents": 40})
            for _ in range(5):
                env.step(act("list_records", table="ledger"))
    assert time.perf_counter() - start < 30


def test_eval_process_mode_large_episode():
    with AuditEnv(mode="eval") as env:
        obs, info = env.reset(seed=515151, config=LARGE)
        rows, steps = _read_all(env, "ledger")
        assert rows == obs["tables"]["ledger"]
        obs, reward, term, trunc, _ = env.step(
            act("submit", findings={"episode_id": info["episode_id"], "findings": []}))
        assert term and reward == 0.0
        assert env.grade_log[-1]["reward"] <= 1.0
