"""fx_conversion: a foreign-currency document booked at the rate of the wrong date.

Variants of the wrong date (all are real rows of fx.csv):
``utc_date``          the UTC calendar date instead of the local one (documents near midnight),
``next_business_day`` the rate published after the correct one,
``stale``             a rate several business days old,
``due_date``          (invoices) the rate of the due date.
Canonical form: affected = {document}; amount = |booked - correct| of the main row, base currency.
"""

from __future__ import annotations

from ..history import RetryGeneration
from ..money import convert
from ..timeutil import UTC

MIN_FX_DIFF = 10  # minor units; keeps the discrepancy well above the matching tolerance
FX_TOLERANCE = 1


def pick_wrong_date(ctx, doc: dict, ts_field: str, variants: tuple[str, ...]):
    b = ctx.b
    ccy, ts = doc["currency"], doc[ts_field]
    amount = doc["amount_minor"] if "payment_id" in doc else doc["total_minor"]
    correct_date, correct_rate = b.fx.lookup(ccy, b.local_date(ts))
    correct = convert(amount, correct_rate, ccy, b.base, b.rounding)
    dates = b.fx.dates(ccy)
    i = dates.index(correct_date)

    def check(variant, day):
        used, rate = b.fx.lookup(ccy, day)
        wrong = convert(amount, rate, ccy, b.base, b.rounding)
        if used != correct_date and abs(wrong - correct) >= MIN_FX_DIFF:
            return variant, used, correct_date
        return None

    order = list(variants)
    b.rng.shuffle(order)
    for v in order:
        if v == "utc_date":
            day = ts.astimezone(UTC).date()
        elif v == "next_business_day":
            if i + 1 >= len(dates):
                continue
            day = dates[i + 1]
        elif v == "stale":
            k = b.rng.randint(2, 8)
            if i - k < 0:
                continue
            day = dates[i - k]
        elif v == "due_date":
            day = b.local_date(doc["due_at"])
        else:  # pragma: no cover
            raise ValueError(v)
        found = check(v, day)
        if found:
            return found
    for k in range(2, 25):
        if i - k >= 0:
            found = check("stale", dates[i - k])
            if found:
                return found
    raise RetryGeneration("no FX date with a material difference")


def apply_error(ctx, doc: dict, table: str, issue_ids: list[str], variants=None):
    ts_field = "timestamp" if table == "payments" else "issued_at"
    if variants is None:
        variants = ("utc_date", "next_business_day", "stale") + (("due_date",) if table == "invoices" else ())
    correct_amt = ctx.main_amount(doc)
    variant, used, correct_date = pick_wrong_date(ctx, doc, ts_field, variants)
    doc["_book"]["rate_date"] = used
    wrong_amt = ctx.main_amount(doc)
    key = f"{table}:{doc[table[:-1] + '_id']}"
    ctx.change(issue_ids, key, "_book.rate_date", correct_date, used, variant)
    return key, abs(wrong_amt - correct_amt), correct_date, variant


def issue_for(ctx, doc: dict, key: str, diff: int, correct_date, variant: str, group: str | None = None) -> str:
    return ctx.issue(
        "fx_conversion",
        [key],
        diff,
        ctx.b.base,
        [[key, f"@main:{key}", ctx.fx_key(correct_date, doc["currency"])]],
        tolerance=FX_TOLERANCE,
        group=group,
        description=(
            f"{key} converted at the {doc['currency']} rate of the wrong date ({variant}); "
            f"policy rate is dated {correct_date.isoformat()}"
        ),
    )


def inject(ctx, doc: dict, table: str) -> None:
    key, diff, correct_date, variant = apply_error(ctx, doc, table, [])
    iid = issue_for(ctx, doc, key, diff, correct_date, variant)
    ctx.changes[-1]["issue_ids"] = [iid]
