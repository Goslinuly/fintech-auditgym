"""Reference auditor: finds every discrepancy using *only* public records and policy.

It shares no state with the injectors. ``verify`` requires its findings to equal
``truth.json`` exactly, which proves that (a) every declared issue is really visible in
the records and (b) the episode contains no undeclared discrepancy.

Detection rules (the canonical definition of each error type):

duplicate_invoice  booked invoices with the same entity, currency, total and identical
                   line items; every one except the earliest (by issued_at) is a duplicate.
payment_allocation a booked receipt with an unapplied remainder r > 0 such that
                   - no invoice_refs and exactly one invoice of that entity and currency has
                     an open balance equal to the payment amount, or
                   - one invoice_ref whose invoice has an open balance equal to r.
                   Open balance = total - all applied_minor referencing the invoice.
fx_conversion      a booked foreign-currency document whose main row (AR debit / bank row)
                   differs from the amount converted at the policy rate.
period_cutoff      a booked document whose own date, in the company timezone, is outside
                   [period_start, period_end).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from .fx import FxTable
from .money import convert
from .render import ACC_AR, ACC_UNAPPLIED
from .timeutil import parse_tz, parse_ts


class Records:
    """Parsed view of public records shared by audit, invariants and verify."""

    def __init__(self, public: dict):
        m = public["manifest"]
        self.public = public
        self.base = m["base_currency"]
        self.tz = parse_tz(m["timezone"])
        self.start = parse_ts(m["period_start"])
        self.end = parse_ts(m["period_end"])
        self.rounding = m["rounding"]["mode"]
        self.fx = FxTable.from_rows(self.base, public["fx"])
        self.fx_keys = {f"fx:{r['date']}/{r['source_currency']}/{r['target_currency']}": r for r in public["fx"]}
        self.invoices = {i["invoice_id"]: i for i in public["invoices"]}
        self.payments = {p["payment_id"]: p for p in public["payments"]}
        self.rows = {r["row_id"]: r for r in public["ledger"]}
        self.inv_entries: dict[str, list[dict]] = defaultdict(list)
        self.pay_entries: dict[str, list[dict]] = defaultdict(list)
        self.bg_entries: dict[str, list[dict]] = defaultdict(list)
        for r in public["ledger"]:
            if r["payment_id"]:
                self.pay_entries[r["payment_id"]].append(r)
            elif r["invoice_id"]:
                self.inv_entries[r["invoice_id"]].append(r)
            else:
                self.bg_entries[r["timestamp"]].append(r)
        self.applied: dict[str, int] = defaultdict(int)
        for p in public["payments"]:
            for ref in p["invoice_refs"]:
                self.applied[ref["invoice_id"]] += ref["applied_minor"]

    # time
    def local(self, ts: str):
        return parse_ts(ts).astimezone(self.tz)

    def local_day(self, ts: str) -> date:
        return self.local(ts).date()

    def in_period(self, ts: str) -> bool:
        return self.start <= parse_ts(ts) < self.end

    # money
    def conv(self, amount: int, ccy: str, day: date) -> int:
        _, rate = self.fx.lookup(ccy, day)
        return convert(amount, rate, ccy, self.base, self.rounding)

    def outstanding(self, invoice_id: str) -> int:
        return self.invoices[invoice_id]["total_minor"] - self.applied[invoice_id]

    # entries
    def invoice_main_row(self, invoice_id: str) -> dict | None:
        for r in self.inv_entries.get(invoice_id, []):
            if r["account"] == ACC_AR and r["debit_minor"] > 0:
                return r
        return None

    def payment_bank_row(self, payment_id: str) -> dict | None:
        for r in self.pay_entries.get(payment_id, []):
            if r["account"].startswith("bank:"):
                return r
        return None

    def payment_unapplied_row(self, payment_id: str) -> dict | None:
        for r in self.pay_entries.get(payment_id, []):
            if r["account"] == ACC_UNAPPLIED:
                return r
        return None


def _signed(row: dict) -> int:
    return row["debit_minor"] - row["credit_minor"]


def _issue(type_: str, affected: list[str], amount: int, currency: str) -> dict:
    return {"type": type_, "affected_ids": sorted(affected), "amount_minor": abs(int(amount)), "currency": currency}


def issue_key(issue: dict) -> tuple:
    amount = issue.get("amount_minor", issue.get("expected_amount_minor"))
    return (issue["type"], tuple(sorted(issue["affected_ids"])), amount, issue["currency"])


def audit(public: dict, records: Records | None = None) -> list[dict]:
    rec = records or Records(public)
    found: list[dict] = []

    # invoices: FX and cutoff
    booked_invoices = []
    for inv_id in rec.inv_entries:
        inv = rec.invoices[inv_id]
        row = rec.invoice_main_row(inv_id)
        booked_invoices.append(inv)
        key = f"invoices:{inv_id}"
        if inv["currency"] != rec.base:
            expected = rec.conv(inv["total_minor"], inv["currency"], rec.local_day(inv["issued_at"]))
            if row["debit_minor"] != expected:
                found.append(_issue("fx_conversion", [key], row["debit_minor"] - expected, rec.base))
        if not rec.in_period(inv["issued_at"]):
            found.append(_issue("period_cutoff", [key], row["debit_minor"], rec.base))

    # duplicates
    groups: dict[str, list[dict]] = defaultdict(list)
    for inv in booked_invoices:
        sig = json.dumps(
            [inv["entity_id"], inv["currency"], inv["total_minor"], inv["line_items"]], sort_keys=True
        )
        groups[sig].append(inv)
    for invs in groups.values():
        if len(invs) < 2:
            continue
        invs.sort(key=lambda i: (parse_ts(i["issued_at"]), i["invoice_id"]))
        for dup in invs[1:]:
            found.append(_issue("duplicate_invoice", [f"invoices:{dup['invoice_id']}"], dup["total_minor"], dup["currency"]))

    # payments: FX, cutoff, allocation
    for pid in rec.pay_entries:
        pay = rec.payments[pid]
        key = f"payments:{pid}"
        bank = _signed(rec.payment_bank_row(pid))
        if pay["currency"] != rec.base:
            expected = rec.conv(pay["amount_minor"], pay["currency"], rec.local_day(pay["timestamp"]))
            if bank != expected:
                found.append(_issue("fx_conversion", [key], bank - expected, rec.base))
        if not rec.in_period(pay["timestamp"]):
            found.append(_issue("period_cutoff", [key], bank, rec.base))
        if pay["kind"] != "receipt":
            continue
        unapplied = pay["amount_minor"] - sum(r["applied_minor"] for r in pay["invoice_refs"])
        if unapplied <= 0:
            continue
        if not pay["invoice_refs"]:
            candidates = [
                inv
                for inv in rec.invoices.values()
                if inv["entity_id"] == pay["entity_id"]
                and inv["currency"] == pay["currency"]
                and rec.outstanding(inv["invoice_id"]) == pay["amount_minor"]
            ]
            if len(candidates) == 1:
                inv = candidates[0]
                found.append(_issue("payment_allocation", [key, f"invoices:{inv['invoice_id']}"], unapplied, pay["currency"]))
        elif len(pay["invoice_refs"]) == 1:
            inv_id = pay["invoice_refs"][0]["invoice_id"]
            if rec.outstanding(inv_id) == unapplied:
                found.append(_issue("payment_allocation", [key, f"invoices:{inv_id}"], unapplied, pay["currency"]))
    return found
