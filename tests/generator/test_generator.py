import os
from collections import Counter

import pytest

from auditgym.generator import ConfigError, generate, load_config, load_episode
from auditgym.generator.audit import audit, issue_key
from auditgym.generator.config import ERROR_TYPES, resolve_config
from auditgym.generator.invariants import check_invariants
from auditgym.generator.splits import PUBLISHED_SPLITS, REPO_ROOT, check_split
from auditgym.generator.transforms import remap_ids, shuffle_rows
from auditgym.generator.verify import verify_episode

PRESETS = sorted((REPO_ROOT / "configs" / "presets").glob("*.json"))
N_SEEDS = int(os.environ.get("AUDITGYM_SEEDS", "150"))


def all_types(n):
    return {t: n for t in ERROR_TYPES}


# --------------------------------------------------------------- determinism
def test_same_seed_same_bytes(tmp_path):
    cfg = load_config(REPO_ROOT / "configs/presets/interactions.json")
    a = generate(11, cfg).write(tmp_path / "a")
    b = generate(11, cfg).write(tmp_path / "b")
    for f in sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file()):
        assert (a / f).read_bytes() == (b / f).read_bytes(), f


def test_different_seed_differs():
    a, b = generate(1), generate(2)
    assert a.public_files()["ledger.csv"] != b.public_files()["ledger.csv"]
    assert a.episode_id != b.episode_id


@pytest.mark.parametrize("split", PUBLISHED_SPLITS)
def test_published_splits_reproduce(split):
    assert check_split(split) == []


def test_round_trip_through_disk(tmp_path):
    ep = generate(5, {"interactions": {"A": 1, "B": 1}})
    public, private = load_episode(ep.write(tmp_path / "ep"))
    assert public == ep.public
    assert private["truth"] == ep.truth
    assert verify_episode(public, private["truth"]) == []


