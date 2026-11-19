"""Hidden company history.

Everything the builder creates is *correct* by construction: documents agree with
each other and with the booking rules in ``policy.json``. Errors are injected later
(``auditgym.generator.errors``) by mutating this state; every mutation is recorded.

Internal document fields start with ``_`` and never reach public files:
``_book`` holds booking parameters (booked?, ledger timestamp override, FX date override),
``_fmt`` says whether timestamps are written in local time or UTC.
"""

from __future__ import annotations

import copy
import random
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from types import SimpleNamespace

from .config import DISTRACTOR_KINDS, ERROR_TYPES, TIMEZONES, sample_count
from .fx import FxTable
from .ids import IdFactory
from .money import EXPONENTS, ROUNDING_MODES, USD_VALUE
from .timeutil import parse_tz

PRODUCTS = (
    "Cloud hosting",
    "Support hours",
    "Hardware lease",
    "Training session",
    "Software licence",
    "Logistics service",
    "Data migration",
    "Consulting day",
    "Maintenance plan",
    "Security review",
)
SYLLABLES = ("nor", "tal", "ver", "ka", "dun", "ilo", "mar", "sen", "qua", "bri", "zel", "oru", "fen")
SUFFIXES = ("LLP", "Ltd", "Group", "Trading", "Systems", "Holdings")
BG_EXPENSES = (
    "expense:rent",
    "expense:utilities",
    "expense:software",
    "expense:bank_fees",
    "expense:travel",
    "expense:office",
    "expense:marketing",
)


class RetryGeneration(Exception):
    """A random draw could not satisfy a constraint; the generator retries deterministically."""


