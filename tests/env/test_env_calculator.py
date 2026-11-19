"""The calculator evaluates arithmetic only — never code, names, files or huge powers."""

from __future__ import annotations

import time

import pytest

from auditgym.env.calculator import MAX_DEPTH, CalcError, calculate


@pytest.mark.parametrize("expr, value", [
    ("1+1", "2"),
    ("2 + 3 * 4", "14"),
    ("(2 + 3) * 4", "20"),
    ("10 / 4", "2.5"),
    ("1 / 3", "0.33333333333333333333333333333333333333333333333333"),
    ("-5 + 2", "-3"),
    ("--5", "5"),
    ("+-+2", "-2"),
    ("0.1 + 0.2", "0.3"),
    (".5 * 2", "1"),
    ("1500 * 470.25", "705375"),
    ("123456789012 * 3.1989425", "394931169284.01981"),
    ("100 - 100", "0"),
    ("2.50", "2.5"),
    ("  7  ", "7"),
    ("1000000 * 1000000", "1000000000000"),
])
def test_arithmetic(expr, value):
    assert calculate(expr) == value


def test_decimal_not_float():
    assert calculate("0.1 * 3") == "0.3"
    assert calculate("1.005 * 1000") == "1005"


@pytest.mark.parametrize("expr", [
    "__import__('os').system('ls')",
    "import os",
    "open('truth.json')",
    "x + 1",
    "abs(-1)",
    "2 ** 10",
    "2**99999999",
    "9 ** 9 ** 9",
    "7 % 2",
    "7 // 2",
    "1e9",
    "1_000",
    "1,5",
    "0x10",
    "[1]",
    "1; 2",
    "",
    "   ",
    "()",
    "(1 + 2",
    "1 + 2)",
    "1 +",
    "* 2",
    "1 2",
    "1..2",
    "1.",
    ".",
    "١٢",  # non-ASCII digits
    "1 / 0",
    "1 / (2 - 2)",
    "0 / 0",
])
def test_rejected(expr):
    with pytest.raises(CalcError):
        calculate(expr)


def test_not_a_string():
    for value in (None, 5, ["1+1"], {"e": "1"}):
        with pytest.raises(CalcError):
            calculate(value)


def test_limits_are_cheap():
    start = time.perf_counter()
    cases = [
        "1" * 33,                                   # number too long
        "1+" * 200 + "1",                           # too long / too many tokens
        "(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1),
        "-" * (MAX_DEPTH + 1) + "1",
        "*".join(["99999999999999999999999999999999"] * 3),  # overflow
    ]
    for expr in cases:
        with pytest.raises(CalcError):
            calculate(expr)
    assert time.perf_counter() - start < 1.0


def test_max_depth_allowed():
    expr = "(" * MAX_DEPTH + "1" + ")" * MAX_DEPTH
    assert calculate(expr) == "1"


def test_messages_do_not_echo_input():
    try:
        calculate("secret_name + 1")
    except CalcError as exc:
        assert "secret" not in str(exc)
