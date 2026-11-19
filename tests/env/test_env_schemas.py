"""Every observation and action matches schemas/observation.schema.json and
schemas/action.schema.json, and the env rejects what the action schema rejects."""

from __future__ import annotations

import json

import pytest
from conftest import ROOT, SAMPLE, act, oracle_findings
from jsonschema import Draft202012Validator

from auditgym.env import AuditEnv


def _validator(name):
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


OBS = _validator("observation.schema.json")
ACT = _validator("action.schema.json")


def _actions(public, truth):
    inv = public["invoices"][0]
    return [
        act("list_records", table="ledger", limit=3),
        act("list_records", table="fx"),
        act("get_record", table="ledger", id=public["ledger"][0]["row_id"]),
        act("get_record", table="ledger", id="missing"),
        act("get_document", doc_id=inv["doc_id"]),
        act("get_document", doc_id=public["payments"][0]["doc_id"]),
        act("calculator", expression="10 / 4"),
        act("calculator", expression="1/0"),
        {"name": "unknown"},
        act("submit", findings=oracle_findings(truth)),
        act("list_records", table="ledger"),
    ]


def test_observations_match_schema(sample):
    public, private = sample
    with AuditEnv() as env:
        obs, _ = env.reset(episode_dir=SAMPLE)
        OBS.validate(obs)
        for a in _actions(public, private["truth"]):
            OBS.validate(env.step(a)[0])


def test_truncated_observations_match_schema():
    now = [0.0]
    with AuditEnv(max_steps=2, clock=lambda: now[0]) as env:
        env.reset(episode_dir=SAMPLE)
        env.step(act("calculator", expression="1"))
        OBS.validate(env.step(act("calculator", expression="1"))[0])
        env.reset(episode_dir=SAMPLE)
        now[0] = 1000
        OBS.validate(env.step(act("calculator", expression="1"))[0])


def test_valid_actions_match_schema(sample):
    public, private = sample
    for a in _actions(public, private["truth"])[:-3] + [_actions(public, private["truth"])[-2]]:
        if a["name"] != "unknown":
            ACT.validate(a)


@pytest.mark.parametrize("bad", [
    {"name": "list_records", "args": {"table": "ledger", "limit": 51}},
    {"name": "list_records", "args": {"table": "users"}},
    {"name": "get_record", "args": {"table": "ledger"}},
    {"name": "calculator", "args": {"expression": "2**8"}},
    {"name": "calculator", "args": {"expression": "abs(1)"}},
    {"name": "shell", "args": {}},
    {"name": "submit", "args": {"findings": {}, "debug": True}},
])
def test_schema_and_env_agree_on_invalid_actions(env, bad):
    assert not ACT.is_valid(bad)
    obs = env.step(bad)[0]
    assert "error" in obs and obs["error"]["code"] in ("bad_args", "unknown_action")
