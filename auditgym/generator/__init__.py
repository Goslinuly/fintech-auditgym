"""Synthetic company generator with injected, reference-labelled financial errors.

    from auditgym.generator import generate
    ep = generate(seed=7, config={"n_documents": 60})
    ep.public   # manifest, policy, ledger, invoices, payments, fx (what the env may expose)
    ep.private  # truth, provenance (grader only)
    ep.write("episodes/tmp/ep7")

Pipeline: hidden history -> clean render + invariants -> error injection (causal changes
recorded) -> render -> independent verification. Same (seed, config, GENERATOR_VERSION)
gives byte-identical files.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_VERSION, ConfigError, load_config, resolve_config
from .errors import inject_all
from .history import Builder, RetryGeneration
from .io import load_private, load_public, public_files, write_episode
from .render import SCHEMA_VERSION, build_doc_entries, render
from .verify import check_clean, verify_episode

GENERATOR_VERSION = "0.1.0"
MAX_ATTEMPTS = 25

__all__ = [
    "CONFIG_VERSION",
    "GENERATOR_VERSION",
    "ConfigError",
    "Episode",
    "GenerationError",
    "generate",
    "load_config",
    "load_episode",
]


class GenerationError(RuntimeError):
    pass


@dataclass
class Episode:
    public: dict
    private: dict
    seed: int
    config: dict = field(repr=False)

    @property
    def episode_id(self) -> str:
        return self.public["manifest"]["episode_id"]

    @property
    def truth(self) -> dict:
        return self.private["truth"]

    def write(self, out_dir: str | Path) -> Path:
        return write_episode(self.public, self.private, out_dir)

    def public_files(self) -> dict[str, str]:
        return public_files(self.public)


def episode_id_for(seed: int) -> str:
    rng = random.Random(f"auditgym:episode-id:{seed}")
    return "ep-" + "".join(rng.choice("0123456789abcdef") for _ in range(12))


def _strip_rows(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def _generate_once(seed: int, cfg: dict, episode_id: str, attempt: int) -> Episode:
    rng = random.Random(f"auditgym:{seed}:{attempt}")
    b = Builder(cfg, rng, episode_id)
    b.build_documents()
    clean = b.snapshot()
    ctx = inject_all(b)

    doc_entries = build_doc_entries(b)
    b.add_background(cfg["n_ledger_rows"] - sum(len(e["rows"]) for e in doc_entries))
    clean.background = b.background

    clean_public, _ = render(clean, random.Random(f"auditgym:{seed}:{attempt}:render-clean"))
    problems = check_clean(clean_public)
    if problems:
        raise RetryGeneration("clean history: " + "; ".join(problems[:3]))

    public, index = render(b, random.Random(f"auditgym:{seed}:{attempt}:render"))

    def resolve(ref: str) -> str:
        if ref.startswith("@main:"):
            return "ledger:" + index["main"][ref[len("@main:"):]]
        if ref.startswith("@unapplied:"):
            return "ledger:" + index["unapplied"][ref[len("@unapplied:"):]]
        return ref

    issues = []
    for issue in ctx.issues:
        issue = dict(issue)
        issue["evidence_sets"] = [sorted(resolve(x) for x in s) for s in issue["evidence_sets"]]
        issues.append(issue)
    truth = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "issue_count": len(issues),
        "issues": issues,
    }
    problems = verify_episode(public, truth)
    if problems:
        raise RetryGeneration("verify: " + "; ".join(problems[:3]))

    clean_entries = {e["key"]: _strip_rows(e["rows"]) for e in build_doc_entries(clean)}
    final_entries = {e["key"]: _strip_rows(e["rows"]) for e in doc_entries}
    ledger_diff = {
        key: {"before": clean_entries.get(key), "after": final_entries.get(key)}
        for key in ctx.touched
        if clean_entries.get(key) != final_entries.get(key)
    }
    clean_ledger = clean_public["ledger"]
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "generator_version": GENERATOR_VERSION,
        "config_version": cfg["config_version"],
        "config": cfg,
        "seed": seed,
        "attempt": attempt,
        "sampled": b.plan,
        "company": {
            "base_currency": b.base,
            "foreign_currencies": b.foreign,
            "timezone": b.tz_str,
            "rounding": b.rounding,
        },
        "pre_injection": {
            "ledger_rows": len(clean_ledger),
            "total_debit_minor": sum(r["debit_minor"] for r in clean_ledger),
            "total_credit_minor": sum(r["credit_minor"] for r in clean_ledger),
            "invariants": "passed (strict)",
            "reference_audit_issues": 0,
        },
        "changes": ctx.changes,
        "ledger_diff": ledger_diff,
        "distractors": b.distractor_log,
    }
    return Episode(public=public, private={"truth": truth, "provenance": provenance}, seed=seed, config=cfg)


def generate(seed: int, config: dict | None = None) -> Episode:
    """Generate one episode. ``config`` may be partial; it is merged with defaults and validated."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed must be an int")
    cfg = resolve_config(config)
    episode_id = episode_id_for(seed)
    reasons = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _generate_once(seed, cfg, episode_id, attempt)
        except RetryGeneration as exc:
            reasons.append(str(exc))
    raise GenerationError(f"seed {seed}: no valid episode after {MAX_ATTEMPTS} attempts: {reasons[-3:]}")


def load_episode(episode_dir: str | Path) -> tuple[dict, dict]:
    """Return ``(public, private)`` read back from disk."""
    return load_public(episode_dir), load_private(episode_dir)
