"""Random, unique, meaning-free identifiers.

IDs are drawn from the seeded RNG and never encode the role of a record
(no ``INV-DUP``, no sequential numbers that reveal insertion order).
"""

from __future__ import annotations

import random

ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# prefix -> random body length
ID_FORMATS = {
    "invoice": ("INV-", 6),
    "payment": ("PAY-", 6),
    "doc": ("DOC-", 8),
    "entity": ("C-", 5),
    "row": ("L", 7),
}


class IdFactory:
    def __init__(self, rng: random.Random, used: set[str] | None = None):
        self.rng = rng
        self.used: set[str] = set() if used is None else used

    def new(self, kind: str) -> str:
        prefix, n = ID_FORMATS[kind]
        while True:
            value = prefix + "".join(self.rng.choice(ALNUM) for _ in range(n))
            if value not in self.used:
                self.used.add(value)
                return value
