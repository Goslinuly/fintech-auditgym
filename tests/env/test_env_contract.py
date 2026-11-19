"""Contract of AuditEnv.reset/step: shapes, step accounting, limits, termination, errors."""

from __future__ import annotations

import pytest
from conftest import SAMPLE, SMALL, act, dump, oracle_findings

from auditgym import ENV_VERSION
from auditgym.env import AuditEnv

# --- reset ------------------------------------------------------------------------------


def test_reset_shape_and_info(sample):
    public, _ = sample
    with AuditEnv() as env:
        obs, info = env.reset(episode_dir=SAMPLE)
    assert set(info) == {"episode_id", "env_version", "config_version"}
    assert info["episode_id"] == public["manifest"]["episode_id"]
    assert info["env_version"] == ENV_VERSION
    assert obs["steps_remaining"] == 100 and obs["step"] == 0
    assert obs["manifest"] == public["manifest"]
    assert obs["policy"] == public["policy"]
    assert [a["name"] for a in obs["actions"]] == [
        "list_records", "get_record", "get_document", "calculator", "submit"]
    assert "seed" not in dump(obs).lower()


def test_reset_from_seed_is_deterministic():
    with AuditEnv() as a, AuditEnv() as b:
        assert dump(a.reset(seed=123, config=SMALL)) == dump(b.reset(seed=123, config=SMALL))
        assert dump(a.reset(seed=124, config=SMALL)) != dump(b.reset(seed=123, config=SMALL))


def test_reset_rejects_bad_arguments():
    with AuditEnv() as env:
        with pytest.raises(TypeError):
            env.reset(seed="7")
        with pytest.raises(TypeError):
            env.reset(seed=True)
        with pytest.raises(TypeError):
            env.reset(seed=1, config=[1])


def test_step_before_reset_raises():
    with AuditEnv() as env, pytest.raises(RuntimeError):
        env.step(act("calculator", expression="1+1"))


def test_reset_isolates_state(env, sample):
    obs, *_ = env.step(act("list_records", table="ledger", limit=5))
    old_cursor = obs["result"]["next_cursor"]
    env.step(act("submit", findings={"episode_id": "x", "findings": []}))
    obs, info = env.reset(seed=5, config=SMALL)
    assert obs["step"] == 0 and obs["steps_remaining"] == 100
    o, r, term, trunc, _ = env.step(act("list_records", table="ledger", cursor=old_cursor))
    assert o["error"]["code"] == "bad_args"
    assert (term, trunc, o["step"]) == (False, False, 1)
    # and back to the same episode: identical to a fresh environment
    first, _ = env.reset(episode_dir=SAMPLE)
    with AuditEnv() as fresh:
        assert dump(first) == dump(fresh.reset(episode_dir=SAMPLE)[0])


# --- determinism ------------------------------------------------------------------------


def _script(truth):
    return [
        act("list_records", table="ledger", limit=7),
        act("list_records", table="fx"),
        act("get_record", table="ledger", id="NOPE"),
        act("get_document", doc_id="DOC-NOPE"),
        act("calculator", expression="(1 + 2) * 3 / 7"),
        {"name": "fly"},
        act("list_records", table="ledger", limit=99),
        act("submit", findings=oracle_findings(truth)),
    ]


def _run(env, actions):
    out = []
    for a in actions:
        obs, reward, term, trunc, info = env.step(a)
        out.append(dump([obs, reward, term, trunc, info]))
    return out


def test_same_actions_same_transitions(sample):
    _, private = sample
    runs = []
    for _ in range(2):
        with AuditEnv() as env:
            env.reset(episode_dir=SAMPLE)
            runs.append(_run(env, _script(private["truth"])))
    assert runs[0] == runs[1]


def test_same_seed_same_transitions_from_generator():
    from auditgym.generator import generate

    truth = generate(77, SMALL).truth
    runs = []
    for _ in range(2):
        with AuditEnv() as env:
            env.reset(seed=77, config=SMALL)
            runs.append(_run(env, _script(truth)))
    assert runs[0] == runs[1]


