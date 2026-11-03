"""The check registry and the runner.

To add a check type: write `def my_check(ctx, params) -> Outcome`, wrap it in a `CheckType`
(see any module here) and add it to `CHECK_TYPES` below. Lint and docs pick it up from there.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from .. import gitutil
from ..model import FAIL, PASS, SKIP, WARN, CheckSpec, Outcome, Result, Ruleset, skipped
from .base import CheckType, Context, Param
from .command import COMMAND
from .files import FILE_ABSENT, FILE_CONTAINS, FILE_EXISTS, MAX_FILE_MB, TEXT_ABSENT
from .gitchecks import GIT_CLEAN, GIT_FIRST_COMMIT_AFTER, GIT_REMOTE_PUBLIC
from .links import LINKS_OK
from .readme import README_HEADINGS
from .secrets import SECRET_SCAN, redact
from .video import VIDEO_LINK_PRESENT, VIDEO_MAX_SECONDS

CHECK_TYPES: dict[str, CheckType] = {
    ctype.name: ctype
    for ctype in (
        FILE_EXISTS,
        FILE_ABSENT,
        README_HEADINGS,
        FILE_CONTAINS,
        TEXT_ABSENT,
        SECRET_SCAN,
        GIT_FIRST_COMMIT_AFTER,
        GIT_CLEAN,
        GIT_REMOTE_PUBLIC,
        LINKS_OK,
        VIDEO_LINK_PRESENT,
        VIDEO_MAX_SECONDS,
        MAX_FILE_MB,
        COMMAND,
    )
}

__all__ = ["CHECK_TYPES", "CheckType", "Context", "Param", "run_checks", "run_one"]


def with_defaults(ctype: CheckType, params: Mapping[str, Any]) -> dict[str, Any]:
    """The ruleset's params plus the type's defaults for anything left out."""
    merged = {name: param.default for name, param in ctype.params.items() if not param.required}
    merged.update(params)
    return merged


def run_one(spec: CheckSpec, ctx: Context) -> Outcome:
    """Run one check. Whatever goes wrong inside it becomes a SKIP with a reason, never a crash."""
    ctype = CHECK_TYPES.get(spec.type)
    if ctype is None:
        return skipped(f"unknown check type '{spec.type}'")
    try:
        outcome = ctype.run(ctx, with_defaults(ctype, spec.params))
    except gitutil.GitError as exc:
        return skipped(f"git problem, cannot verify: {exc}")
    except OSError as exc:
        return skipped(f"cannot read a file, cannot verify: {exc}")
    except Exception as exc:  # noqa: BLE001 - a bug in one check must never stop the run
        return skipped(f"check crashed ({type(exc).__name__}: {exc}); cannot verify")
    if not isinstance(outcome, Outcome) or outcome.status not in (PASS, FAIL, SKIP):
        return skipped("check returned an invalid outcome; cannot verify")
    detail = redact(" ".join(outcome.detail.split())) or "(no detail)"
    return Outcome(outcome.status, detail)


def run_checks(ruleset: Ruleset, ctx: Context, only: Collection[str] | None = None) -> list[Result]:
    """Run the ruleset in order. Checks outside `only` are recorded as SKIP, so they still block."""
    results: list[Result] = []
    for spec in ruleset.checks:
        if only is not None and spec.id not in only:
            outcome = skipped("not run (excluded by --only)")
        else:
            outcome = run_one(spec, ctx)
        status = outcome.status
        if status == FAIL and not spec.required:
            status = WARN  # a non-required check that failed is a warning, not a blocker
        results.append(
            Result(spec.id, spec.title, spec.type, spec.required, status, outcome.detail, spec.fix_hint)
        )
    return results
