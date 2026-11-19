"""Render a history state into public episode records.

Booking rules (also stated in ``policy.json``):

* The ledger is kept in the base currency; every row has exactly one non-zero side.
* Invoice entry: Dr ``accounts_receivable`` / Cr ``revenue`` for the invoice total converted
  at the rate of the invoice's local date (``issued_at`` in the company timezone).
* Payment entry: ``bank:<CCY>`` for the amount at the rate of the payment's local date;
  Cr ``accounts_receivable`` per applied invoice at that *invoice's* rate (historical rate);
  Cr ``unapplied_cash`` for any unallocated remainder at the payment rate;
  refunds debit ``unapplied_cash`` at the refunded payment's rate;
  the difference goes to ``fx_gain_loss``.
* Each converted line is rounded once to a whole base-currency minor unit.
* Rate for a date = the last published rate on or before that date.
"""

from __future__ import annotations

import copy
import random
from typing import Any

from .ids import IdFactory
from .money import EXPONENTS, convert
from .timeutil import UTC, fmt

SCHEMA_VERSION = "1.0"

ACC_AR = "accounts_receivable"
ACC_REVENUE = "revenue"
ACC_UNAPPLIED = "unapplied_cash"
ACC_FX = "fx_gain_loss"

ACCOUNTS = {
    ACC_AR: "Amounts customers owe on invoices (base currency)",
    ACC_REVENUE: "Sales revenue",
    ACC_UNAPPLIED: "Cash received but not allocated to any invoice",
    ACC_FX: "Realised FX gains (credit) and losses (debit)",
    "bank:<CCY>": "Bank account held in <CCY>, carried at base-currency equivalent",
    "expense:*": "Operating expenses without customer documents",
    "payroll_tax_payable": "Payroll taxes withheld",
    "other_income": "Other income",
}

LEDGER_COLUMNS = (
    "row_id",
    "timestamp",
    "account",
    "debit_minor",
    "credit_minor",
    "currency",
    "entity_id",
    "invoice_id",
    "payment_id",
)


def bank_account(ccy: str) -> str:
    return f"bank:{ccy}"


def doc_rate_date(state, doc: dict, ts_field: str):
    override = doc["_book"]["rate_date"]
    return override if override is not None else doc[ts_field].astimezone(state.tz).date()


def conv(state, amount: int, ccy: str, day) -> int:
    _, rate = state.fx.lookup(ccy, day)
    return convert(amount, rate, ccy, state.base, state.rounding)


def main_amount(state, doc: dict) -> int:
    """Signed base amount of the entry's main row (AR debit for invoices, bank for payments)."""
    if "payment_id" in doc:
        return conv(state, doc["amount_minor"], doc["currency"], doc_rate_date(state, doc, "timestamp"))
    return conv(state, doc["total_minor"], doc["currency"], doc_rate_date(state, doc, "issued_at"))


def _row(account: str, amount: int, *, entity, invoice_id=None, payment_id=None, role=None) -> dict:
    """``amount`` > 0 is a debit, < 0 a credit."""
    return {
        "account": account,
        "debit_minor": amount if amount > 0 else 0,
        "credit_minor": -amount if amount < 0 else 0,
        "entity_id": entity,
        "invoice_id": invoice_id,
        "payment_id": payment_id,
        "_role": role,
    }


def build_doc_entries(state) -> list[dict]:
    invoices = {i["invoice_id"]: i for i in state.invoices}
    payments = {p["payment_id"]: p for p in state.payments}
    entries = []
    for inv in state.invoices:
        if not inv["_book"]["booked"]:
            continue
        amt = main_amount(state, inv)
        ids = {"entity": inv["entity_id"], "invoice_id": inv["invoice_id"]}
        entries.append(
            {
                "key": f"invoices:{inv['invoice_id']}",
                "ts": inv["_book"]["ledger_ts"] or inv["issued_at"],
                "rows": [_row(ACC_AR, amt, role="main", **ids), _row(ACC_REVENUE, -amt, **ids)],
            }
        )
    for pay in state.payments:
        if not pay["_book"]["booked"]:
            continue
        pid, ent, ccy = pay["payment_id"], pay["entity_id"], pay["currency"]
        pay_day = doc_rate_date(state, pay, "timestamp")
        bank = conv(state, pay["amount_minor"], ccy, pay_day)
        rows = [_row(bank_account(ccy), bank, entity=ent, payment_id=pid, role="main")]
        balance = bank
        for ref in pay["invoice_refs"]:
            inv = invoices[ref["invoice_id"]]
            ar = conv(state, ref["applied_minor"], ccy, inv["issued_at"].astimezone(state.tz).date())
            rows.append(_row(ACC_AR, -ar, entity=ent, invoice_id=inv["invoice_id"], payment_id=pid))
            balance -= ar
        if pay["kind"] == "receipt":
            unapplied = pay["amount_minor"] - sum(r["applied_minor"] for r in pay["invoice_refs"])
            if unapplied > 0:
                ua = conv(state, unapplied, ccy, pay_day)
                rows.append(_row(ACC_UNAPPLIED, -ua, entity=ent, payment_id=pid, role="unapplied"))
                balance -= ua
        else:  # refund of unapplied cash
            orig = payments[pay["refund_of"]]
            ua = conv(state, -pay["amount_minor"], ccy, orig["timestamp"].astimezone(state.tz).date())
            rows.append(_row(ACC_UNAPPLIED, ua, entity=ent, payment_id=pid, role="unapplied"))
            balance += ua
        if balance:
            rows.append(_row(ACC_FX, -balance, entity=ent, payment_id=pid))
        entries.append({"key": f"payments:{pid}", "ts": pay["_book"]["ledger_ts"] or pay["timestamp"], "rows": rows})
    return entries


