"""``AuditEnv`` — Gymnasium-style ``reset``/``step`` contract for the audit task.

    env = AuditEnv(mode="train")
    obs, info = env.reset(seed=7, config={})
    obs, reward, terminated, truncated, info = env.step(
        {"name": "list_records", "args": {"table": "ledger", "limit": 50}})

Everything returned is plain JSON data. The environment holds only the public part of the
episode (:class:`~auditgym.env.store.PublicStore`); generation of the private part and
grading happen in a backend (:mod:`auditgym.env.grading`), in a separate process in
``eval`` mode.

Step rules:
* every action — valid, invalid or ``submit`` — costs one step; at most ``max_steps``;
* exploration reward is 0; ``submit`` ends the episode (``terminated=True``); in ``train``
  the reward is the grader's terminal reward, in ``eval`` it is 0 and the grade goes only
  to :attr:`AuditEnv.grade_log`;
* running out of steps or wall-clock time gives ``truncated=True`` and is graded as an
  empty answer (``findings: []``);
* a step after the end returns an ``episode_over`` error and changes nothing; a second
  ``submit`` is never re-graded.
"""

from __future__ import annotations

import json
import time

from auditgym import ENV_VERSION

from . import errors as E
from .calculator import CalcError, calculate
from .grading import InlineBackend, ProcessBackend
from .store import MAX_PAGE, TABLES, PublicStore

MODES = ("train", "eval")
MAX_ACTION_BYTES = 64 * 1024

TASK = (
    "You are auditing one reporting period of a fictional company. Investigate the public "
    "records with the actions below and submit the accounting errors you can prove. "
    "Error types: duplicate_invoice, payment_allocation, fx_conversion, period_cutoff. "
    "Some companies have no errors; suspicious-looking but correct records also exist. "
    "Money is in integer minor units; FX rates are decimal strings; times are ISO-8601 "
    "with offset. Refer to records as '<table>:<id>', e.g. 'ledger:L0042', "
    "'invoices:INV-7Q2K', 'payments:PAY-19XA', 'fx:2026-03-31/USD/KZT'. Submit "
    "{episode_id, findings: [{type, affected_ids, claimed_amount_minor, currency, "
    "evidence_ids, explanation}]} with at most 30 findings, 10 evidence ids and 1000 "
    "characters of explanation each. Every action costs one step."
)

ACTIONS = (
    {
        "name": "list_records",
        "args": {"table": "ledger|invoices|payments|fx", "cursor": "string|null",
                 "limit": f"1..{MAX_PAGE}"},
        "description": "One page of a table in a fixed order; pass next_cursor for the next page.",
    },
    {
        "name": "get_record",
        "args": {"table": "ledger|invoices|payments|fx", "id": "string"},
        "description": "One record by id ('L0042' or 'ledger:L0042').",
    },
    {
        "name": "get_document",
        "args": {"doc_id": "string"},
        "description": "Full invoice (with line_items) or payment by doc_id, invoice_id or "
                       "payment_id.",
    },
    {
        "name": "calculator",
        "args": {"expression": "string"},
        "description": "Decimal arithmetic: numbers, + - * / and parentheses only.",
    },
    {
        "name": "submit",
        "args": {"findings": "object"},
        "description": "Submit the final answer; ends the episode.",
    },
)
ACTION_NAMES = tuple(a["name"] for a in ACTIONS)

_ARGS = {
    "list_records": ({"table"}, {"table", "cursor", "limit"}),
    "get_record": ({"table", "id"}, {"table", "id"}),
    "get_document": ({"doc_id"}, {"doc_id"}),
    "calculator": ({"expression"}, {"expression"}),
    "submit": ({"findings"}, {"findings"}),
}


class _State:
    __slots__ = ("meta", "started", "steps", "store", "submitted", "terminated", "truncated")

    def __init__(self, store: PublicStore, meta: dict, started: float):
        self.store = store
        self.meta = meta
        self.steps = 0
        self.terminated = False
        self.truncated = False
        self.started = started
        self.submitted = False

    @property
    def done(self) -> bool:
        return self.terminated or self.truncated


