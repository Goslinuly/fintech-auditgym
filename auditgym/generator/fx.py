"""FX rate table: one rate per business day for each foreign currency into the base currency."""

from __future__ import annotations

import bisect
import random
from datetime import date, timedelta
from decimal import Decimal, localcontext

from .money import USD_VALUE, rate_str, round_sig
from .timeutil import is_business_day


class FxTable:
    def __init__(self, base: str, rates: dict[str, list[tuple[date, Decimal]]]):
        self.base = base
        self.rates = {src: sorted(series) for src, series in rates.items()}
        self._dates = {src: [d for d, _ in series] for src, series in self.rates.items()}

    @classmethod
    def generate(
        cls, rng: random.Random, base: str, foreign: list[str], start: date, end: date
    ) -> FxTable:
        rates: dict[str, list[tuple[date, Decimal]]] = {}
        days = []
        d = start
        while d <= end:
            if is_business_day(d):
                days.append(d)
            d += timedelta(days=1)
        for src in foreign:
            with localcontext() as ctx:
                ctx.prec = 40
                level = USD_VALUE[src] / USD_VALUE[base]
                level *= 1 + Decimal(rng.randint(-300, 300)) / 10000
                level = round_sig(level)
                series = []
                for day in days:
                    level = round_sig(level * (1 + Decimal(rng.randint(-70, 70)) / 10000))
                    series.append((day, level))
            rates[src] = series
        return cls(base, rates)

    @classmethod
    def from_rows(cls, base: str, rows: list[dict]) -> FxTable:
        rates: dict[str, list[tuple[date, Decimal]]] = {}
        for row in rows:
            if row["target_currency"] != base:
                continue
            rates.setdefault(row["source_currency"], []).append(
                (date.fromisoformat(row["date"]), Decimal(row["rate"]))
            )
        return cls(base, rates)

    def dates(self, src: str) -> list[date]:
        return self._dates[src]

    def lookup(self, src: str, day: date) -> tuple[date | None, Decimal]:
        """Rate valid for ``day``: the last published rate on or before it."""
        if src == self.base:
            return None, Decimal(1)
        dates = self._dates[src]
        i = bisect.bisect_right(dates, day) - 1
        if i < 0:
            raise KeyError(f"no {src}/{self.base} rate on or before {day}")
        return self.rates[src][i]

    def rows(self) -> list[dict]:
        out = []
        for src in sorted(self.rates):
            for d, r in self.rates[src]:
                out.append(
                    {
                        "date": d.isoformat(),
                        "source_currency": src,
                        "target_currency": self.base,
                        "rate": rate_str(r),
                    }
                )
        out.sort(key=lambda r: (r["date"], r["source_currency"]))
        return out
