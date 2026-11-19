"""Independent episode verification.

An episode is released only if all checks pass:

1. invariants (double entry, references, settlement) hold on the published records;
2. the reference auditor, working from public records only, finds exactly the issues in truth.json
   (every declared discrepancy is real and no undeclared one exists);
3. every evidence ID exists in the public records;
4. for every issue at least one evidence set *alone* (plus manifest/policy) is enough to
   re-derive the issue type, affected IDs and expected amount;
5. public records contain no reference-only data (issue ids, truth/provenance fields).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from .audit import Records, audit, issue_key
from .config import ERROR_TYPES
from .invariants import check_invariants
from .money import convert
from .timeutil import is_business_day, parse_ts

FORBIDDEN_PUBLIC_TOKENS = ("issue_id", "evidence_sets", "expected_amount", "provenance", "truth", "ISS-")


def resolve_id(rec: Records, ref: str):
    table, _, key = ref.partition(":")
    source = {
        "invoices": rec.invoices,
        "payments": rec.payments,
        "ledger": rec.rows,
    }.get(table)
    if source is not None:
        return source.get(key)
    if table == "fx":
        return rec.fx_keys.get(ref)
    return None


def _evidence_supports(rec: Records, issue: dict, ids: list[str]) -> bool:
    """Re-derive the issue from the records in ``ids`` only."""
    got = {ref: resolve_id(rec, ref) for ref in ids}
    if any(v is None for v in got.values()):
        return False
    invoices = [v for k, v in got.items() if k.startswith("invoices:")]
    payments = [v for k, v in got.items() if k.startswith("payments:")]
    rows = [v for k, v in got.items() if k.startswith("ledger:")]
    fx_rows = [v for k, v in got.items() if k.startswith("fx:")]
    t = issue["type"]
    amount = issue["expected_amount_minor"]
    affected = sorted(issue["affected_ids"])

    if t == "duplicate_invoice":
        if len(invoices) != 2:
            return False
        a, b = sorted(invoices, key=lambda i: (parse_ts(i["issued_at"]), i["invoice_id"]))
        same = all(a[f] == b[f] for f in ("entity_id", "currency", "total_minor", "line_items"))
        return same and affected == [f"invoices:{b['invoice_id']}"] and amount == b["total_minor"]

    if t == "payment_allocation":
        if len(invoices) != 1 or len(payments) != 1:
            return False
        inv, pay = invoices[0], payments[0]
        if inv["entity_id"] != pay["entity_id"] or inv["currency"] != pay["currency"]:
            return False
        applied_here = sum(r["applied_minor"] for r in pay["invoice_refs"] if r["invoice_id"] == inv["invoice_id"])
        if any(r["invoice_id"] != inv["invoice_id"] for r in pay["invoice_refs"]):
            return False
        unapplied = pay["amount_minor"] - applied_here
        # the invoice is fully settled by this payment, so the remainder belongs to it
        return (
            unapplied > 0
            and inv["total_minor"] - applied_here == unapplied
            and amount == unapplied
            and affected == sorted([f"payments:{pay['payment_id']}", f"invoices:{inv['invoice_id']}"])
        )

    docs = invoices + payments
    if len(docs) != 1 or len(rows) != 1:
        return False
    doc, row = docs[0], rows[0]
    is_pay = "payment_id" in doc
    ts = doc["timestamp"] if is_pay else doc["issued_at"]
    doc_key = f"payments:{doc['payment_id']}" if is_pay else f"invoices:{doc['invoice_id']}"
    linked = row["payment_id"] == doc["payment_id"] if is_pay else (row["invoice_id"] == doc["invoice_id"] and not row["payment_id"])
    booked = abs(row["debit_minor"] - row["credit_minor"])
    main_ok = row["account"].startswith("bank:") if is_pay else row["account"] == "accounts_receivable"
    if not (linked and main_ok and affected == [doc_key]):
        return False

    if t == "period_cutoff":
        return not rec.in_period(ts) and rec.in_period(row["timestamp"]) and amount == booked

    if t == "fx_conversion":
        if len(fx_rows) != 1:
            return False
        fx = fx_rows[0]
        day = rec.local_day(ts)
        fx_day = parse_ts(fx["date"] + "T00:00:00+00:00").date()
        if fx["source_currency"] != doc["currency"] or fx["target_currency"] != rec.base or fx_day > day:
            return False
        gap = fx_day + timedelta(days=1)
        while gap <= day:  # only non-business days may lie between the rate date and the document date
            if is_business_day(gap):
                return False
            gap += timedelta(days=1)
        amt = doc["amount_minor"] if is_pay else doc["total_minor"]
        correct = convert(amt, Decimal(fx["rate"]), doc["currency"], rec.base, rec.rounding)
        return booked != abs(correct) and amount == abs(booked - abs(correct))
    return False


def verify_episode(public: dict, truth: dict) -> list[str]:
    rec = Records(public)
    problems = check_invariants(public, strict=False, records=rec)

    issues = truth["issues"]
    if truth.get("episode_id") != public["manifest"]["episode_id"]:
        problems.append("truth episode_id does not match manifest")
    for issue in issues:
        if issue["type"] not in ERROR_TYPES:
            problems.append(f"{issue['issue_id']}: unknown type")
        if issue["currency"] not in public["manifest"]["currencies"]:
            problems.append(f"{issue['issue_id']}: currency not in manifest")
        if not issue["evidence_sets"]:
            problems.append(f"{issue['issue_id']}: no evidence sets")
        for s in issue["evidence_sets"]:
            if len(s) > 10:
                problems.append(f"{issue['issue_id']}: evidence set longer than 10")
            for ref in s:
                if resolve_id(rec, ref) is None:
                    problems.append(f"{issue['issue_id']}: evidence {ref} not in public records")
        for ref in issue["affected_ids"]:
            if resolve_id(rec, ref) is None:
                problems.append(f"{issue['issue_id']}: affected {ref} not in public records")
        if not any(_evidence_supports(rec, issue, s) for s in issue["evidence_sets"]):
            problems.append(f"{issue['issue_id']}: no evidence set re-derives the issue")

    expected = Counter(issue_key(i) for i in issues)
    found = Counter(issue_key(i) for i in audit(public, rec))
    for k in expected - found:
        problems.append(f"declared issue not detectable from records: {k}")
    for k in found - expected:
        problems.append(f"undeclared discrepancy in records: {k}")

    blob = json.dumps(public, ensure_ascii=False)
    for token in FORBIDDEN_PUBLIC_TOKENS:
        if token in blob:
            problems.append(f"public records contain reference-only token {token!r}")
    for issue in issues:
        if issue["issue_id"] in blob:
            problems.append(f"public records mention {issue['issue_id']}")
    # explicit nulls, never guessed links
    for r in public["ledger"]:
        for f in ("entity_id", "invoice_id", "payment_id"):
            if r[f] == "":
                problems.append(f"ledger row {r['row_id']} has empty string instead of null in {f}")
    return problems


def check_clean(public: dict) -> list[str]:
    rec = Records(public)
    problems = check_invariants(public, strict=True, records=rec)
    problems += [f"clean history has a discrepancy: {issue_key(i)}" for i in audit(public, rec)]
    return problems
