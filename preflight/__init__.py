"""Preflight: a deterministic check engine for "is this project ready to submit?".

The model proposes, code decides. Required checks are plain code with tests, and
"ready" exists only when `python3 -m preflight verdict` says so for the current commit.
"""

__version__ = "1.0.0"
