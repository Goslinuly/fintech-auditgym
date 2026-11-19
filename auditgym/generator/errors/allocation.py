"""payment_allocation: a receipt is not (or only partly) applied to the invoice it settles.

Canonical form: affected = {payment, invoice}; amount = unapplied remainder in the
payment currency. The invoice is identifiable from public records: same customer,
same currency, and its open balance equals the unapplied remainder.
"""

from __future__ import annotations


def apply_error(ctx, pay: dict, inv: dict, mode: str, issue_ids: list[str]) -> int:
    b = ctx.b
    pay_key = f"payments:{pay['payment_id']}"
    before = [dict(r) for r in pay["invoice_refs"]]
    if mode == "missing":
        pay["invoice_refs"] = []
    else:
        applied = pay["amount_minor"] * b.rng.randint(35, 85) // 100
        applied = max(1, min(applied, pay["amount_minor"] - 1))
        pay["invoice_refs"] = [{"invoice_id": inv["invoice_id"], "applied_minor": applied}]
    ctx.change(issue_ids, pay_key, "invoice_refs", before, [dict(r) for r in pay["invoice_refs"]], mode)
    return pay["amount_minor"] - sum(r["applied_minor"] for r in pay["invoice_refs"])


def issue_for(ctx, pay: dict, inv: dict, unapplied: int, group: str | None = None) -> str:
    pay_key, inv_key = f"payments:{pay['payment_id']}", f"invoices:{inv['invoice_id']}"
    return ctx.issue(
        "payment_allocation",
        [pay_key, inv_key],
        unapplied,
        pay["currency"],
        [[pay_key, inv_key]],
        group=group,
        description=(
            f"{pay['payment_id']} settles {inv['invoice_id']} but {unapplied} {pay['currency']} minor units "
            "were left in unapplied_cash"
        ),
    )


def inject(ctx, inv: dict, pay: dict, mode: str) -> None:
    unapplied = apply_error(ctx, pay, inv, mode, [])
    iid = issue_for(ctx, pay, inv, unapplied)
    ctx.changes[-1]["issue_ids"] = [iid]
