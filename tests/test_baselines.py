"""Smoke test for examples/baselines.py: the reference policies behave as documented."""

import importlib.util
from pathlib import Path

import pytest

from auditgym.env import AuditEnv
from auditgym.generator import load_episode

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("baselines", ROOT / "examples" / "baselines.py")
baselines = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baselines)


@pytest.mark.parametrize("episode", ["episodes/train/train_09", "episodes/dev/dev_07", "episodes/dev/dev_00"])
def test_baselines(episode):
    ep = ROOT / episode
    _, private = load_episode(ep)
    has_issues = bool(private["truth"]["issues"])
    rewards = {}
    with AuditEnv(mode="train") as env:
        for name, policy in baselines.POLICIES.items():
            obs, _ = env.reset(episode_dir=str(ep))
            rewards[name] = policy(env, obs, private["truth"])
    assert rewards["Oracle"] == 1.0
    assert rewards["ReadAll"] > 0.9
    assert rewards["FlagAll"] == -1.0
    assert rewards["Silent"] == (-1.0 if has_issues else 1.0)
