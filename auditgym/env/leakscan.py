"""Leak scan: does anything the agent can see reveal the grader's answer?

Used by the env tests and by the runner after writing trajectories. Given an observation /
info / trajectory line and the episode's private part, it reports:

* forbidden keys anywhere (``seed``, ``issue_id``, ``expected_amount_minor``, ...);
* forbidden words in any key or string (``truth``, ``provenance``, ``seed``, ``private/``);
* any issue_id from ``truth.json`` in any string;
* the seed or any ``expected_amount_minor`` as a number in ``info`` or in an error object —
  places where no record data is ever expected.

Public records themselves (``result.items``, ``result.record``, ``result.document``) may
legitimately contain the same amounts as the answer (e.g. a duplicate invoice's total), so
numbers are not checked inside them; words and IDs are.
"""

from __future__ import annotations

FORBIDDEN_KEYS = frozenset({
    "seed", "issue_id", "issues", "issue_count", "expected_amount_minor", "evidence_sets",
    "tolerance_minor", "truth", "provenance", "ledger_diff", "pre_injection",
    "generator_version", "attempt",
})
FORBIDDEN_WORDS = ("truth", "provenance", "seed", "private/", "private\\", "issue_id")
_RECORD_KEYS = frozenset({"items", "record", "document"})


def _walk(value, path, in_record, visit):
    visit(path, value, in_record)
    if isinstance(value, dict):
        for k, v in value.items():
            visit(path + (k,), k, in_record, is_key=True)
            _walk(v, path + (k,), in_record or k in _RECORD_KEYS, visit)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk(v, path + (i,), in_record, visit)


def scan(payload, private: dict, *, seed: int | None = None, where: str = "observation"
         ) -> list[str]:
    """Return a list of problems (empty = clean). ``where`` is ``observation`` or ``info``."""
    truth = private.get("truth", {}) if private else {}
    issue_ids = [i["issue_id"] for i in truth.get("issues", [])]
    amounts = {i["expected_amount_minor"] for i in truth.get("issues", [])}
    if seed is None and private:
        seed = private.get("provenance", {}).get("seed")
    problems: list[str] = []

    def visit(path, value, in_record, is_key=False):
        loc = "/".join(str(p) for p in path) or "<root>"
        if is_key:
            if isinstance(value, str) and value.lower() in FORBIDDEN_KEYS:
                problems.append(f"{where}:{loc}: forbidden key {value!r}")
            return
        if isinstance(value, str):
            low = value.lower()
            for word in FORBIDDEN_WORDS:
                if word in low:
                    problems.append(f"{where}:{loc}: forbidden word {word!r}")
            for iid in issue_ids:
                if iid in value:
                    problems.append(f"{where}:{loc}: issue id {iid!r}")
        elif isinstance(value, int) and not isinstance(value, bool) and not in_record:
            # info and error objects never carry record data, so no number there may equal
            # the seed or an expected amount.
            if where == "info" or "error" in path:
                if seed is not None and value == seed:
                    problems.append(f"{where}:{loc}: seed value")
                if value in amounts:
                    problems.append(f"{where}:{loc}: expected amount")

    _walk(payload, (), False, visit)
    return problems