class AuditEnv:
    """RL environment for an AI auditor. See the module docstring for the rules."""

    metadata = {"env_version": ENV_VERSION, "actions": ACTION_NAMES, "tables": TABLES}

    def __init__(
        self,
        mode: str = "train",
        grader=None,
        max_steps: int = 100,
        time_limit_s: float | None = 60.0,
        isolation: str | None = None,
        clock=time.monotonic,
        max_action_bytes: int = MAX_ACTION_BYTES,
    ):
        """
        mode: ``"train"`` (terminal reward returned to the agent) or ``"eval"`` (reward 0).
        grader: grader *class* (or zero-argument factory); default — the full
            ``auditgym.grader.Grader`` if present, else the temporary stub.
        isolation: ``"process"`` or ``"inline"``; default ``"process"`` in eval, ``"inline"``
            in train.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if isolation is None:
            isolation = "process" if mode == "eval" else "inline"
        if isolation not in ("process", "inline"):
            raise ValueError("isolation must be 'process' or 'inline'")
        if not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        self.mode = mode
        self.max_steps = max_steps
        self.time_limit_s = time_limit_s
        self.isolation = isolation
        self.max_action_bytes = max_action_bytes
        self._clock = clock
        backend_cls = ProcessBackend if isolation == "process" else InlineBackend
        self._backend = backend_cls(grader)
        self._state: _State | None = None
        self.grade_log: list[dict] = []
        """Grader results for the runner/diagnostics. Never part of observations or info."""

    # --- lifecycle ----------------------------------------------------------------------
    def reset(self, seed: int | None = None, config: dict | None = None, *,
              episode_dir=None) -> tuple[dict, dict]:
        """Start a new episode: generate it from ``(seed, config)`` or load ``episode_dir``.

        All state of the previous episode (step counter, cursors, timer, submission) is
        discarded.
        """
        if episode_dir is None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise TypeError("reset() needs an integer seed or episode_dir")
        if config is not None and not isinstance(config, dict):
            raise TypeError("config must be a dict")
        self._state = None
        public, meta = self._backend.start(seed, config, episode_dir)
        store = PublicStore(public, meta["episode_id"])
        self._state = _State(store, dict(meta), self._clock())
        obs = {
            "task": TASK,
            "episode_id": meta["episode_id"],
            "manifest": store.manifest,
            "policy": store.policy,
            "tables": store.counts(),
            "actions": json.loads(json.dumps(ACTIONS)),
            "step": 0,
            "steps_remaining": self.max_steps,
        }
        return obs, self._info()

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
        self._state = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- step ---------------------------------------------------------------------------
    def step(self, action) -> tuple[dict, float, bool, bool, dict]:
        st = self._state
        if st is None:
            raise RuntimeError("call reset() before step()")
        if st.done:
            err = E.ActionError(E.EPISODE_OVER, "the episode has ended; call reset()")
            obs = self._obs(st, None, error=err)
            return obs, 0.0, st.terminated, st.truncated, self._info()

        if self._out_of_time(st):
            return self._truncate(st, None, reason="time_limit",
                                  error=E.ActionError(E.TIME_LIMIT, "time limit reached"))

        st.steps += 1
        name = None
        result = error = None
        try:
            name, args = self._parse(action)
            result = self._dispatch(st, name, args)
        except E.ActionError as exc:
            error = exc

        if name == "submit" and error is None:
            return self._submit(st, result)
        if st.steps >= self.max_steps:
            return self._truncate(st, name, reason="step_limit", result=result, error=error)
        if self._out_of_time(st):
            return self._truncate(st, name, reason="time_limit", result=result, error=error)
        return self._obs(st, name, result=result, error=error), 0.0, False, False, self._info()

    # --- helpers ------------------------------------------------------------------------
    def _info(self) -> dict:
        st = self._state
        meta = st.meta if st else {}
        return {
            "episode_id": meta.get("episode_id"),
            "env_version": ENV_VERSION,
            "config_version": meta.get("config_version"),
        }

    def _obs(self, st: _State, name, *, result=None, error=None) -> dict:
        obs = {
            "step": st.steps,
            "steps_remaining": max(0, self.max_steps - st.steps),
            "action": name,
        }
        if error is not None:
            obs["error"] = error.to_obs()
        else:
            obs["result"] = result
        return obs

    def _out_of_time(self, st: _State) -> bool:
        return self.time_limit_s is not None and self._clock() - st.started > self.time_limit_s

    def _parse(self, action) -> tuple[str, dict]:
        if not isinstance(action, dict):
            raise E.bad_args("action must be an object {\"name\": ..., \"args\": {...}}")
        try:
            size = len(json.dumps(action, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            raise E.bad_args("action must be JSON-serialisable") from None
        if size > self.max_action_bytes:
            raise E.bad_args(f"action is larger than {self.max_action_bytes} bytes")
        if set(action) - {"name", "args"}:
            raise E.bad_args("action may only contain 'name' and 'args'")
        name = action.get("name")
        if not isinstance(name, str) or name not in ACTION_NAMES:
            raise E.ActionError(E.UNKNOWN_ACTION, "unknown action; see the actions list")
        args = action.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise E.bad_args("args must be an object")
        required, allowed = _ARGS[name]
        if set(args) - allowed:
            raise E.bad_args(f"unexpected argument for {name}")
        if required - set(args):
            raise E.bad_args(f"missing argument for {name}")
        return name, args

    def _dispatch(self, st: _State, name: str, args: dict):
        store = st.store
        if name == "list_records":
            return store.list_records(args["table"], args.get("cursor"), args.get("limit", MAX_PAGE))
        if name == "get_record":
            return store.get_record(args["table"], args["id"])
        if name == "get_document":
            return store.get_document(args["doc_id"])
        if name == "calculator":
            expr = args["expression"]
            try:
                return {"expression": expr, "value": calculate(expr)}
            except CalcError as exc:
                raise E.bad_args(f"calculator: {exc}") from None
        if name == "submit":
            findings = args["findings"]
            count = None
            if isinstance(findings, dict) and isinstance(findings.get("findings"), list):
                count = len(findings["findings"])
            return {"findings": findings, "count": count}
        raise E.ActionError(E.UNKNOWN_ACTION, "unknown action; see the actions list")

    def _grade(self, st: _State, findings, inspection_steps: int, reason: str) -> float:
        grade = self._backend.score(findings, inspection_steps)
        self.grade_log.append({
            "episode_id": st.meta["episode_id"],
            "mode": self.mode,
            "reason": reason,
            "steps": st.steps,
            "inspection_steps": inspection_steps,
            **grade,
        })
        return float(grade["reward"]) if self.mode == "train" else 0.0

    def _submit(self, st: _State, submission: dict):
        st.terminated = True
        st.submitted = True
        # Spec: inspection_steps = steps before submit, not counting the submit itself.
        reward = self._grade(st, submission["findings"], st.steps - 1, reason="submit")
        obs = self._obs(st, "submit", result={"submitted": True, "count": submission["count"]})
        return obs, reward, True, False, self._info()

    def _truncate(self, st: _State, name, *, reason: str, result=None, error=None):
        st.truncated = True
        empty = {"episode_id": st.meta["episode_id"], "findings": []}
        reward = self._grade(st, empty, st.steps, reason=reason)
        obs = self._obs(st, name, result=result, error=error)
        obs["truncated_reason"] = reason
        return obs, reward, False, True, self._info()
