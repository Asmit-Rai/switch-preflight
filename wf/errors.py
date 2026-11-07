"""The three ways a wf command can end other than "done", each with its exit code."""

from __future__ import annotations


class WfError(Exception):
    """Exit 1: the environment is wrong or something broke. The text says what to do."""

    code = 1


class Refused(WfError):
    """Exit 2: a rule said no. Nothing was changed. The text says where the task is and what is next."""

    code = 2


class GateClosed(WfError):
    """Exit 3: the ship gate is closed. Nothing left the machine."""

    code = 3