# --- step accounting and errors ---------------------------------------------------------


@pytest.mark.parametrize("action, code", [
    ({"name": "rm -rf"}, "unknown_action"),
    ({"name": None}, "unknown_action"),
    ({"args": {}}, "unknown_action"),
    ("list_records", "bad_args"),
    ({"name": "list_records", "args": []}, "bad_args"),
    ({"name": "list_records", "args": {}}, "bad_args"),
    ({"name": "list_records", "args": {"table": "users"}}, "bad_args"),
    ({"name": "list_records", "args": {"table": "ledger", "sql": "x"}}, "bad_args"),
    ({"name": "list_records", "args": {"table": "ledger"}, "extra": 1}, "bad_args"),
    ({"name": "get_record", "args": {"table": "ledger", "id": ""}}, "bad_args"),
    ({"name": "get_record", "args": {"table": "ledger", "id": 42}}, "bad_args"),
    ({"name": "get_record", "args": {"table": "ledger", "id": "L" * 101}}, "bad_args"),
    ({"name": "get_record", "args": {"table": "ledger", "id": "LNOTREAL"}}, "not_found"),
    ({"name": "get_record", "args": {"table": "ledger", "id": "invoices:INV-X"}}, "not_found"),
    ({"name": "get_document", "args": {"doc_id": "../private/truth.json"}}, "not_found"),
    ({"name": "calculator", "args": {"expression": "__import__('os')"}}, "bad_args"),
])
def test_errors_are_bounded_and_cost_a_step(env, action, code):
    obs, reward, term, trunc, _ = env.step(action)
    assert obs["error"]["code"] == code
    assert len(obs["error"]["message"]) <= 200
    assert (reward, term, trunc) == (0.0, False, False)
    assert obs["step"] == 1 and obs["steps_remaining"] == 99
    text = dump(obs)
    for leak in ("Traceback", "File \"", "LNOTREAL", "truth", "private", "INV-X", "/home"):
        assert leak not in text


def test_every_action_costs_one_step(env):
    actions = [
        act("list_records", table="ledger"),
        act("get_record", table="ledger", id="nope"),
        act("calculator", expression="2*2"),
        {"name": "bogus"},
    ]
    for n, a in enumerate(actions, start=1):
        obs, *_ = env.step(a)
        assert obs["step"] == n and obs["steps_remaining"] == 100 - n


def test_exploration_reward_is_zero(env):
    for a in (act("list_records", table="payments"), act("calculator", expression="1/3")):
        assert env.step(a)[1] == 0.0


def test_action_size_and_serialisability(env):
    big = act("calculator", expression="1" + "+1" * 40000)
    assert env.step(big)[0]["error"]["code"] == "bad_args"
    assert env.step({"name": "calculator", "args": {"expression": {1, 2}}})[0]["error"][
        "code"] == "bad_args"
    circular: dict = {"name": "calculator"}
    circular["args"] = circular
    assert env.step(circular)[0]["error"]["code"] == "bad_args"


# --- tools --------------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [51, 0, -1, 1000, True, 2.5, "10"])
def test_limit_is_bounded(env, limit):
    obs, *_ = env.step(act("list_records", table="ledger", limit=limit))
    assert obs["error"]["code"] == "bad_args"


def _page_through(env, table, limit):
    seen, cursor, cursors = [], None, []
    while True:
        args = {"table": table, "limit": limit}
        if cursor:
            args["cursor"] = cursor
        res = env.step({"name": "list_records", "args": args})[0]["result"]
        assert len(res["items"]) <= 50
        seen += [it["ref"] for it in res["items"]]
        cursor = res["next_cursor"]
        if cursor is None:
            return seen, cursors
        cursors.append(cursor)


def test_pagination_covers_table_once_in_order_and_cursors_are_stable(sample):
    public, _ = sample
    expected = [f"invoices:{r['invoice_id']}" for r in public["invoices"]]
    with AuditEnv() as env:
        env.reset(episode_dir=SAMPLE)
        first, c1 = _page_through(env, "invoices", 10)
        second, c2 = _page_through(env, "invoices", 10)
    assert first == expected == second
    assert c1 == c2 and len(set(c1)) == len(c1)
    assert all(c.startswith("c_") and "invoices" not in c for c in c1)


