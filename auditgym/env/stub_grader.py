"""TEMPORARY stand-in for the real grader (``auditgym.grader``, work in progress).

It exists only so the environment can be run end to end before the real grader lands:
``sample episode -> env -> oracle -> grader = 1.0``. It follows the spec rules in a simplified
way (greedy matching instead of maximum matching, minimal normalisation). As soon as
``auditgym.grader.Grader`` exists, :func:`auditgym.env.grading.default_grader` picks it up
instead of this class.

Grader contract expected by the environment::

    grader.score(findings, *, truth: dict, public: dict, inspection_steps: int)
        -> dict | dataclass with at least ``reward`` (float in [-1, 1])
           and optionally precision, recall, f1, tp, fp, fn, errors
"""

from __future__ import annotations

TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
MAX_FINDINGS = 30
MAX_EVIDENCE = 10
MAX_EXPLANATION = 1000
STEP_COST = 0.002


def _norm_id(value: str) -> str:
    value = value.strip()
    if ":" in value:
        prefix, _, rest = value.partition(":")
        return f"{prefix.strip().lower()}:{rest.strip()}"
    return value


def _invalid(errors: list[str]) -> dict:
    return {"reward": -1.0, "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "tp": 0, "fp": 0, "fn": 0, "errors": errors}


def _validate(findings, episode_id: str, currencies: set[str]) -> list[str]:
    if not isinstance(findings, dict):
        return ["findings must be an object"]
    if findings.get("episode_id") != episode_id:
        return ["episode_id does not match"]
    items = findings.get("findings")
    if not isinstance(items, list):
        return ["findings.findings must be a list"]
    if len(items) > MAX_FINDINGS:
        return [f"more than {MAX_FINDINGS} findings"]
    for n, f in enumerate(items):
        if not isinstance(f, dict):
            return [f"finding {n}: must be an object"]
        if f.get("type") not in TYPES:
            return [f"finding {n}: unknown type"]
        ids = f.get("affected_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            return [f"finding {n}: affected_ids must be a list of strings"]
        amount = f.get("claimed_amount_minor")
        if isinstance(amount, bool) or not isinstance(amount, int):
            return [f"finding {n}: claimed_amount_minor must be an integer"]
        cur = f.get("currency")
        if not isinstance(cur, str) or cur.strip().upper() not in currencies:
            return [f"finding {n}: unknown currency"]
        ev = f.get("evidence_ids")
        if not isinstance(ev, list) or not all(isinstance(x, str) for x in ev):
            return [f"finding {n}: evidence_ids must be a list of strings"]
        if len(ev) > MAX_EVIDENCE:
            return [f"finding {n}: more than {MAX_EVIDENCE} evidence ids"]
        expl = f.get("explanation", "")
        if not isinstance(expl, str) or len(expl) > MAX_EXPLANATION:
            return [f"finding {n}: explanation must be a string of at most {MAX_EXPLANATION}"]
    return []


def _compatible(f: dict, issue: dict) -> bool:
    if f["type"] != issue["type"]:
        return False
    if f["currency"].strip().upper() != issue["currency"]:
        return False
    if sorted({_norm_id(x) for x in f["affected_ids"]}) != sorted(issue["affected_ids"]):
        return False
    tol = issue.get("tolerance_minor", 0)
    if abs(f["claimed_amount_minor"] - issue["expected_amount_minor"]) > tol:
        return False
    evidence = {_norm_id(x) for x in f["evidence_ids"]}
    return any(set(s) <= evidence for s in issue["evidence_sets"])


class StubGrader:
    """Simplified grader used until ``auditgym.grader.Grader`` exists."""

    def score(self, findings, *, truth: dict, public: dict, inspection_steps: int) -> dict:
        currencies = set(public["manifest"]["currencies"])
        errors = _validate(findings, truth["episode_id"], currencies)
        if errors:
            return _invalid(errors)
        issues = truth["issues"]
        used: set[int] = set()
        tp = 0
        for f in findings["findings"]:
            for i, issue in enumerate(issues):
                if i not in used and _compatible(f, issue):
                    used.add(i)
                    tp += 1
                    break
        fp = len(findings["findings"]) - tp
        fn = len(issues) - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        if not issues and not findings["findings"]:
            f1 = 1.0
        else:
            f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
        reward = max(-1.0, min(1.0, 2 * f1 - 1 - STEP_COST * inspection_steps))
        return {"reward": reward, "precision": precision, "recall": recall, "f1": f1,
                "tp": tp, "fp": fp, "fn": fn, "errors": []}
