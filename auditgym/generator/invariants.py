"""Accounting and consistency invariants over public records.

``strict=True`` is used on the clean history *before* error injection: then every entry
must also match the booking rules exactly and every document must be booked iff it is
dated inside the period. After injection (``strict=False``) only the rules that errors
never break are checked (double entry, references, settlement of invoices, formats).
"""

from __future__ import annotations

from collections import Counter

from .audit import Records
from .render import ACC_AR, ACC_FX, ACC_REVENUE, ACC_UNAPPLIED, LEDGER_COLUMNS
from .timeutil import parse_ts


def _balanced(rows: list[dict]) -> bool:
    return sum(r["debit_minor"] for r in rows) == sum(r["credit_minor"] for r in rows)


def check_invariants(public: dict, strict: bool = False, records: Records | None = None) -> list[str]:
    rec = records or Records(public)
    p: list[str] = []
    m = public["manifest"]
    base = rec.base

    # --- formats and counts
    counts = m["counts"]
    if counts["ledger_rows"] != len(public["ledger"]):
        p.append("manifest ledger_rows mismatch")
    if counts["invoices"] != len(public["invoices"]) or counts["payments"] != len(public["payments"]):
        p.append("manifest document counts mismatch")
    if counts["documents"] != counts["invoices"] + counts["payments"]:
        p.append("manifest documents != invoices + payments")
    if counts["fx_rates"] != len(public["fx"]):
        p.append("manifest fx_rates mismatch")
    for kind, values in (
        ("row_id", [r["row_id"] for r in public["ledger"]]),
        ("invoice_id", [i["invoice_id"] for i in public["invoices"]]),
        ("payment_id", [x["payment_id"] for x in public["payments"]]),
        ("doc_id", [i["doc_id"] for i in public["invoices"]] + [x["doc_id"] for x in public["payments"]]),
    ):
        dup = [v for v, n in Counter(values).items() if n > 1]
        if dup:
            p.append(f"duplicate {kind}: {dup[:3]}")

    for r in public["ledger"]:
        if tuple(r) != LEDGER_COLUMNS:
            p.append(f"ledger row {r['row_id']} has columns {tuple(r)}")
            break
        d, c = r["debit_minor"], r["credit_minor"]
        if not (isinstance(d, int) and isinstance(c, int) and d >= 0 and c >= 0 and (d > 0) != (c > 0)):
            p.append(f"ledger row {r['row_id']} must have exactly one positive side")
        if r["currency"] != base:
            p.append(f"ledger row {r['row_id']} not in base currency")
        if r["invoice_id"] is not None and r["invoice_id"] not in rec.invoices:
            p.append(f"ledger row {r['row_id']} references unknown invoice")
        if r["payment_id"] is not None and r["payment_id"] not in rec.payments:
            p.append(f"ledger row {r['row_id']} references unknown payment")
        parse_ts(r["timestamp"])

    # --- double entry
    if not _balanced(public["ledger"]):
        p.append("ledger total debit != total credit")
    for name, groups in (("invoice", rec.inv_entries), ("payment", rec.pay_entries), ("background", rec.bg_entries)):
        for key, rows in groups.items():
            if not _balanced(rows):
                p.append(f"{name} entry {key} unbalanced")

    # --- documents
    for inv in public["invoices"]:
        if sum(li["amount_minor"] for li in inv["line_items"]) != inv["total_minor"]:
            p.append(f"invoice {inv['invoice_id']} lines do not sum to total")
        for li in inv["line_items"]:
            if li["quantity"] * li["unit_price_minor"] != li["amount_minor"]:
                p.append(f"invoice {inv['invoice_id']} line amount != quantity * unit price")
        if inv["currency"] not in m["currencies"]:
            p.append(f"invoice {inv['invoice_id']} currency not declared in manifest")
        rows = rec.inv_entries.get(inv["invoice_id"], [])
        if rows:
            accounts = sorted((r["account"], r["debit_minor"] > 0) for r in rows)
            if accounts != [(ACC_AR, True), (ACC_REVENUE, False)]:
                p.append(f"invoice {inv['invoice_id']} entry has unexpected accounts {accounts}")
            if any(r["entity_id"] != inv["entity_id"] for r in rows):
                p.append(f"invoice {inv['invoice_id']} entry entity mismatch")
    for inv_id, applied in rec.applied.items():
        if inv_id not in rec.invoices:
            p.append(f"invoice_refs points to unknown invoice {inv_id}")
        elif applied > rec.invoices[inv_id]["total_minor"]:
            p.append(f"invoice {inv_id} over-applied")

    for pay in public["payments"]:
        pid = pay["payment_id"]
        if pay["kind"] == "receipt" and pay["amount_minor"] <= 0:
            p.append(f"receipt {pid} must be positive")
        if pay["kind"] == "refund":
            orig = rec.payments.get(pay["refund_of"])
            if pay["amount_minor"] >= 0 or orig is None or orig["entity_id"] != pay["entity_id"]:
                p.append(f"refund {pid} malformed")
        for ref in pay["invoice_refs"]:
            inv = rec.invoices.get(ref["invoice_id"])
            if ref["applied_minor"] <= 0:
                p.append(f"payment {pid} has non-positive applied_minor")
            if inv and (inv["currency"] != pay["currency"] or inv["entity_id"] != pay["entity_id"]):
                p.append(f"payment {pid} applied to invoice of another currency or customer")
        if sum(r["applied_minor"] for r in pay["invoice_refs"]) > max(pay["amount_minor"], 0):
            p.append(f"payment {pid} applies more than it received")
        rows = rec.pay_entries.get(pid, [])
        if not rows:
            continue
        if any(r["entity_id"] != pay["entity_id"] for r in rows):
            p.append(f"payment {pid} entry entity mismatch")
        # settlement: AR is cleared at each invoice's own rate (never changed by injected errors)
        ar_rows = sorted(
            (r["invoice_id"], r["credit_minor"]) for r in rows if r["account"] == ACC_AR
        )
        ar_expected = sorted(
            (
                ref["invoice_id"],
                rec.conv(
                    ref["applied_minor"],
                    pay["currency"],
                    rec.local_day(rec.invoices[ref["invoice_id"]]["issued_at"]),
                ),
            )
            for ref in pay["invoice_refs"]
        )
        if ar_rows != ar_expected:
            p.append(f"payment {pid} clears AR inconsistently")
        if strict:
            day = rec.local_day(pay["timestamp"])
            bank = rec.payment_bank_row(pid)
            if bank is None or bank["account"] != f"bank:{pay['currency']}":
                p.append(f"payment {pid} has no bank row")
                continue
            exp_bank = rec.conv(pay["amount_minor"], pay["currency"], day)
            if bank["debit_minor"] - bank["credit_minor"] != exp_bank:
                p.append(f"payment {pid} bank amount off policy")
            ua = rec.payment_unapplied_row(pid)
            if pay["kind"] == "receipt":
                unapplied = pay["amount_minor"] - sum(r["applied_minor"] for r in pay["invoice_refs"])
                exp_ua = rec.conv(unapplied, pay["currency"], day) if unapplied else 0
                got_ua = ua["credit_minor"] if ua else 0
            else:
                orig = rec.payments[pay["refund_of"]]
                exp_ua = rec.conv(-pay["amount_minor"], pay["currency"], rec.local_day(orig["timestamp"]))
                got_ua = ua["debit_minor"] if ua else 0
            if got_ua != exp_ua:
                p.append(f"payment {pid} unapplied amount off policy")
            allowed = {bank["account"], ACC_AR, ACC_UNAPPLIED, ACC_FX}
            if any(r["account"] not in allowed for r in rows):
                p.append(f"payment {pid} entry has unexpected accounts")

    if strict:
        for inv in public["invoices"]:
            booked = inv["invoice_id"] in rec.inv_entries
            if booked != rec.in_period(inv["issued_at"]):
                p.append(f"invoice {inv['invoice_id']} booked={booked} but in_period={not booked}")
            if booked:
                row = rec.invoice_main_row(inv["invoice_id"])
                if parse_ts(row["timestamp"]) != parse_ts(inv["issued_at"]):
                    p.append(f"invoice {inv['invoice_id']} posted at a different time")
                exp = rec.conv(inv["total_minor"], inv["currency"], rec.local_day(inv["issued_at"]))
                if row["debit_minor"] != exp:
                    p.append(f"invoice {inv['invoice_id']} amount off policy")
        for pay in public["payments"]:
            booked = pay["payment_id"] in rec.pay_entries
            if booked != rec.in_period(pay["timestamp"]):
                p.append(f"payment {pay['payment_id']} booked={booked} but in_period={not booked}")
            if booked and any(
                parse_ts(r["timestamp"]) != parse_ts(pay["timestamp"]) for r in rec.pay_entries[pay["payment_id"]]
            ):
                p.append(f"payment {pay['payment_id']} posted at a different time")
    return p