def _doc_tz(state, doc):
    return UTC if doc["_fmt"] == "utc" else state.tz


def public_invoice(state, inv: dict) -> dict:
    tz = _doc_tz(state, inv)
    return {
        "doc_id": inv["doc_id"],
        "invoice_id": inv["invoice_id"],
        "entity_id": inv["entity_id"],
        "issued_at": fmt(inv["issued_at"], tz),
        "due_at": fmt(inv["due_at"], tz),
        "currency": inv["currency"],
        "total_minor": inv["total_minor"],
        "line_items": copy.deepcopy(inv["line_items"]),
    }


def public_payment(state, pay: dict) -> dict:
    return {
        "doc_id": pay["doc_id"],
        "payment_id": pay["payment_id"],
        "entity_id": pay["entity_id"],
        "timestamp": fmt(pay["timestamp"], _doc_tz(state, pay)),
        "currency": pay["currency"],
        "amount_minor": pay["amount_minor"],
        "kind": pay["kind"],
        "invoice_refs": copy.deepcopy(pay["invoice_refs"]),
        "refund_of": pay["refund_of"],
    }


def build_policy(state) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "period": {
            "start": fmt(state.start, state.tz),
            "end": fmt(state.end, state.tz),
            "end_exclusive": True,
            "timezone": state.tz_str,
            "cutoff_basis": "document date in the company timezone: invoices.issued_at, payments.timestamp",
        },
        "fx": {
            "quote": "1 unit of source_currency = rate units of target_currency (major units)",
            "rate_date": "document date in the company timezone (not the UTC date)",
            "missing_rate": "use the last rate published on or before that date (no weekend rates)",
            "invoice_rate": "invoices are booked at the rate of their own date",
            "payment_rate": "payments are booked at the rate of their own date",
            "settlement": "accounts_receivable is cleared at the invoice's own rate; the difference goes to fx_gain_loss",
            "refund": "refunds clear unapplied_cash at the rate of the refunded payment",
        },
        "rounding": {
            "mode": state.rounding,
            "scope": "each converted ledger line is rounded once to a whole base-currency minor unit",
        },
        "allocation": {
            "invoice_refs": "applied_minor is in the payment currency, which always equals the invoice currency",
            "unapplied": "any part of a receipt not applied to an invoice is credited to unapplied_cash",
        },
        "ledger": {"currency": state.base, "accounts": ACCOUNTS, "null": "empty CSV cell means null"},
    }


def render(state, rng: random.Random) -> tuple[dict[str, Any], dict]:
    """Return ``(public, index)``; ``index`` maps documents to their ledger row ids."""
    entries = build_doc_entries(state)
    for bg in state.background:
        entries.append(
            {
                "key": None,
                "ts": bg["ts"],
                "rows": [_row(acc, d - c, entity=None) for acc, d, c in bg["rows"]],
            }
        )
    order = sorted(
        ((e["ts"].timestamp(), rng.random(), i) for i, e in enumerate(entries)),
    )
    ids = IdFactory(rng)
    ledger = []
    index: dict[str, dict] = {"main": {}, "unapplied": {}, "entries": {}}
    for _, _, i in order:
        e = entries[i]
        row_ids = []
        for r in e["rows"]:
            rid = ids.new("row")
            row_ids.append(rid)
            ledger.append(
                {
                    "row_id": rid,
                    "timestamp": fmt(e["ts"], state.tz),
                    "account": r["account"],
                    "debit_minor": r["debit_minor"],
                    "credit_minor": r["credit_minor"],
                    "currency": state.base,
                    "entity_id": r["entity_id"],
                    "invoice_id": r["invoice_id"],
                    "payment_id": r["payment_id"],
                }
            )
            if r["_role"] == "main":
                index["main"][e["key"]] = rid
            elif r["_role"] == "unapplied":
                index["unapplied"][e["key"]] = rid
        if e["key"]:
            index["entries"][e["key"]] = row_ids

    invoices = list(state.invoices)
    payments = list(state.payments)
    rng.shuffle(invoices)
    rng.shuffle(payments)
    currencies = sorted({state.base, *state.foreign})
    fx_rows = state.fx.rows()
    public = {
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "episode_id": state.episode_id,
            "company_name": state.company_name,
            "base_currency": state.base,
            "currencies": {c: {"exponent": EXPONENTS[c]} for c in currencies},
            "period_start": fmt(state.start, state.tz),
            "period_end": fmt(state.end, state.tz),
            "period_end_exclusive": True,
            "timezone": state.tz_str,
            "counts": {
                "ledger_rows": len(ledger),
                "invoices": len(invoices),
                "payments": len(payments),
                "documents": len(invoices) + len(payments),
                "fx_rates": len(fx_rows),
            },
            "rounding": {"mode": state.rounding, "unit": "base-currency minor unit, per ledger line"},
            "money": "integer minor units; exponent = number of decimal places of the currency",
        },
        "policy": build_policy(state),
        "ledger": ledger,
        "invoices": [public_invoice(state, i) for i in invoices],
        "payments": [public_payment(state, p) for p in payments],
        "fx": fx_rows,
    }
    return public, index