def test_default_page_is_50(env, sample):
    public, _ = sample
    res = env.step(act("list_records", table="ledger"))[0]["result"]
    assert len(res["items"]) == 50 and res["total"] == len(public["ledger"])


def test_cursor_from_other_table_or_unknown_is_rejected(env):
    cur = env.step(act("list_records", table="ledger", limit=5))[0]["result"]["next_cursor"]
    assert env.step(act("list_records", table="payments", cursor=cur))[0]["error"][
        "code"] == "bad_args"
    assert env.step(act("list_records", table="ledger", cursor="c_0000"))[0]["error"][
        "code"] == "bad_args"
    assert env.step(act("list_records", table="ledger", cursor=123))[0]["error"][
        "code"] == "bad_args"


def test_get_record_forms(env, sample):
    public, _ = sample
    row = public["ledger"][3]
    fx = public["fx"][0]
    fx_id = f"{fx['date']}/{fx['source_currency']}/{fx['target_currency']}"
    for rid in (row["row_id"], f"ledger:{row['row_id']}", f" Ledger:{row['row_id']} "):
        res = env.step(act("get_record", table="ledger", id=rid))[0]["result"]
        assert res == {"ref": f"ledger:{row['row_id']}", "record": row}
    res = env.step(act("get_record", table="fx", id=f"fx:{fx_id}"))[0]["result"]
    assert res["record"] == fx


def test_get_document_aliases(env, sample):
    public, _ = sample
    inv = public["invoices"][0]
    pay = public["payments"][0]
    for key in (inv["doc_id"], inv["invoice_id"], f"invoices:{inv['invoice_id']}"):
        res = env.step(act("get_document", doc_id=key))[0]["result"]
        assert res["kind"] == "invoice" and res["document"] == inv
        assert res["ref"] == f"invoices:{inv['invoice_id']}"
    res = env.step(act("get_document", doc_id=pay["payment_id"]))[0]["result"]
    assert res["kind"] == "payment" and res["document"] == pay


def test_returned_records_are_copies(env, sample):
    public, _ = sample
    inv = public["invoices"][0]
    res = env.step(act("get_document", doc_id=inv["doc_id"]))[0]["result"]
    res["document"]["total_minor"] = -1
    res["document"]["line_items"].clear()
    page = env.step(act("list_records", table="invoices", limit=1))[0]["result"]
    page["items"][0]["record"]["currency"] = "XXX"
    again = env.step(act("get_document", doc_id=inv["doc_id"]))[0]["result"]
    assert again["document"] == inv
    obs, _ = env.reset(episode_dir=SAMPLE)
    obs["manifest"]["base_currency"] = "XXX"
    assert env.reset(episode_dir=SAMPLE)[0]["manifest"] == public["manifest"]


def test_calculator_action(env):
    res = env.step(act("calculator", expression="1500 * 470.25 / (3 - 1)"))[0]["result"]
    assert res == {"expression": "1500 * 470.25 / (3 - 1)", "value": "352687.5"}
    assert env.step(act("calculator", expression="1/0"))[0]["error"]["code"] == "bad_args"


# --- submit, termination, truncation ------------------------------------------------------


def test_oracle_submit_terminates_with_full_reward(env, sample):
    _, private = sample
    obs, reward, term, trunc, info = env.step(
        act("submit", findings=oracle_findings(private["truth"])))
    assert (term, trunc) == (True, False)
    assert reward == pytest.approx(1.0)  # submit on step 1: inspection_steps = 0
    assert obs["result"] == {"submitted": True, "count": len(private["truth"]["issues"])}
    assert set(info) == {"episode_id", "env_version", "config_version"}
    log = env.grade_log[-1]
    assert log["inspection_steps"] == 0 and log["f1"] == 1.0


