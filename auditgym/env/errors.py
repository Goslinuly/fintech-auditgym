"""Bounded, non-revealing action errors.

Every error the agent can see is ``{"code": <code>, "message": <fixed text>}``. Messages are
fixed strings chosen by the environment: they never echo the agent's input, never list
existing IDs, and never contain paths, stack traces or anything from the grader's files.
"""

from __future__ import annotations

MAX_MESSAGE_LEN = 200

UNKNOWN_ACTION = "unknown_action"
BAD_ARGS = "bad_args"
NOT_FOUND = "not_found"
EPISODE_OVER = "episode_over"
TIME_LIMIT = "time_limit"

ERROR_CODES = (UNKNOWN_ACTION, BAD_ARGS, NOT_FOUND, EPISODE_OVER, TIME_LIMIT)


class ActionError(Exception):
    """Raised inside an action handler; turned into an observation error by the env."""

    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message[:MAX_MESSAGE_LEN]

    def to_obs(self) -> dict:
        return {"code": self.code, "message": self.message}


def bad_args(message: str) -> ActionError:
    return ActionError(BAD_ARGS, message)


def not_found(message: str = "no record with this id") -> ActionError:
    return ActionError(NOT_FOUND, message)
