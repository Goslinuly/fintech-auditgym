"""Episode generation config: defaults, loading, validation and sampling of counts.

A count anywhere in the config is either an int or an inclusive range ``[lo, hi]``
sampled per seed, so one config file describes a family of episodes.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path
from typing import Any

CONFIG_VERSION = "1.0"

ERROR_TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")

# Interacting scenarios and the issues each one produces.
INTERACTION_ISSUES = {
    # Partial payment in a foreign currency on the period boundary:
    # booked in the wrong period, partially allocated, converted at the wrong date's rate.
    "A": ("period_cutoff", "payment_allocation", "fx_conversion"),
    # Duplicate foreign-currency invoice whose payment is converted at the wrong date's rate.
    "B": ("duplicate_invoice", "fx_conversion"),
}

DISTRACTOR_KINDS = (
    "split_payment",  # one invoice settled by 2-3 payments
    "partial_payment",  # customer legitimately paid only part of an invoice
    "recurring",  # same customer, same amount, different service weeks
    "refund",  # double payment kept unapplied and then refunded
    "boundary",  # documents minutes from the period edge, written in UTC
    "next_period_invoice",  # document from the next period, correctly not booked
    "weekend_fx",  # weekend document converted at the last business day's rate
    "prior_invoice",  # invoice from the previous period, paid or open now
)

SUPPORTED_CURRENCIES = ("KZT", "USD", "EUR", "JPY")
TIMEZONES = ("+05:00", "+06:00", "+03:00", "+00:00", "-05:00", "+09:00", "+01:00", "-03:00")
MAX_ISSUES = 12

DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "name": "default",
    "n_ledger_rows": 400,
    "n_documents": 60,
    "base_currency": None,
    "currencies": list(SUPPORTED_CURRENCIES),
    "n_foreign_currencies": [1, 2],
    "foreign_invoice_share": 0.35,
    "timezone": None,
    "period": None,
    "clean": False,
    "error_counts": {t: [0, 1] for t in ERROR_TYPES},
    "interactions": {"A": 0, "B": 0},
    "distractors": {
        "split_payment": [1, 2],
        "partial_payment": [1, 2],
        "recurring": [1, 2],
        "refund": [0, 1],
        "boundary": [1, 3],
        "next_period_invoice": [1, 2],
        "weekend_fx": [0, 2],
        "prior_invoice": [1, 3],
    },
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def bounds(spec: Any, name: str) -> tuple[int, int]:
    if isinstance(spec, bool):
        raise ConfigError(f"{name}: expected int or [lo, hi], got {spec!r}")
    if isinstance(spec, int):
        lo = hi = spec
    elif (
        isinstance(spec, list)
        and len(spec) == 2
        and all(isinstance(x, int) and not isinstance(x, bool) for x in spec)
    ):
        lo, hi = spec
    else:
        raise ConfigError(f"{name}: expected int or [lo, hi], got {spec!r}")
    if lo < 0 or hi < lo:
        raise ConfigError(f"{name}: invalid range {spec!r}")
    return lo, hi


def sample_count(rng: random.Random, spec: Any) -> int:
    lo, hi = bounds(spec, "count")
    return rng.randint(lo, hi)


def resolve_config(cfg: dict | None = None) -> dict:
    """Merge a (partial) config with defaults and validate it."""
    full = _merge(DEFAULTS, cfg or {})
    validate(full)
    return full


def load_config(path: str | Path) -> dict:
    return resolve_config(json.loads(Path(path).read_text(encoding="utf-8")))


def needs_foreign_max(cfg: dict) -> bool:
    if cfg["clean"]:
        return False
    return any(
        bounds(spec, name)[1] > 0
        for name, spec in (
            ("fx_conversion", cfg["error_counts"]["fx_conversion"]),
            ("A", cfg["interactions"]["A"]),
            ("B", cfg["interactions"]["B"]),
        )
    )


def validate(cfg: dict) -> None:
    for key in cfg:
        if key not in DEFAULTS:
            raise ConfigError(f"unknown config key {key!r}")
    if cfg["config_version"] != CONFIG_VERSION:
        raise ConfigError(f"config_version must be {CONFIG_VERSION!r}")
    if not isinstance(cfg["name"], str):
        raise ConfigError("name must be a string")

    for key, lo, hi in (("n_ledger_rows", 200, 2000), ("n_documents", 20, 200)):
        v = cfg[key]
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ConfigError(f"{key} must be an int in [{lo}, {hi}]")

    cur = cfg["currencies"]
    if (
        not isinstance(cur, list)
        or not cur
        or len(set(cur)) != len(cur)
        or any(c not in SUPPORTED_CURRENCIES for c in cur)
    ):
        raise ConfigError(f"currencies must be a non-empty unique subset of {SUPPORTED_CURRENCIES}")
    if cfg["base_currency"] is not None and cfg["base_currency"] not in cur:
        raise ConfigError("base_currency must be one of currencies")
    bounds(cfg["n_foreign_currencies"], "n_foreign_currencies")
    share = cfg["foreign_invoice_share"]
    if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0 <= share <= 1:
        raise ConfigError("foreign_invoice_share must be in [0, 1]")

    tz = cfg["timezone"]
    if tz is not None and (not isinstance(tz, str) or not re.fullmatch(r"[+-]\d{2}:\d{2}", tz)):
        raise ConfigError("timezone must look like '+05:00'")
    period = cfg["period"]
    if period is not None:
        if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ConfigError("period must look like '2026-03'")
        if not 1 <= int(period[5:]) <= 12:
            raise ConfigError("period month must be 01..12")
    if not isinstance(cfg["clean"], bool):
        raise ConfigError("clean must be a bool")

    for section, allowed in (
        ("error_counts", ERROR_TYPES),
        ("interactions", tuple(INTERACTION_ISSUES)),
        ("distractors", DISTRACTOR_KINDS),
    ):
        if not isinstance(cfg[section], dict):
            raise ConfigError(f"{section} must be an object")
        for key, spec in cfg[section].items():
            if key not in allowed:
                raise ConfigError(f"{section}: unknown key {key!r}")
            bounds(spec, f"{section}.{key}")

    if not cfg["clean"]:
        max_issues = sum(bounds(s, "e")[1] for s in cfg["error_counts"].values())
        max_issues += sum(
            bounds(cfg["interactions"][k], k)[1] * len(v) for k, v in INTERACTION_ISSUES.items()
        )
        if max_issues > MAX_ISSUES:
            raise ConfigError(f"config allows up to {max_issues} issues, limit is {MAX_ISSUES}")

    if needs_foreign_max(cfg):
        if len(cur) < 2:
            raise ConfigError("fx_conversion and interactions A/B need at least two currencies")
        if cfg["base_currency"] is not None and len([c for c in cur if c != cfg["base_currency"]]) < 1:
            raise ConfigError("no foreign currency available")
