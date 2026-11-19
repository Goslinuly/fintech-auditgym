"""Baseline policies on the published train/dev episodes.

    python examples/baselines.py            # summary table
    python examples/baselines.py --trace    # step-by-step walkthrough of one episode

Policies (all interact only through ``AuditEnv.step``, except Oracle, which is handed the
reference answer by this runner, never by the environment):

* ``Silent``    - submits an empty report immediately.
* ``FlagAll``   - flags the first 30 visible invoices as duplicates (a "spray" attack).
* ``ReadAll``   - pages through every table with ``list_records`` and then runs the open
                  reference auditor (``auditgym.generator.audit``) on what it has read.
* ``Oracle``    - submits the reference answer with the first admissible evidence set.

The grader used here is the environment's default (currently the simplified StubGrader).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auditgym.env import AuditEnv  # noqa: E402
from auditgym.generator import load_episode  # noqa: E402
from auditgym.generator.audit import Records, audit  # noqa: E402

TABLES = ("ledger", "invoices", "payments", "fx")


def episodes() -> list[Path]:
    out = []
    for split in ("train", "dev"):
        out += sorted(p for p in (ROOT / "episodes" / split).iterdir() if p.is_dir())
    return out


def submit(env, episode_id, findings, trace=False):
    action = {"name": "submit", "args": {"findings": {"episode_id": episode_id, "findings": findings}}}
    obs, reward, term, trunc, _ = env.step(action)
    if trace:
        print(f"  step {obs['step']:>3}  submit({len(findings)} findings) -> reward {reward:+.3f}")
    return reward


def silent(env, obs, truth, trace=False):
    return submit(env, obs["episode_id"], [], trace)


def oracle(env, obs, truth, trace=False):
    findings = [{"type": i["type"], "affected_ids": i["affected_ids"],
                 "claimed_amount_minor": i["expected_amount_minor"], "currency": i["currency"],
                 "evidence_ids": i["evidence_sets"][0], "explanation": i.get("description", "")}
                for i in truth["issues"]]
    return submit(env, obs["episode_id"], findings, trace)


def read_table(env, table, trace=False):
    rows, cursor = [], None
    while True:
        args = {"table": table, "limit": 50}
        if cursor:
            args["cursor"] = cursor
        o, _, term, trunc, _ = env.step({"name": "list_records", "args": args})
        if term or trunc or "error" in o:
            return rows, False
        res = o["result"]
        rows += [it["record"] for it in res["items"]]
        if trace:
            print(f"  step {o['step']:>3}  list_records({table}) -> {len(rows)}/{res['total']} rows")
        cursor = res["next_cursor"]
        if not cursor:
            return rows, True


def flag_all(env, obs, truth, trace=False):
    rows, _ = read_table(env, "invoices", trace)
    base = obs["manifest"]["base_currency"]
    findings = [{"type": "duplicate_invoice", "affected_ids": [f"invoices:{r['invoice_id']}"],
                 "claimed_amount_minor": r["total_minor"], "currency": r.get("currency", base),
                 "evidence_ids": [f"invoices:{r['invoice_id']}"], "explanation": "looks odd"}
                for r in rows[:30]]
    return submit(env, obs["episode_id"], findings, trace)


def evidence_for(rec: Records, issue: dict) -> list[str]:
    """Pick an evidence set for a detected issue, following the rules in docs/generator.md."""
    t, (key, *_) = issue["type"], issue["affected_ids"]
    if t == "payment_allocation":
        return list(issue["affected_ids"])
    if t == "duplicate_invoice":
        dup = rec.invoices[key.split(":", 1)[1]]
        twins = [i for i in rec.invoices.values()
                 if (i["entity_id"], i["currency"], i["total_minor"], i["line_items"])
                 == (dup["entity_id"], dup["currency"], dup["total_minor"], dup["line_items"])]
        return sorted(f"invoices:{i['invoice_id']}" for i in twins)[:10]
    table, doc_id = key.split(":", 1)
    if table == "invoices":
        doc, row, ts = rec.invoices[doc_id], rec.invoice_main_row(doc_id), rec.invoices[doc_id]["issued_at"]
    else:
        doc, row, ts = rec.payments[doc_id], rec.payment_bank_row(doc_id), rec.payments[doc_id]["timestamp"]
    ev = [key, f"ledger:{row['row_id']}"]
    if t == "fx_conversion":
        day, _ = rec.fx.lookup(doc["currency"], rec.local_day(ts))
        ev.append(f"fx:{day.isoformat()}/{doc['currency']}/{rec.base}")
    return ev


def read_all(env, obs, truth, trace=False):
    public = {"manifest": obs["manifest"], "policy": obs["policy"]}
    for table in TABLES:
        rows, ok = read_table(env, table, trace)
        if not ok:
            return 0.0  # ran out of budget; the episode was truncated
        public[table] = rows
    rec = Records(public)
    findings = [{"type": i["type"], "affected_ids": i["affected_ids"],
                 "claimed_amount_minor": i["amount_minor"], "currency": i["currency"],
                 "evidence_ids": evidence_for(rec, i), "explanation": ""} for i in audit(public, rec)]
    return submit(env, obs["episode_id"], findings, trace)


POLICIES = {"Silent": silent, "FlagAll": flag_all, "ReadAll": read_all, "Oracle": oracle}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="store_true", help="walk through one episode step by step")
    ap.add_argument("--episode", default="episodes/train/train_09")
    args = ap.parse_args()

    if args.trace:
        ep = ROOT / args.episode
        _, private = load_episode(ep)
        with AuditEnv(mode="train") as env:
            for name, policy in POLICIES.items():
                obs, info = env.reset(episode_dir=str(ep))
                print(f"\n{name} on {ep.name} ({obs['manifest']['company_name']}, "
                      f"{len(private['truth']['issues'])} hidden issues), tables={obs['tables']}")
                policy(env, obs, private["truth"], trace=True)
        return

    results = {name: [] for name in POLICIES}
    with AuditEnv(mode="train") as env:
        for ep in episodes():
            _, private = load_episode(ep)
            for name, policy in POLICIES.items():
                obs, _ = env.reset(episode_dir=str(ep))
                results[name].append(policy(env, obs, private["truth"]))
    print(f"{'policy':<10} {'mean reward':>12} {'min':>8} {'max':>8}   (n={len(episodes())} train+dev episodes)")
    for name, rs in results.items():
        print(f"{name:<10} {sum(rs) / len(rs):>12.3f} {min(rs):>8.3f} {max(rs):>8.3f}")
    print(json.dumps({k: [round(r, 3) for r in v] for k, v in results.items()}))


if __name__ == "__main__":
    main()
