"""Safe decimal calculator for the ``calculator`` action.

Grammar (whitespace is ignored)::

    expr    := term (("+" | "-") term)*
    term    := unary (("*" | "/") unary)*
    unary   := ("+" | "-") unary | primary
    primary := NUMBER | "(" expr ")"
    NUMBER  := digits ["." digits] | "." digits

It is a hand-written recursive-descent parser over ``decimal.Decimal``. There is no
``eval``/``exec``/``ast``: names, attributes, calls, ``**``, ``%``, ``//``, exponents
(``1e9``), underscores and every other character are rejected by the tokenizer, before
anything is evaluated. Length, token count, number length and nesting depth are bounded,
and the decimal context traps overflow, so no input can make the calculator slow or huge.
"""

from __future__ import annotations

from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

MAX_EXPRESSION_LEN = 256
MAX_TOKENS = 128
MAX_NUMBER_LEN = 32
MAX_DEPTH = 32
PRECISION = 50
MAX_EXPONENT = 60

_OPERATORS = set("+-*/()")
_DIGITS = set("0123456789")


class CalcError(ValueError):
    """Expression rejected or not computable. The message is a fixed, short string."""


def _context() -> Context:
    return Context(
        prec=PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emax=MAX_EXPONENT,
        Emin=-MAX_EXPONENT,
        traps=[DivisionByZero, InvalidOperation, Overflow],
    )


def tokenize(expression: str) -> list[str]:
    if not isinstance(expression, str):
        raise CalcError("expression must be a string")
    if len(expression) > MAX_EXPRESSION_LEN:
        raise CalcError(f"expression is longer than {MAX_EXPRESSION_LEN} characters")
    tokens: list[str] = []
    i, n = 0, len(expression)
    while i < n:
        ch = expression[i]
        if ch in " \t":
            i += 1
            continue
        if ch in _OPERATORS:
            tokens.append(ch)
            i += 1
        elif ch in _DIGITS or ch == ".":
            j = i
            while j < n and expression[j] in _DIGITS:
                j += 1
            if j < n and expression[j] == ".":
                j += 1
                k = j
                while j < n and expression[j] in _DIGITS:
                    j += 1
                if j == k:
                    raise CalcError("malformed number")
            number = expression[i:j]
            if number == ".":
                raise CalcError("malformed number")
            if len(number) > MAX_NUMBER_LEN:
                raise CalcError(f"numbers are limited to {MAX_NUMBER_LEN} characters")
            tokens.append(number)
            i = j
        else:
            raise CalcError("only numbers, + - * / and parentheses are allowed")
        if len(tokens) > MAX_TOKENS:
            raise CalcError(f"expression has more than {MAX_TOKENS} tokens")
    if not tokens:
        raise CalcError("empty expression")
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.peek()
        if tok is None:
            raise CalcError("unexpected end of expression")
        self.pos += 1
        return tok

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalcError(f"nesting deeper than {MAX_DEPTH}")

    def expr(self) -> Decimal:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self) -> Decimal:
        value = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            rhs = self.unary()
            if op == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise CalcError("division by zero")
                value = value / rhs
        return value

    def unary(self) -> Decimal:
        if self.peek() in ("+", "-"):
            op = self.take()
            self.enter()
            try:
                value = self.unary()
            finally:
                self.depth -= 1
            return -value if op == "-" else +value
        return self.primary()

    def primary(self) -> Decimal:
        tok = self.take()
        if tok == "(":
            self.enter()
            try:
                value = self.expr()
            finally:
                self.depth -= 1
            if self.take() != ")":
                raise CalcError("missing closing parenthesis")
            return value
        if tok in _OPERATORS:
            raise CalcError("unexpected operator")
        return Decimal(tok)


def format_decimal(value: Decimal) -> str:
    """Plain decimal notation, no exponent, no trailing zeros (``"1200"``, ``"0.5"``)."""
    if value == 0:
        return "0"
    text = format(value.normalize(_context()), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def evaluate(expression: str) -> Decimal:
    tokens = tokenize(expression)
    with localcontext(_context()):
        parser = _Parser(tokens)
        try:
            value = parser.expr()
        except DivisionByZero as exc:
            raise CalcError("division by zero") from exc
        except Overflow as exc:
            raise CalcError("result is too large") from exc
        except InvalidOperation as exc:
            raise CalcError("invalid arithmetic") from exc
        except RecursionError as exc:  # defensive; MAX_DEPTH keeps us far from it
            raise CalcError(f"nesting deeper than {MAX_DEPTH}") from exc
        if parser.peek() is not None:
            raise CalcError("unexpected token after expression")
        return +value  # apply context rounding


def calculate(expression: str) -> str:
    """Evaluate ``expression`` and return the result as a decimal string."""
    return format_decimal(evaluate(expression))