def test_inspection_cost_10_vs_90_steps(sample):
    _, private = sample
    rewards = []
    for k in (10, 90):
        with AuditEnv() as env:
            env.reset(episode_dir=SAMPLE)
            for _ in range(k):
                env.step(act("calculator", expression="1+1"))
            rewards.append(env.step(act("submit", findings=oracle_findings(private["truth"])))[1])
    assert rewards[0] - rewards[1] == pytest.approx(0.16)
    assert rewards[0] > rewards[1]


def test_invalid_submission_still_ends_episode_with_minus_one(env):
    obs, reward, term, trunc, _ = env.step(act("submit", findings="not an object"))
    assert (reward, term, trunc) == (-1.0, True, False)
    assert env.grade_log[-1]["reward"] == -1.0


def test_step_after_end_changes_nothing_and_no_regrade(env, sample):
    _, private = sample
    good = oracle_findings(private["truth"])
    env.step(act("submit", findings=good))
    n_logs = len(env.grade_log)
    for a in (act("submit", findings=good), act("list_records", table="ledger")):
        obs, reward, term, trunc, _ = env.step(a)
        assert obs["error"]["code"] == "episode_over"
        assert obs["step"] == 1
        assert (reward, term, trunc) == (0.0, True, False)
    assert len(env.grade_log) == n_logs


def test_step_100_truncates_with_empty_answer(env):
    for n in range(1, 100):
        obs, reward, term, trunc, _ = env.step(act("calculator", expression="1"))
        assert (term, trunc) == (False, False), n
    obs, reward, term, trunc, _ = env.step(act("calculator", expression="1"))
    assert (term, trunc) == (False, True)
    assert obs["truncated_reason"] == "step_limit" and obs["steps_remaining"] == 0
    log = env.grade_log[-1]
    assert log["reason"] == "step_limit" and log["tp"] == 0
    assert reward == log["reward"]
    assert env.step(act("calculator", expression="1"))[0]["error"]["code"] == "episode_over"


def test_submit_as_step_100_terminates(env, sample):
    _, private = sample
    for _ in range(99):
        env.step(act("calculator", expression="1"))
    obs, reward, term, trunc, _ = env.step(act("submit", findings=oracle_findings(
        private["truth"])))
    assert (term, trunc) == (True, False)
    assert reward == pytest.approx(1 - 0.002 * 99)


def test_time_limit_truncates():
    now = [0.0]
    with AuditEnv(time_limit_s=60, clock=lambda: now[0]) as env:
        env.reset(episode_dir=SAMPLE)
        assert env.step(act("calculator", expression="1"))[3] is False
        now[0] = 60.5
        obs, reward, term, trunc, _ = env.step(act("calculator", expression="1"))
        assert (term, trunc) == (False, True)
        assert obs["error"]["code"] == "time_limit"
        assert obs["truncated_reason"] == "time_limit"
        assert env.grade_log[-1]["reason"] == "time_limit"
        assert env.step(act("calculator", expression="1"))[0]["error"]["code"] == "episode_over"


def test_eval_mode_hides_reward(sample):
    _, private = sample
    with AuditEnv(mode="eval") as env:
        env.reset(episode_dir=SAMPLE)
        obs, reward, term, trunc, info = env.step(
            act("submit", findings=oracle_findings(private["truth"])))
        assert (reward, term) == (0.0, True)
        assert "reward" not in dump(obs) and "f1" not in dump(obs) and "f1" not in dump(info)
        assert env.grade_log[-1]["reward"] == pytest.approx(1.0)
        assert env.grade_log[-1]["mode"] == "eval"


def test_constructor_validation():
    with pytest.raises(ValueError):
        AuditEnv(mode="test")
    with pytest.raises(ValueError):
        AuditEnv(isolation="thread")
    with pytest.raises(ValueError):
        AuditEnv(max_steps=0)


def test_custom_grader_class_is_used(sample):
    class FixedGrader:
        def score(self, findings, *, truth, public, inspection_steps):
            return {"reward": 0.25, "tp": 0, "fp": 0, "fn": 0}

    with AuditEnv(grader=FixedGrader) as env:
        env.reset(episode_dir=SAMPLE)
        assert env.step(act("submit", findings={}))[1] == 0.25