# --------------------------------------------------------------- validity at scale
@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.stem)
def test_presets_verify_over_many_seeds(preset):
    cfg = load_config(preset)
    attempts = Counter()
    for seed in range(N_SEEDS // len(PRESETS) + 5):
        ep = generate(seed, cfg)
        attempts[ep.private["provenance"]["attempt"]] += 1
        assert verify_episode(ep.public, ep.truth) == [], (preset.stem, seed)
        m = ep.public["manifest"]
        assert m["counts"]["ledger_rows"] >= cfg["n_ledger_rows"]
        assert 20 <= m["counts"]["documents"] <= 200
        assert len(ep.truth["issues"]) <= 12
    # retries are rare and deterministic
    assert attempts[0] >= sum(attempts.values()) * 0.9


def test_clean_companies_have_empty_truth_and_no_discrepancy():
    for seed in range(20):
        ep = generate(seed, {"clean": True, "error_counts": all_types(3)})
        assert ep.truth["issues"] == []
        assert audit(ep.public) == []
        assert check_invariants(ep.public, strict=True) == []


def test_pre_injection_ledger_balanced():
    ep = generate(9, load_config(REPO_ROOT / "configs/presets/mixed_large.json"))
    pre = ep.private["provenance"]["pre_injection"]
    assert pre["total_debit_minor"] == pre["total_credit_minor"]
    led = ep.public["ledger"]
    assert sum(r["debit_minor"] for r in led) == sum(r["credit_minor"] for r in led)


# --------------------------------------------------------------- error types
@pytest.mark.parametrize("etype", ERROR_TYPES)
def test_each_type_is_independently_configurable(etype):
    counts = {t: 0 for t in ERROR_TYPES}
    counts[etype] = 3
    for seed in range(8):
        ep = generate(seed, {"error_counts": counts})
        types = Counter(i["type"] for i in ep.truth["issues"])
        assert types == Counter({etype: 3})


def test_interaction_a_and_b():
    for seed in range(10):
        ep = generate(seed, {"error_counts": all_types(0), "interactions": {"A": 1, "B": 1}})
        by_group = {}
        for i in ep.truth["issues"]:
            by_group.setdefault(i["group"], []).append(i["type"])
        assert sorted(by_group["A1"]) == ["fx_conversion", "payment_allocation", "period_cutoff"]
        assert sorted(by_group["B1"]) == ["duplicate_invoice", "fx_conversion"]
        a_payments = {x for i in ep.truth["issues"] if i["group"] == "A1" for x in i["affected_ids"] if x.startswith("payments:")}
        assert len(a_payments) == 1  # one payment carries all three issues
        pay = next(p for p in ep.public["payments"] if f"payments:{p['payment_id']}" in a_payments)
        assert pay["currency"] != ep.public["manifest"]["base_currency"]


def test_truth_amounts_and_evidence_are_well_formed():
    ep = generate(21, load_config(REPO_ROOT / "configs/presets/single_type.json"))
    ids = {f"ledger:{r['row_id']}" for r in ep.public["ledger"]}
    ids |= {f"invoices:{i['invoice_id']}" for i in ep.public["invoices"]}
    ids |= {f"payments:{p['payment_id']}" for p in ep.public["payments"]}
    ids |= {f"fx:{r['date']}/{r['source_currency']}/{r['target_currency']}" for r in ep.public["fx"]}
    for issue in ep.truth["issues"]:
        assert isinstance(issue["expected_amount_minor"], int) and issue["expected_amount_minor"] > 0
        assert issue["affected_ids"] == sorted(issue["affected_ids"])
        for s in issue["evidence_sets"]:
            assert 1 <= len(s) <= 10 and set(s) <= ids


# --------------------------------------------------------------- isolation
def test_public_records_do_not_leak_reference():
    seed = 987654321
    ep = generate(seed, {"interactions": {"A": 1, "B": 1}})
    blob = "".join(ep.public_files().values())
    for token in ("truth", "provenance", "issue_id", "expected_amount", "ISS-", "evidence_sets", str(seed)):
        assert token not in blob


def test_ids_do_not_encode_roles():
    ep = generate(6, {"error_counts": all_types(2)})
    dup_ids = {i["affected_ids"][0].split(":")[1] for i in ep.truth["issues"] if i["type"] == "duplicate_invoice"}
    for d in dup_ids:
        assert "DUP" not in d
    all_inv = [i["invoice_id"] for i in ep.public["invoices"]]
    assert sorted(all_inv) != all_inv or len(all_inv) < 3  # not sequential by creation


# --------------------------------------------------------------- transforms
@pytest.mark.parametrize("seed", range(6))
def test_remap_and_shuffle_preserve_answer(seed):
    ep = generate(seed, load_config(REPO_ROOT / "configs/presets/interactions.json"))
    pub, truth, mapping = remap_ids(ep.public, ep.truth, seed=seed + 100)
    pub = shuffle_rows(pub, seed=seed + 200)
    assert verify_episode(pub, truth) == []
    assert len(truth["issues"]) == len(ep.truth["issues"])
    old_ids = set(mapping)
    for issue in truth["issues"]:
        assert not any(x.split(":", 1)[1] in old_ids for x in issue["affected_ids"])
    assert Counter(issue_key(i) for i in audit(pub)) == Counter(issue_key(i) for i in truth["issues"])


# --------------------------------------------------------------- config
@pytest.mark.parametrize(
    "bad",
    [
        {"n_ledger_rows": 10},
        {"n_documents": 500},
        {"currencies": ["XXX"]},
        {"error_counts": {"nonsense": 1}},
        {"error_counts": all_types(4)},
        {"currencies": ["KZT"], "error_counts": {"fx_conversion": 1}},
        {"timezone": "Asia/Almaty"},
        {"period": "2026-13"},
        {"unknown_key": 1},
        {"interactions": {"A": [2, 1]}},
    ],
)
def test_invalid_configs_rejected(bad):
    with pytest.raises(ConfigError):
        resolve_config(bad)


def test_single_currency_config_works():
    ep = generate(3, {"currencies": ["KZT"], "error_counts": {"duplicate_invoice": 1, "payment_allocation": 1, "fx_conversion": 0, "period_cutoff": 1}})
    assert set(ep.public["manifest"]["currencies"]) == {"KZT"}
    assert ep.public["fx"] == []
    assert verify_episode(ep.public, ep.truth) == []


def test_bad_seed_type():
    with pytest.raises(TypeError):
        generate("7")
