"""Money helpers. Amounts are integer minor units; rates are Decimal, never float."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

EXPONENTS = {"KZT": 2, "USD": 2, "EUR": 2, "JPY": 0}

# Rough value of one major unit in USD; used only to seed FX levels and price ranges.
USD_VALUE = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.09"),
    "KZT": Decimal("0.00202"),
    "JPY": Decimal("0.00658"),
}

ROUNDING_MODES = ("ROUND_HALF_EVEN", "ROUND_HALF_UP")


def convert(amount_minor: int, rate: Decimal, src: str, dst: str, rounding: str) -> int:
    """Convert ``amount_minor`` of ``src`` into minor units of ``dst``.

    ``rate`` is the price of one major unit of ``src`` in major units of ``dst``.
    The result is rounded once, to a whole minor unit of ``dst``, with ``rounding``.
    """
    if src == dst:
        return int(amount_minor)
    with localcontext() as ctx:
        ctx.prec = 60
        scaled = Decimal(amount_minor) * rate * (Decimal(10) ** (EXPONENTS[dst] - EXPONENTS[src]))
        return int(scaled.quantize(Decimal(1), rounding=rounding))


def round_sig(x: Decimal, sig: int = 8) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return x.quantize(Decimal(1).scaleb(x.adjusted() - sig + 1), rounding=ROUND_HALF_EVEN)


def rate_str(x: Decimal) -> str:
    return format(x, "f")
