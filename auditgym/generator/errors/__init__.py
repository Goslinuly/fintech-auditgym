"""Error injectors.

Each injector takes a *correct* flow reserved by the history builder, mutates the
booking or documents, records the causal change, and emits the reference issue(s).

Evidence references are symbolic until the episode is rendered:
``@main:<table>:<id>`` -> the entry's main ledger row (AR debit / bank row),
``@unapplied:payments:<id>`` -> the payment's ``unapplied_cash`` row.
"""

from __future__ import annotations

from datetime import date

from ..history import Builder
from ..render import main_amount
from . import allocation, cutoff, duplicate, fx_error, scenarios


class InjectionContext:
    def __init__(self, builder: Builder):
        self.b = builder
        self.issues: list[dict] = []
        self.changes: list[dict] = []
        self.touched: list[str] = []

    def issue(
        self,
        type_: str,
        affected: list[str],
        amount: int,
        currency: str,
        evidence: list[list[str]],
        *,
        tolerance: int = 0,
        group: str | None = None,
        description: str = "",
    ) -> str:
        issue_id = f"ISS-{len(self.issues) + 1:02d}"
        self.issues.append(
            {
                "issue_id": issue_id,
                "type": type_,
                "affected_ids": sorted(affected),
                "expected_amount_minor": int(abs(amount)),
                "currency": currency,
                "evidence_sets": [sorted(s) for s in evidence],
                "tolerance_minor": tolerance,
                "group": group,
                "description": description,
            }
        )
        return issue_id

    def change(self, issue_ids: list[str], target: str, field: str, before, after, note: str = "") -> None:
        def plain(v):
            if isinstance(v, date):
                return v.isoformat()
            return v

        self.changes.append(
            {
                "issue_ids": issue_ids,
                "target": target,
                "field": field,
                "before": plain(before),
                "after": plain(after),
                "note": note,
            }
        )
        if target not in self.touched:
            self.touched.append(target)

    def main_amount(self, doc: dict) -> int:
        return main_amount(self.b, doc)

    def fx_key(self, day: date, ccy: str) -> str:
        return f"fx:{day.isoformat()}/{ccy}/{self.b.base}"


def inject_all(b: Builder) -> InjectionContext:
    ctx = InjectionContext(b)
    group_n = {"A": 0, "B": 0}
    for t in b.targets:
        kind = t["kind"]
        if kind == "duplicate_invoice":
            duplicate.inject(ctx, t["invoice"])
        elif kind == "payment_allocation":
            allocation.inject(ctx, t["invoice"], t["payment"], t["mode"])
        elif kind == "fx_conversion":
            fx_error.inject(ctx, t["doc"], t["table"])
        elif kind == "period_cutoff":
            cutoff.inject(ctx, t["doc"], t["table"], t["variant"])
        elif kind in ("A", "B"):
            group_n[kind] += 1
            group = f"{kind}{group_n[kind]}"
            if kind == "A":
                scenarios.inject_a(ctx, t["invoice"], t["payment"], group)
            else:
                scenarios.inject_b(ctx, t["invoice"], group)
        else:  # pragma: no cover
            raise ValueError(kind)
    return ctx
