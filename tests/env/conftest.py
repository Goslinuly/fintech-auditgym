from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditgym.env import AuditEnv
from auditgym.generator import load_episode

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "episodes" / "sample" / "sample_00"
SMALL = {"n_ledger_rows": 250, "n_documents": 30}


def oracle_findings(truth: dict) -> dict:
    return {
        "episode_id": truth["episode_id"],
        "findings": [
            {
                "type": i["type"],
                "affected_ids": i["affected_ids"],
                "claimed_amount_minor": i["expected_amount_minor"],
                "currency": i["currency"],
                "evidence_ids": i["evidence_sets"][0],
                "explanation": "oracle",
            }
            for i in truth["issues"]
        ],
    }


def act(name: str, **args) -> dict:
    return {"name": name, "args": args}


def dump(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


@pytest.fixture
def sample():
    """(public, private) of the committed sample episode."""
    return load_episode(SAMPLE)


@pytest.fixture
def env():
    e = AuditEnv(mode="train")
    e.reset(episode_dir=SAMPLE)
    yield e
    e.close()
