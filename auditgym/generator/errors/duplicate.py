"""duplicate_invoice: the same invoice entered twice under a new invoice_id."""

from __future__ import annotations

import copy
from datetime import timedelta


def make_duplicate(ctx, inv: dict) -> dict:
    b = ctx.b
    issued = b.rand_dt(inv["issued_at"] + timedelta(hours=1), inv["issued_at"] + timedelta(days=3))
    dup = {
        "doc_id": b.ids.new("doc"),
        "invoice_id": b.ids.new("invoice"),
        "entity_id": inv["entity_id"],
        "issued_at": issued,
        "due_at": issued + (inv["due_at"] - inv["issued_at"]),
        "currency": inv["currency"],
        "total_minor": inv["total_minor"],
        "line_items": copy.deepcopy(inv["line_items"]),
        "_fmt": b.rng.choice(("local", "utc")),
        "_book": {"booked": True, "ledger_ts": None, "rate_date": None},
    }
    b.invoices.append(dup)
    return dup


def inject(ctx, inv: dict, group: str | None = None) -> dict:
    dup = make_duplicate(ctx, inv)
    orig_key, dup_key = f"invoices:{inv['invoice_id']}", f"invoices:{dup['invoice_id']}"
    iid = ctx.issue(
        "duplicate_invoice",
        [dup_key],
        dup["total_minor"],
        dup["currency"],
        [[orig_key, dup_key]],
        group=group,
        description=(
            f"{dup['invoice_id']} repeats {inv['invoice_id']}: same customer, line items and total; "
            "the later copy is booked a second time to revenue"
        ),
    )
    ctx.change([iid], dup_key, "document", None, "added (copy of " + inv["invoice_id"] + ")")
    ctx.change([iid], dup_key, "_book.booked", None, True)
    return dup