class Builder:
    def __init__(self, cfg: dict, rng: random.Random, episode_id: str):
        self.cfg = cfg
        self.rng = rng
        self.episode_id = episode_id
        self.ids = IdFactory(rng)
        self.invoices: list[dict] = []
        self.payments: list[dict] = []
        self.background: list[dict] = []
        self.targets: list[dict] = []
        self.distractor_log: list[dict] = []
        self._refs: set[str] = set()
        self._sample_plan()
        self._init_company()

    # ------------------------------------------------------------------ setup
    def _sample_plan(self) -> None:
        cfg, rng = self.cfg, self.rng
        if cfg["clean"]:
            errors = {t: 0 for t in ERROR_TYPES}
            inter = {"A": 0, "B": 0}
        else:
            errors = {t: sample_count(rng, cfg["error_counts"].get(t, 0)) for t in ERROR_TYPES}
            inter = {k: sample_count(rng, cfg["interactions"].get(k, 0)) for k in ("A", "B")}
        distractors = {k: sample_count(rng, cfg["distractors"].get(k, 0)) for k in DISTRACTOR_KINDS}
        self.plan = {"error_counts": errors, "interactions": inter, "distractors": distractors}

    @property
    def needs_foreign(self) -> bool:
        p = self.plan
        return p["error_counts"]["fx_conversion"] + p["interactions"]["A"] + p["interactions"]["B"] > 0

    def _init_company(self) -> None:
        cfg, rng = self.cfg, self.rng
        currencies = list(cfg["currencies"])
        if cfg["base_currency"]:
            base = cfg["base_currency"]
        else:
            base = rng.choice([c for c in currencies if c != "JPY"] or currencies)
        pool = [c for c in currencies if c != base]
        n_foreign = sample_count(rng, cfg["n_foreign_currencies"])
        if self.needs_foreign:
            n_foreign = max(1, n_foreign)
        n_foreign = min(n_foreign, len(pool))
        self.base = base
        self.foreign = sorted(rng.sample(pool, n_foreign))

        self.tz_str = cfg["timezone"] or rng.choice(TIMEZONES)
        self.tz = parse_tz(self.tz_str)
        if cfg["period"]:
            year, month = (int(x) for x in cfg["period"].split("-"))
        else:
            year, month = rng.choice((2025, 2026)), rng.randint(1, 12)
        self.start = datetime(year, month, 1, tzinfo=self.tz)
        self.end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=self.tz)
        self.rounding = rng.choice(ROUNDING_MODES)
        self.fx = FxTable.generate(
            rng,
            base,
            self.foreign,
            (self.start - timedelta(days=45)).date(),
            (self.end + timedelta(days=8)).date(),
        )
        name = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3))).capitalize()
        self.company_name = f"{name} {rng.choice(SUFFIXES)}"
        n_entities = max(4, cfg["n_documents"] // 5)
        self.entities = [self.ids.new("entity") for _ in range(n_entities)]

    # ---------------------------------------------------------------- helpers
    def in_period(self, dt: datetime) -> bool:
        return self.start <= dt < self.end

    def local_date(self, dt: datetime) -> date:
        return dt.astimezone(self.tz).date()

    def day_start(self, d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=self.tz)

    def rand_dt(self, lo: datetime, hi: datetime) -> datetime:
        span = int((hi - lo).total_seconds())
        if span <= 0:
            raise RetryGeneration("empty time window")
        return lo + timedelta(seconds=self.rng.randrange(span))

    def offset_minutes(self) -> int:
        return int(self.tz.utcoffset(None).total_seconds() // 60)

    def utc_shift_window(self, d: date) -> tuple[datetime, datetime] | None:
        """Local times on day ``d`` whose UTC calendar date differs from the local one."""
        off = self.offset_minutes()
        if off > 0:
            w = min(off, 240)
            s = self.day_start(d)
            return s + timedelta(minutes=5), s + timedelta(minutes=w - 5)
        if off < 0:
            w = min(-off, 240)
            nxt = self.day_start(d) + timedelta(days=1)
            return nxt - timedelta(minutes=w - 5), nxt - timedelta(minutes=5)
        return None

    def pick_currency(self, foreign: bool | None = None) -> str:
        if foreign is True:
            return self.rng.choice(self.foreign)
        if foreign is False or not self.foreign:
            return self.base
        if self.rng.random() < self.cfg["foreign_invoice_share"]:
            return self.rng.choice(self.foreign)
        return self.base

    def new_ref(self) -> str:
        while True:
            ref = f"PO-{self.rng.randrange(10000, 100000)}"
            if ref not in self._refs:
                self._refs.add(ref)
                return ref

    def unit_price(self, ccy: str) -> int:
        usd = Decimal(self.rng.randint(15, 2400))
        major = usd / USD_VALUE[ccy]
        step = Decimal(100) if major >= 1000 else Decimal(10) if major >= 100 else Decimal(1)
        major = max(Decimal(1), (major / step).to_integral_value(rounding=ROUND_HALF_EVEN) * step)
        return int(major * (10 ** EXPONENTS[ccy]))

    def line_items(self, ccy: str, n: int | None = None) -> list[dict]:
        items = []
        for _ in range(n or self.rng.randint(1, 4)):
            qty = self.rng.randint(1, 12)
            unit = self.unit_price(ccy)
            items.append(
                {
                    "description": f"{self.rng.choice(PRODUCTS)} · {self.new_ref()}",
                    "quantity": qty,
                    "unit_price_minor": unit,
                    "amount_minor": qty * unit,
                }
            )
        return items

    def new_invoice(
        self,
        issued_at: datetime,
        *,
        entity: str | None = None,
        currency: str | None = None,
        items: list[dict] | None = None,
        booked: bool | None = None,
        fmt: str | None = None,
        term_days: int | None = None,
    ) -> dict:
        currency = currency or self.pick_currency()
        items = items if items is not None else self.line_items(currency)
        term = term_days or self.rng.choice((7, 14, 30, 45))
        inv = {
            "doc_id": self.ids.new("doc"),
            "invoice_id": self.ids.new("invoice"),
            "entity_id": entity or self.rng.choice(self.entities),
            "issued_at": issued_at,
            "due_at": issued_at + timedelta(days=term),
            "currency": currency,
            "total_minor": sum(i["amount_minor"] for i in items),
            "line_items": items,
            "_fmt": fmt or self.rng.choice(("local", "local", "utc")),
            "_book": {
                "booked": self.in_period(issued_at) if booked is None else booked,
                "ledger_ts": None,
                "rate_date": None,
            },
        }
        self.invoices.append(inv)
        return inv

    def new_payment(
        self,
        ts: datetime,
        *,
        entity: str,
        currency: str,
        amount: int,
        refs: list[tuple[dict, int]],
        kind: str = "receipt",
        refund_of: str | None = None,
        booked: bool | None = None,
        fmt: str | None = None,
    ) -> dict:
        pay = {
            "doc_id": self.ids.new("doc"),
            "payment_id": self.ids.new("payment"),
            "entity_id": entity,
            "timestamp": ts,
            "currency": currency,
            "amount_minor": amount,
            "kind": kind,
            "invoice_refs": [{"invoice_id": inv["invoice_id"], "applied_minor": a} for inv, a in refs],
            "refund_of": refund_of,
            "_fmt": fmt or self.rng.choice(("local", "local", "utc")),
            "_book": {
                "booked": self.in_period(ts) if booked is None else booked,
                "ledger_ts": None,
                "rate_date": None,
            },
        }
        self.payments.append(pay)
        return pay

    def pay_in_full(self, inv: dict, ts: datetime, **kw) -> dict:
        return self.new_payment(
            ts,
            entity=inv["entity_id"],
            currency=inv["currency"],
            amount=inv["total_minor"],
            refs=[(inv, inv["total_minor"])],
            **kw,
        )

    def n_docs(self) -> int:
        return len(self.invoices) + len(self.payments)

    # ------------------------------------------------------------ regular flows
    def flow_paid(self, foreign: bool | None = None) -> tuple[dict, dict]:
        issued = self.rand_dt(self.start, self.end - timedelta(days=2))
        inv = self.new_invoice(issued, currency=self.pick_currency(foreign))
        pay = self.pay_in_full(inv, self.rand_dt(issued + timedelta(hours=2), self.end - timedelta(minutes=5)))
        return inv, pay

    def flow_open(self, foreign: bool | None = None) -> dict:
        return self.new_invoice(self.rand_dt(self.start, self.end), currency=self.pick_currency(foreign))

    def flow_prior_paid(self) -> tuple[dict, dict]:
        issued = self.rand_dt(self.start - timedelta(days=35), self.start - timedelta(days=3))
        inv = self.new_invoice(issued)
        pay = self.pay_in_full(inv, self.rand_dt(self.start, self.end - timedelta(minutes=5)))
        return inv, pay

    def flow_next_invoice(self) -> dict:
        return self.new_invoice(self.rand_dt(self.end + timedelta(hours=1), self.end + timedelta(days=5)))

    # ------------------------------------------------------- error targets
    # Each creates a *correct* flow and reserves it for an injector.
    def _target_duplicate_invoice(self) -> None:
        issued = self.rand_dt(self.start, self.end - timedelta(days=4))
        inv = self.new_invoice(issued)
        if self.rng.random() < 0.5:
            self.pay_in_full(inv, self.rand_dt(issued + timedelta(hours=2), self.end - timedelta(minutes=5)))
        self.targets.append({"kind": "duplicate_invoice", "invoice": inv})

    def _target_payment_allocation(self) -> None:
        issued = self.rand_dt(self.start, self.end - timedelta(days=3))
        inv = self.new_invoice(issued)
        pay = self.pay_in_full(inv, self.rand_dt(issued + timedelta(hours=2), self.end - timedelta(minutes=5)))
        mode = self.rng.choice(("missing", "partial"))
        self.targets.append({"kind": "payment_allocation", "invoice": inv, "payment": pay, "mode": mode})

    def _fx_target_time(self) -> tuple[datetime, str]:
        """Random in-period time; half the time just after/before local midnight, written in UTC."""
        day = (self.start + timedelta(days=self.rng.randrange((self.end - self.start).days))).date()
        window = self.utc_shift_window(day)
        if window and self.rng.random() < 0.5:
            ts = self.rand_dt(*window)
            if self.in_period(ts):
                return ts, "utc"
        return self.rand_dt(self.day_start(day), self.day_start(day) + timedelta(days=1)), self.rng.choice(
            ("local", "utc")
        )

    def _target_fx_conversion(self) -> None:
        ts, fmt = self._fx_target_time()
        ccy = self.pick_currency(True)
        if self.rng.random() < 0.5:
            inv = self.new_invoice(ts, currency=ccy, fmt=fmt)  # stays unpaid
            self.targets.append({"kind": "fx_conversion", "doc": inv, "table": "invoices"})
        else:
            if ts - self.start < timedelta(hours=6):
                ts = self.start + timedelta(hours=6)
            inv = self.new_invoice(self.rand_dt(self.start - timedelta(days=20), ts - timedelta(hours=2)), currency=ccy)
            pay = self.pay_in_full(inv, ts, fmt=fmt)
            self.targets.append({"kind": "fx_conversion", "doc": pay, "table": "payments"})

    def _target_period_cutoff(self) -> None:
        variants = ["early", "late"]
        if self.offset_minutes() > 0:
            variants.append("early_tz")
        variant = self.rng.choice(variants)
        if variant == "early_tz":
            window = self.utc_shift_window(self.end.date())
            inv = self.new_invoice(self.rand_dt(*window), fmt="utc", booked=False)
        elif variant == "early":
            inv = self.new_invoice(self.rand_dt(self.end + timedelta(hours=1), self.end + timedelta(days=3)), booked=False)
        else:
            inv = self.new_invoice(self.rand_dt(self.start - timedelta(days=20), self.start - timedelta(days=1)), booked=False)
        self.targets.append({"kind": "period_cutoff", "doc": inv, "table": "invoices", "variant": variant})

    def _target_A(self) -> None:
        ccy = self.pick_currency(True)
        inv = self.new_invoice(self.rand_dt(self.start, self.end - timedelta(days=5)), currency=ccy)
        window = self.utc_shift_window(self.end.date()) if self.offset_minutes() > 0 else None
        if window:
            ts = self.rand_dt(*window)
        else:
            ts = self.rand_dt(self.end + timedelta(minutes=5), self.end + timedelta(hours=3))
        pay = self.pay_in_full(inv, ts, fmt="utc", booked=False)  # next period: correctly not booked
        self.targets.append({"kind": "A", "invoice": inv, "payment": pay})

    def _target_B(self) -> None:
        ccy = self.pick_currency(True)
        inv = self.new_invoice(self.rand_dt(self.start, self.end - timedelta(days=8)), currency=ccy)
        self.targets.append({"kind": "B", "invoice": inv})

    # ------------------------------------------------------------ distractors
    def _log(self, kind: str, docs: list[dict]) -> None:
        ids = []
        for d in docs:
            ids.append(f"invoices:{d['invoice_id']}" if "invoice_id" in d and "payment_id" not in d else f"payments:{d['payment_id']}")
        self.distractor_log.append({"kind": kind, "ids": ids})

    def _distractor_split_payment(self) -> None:
        issued = self.rand_dt(self.start, self.end - timedelta(days=6))
        inv = self.new_invoice(issued)
        k = self.rng.randint(2, 3)
        total = inv["total_minor"]
        cuts = sorted(self.rng.sample(range(1, total), k - 1))
        parts = [b - a for a, b in zip([0] + cuts, cuts + [total])]
        ts = issued
        docs = [inv]
        for part in parts:
            ts = self.rand_dt(ts + timedelta(hours=1), ts + (self.end - ts) / 2)
            docs.append(
                self.new_payment(ts, entity=inv["entity_id"], currency=inv["currency"], amount=part, refs=[(inv, part)])
            )
        self._log("split_payment", docs)

    def _distractor_partial_payment(self) -> None:
        issued = self.rand_dt(self.start, self.end - timedelta(days=3))
        inv = self.new_invoice(issued)
        part = max(1, inv["total_minor"] * self.rng.randint(30, 80) // 100)
        pay = self.new_payment(
            self.rand_dt(issued + timedelta(hours=2), self.end - timedelta(minutes=5)),
            entity=inv["entity_id"],
            currency=inv["currency"],
            amount=part,
            refs=[(inv, part)],
        )
        self._log("partial_payment", [inv, pay])

    def _distractor_recurring(self) -> None:
        entity = self.rng.choice(self.entities)
        ccy = self.pick_currency()
        product = self.rng.choice(PRODUCTS)
        qty, unit = self.rng.randint(1, 5), self.unit_price(ccy)
        n = self.rng.randint(2, 4)
        first = self.rand_dt(self.start, self.start + timedelta(days=6))
        docs = []
        for i in range(n):
            planned = first + timedelta(days=7 * i)
            issued = planned + timedelta(minutes=self.rng.randint(-90, 90))
            if not self.in_period(issued):
                break
            week_of = planned.astimezone(self.tz).date().isoformat()
            items = [
                {
                    "description": f"{product} · service week of {week_of}",
                    "quantity": qty,
                    "unit_price_minor": unit,
                    "amount_minor": qty * unit,
                }
            ]
            docs.append(self.new_invoice(issued, entity=entity, currency=ccy, items=items, term_days=14))
        self._log("recurring", docs)

    def _distractor_refund(self) -> None:
        issued = self.rand_dt(self.start, self.end - timedelta(days=8))
        inv = self.new_invoice(issued)
        t1 = self.rand_dt(issued + timedelta(hours=2), issued + timedelta(days=2))
        p1 = self.pay_in_full(inv, t1)
        t2 = self.rand_dt(t1 + timedelta(hours=1), t1 + timedelta(days=2))
        p2 = self.new_payment(t2, entity=inv["entity_id"], currency=inv["currency"], amount=inv["total_minor"], refs=[])
        t3 = self.rand_dt(t2 + timedelta(hours=4), t2 + timedelta(days=3))
        p3 = self.new_payment(
            t3,
            entity=inv["entity_id"],
            currency=inv["currency"],
            amount=-inv["total_minor"],
            refs=[],
            kind="refund",
            refund_of=p2["payment_id"],
        )
        self._log("refund", [inv, p1, p2, p3])

    def _distractor_boundary(self) -> None:
        if self.rng.random() < 0.5:
            ts = self.rand_dt(self.end - timedelta(minutes=40), self.end - timedelta(minutes=1))
        else:
            ts = self.rand_dt(self.start + timedelta(minutes=1), self.start + timedelta(minutes=40))
        inv = self.new_invoice(ts, fmt="utc")
        self._log("boundary", [inv])

    def _distractor_next_period_invoice(self) -> None:
        self._log("next_period_invoice", [self.flow_next_invoice()])

    def _distractor_weekend_fx(self) -> None:
        if not self.foreign:
            return
        weekend = [
            (self.start + timedelta(days=i)).date()
            for i in range((self.end - self.start).days)
            if (self.start + timedelta(days=i)).weekday() >= 5
        ]
        day = self.rng.choice(weekend)
        ts = self.rand_dt(self.day_start(day) + timedelta(hours=9), self.day_start(day) + timedelta(hours=18))
        self._log("weekend_fx", [self.new_invoice(ts, currency=self.pick_currency(True))])

    def _distractor_prior_invoice(self) -> None:
        if self.rng.random() < 0.6:
            inv, pay = self.flow_prior_paid()
            self._log("prior_invoice", [inv, pay])
        else:
            inv = self.new_invoice(self.rand_dt(self.start - timedelta(days=35), self.start - timedelta(days=2)))
            self._log("prior_invoice", [inv])

    # ------------------------------------------------------------------ build
    def build_documents(self) -> None:
        p = self.plan
        for etype in ERROR_TYPES:
            for _ in range(p["error_counts"][etype]):
                getattr(self, f"_target_{etype}")()
        for _ in range(p["interactions"]["A"]):
            self._target_A()
        for _ in range(p["interactions"]["B"]):
            self._target_B()
        for kind in DISTRACTOR_KINDS:
            for _ in range(p["distractors"][kind]):
                getattr(self, f"_distractor_{kind}")()
        # documents that injectors will add later
        reserved = p["error_counts"]["duplicate_invoice"] + 2 * p["interactions"]["B"]
        while True:
            remaining = self.cfg["n_documents"] - self.n_docs() - reserved
            if remaining <= 0:
                break
            if remaining == 1:
                self.flow_open()
                continue
            r = self.rng.random()
            if r < 0.45:
                self.flow_paid()
            elif r < 0.75:
                self.flow_open()
            elif r < 0.92:
                self.flow_prior_paid()
            else:
                self.flow_next_invoice()

    def bg_amount(self) -> int:
        usd = Decimal(self.rng.randint(20, 6000))
        return int(usd / USD_VALUE[self.base] * (10 ** EXPONENTS[self.base]))

    def add_background(self, n_rows: int) -> None:
        """Routine entries without documents (rent, payroll, fees) to reach the ledger size."""
        remaining = max(0, n_rows)
        if remaining == 1:
            remaining = 3
        bank = f"bank:{self.base}"
        while remaining > 0:
            ts = self.rand_dt(self.start, self.end)
            amount = self.bg_amount()
            if remaining % 2 == 1:
                tax = amount * self.rng.randint(8, 12) // 100
                rows = [("expense:payroll", amount, 0), (bank, 0, amount - tax), ("payroll_tax_payable", 0, tax)]
                remaining -= 3
            elif self.rng.random() < 0.12:
                rows = [(bank, amount, 0), ("other_income", 0, amount)]
                remaining -= 2
            else:
                rows = [(self.rng.choice(BG_EXPENSES), amount, 0), (bank, 0, amount)]
                remaining -= 2
            self.background.append({"ts": ts, "rows": rows})

    def snapshot(self) -> SimpleNamespace:
        """Frozen copy of the (still correct) history; background is attached later."""
        return SimpleNamespace(
            episode_id=self.episode_id,
            company_name=self.company_name,
            base=self.base,
            foreign=self.foreign,
            tz=self.tz,
            tz_str=self.tz_str,
            start=self.start,
            end=self.end,
            rounding=self.rounding,
            fx=self.fx,
            invoices=copy.deepcopy(self.invoices),
            payments=copy.deepcopy(self.payments),
            background=self.background,
        )
