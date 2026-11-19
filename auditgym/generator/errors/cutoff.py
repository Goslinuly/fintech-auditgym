"""period_cutoff: a document dated outside the period is booked inside it.

Variants: ``early`` (next-period document posted on the last day), ``early_tz`` (same, but the
document is written in UTC so its date string still shows the last day of the period),
``late`` (previous-period document posted in this period).
Canonical form: affected = {document}; amount = |main row as booked|, base currency.
"""

from __future__ import annotations

from datetime import timedelta


def apply_error(ctx, doc: dict, table: str, variant: str, issue_ids: list[str]) -> str:
    b = ctx.b
    if variant in ("early", "early_tz"):
        ledger_ts = b.rand_dt(b.end - timedelta(hours=3), b.end - timedelta(minutes=1))
    else:
        ledger_ts = b.rand_dt(b.start + timedelta(hours=1), b.start + timedelta(days=3))
    doc["_book"]["booked"] = True
    doc["_book"]["ledger_ts"] = ledger_ts
    key = f"{table}:{doc[table[:-1] + '_id']}"
    ctx.change(issue_ids, key, "_book.booked", False, True, variant)
    ctx.change(issue_ids, key, "_book.ledger_ts", None, ledger_ts.isoformat(timespec="seconds"), variant)
    return key


def issue_for(ctx, doc: dict, key: str, variant: str, group: str | None = None) -> str:
    return ctx.issue(
        "period_cutoff",
        [key],
        ctx.main_amount(doc),
        ctx.b.base,
        [[key, f"@main:{key}"]],
        group=group,
        description=f"{key} is dated outside the period ({variant}) but booked inside it",
    )


def inject(ctx, doc: dict, table: str, variant: str) -> None:
    key = apply_error(ctx, doc, table, variant, [])
    iid = issue_for(ctx, doc, key, variant)
    ctx.changes[-2]["issue_ids"] = [iid]
    ctx.changes[-1]["issue_ids"] = [iid]
