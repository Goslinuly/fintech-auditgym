"""Interacting scenarios.

A: foreign-currency payment received just after the period end (its UTC timestamp still shows
   the last day), posted in this period, applied only partly, and converted at the wrong date's
   rate -> period_cutoff + payment_allocation + fx_conversion on one payment.
   Order of changes: allocation, FX date, booking. The cutoff amount is the bank row *as booked*
   (i.e. after the FX error); the FX amount is |booked - correct| of that row.
B: foreign-currency invoice entered twice; the duplicate is paid, and that payment is converted
   at the wrong date's rate -> duplicate_invoice + fx_conversion.
"""

from __future__ import annotations

from datetime import timedelta

from . import allocation, cutoff, duplicate, fx_error


def inject_a(ctx, inv: dict, pay: dict, group: str) -> None:
    n0 = len(ctx.changes)
    unapplied = allocation.apply_error(ctx, pay, inv, "partial", [])
    key, diff, correct_date, variant = fx_error.apply_error(
        ctx, pay, "payments", [], variants=("utc_date", "stale", "next_business_day")
    )
    cutoff.apply_error(ctx, pay, "payments", "early", [])
    ids = [
        cutoff.issue_for(ctx, pay, key, "early_tz" if pay["_fmt"] == "utc" else "early", group),
        allocation.issue_for(ctx, pay, inv, unapplied, group),
        fx_error.issue_for(ctx, pay, key, diff, correct_date, variant, group),
    ]
    for ch in ctx.changes[n0:]:
        ch["issue_ids"] = ids


def inject_b(ctx, inv: dict, group: str) -> None:
    b = ctx.b
    n0 = len(ctx.changes)
    dup = duplicate.inject(ctx, inv, group)
    ts = b.rand_dt(dup["issued_at"] + timedelta(hours=2), dup["issued_at"] + timedelta(days=4))
    pay = b.pay_in_full(dup, ts)
    pay_key = f"payments:{pay['payment_id']}"
    key, diff, correct_date, variant = fx_error.apply_error(ctx, pay, "payments", [])
    fid = fx_error.issue_for(ctx, pay, key, diff, correct_date, variant, group)
    dup_issue = ctx.issues[-2]["issue_id"]
    ctx.change([dup_issue, fid], pay_key, "document", None, f"added (customer paid duplicate {dup['invoice_id']})")
    for ch in ctx.changes[n0:]:
        ch["issue_ids"] = [dup_issue, fid]
